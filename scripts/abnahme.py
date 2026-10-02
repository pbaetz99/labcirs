# Copyright (C) 2026 Philipp Bätz
#
# This file is part of LabCIRS.
#
# LabCIRS is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# LabCIRS is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with LabCIRS.
# If not, see <https://www.gnu.org/licenses/>.

"""Acceptance run: every function of the function list, per role, in a real browser.

Runs with Playwright in the container mcr.microsoft.com/playwright/python against the acceptance
stack (see scripts/acceptance.sh), which holds the demo data of seed_demo_data:

    sh scripts/acceptance.sh dc run --rm playwright python scripts/abnahme.py

Every point of the list (A1-A6 anonymous, R1-R9 reviewer, S1-S2 superuser, F1-F5 five targeted checks,
SEC security) prints PASS or FAIL with the values that were compared. Evidence
goes to artifacts/accept/: screenshots in screens/, the mails of Mailpit in mails/, files in
files/. The browser checks what a person sees; the database is read where the page cannot show it
(counts, a deleted address, the hash of a new password). All texts are invented. Exit status 1 if
any point fails.

The checks depend on each other (the report of A3 is published in R4, the mails of R9 need the
configuration of R5), so the run needs a fresh stack: sh scripts/acceptance.sh reset
"""

import base64
import hashlib
import io
import json
import os
import re
import secrets
import sys
import time
import traceback
import urllib.request
from datetime import date, timedelta
from urllib.parse import urlsplit

import psycopg
from PIL import Image
from playwright.sync_api import sync_playwright

BASE = os.environ.get('ACCEPT_URL', 'https://cirs.test').rstrip('/')
MAILPIT = os.environ.get('MAILPIT_URL', 'http://mailpit:8025').rstrip('/')
DSN = os.environ.get('ACCEPT_DB_DSN', '')
OUT = os.environ.get('ACCEPT_OUT', 'artifacts/accept')
DEPT = 'demo'
QM_USER = 'qm-demo'
QM_PASSWORD = os.environ.get('DEMO_QM_PASSWORD', '')
ADMIN_USER = 'admin-demo'
ADMIN_PASSWORD = os.environ.get('DEMO_ADMIN_PASSWORD', '')
ORIGIN = '{0.scheme}://{0.netloc}'.format(urlsplit(BASE))
REPORTER_ADDRESS = 'melder-%s@example.test' % secrets.token_hex(3)  # a private address, invented
MONTHS = ['Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli', 'August', 'September',
          'Oktober', 'November', 'Dezember']

RESULTS = []
STATE = {}
CSP_HITS = []
FOREIGN = []

COLLECT_CSP = """
window.__cspViolations = [];
document.addEventListener('securitypolicyviolation', (event) => {
    window.__cspViolations.push(event.violatedDirective + ' ' + event.blockedURI);
});
"""


# --- results, evidence ----------------------------------------------------------------------

def check(point, text, ok, detail=''):
    ok = bool(ok)
    print('%s  %-5s %s%s' % ('PASS' if ok else 'FAIL', point, text,
                             '  [%s]' % detail if detail else ''), flush=True)
    RESULTS.append((point, text, ok, detail))
    return ok


def shot(page, name):
    os.makedirs(OUT + '/screens', exist_ok=True)
    page.screenshot(path='%s/screens/%s.png' % (OUT, name), full_page=True)


def write_file(name, content):
    os.makedirs(OUT + '/files', exist_ok=True)
    mode = 'wb' if isinstance(content, bytes) else 'w'
    with open('%s/files/%s' % (OUT, name), mode, **({} if mode == 'wb' else {'encoding': 'utf-8'})) as f:
        f.write(content)


def section(title):
    print('\n== %s' % title, flush=True)


# --- database, mail ---------------------------------------------------------------------------

def sql(query, *params):
    with psycopg.connect(DSN, autocommit=True) as connection:
        return connection.execute(query, params).fetchall()


def scalar(query, *params):
    return sql(query, *params)[0][0]


def mailpit(path, method='GET'):
    request = urllib.request.Request(MAILPIT + path, method=method)
    with urllib.request.urlopen(request, timeout=10) as response:
        body = response.read()
    try:
        return json.loads(body)
    except ValueError:
        return body.decode()  # DELETE answers with the plain text "ok"


def clear_mails():
    mailpit('/api/v1/messages', 'DELETE')


def all_mails():
    """Every mail in Mailpit, oldest first, with its text."""
    summaries = mailpit('/api/v1/messages?limit=500')['messages']
    mails = []
    for summary in reversed(summaries):
        message = mailpit('/api/v1/message/' + summary['ID'])
        mails.append({'to': [a['Address'] for a in message['To']],
                      'from': message['From']['Address'], 'subject': message['Subject'],
                      'text': message['Text'], 'html': message.get('HTML', ''),
                      'date': message['Date']})
    return mails


def mails_to(address, wait=0):
    """The mails to an address; waits up to `wait` seconds for the first one."""
    deadline = time.time() + wait
    while True:
        found = [m for m in all_mails() if address in m['to']]
        if found or time.time() >= deadline:
            return found
        time.sleep(0.5)


def mailpit_shot(browser, name):
    """The inbox of Mailpit as the QM would see it. A plain context: it is not part of the site."""
    context = browser.new_context(ignore_https_errors=True, locale='de-DE', viewport={'width': 1440, 'height': 900})
    page = context.new_page()
    page.goto(MAILPIT + '/')
    page.wait_for_selector('.message', timeout=15000)
    shot(page, name)
    context.close()


def save_mails(name):
    lines = []
    for number, mail in enumerate(all_mails(), start=1):
        lines += ['== Mail %d ==' % number, 'From: %s' % mail['from'],
                  'To: %s' % ', '.join(mail['to']), 'Subject: %s' % mail['subject'],
                  'Date: %s' % mail['date'], '', mail['text'].strip(), '']
    os.makedirs(OUT + '/mails', exist_ok=True)
    with open('%s/mails/%s.txt' % (OUT, name), 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))


def quiet_period(seconds=2.5):
    """Waits so that a mail that should not come has had time to arrive."""
    time.sleep(seconds)


# --- browser ----------------------------------------------------------------------------------

def new_context(browser, width=1440, height=900):
    """A new browser context; every page in it reports CSP messages and foreign requests."""
    context = browser.new_context(ignore_https_errors=True, locale='de-DE',
                                  viewport={'width': width, 'height': height})
    context.add_init_script(COLLECT_CSP)
    context.set_default_timeout(20000)

    def attach(page):
        page.on('console', lambda m: CSP_HITS.append((page.url, m.text))
                if 'content security policy' in m.text.lower() else None)

        def on_request(request):
            parts = urlsplit(request.url)
            if parts.scheme in ('http', 'https') and \
                    '{0.scheme}://{0.netloc}'.format(parts) != ORIGIN:
                FOREIGN.append((page.url, request.url))
        page.on('request', on_request)
    context.on('page', attach)
    return context


def collect_csp_events(page):
    try:
        for event in page.evaluate('window.__cspViolations || []'):
            CSP_HITS.append((page.url, 'violation event: ' + event))
    except Exception:
        pass  # the page navigated away; its events are gone


def login(page, user, password):
    page.goto(BASE + '/login/')
    page.fill('#username', user)
    page.fill('#password', password)
    page.get_by_role('button', name='Anmelden').click()
    page.wait_for_load_state()


def csrf_token(html):
    return re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html).group(1)


def main_text(page):
    return page.inner_text('main') if page.locator('main').count() else page.inner_text('body')


def fits_width(page, point, text):
    """The page must not scroll sideways (the QM works at 1440 px)."""
    sizes = page.evaluate('[document.documentElement.scrollWidth, document.documentElement.clientWidth]')
    return check(point, text + ': kein waagrechtes Scrollen bei 1440 px', sizes[0] <= sizes[1],
                 'scrollWidth %d, clientWidth %d' % tuple(sizes))


def rows(page):
    return page.locator('#result_list tbody tr').count()


# --- invented files -----------------------------------------------------------------------------

def phone_photo():
    """A JPEG as a phone makes it: turned by an EXIF flag, with position, device and author."""
    image = Image.new('RGB', (600, 400), (205, 214, 228))
    for x in range(600):
        for y in range(0, 400, 40):
            image.putpixel((x, y), (30 + x % 200, 74, 140))
    exif = Image.Exif()
    exif[0x010F] = 'DemoPhone Inc.'
    exif[0x0110] = 'DP-1'
    exif[0x0112] = 6  # to be turned by 90 degrees to show it upright
    exif[0x0131] = 'DemoCam 1.0'
    exif[0x013B] = 'Max Muster'
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2], gps[3], gps[4] = 'N', (48.0, 12.0, 0.0), 'E', (16.0, 21.0, 0.0)
    out = io.BytesIO()
    image.save(out, 'JPEG', quality=90, exif=exif)
    return out.getvalue()


def small_jpeg():
    out = io.BytesIO()
    Image.new('RGB', (64, 64), (0, 100, 0)).save(out, 'JPEG')
    return out.getvalue()


def metadata_of(data):
    """What a stored photo still tells: pixels size, EXIF entries, GPS block, marker strings."""
    image = Image.open(io.BytesIO(data))
    exif = image.getexif()
    return {'format': image.format, 'size': image.size, 'exif_entries': len(exif),
            'gps_entries': len(exif.get_ifd(0x8825)),
            'raw_markers': [m.decode() for m in (b'Exif', b'DemoPhone', b'Max Muster', b'DemoCam',
                                                  b'ns.adobe.com') if m in data]}


# --- report form -------------------------------------------------------------------------------

def fill_report(page, marker, photo=None, org_unit=None, email=None, consent=True):
    page.fill('#id_date', (date.today() - timedelta(days=2)).isoformat())
    page.fill('#id_incident', 'Demo %s: erfundenes Ereignis auf Station.' % marker)
    page.fill('#id_reason', 'Demo %s: erfundene Ursache.' % marker)
    page.fill('#id_immediate_action', 'Demo %s: erfundene Sofortmaßnahme.' % marker)
    page.select_option('#id_preventability', 'avoidable')
    if photo is not None:
        page.set_input_files('#id_photo', files=[photo])
    page.check('#id_public_0' if consent else '#id_public_1')
    if org_unit:
        page.select_option('#id_org_unit', label=org_unit)
    if email:
        page.fill('#id_reporter_email', email)


def submit_report(page, expect_success=True):
    page.get_by_role('button', name='Meldung absenden').click()
    if expect_success:
        page.wait_for_selector('.ui-meldecode')
        return page.inner_text('.ui-meldecode').strip()
    page.wait_for_load_state()
    return None


def new_report(context, marker, **options):
    """Reports as an anonymous person in a new page; returns the page (on the success page) and the code."""
    page = context.new_page()
    page.goto(BASE + '/incidents/%s/create/' % DEPT)
    fill_report(page, marker, **options)
    code = submit_report(page)
    return page, code


def enter_code(page, code):
    page.goto(BASE + '/incidents/%s/search/' % DEPT)
    page.fill('#id_incident_code', code)
    page.get_by_role('button', name='Code prüfen').click()
    page.wait_for_load_state()


def reply_as_reporter(page, text):
    page.fill('#id_text', text)
    page.get_by_role('button', name='Antwort senden').click()
    page.wait_for_load_state()


def incident_id(code):
    return scalar('select id from cirs_criticalincident where comment_code = %s', code)


def month_year(days_ago):
    day = date.today() - timedelta(days=days_ago)
    return '%s %d' % (MONTHS[day.month - 1], day.year)


# --- A: anonymous -----------------------------------------------------------------------------

def a1_start_page(browser):
    section('A1 Startseite führt zur Fallliste')
    page = new_context(browser).new_page()
    page.goto(BASE + '/')
    check('A1', 'Die Startseite leitet auf die Fallliste der einzigen Abteilung weiter',
          page.url == BASE + '/incidents/%s/' % DEPT, page.url)
    check('A1', 'Die Fallliste hat die Überschrift "Veröffentlichte Fälle"',
          page.locator('h1').inner_text() == 'Veröffentlichte Fälle')
    shot(page, 'A1-start-page')
    page.context.close()


def a2_published_cases(browser):
    section('A2 Veröffentlichte Fälle: Inhalt, Suche, Blättern')
    context = new_context(browser)
    page = context.new_page()
    page.goto(BASE + '/incidents/%s/?q=Formular' % DEPT)
    row = page.locator('tbody tr', has_text='Doppelt ausgefülltes Formular')
    cells = [c.strip() for c in row.locator('td').all_inner_texts()]
    check('A2', 'Titel, Beschreibung, Maßnahmen und Monat/Jahr des Falls stehen in einer Zeile',
          len(cells) == 5 and cells[0] == month_year(70)
          and cells[1] == 'Doppelt ausgefülltes Formular'
          and cells[2].startswith('Zwei Vorlagen') and cells[3].startswith('Es gibt nur noch'),
          ' | '.join(cells))
    link = row.get_by_role('link', name='Foto ansehen')
    href = link.get_attribute('href')
    response = context.request.get(BASE + href)
    check('A2', 'Das Foto des Falls ist verlinkt und wird als Bild ausgeliefert',
          response.status == 200 and response.headers.get('content-type', '').startswith('image/'),
          '%s %s' % (href, response.headers.get('content-type')))
    shot(page, 'A2-case-with-photo')
    page.goto(BASE + '/incidents/%s/?q=Wagen' % DEPT)
    check('A2', 'Ein Fall ohne Foto zeigt "kein Foto"', 'kein Foto' in page.locator('tbody tr').first.inner_text())

    page.goto(BASE + '/incidents/%s/' % DEPT)
    page.fill('#suche-q', 'Wagen')
    page.get_by_role('button', name='Suchen').click()
    page.wait_for_load_state()
    hits = page.locator('tbody tr').count()
    check('A2', 'Die Suche nach "Wagen" findet genau den Fall "Wagen im Flur"',
          hits == 1 and 'Wagen im Flur' in page.locator('tbody').inner_text(), 'Treffer: %d' % hits)
    check('A2', 'Die Suche nennt ihren Stand und bietet "Filter aufheben"',
          page.get_by_role('link', name='Filter aufheben').count() >= 1)
    shot(page, 'A2-search')
    page.goto(BASE + '/incidents/%s/?q=zzzunbekannt' % DEPT)
    check('A2', 'Eine Suche ohne Treffer zeigt einen Leerzustand mit Ausweg',
          'passt zu Ihrer Suche' in main_text(page) and page.locator('tbody tr').count() == 0)
    shot(page, 'A2-search-empty')

    total = scalar("select count(*) from cirs_publishableincident p join cirs_criticalincident c "
                   "on c.id = p.critical_incident_id join cirs_department d on d.id = c.department_id "
                   "where p.publish and d.label = %s", DEPT)
    page.goto(BASE + '/incidents/%s/' % DEPT)
    first = page.locator('tbody tr').count()
    check('A2', 'Seite 1 zeigt 25 Fälle, "Seite 1 von 2"',
          first == 25 and 'Seite 1 von 2' in main_text(page), 'Fälle in der Datenbank: %d' % total)
    shot(page, 'A2-paging-page-1')
    page.get_by_role('link', name='Nächste Seite').click()
    page.wait_for_load_state()
    second = page.locator('tbody tr').count()
    check('A2', 'Seite 2 zeigt den Rest und führt zurück',
          second == total - 25 and 'Seite 2 von 2' in main_text(page)
          and page.get_by_role('link', name='Vorherige Seite').count() == 1,
          '%d Fälle auf Seite 2' % second)
    shot(page, 'A2-paging-page-2')
    response = page.goto(BASE + '/incidents/%s/?page=99' % DEPT)
    check('A2', 'Eine Seite hinter dem Ende ist ein 404, kein Fehler', response.status == 404, str(response.status))
    context.close()


def a3_report(browser):
    section('A3 Meldung erfassen mit allen Feldern, Foto, Organisationseinheit, Erfolgsseite mit Code')
    context = new_context(browser)
    STATE['marker'] = marker = 'Q' + secrets.token_hex(3)
    photo = phone_photo()
    original = metadata_of(photo)
    check('A3', 'Das Testfoto trägt EXIF-Daten (Ort, Gerät, Autor) und ist gedreht',
          original['exif_entries'] > 0 and original['gps_entries'] > 0 and original['size'] == (600, 400),
          str(original))
    STATE['reports_before'] = scalar('select count(*) from cirs_criticalincident')
    page = context.new_page()
    page.goto(BASE + '/incidents/%s/create/' % DEPT)
    shot(page, 'A3-form-empty')
    options = [o.strip() for o in page.locator('#id_org_unit option').all_inner_texts()]
    check('A3', 'Die Organisationseinheit steht als Auswahl mit zwei Ebenen im Formular',
          'Untereinheit B1' in options and 'Untereinheit A2' in options, ', '.join(options))
    check('A3', 'Das optionale E-Mail-Feld ist da (die Absenderadresse ist gesetzt, siehe A6)',
          page.locator('#id_reporter_email').count() == 1)
    fill_report(page, marker, photo={'name': 'IMG_4711.jpg', 'mimeType': 'image/jpeg', 'buffer': photo},
                org_unit='Untereinheit B1')
    shot(page, 'A3-form-filled')
    code = submit_report(page)
    STATE['code'] = code
    shot(page, 'A3-success-code')
    check('A3', 'Die Erfolgsseite zeigt einen Code mit 16 Zeichen',
          re.fullmatch(r'[a-z2-9]{16}', code) is not None, 'Länge %d' % len(code))
    row = sql('select date, incident, reason, immediate_action, preventability, photo, public, '
              'org_unit_id, department_id from cirs_criticalincident where comment_code = %s', code)[0]
    unit = scalar("select id from cirs_orgunit where name = 'Untereinheit B1'")
    check('A3', 'Alle Felder sind gespeichert: Datum, drei Texte, Vermeidbarkeit, Foto, Zustimmung, Einheit',
          row[0] == date.today() - timedelta(days=2) and marker in row[1] and marker in row[2]
          and marker in row[3] and row[4] == 'avoidable' and row[5] and row[6] is True
          and row[7] == unit, 'Foto: %s, Einheit %s' % (row[5], row[7]))
    STATE['id'] = incident_id(code)
    STATE['photo_path'] = row[5]
    page.reload()
    check('A3', 'Der Code erscheint nur einmal: nach dem Neuladen steht nur der Hinweis',
          page.locator('.ui-meldecode').count() == 0
          and 'nur einmal' in main_text(page), '')
    shot(page, 'A3-success-reloaded')
    context.close()

    # the stored photo, fetched like any visitor of the published list would
    context = new_context(browser)
    data = context.request.get(BASE + '/media/' + STATE['photo_path']).body()
    after = metadata_of(data)
    write_file('photo-before.json', json.dumps(original, indent=2))
    write_file('photo-after.json', json.dumps(after, indent=2))
    check('SEC', 'Das gespeicherte Handyfoto hat keine EXIF-Daten, kein GPS und keine Marker mehr',
          after['exif_entries'] == 0 and after['gps_entries'] == 0 and not after['raw_markers'],
          str(after))
    check('SEC', 'Das gespeicherte Foto steht aufrecht (400x600 statt 600x400) und hat einen Zufallsnamen',
          after['size'] == (400, 600) and re.fullmatch(r'photos/\d{4}/\d{2}/\d{2}/[0-9a-f]{32}\.jpg',
                                                      STATE['photo_path']) is not None,
          '%s, %s' % (after['size'], STATE['photo_path']))
    context.close()


def a4_my_report(browser):
    section('A4 Mit dem Code: eigene Meldung, Status, Kommentare, Antwort')
    # a seeded report with replies of both sides
    code = scalar("select comment_code from cirs_criticalincident where incident like %s", 'Demo 2:%')
    context = new_context(browser)
    page = context.new_page()
    page.goto(BASE + '/incidents/%s/search/' % DEPT)
    shot(page, 'A4-code-entry')
    enter_code(page, code)
    text = main_text(page)
    check('A4', 'Die Meldung zeigt den Stand in der Sprache der Meldenden ("In Bearbeitung")',
          'In Bearbeitung' in text)
    check('A4', 'Die Meldung zeigt ihre Angaben', 'Ein Gerät wurde nach der Reinigung' in text)
    cards = page.locator('article.ui-card h3').all_inner_texts()
    check('A4', 'Die Rückmeldungen stehen mit Rollen, nie mit Namen: Qualitätsmanagement / Meldende Person',
          any(c.startswith('Qualitätsmanagement') for c in cards)
          and any(c.startswith('Meldende Person') for c in cards)
          and 'qm-demo' not in text and 'reporter-demo' not in text, ' | '.join(cards))
    shot(page, 'A4-detail-seeded')
    context.close()

    # the report of A3: status "Eingegangen", reply, end of access
    context = new_context(browser)
    page = context.new_page()
    enter_code(page, STATE['code'])
    check('A4', 'Der Code der neuen Meldung führt zu ihr, Stand "Eingegangen"',
          'Eingegangen' in main_text(page) and STATE['marker'] in main_text(page))
    check('A4', 'Das Foto der Meldung ist als Link mit Text da',
          page.get_by_role('link', name=re.compile('Foto in voller Größe')).count() == 1)
    before = scalar('select count(*) from cirs_comment where critical_incident_id = %s', STATE['id'])
    STATE['reply'] = 'Demo %s: erfundene Antwort der Meldenden.' % STATE['marker']
    reply_as_reporter(page, STATE['reply'])
    shot(page, 'A4-reply-sent')
    after = sql('select c.text, u.username from cirs_comment c join auth_user u on u.id = c.author_id '
                'where c.critical_incident_id = %s', STATE['id'])
    check('A4', 'Die Antwort ist gespeichert und steht unter "Meldende Person"',
          before == 0 and len(after) == 1 and after[0][0] == STATE['reply']
          and 'Meldende Person' in page.locator('article.ui-card h3').all_inner_texts()[0]
          and 'Ihre Antwort wurde gespeichert' in main_text(page), 'Autor in der DB: %s' % after[0][1])
    page.get_by_role('button', name='Zugang beenden').click()
    page.wait_for_load_state()
    response = context.request.get(BASE + '/incidents/%s/%d/' % (DEPT, STATE['id']), max_redirects=0)
    check('A4', '"Zugang beenden" schließt die Meldung: der Aufruf führt zurück zur Codeeingabe',
          response.status == 302 and response.headers['location'].endswith('/incidents/%s/search/' % DEPT),
          '%s -> %s' % (response.status, response.headers.get('location')))
    context.close()


def a5_language(browser):
    section('A5 Sprachwahl nur bei mehreren Sprachen')
    context = new_context(browser)
    page = context.new_page()
    page.goto(BASE + '/incidents/%s/' % DEPT)
    buttons = page.locator('nav form[action$="/i18n/setlang/"] button').all_inner_texts()
    check('A5', 'Bei zwei Sprachen zeigt die Kopfleiste die Sprachwahl', buttons == ['Deutsch', 'English'],
          ', '.join(buttons))
    shot(page, 'A5-language-switch-de')
    page.locator('nav form[action$="/i18n/setlang/"] button', has_text='English').click()
    page.wait_for_load_state()
    check('A5', 'Ein Klick auf "English" stellt die Seite um und bleibt auf der Fallliste',
          page.locator('h1').inner_text() == 'Published cases' and page.url.endswith('/incidents/%s/' % DEPT)
          and page.locator('html').get_attribute('lang') == 'en', page.url)
    shot(page, 'A5-language-switch-en')
    context.close()
    # The single-language case needs another configuration: scripts/acceptance-checks.sh (check A5)


# --- F: the five targeted checks ---------------------------------------------------------

def f1_foreign_report(browser):
    section('F1 Fremde Meldung: pk in der URL ändern (GET), Kommentar an fremde Meldung (POST)')
    ids = {name: scalar("select id from cirs_criticalincident where incident like %s", 'Demo %d:%%' % n)
           for name, n in (('d1', 1), ('d2', 2), ('d3', 3))}
    own_id = STATE['id']
    foreign = [ids['d1'], ids['d2'], ids['d3']]
    count_before = scalar('select count(*) from cirs_comment')
    context = new_context(browser)
    api = context.request
    search_url = BASE + '/incidents/%s/search/' % DEPT
    ok = True
    details = []
    for pk in foreign + [own_id]:
        response = api.get(BASE + '/incidents/%s/%d/' % (DEPT, pk), max_redirects=0)
        body = response.text()
        good = (response.status == 302 and response.headers['location'] == '/incidents/%s/search/' % DEPT
                and 'Demo' not in body and STATE['marker'] not in body)
        ok = ok and good
        details.append('%d: %s' % (pk, response.status))
    check('F1', 'GET der Detailseite ohne Code führt zur Codeeingabe, ohne Daten (4 fremde pk)', ok,
          ', '.join(details))
    token = csrf_token(api.get(search_url).text())
    headers = {'Referer': search_url, 'Origin': ORIGIN}
    results = []
    for pk in foreign:
        response = api.post(BASE + '/incidents/%s/%d/' % (DEPT, pk), max_redirects=0, headers=headers,
                            form={'csrfmiddlewaretoken': token, 'text': 'Demo F1: Kommentar ohne Code'})
        results.append((response.status, response.headers.get('location')))
    count_after = scalar('select count(*) from cirs_comment')
    check('F1', 'POST eines Kommentars an eine fremde Meldung ohne Code: Umleitung zur Codeeingabe, kein Kommentar',
          all(s == 302 and l == '/incidents/%s/search/' % DEPT for s, l in results)
          and count_after == count_before, 'Antworten %s, Kommentare %d -> %d' % (results, count_before, count_after))

    # with the code of one report, the other reports stay closed
    page = context.new_page()
    enter_code(page, scalar('select comment_code from cirs_criticalincident where id = %s', ids['d1']))
    open_own = api.get(BASE + '/incidents/%s/%d/' % (DEPT, ids['d1']), max_redirects=0)
    other = api.get(BASE + '/incidents/%s/%d/' % (DEPT, ids['d2']), max_redirects=0)
    token = csrf_token(page.content())
    post = api.post(BASE + '/incidents/%s/%d/' % (DEPT, ids['d2']), max_redirects=0, headers=headers,
                    form={'csrfmiddlewaretoken': token, 'text': 'Demo F1: Kommentar mit fremdem Code'})
    count_after = scalar('select count(*) from cirs_comment')
    check('F1', 'Mit dem Code von Meldung 1 ist Meldung 2 weiter gesperrt (GET und POST), Meldung 1 offen',
          open_own.status == 200 and other.status == 302 and post.status == 302
          and count_after == count_before,
          'eigene %s, fremde GET %s, fremde POST %s' % (open_own.status, other.status, post.status))
    check('F1', 'Eine pk, die es nicht gibt, und eine falsche Abteilung geben 404',
          api.get(BASE + '/incidents/%s/999999/' % DEPT, max_redirects=0).status == 404
          and api.get(BASE + '/incidents/gibtsnicht/%d/' % ids['d1'], max_redirects=0).status == 404)
    context.close()


def f2_legacy_code(browser):
    section('F2 Alter 8-stelliger Code mit Sonderzeichen, mit Leerzeichen und in Großbuchstaben')
    context = new_context(browser)
    page = context.new_page()
    legacy_id = scalar("select id from cirs_criticalincident where comment_code = 'ab#d$f-9'")
    for typed in (' AB#D$F-9 ', 'ab #d$f -9', 'Ab#D$f-9', 'ab#d$f-9'):
        enter_code(page, typed)
        found = page.url == BASE + '/incidents/%s/%d/' % (DEPT, legacy_id)
        check('F2', 'Eingabe "%s" wird erkannt' % typed, found, page.url)
        if not found:
            break
        context.clear_cookies()
    enter_code(page, ' AB#D$F-9 ')
    shot(page, 'F2-legacy-code-detail')
    check('F2', 'Die Meldung des alten Codes zeigt ihren Stand', 'In Beobachtung' in main_text(page)
          or 'Maßnahmen umgesetzt' in main_text(page))
    enter_code(page, 'ab#d$f-8')
    check('F2', 'Ein falscher alter Code wird abgelehnt',
          'keine Meldung gefunden' in main_text(page) and '/search/' in page.url)
    shot(page, 'F2-wrong-code')
    context.close()


def f3_photos(browser):
    section('F3 Handyfotos: TIFF/HEIC, über 10 MB, kaputte Dateien, gedrehtes JPEG')
    context = new_context(browser)
    page = context.new_page()
    tiff = io.BytesIO()
    Image.new('RGB', (80, 80), (200, 0, 0)).save(tiff, 'TIFF')
    heic = b'\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic' + os.urandom(3000)
    big = small_jpeg() + b'\x00' * int(10.6 * 1024 * 1024)
    broken = small_jpeg()[:400]
    not_an_image = b'Dies ist keine Bilddatei.\n' * 20
    cases = [
        ('TIFF', {'name': 'IMG_0001.tiff', 'mimeType': 'image/tiff', 'buffer': tiff.getvalue()}, 'Bitte laden Sie ein Foto im Format'),
        ('HEIC', {'name': 'IMG_0002.HEIC', 'mimeType': 'image/heic', 'buffer': heic}, 'Bitte laden Sie ein Foto im Format'),
        ('über 10 MB', {'name': 'IMG_0003.jpg', 'mimeType': 'image/jpeg', 'buffer': big}, 'größer als 10 MB'),
        ('abgeschnittenes JPEG', {'name': 'IMG_0004.jpg', 'mimeType': 'image/jpeg', 'buffer': broken}, 'Bitte laden Sie ein Foto im Format'),
        ('Textdatei als .jpg', {'name': 'IMG_0005.jpg', 'mimeType': 'image/jpeg', 'buffer': not_an_image}, 'Bitte laden Sie ein Foto im Format'),
    ]
    for label, photo, message in cases:
        count_before = scalar('select count(*) from cirs_criticalincident')
        page.goto(BASE + '/incidents/%s/create/' % DEPT)
        fill_report(page, 'F3', photo=photo)
        with page.expect_response(lambda r: r.request.method == 'POST' and '/create/' in r.url) as info:
            submit_report(page, expect_success=False)
        status = info.value.status
        error = page.locator('.ui-alert--danger').inner_text() if page.locator('.ui-alert--danger').count() else ''
        count_after = scalar('select count(*) from cirs_criticalincident')
        check('F3', '%s: Formularfehler statt HTTP 500, keine Meldung gespeichert' % label,
              status == 200 and message in error and count_after == count_before,
              'HTTP %s, "%s", Meldungen %d -> %d' % (status, error.replace('\n', ' ')[:110], count_before, count_after))
        if label == 'TIFF':
            shot(page, 'F3-photo-error-tiff')
        if label == 'über 10 MB':
            shot(page, 'F3-photo-error-too-big')
    check('F3', 'Ein gedrehtes JPEG wird aufrecht und ohne Metadaten gespeichert (siehe A3 / SEC)',
          'photo-after.json' in os.listdir(OUT + '/files'))
    context.close()


def f4_unknown_department(browser):
    section('F4 Unbekannte Abteilung in der URL')
    context = new_context(browser)
    page = context.new_page()
    page.goto(BASE + '/incidents/%s/create/' % DEPT)
    token = csrf_token(page.content())
    api = context.request
    count_before = scalar('select count(*) from cirs_criticalincident')
    headers = {'Referer': BASE + '/incidents/%s/create/' % DEPT, 'Origin': ORIGIN}
    form = {'csrfmiddlewaretoken': token, 'date': date.today().isoformat(), 'incident': 'Demo F4',
            'reason': 'Demo F4', 'immediate_action': 'Demo F4', 'preventability': 'avoidable',
            'public': 'True'}
    statuses = []
    for path in ('create/', 'search/', '', 'create/success/'):
        statuses.append(api.get(BASE + '/incidents/gibtsnicht/' + path, max_redirects=0).status)
    post = api.post(BASE + '/incidents/gibtsnicht/create/', max_redirects=0, headers=headers, form=form)
    count_after = scalar('select count(*) from cirs_criticalincident')
    check('F4', '/incidents/gibtsnicht/create/ gibt 404 bei GET und bei POST, keine Meldung entsteht',
          statuses[0] == 404 and post.status == 404 and count_after == count_before,
          'GET %s, POST %s, Meldungen %d -> %d' % (statuses[0], post.status, count_before, count_after))
    check('F4', 'Auch Codeeingabe, Liste und Erfolgsseite einer unbekannten Abteilung geben 404',
          statuses[1:] == [404, 404, 404], str(statuses[1:]))
    page.goto(BASE + '/incidents/gibtsnicht/create/')
    shot(page, 'F4-not-found')
    context.close()


# --- R: reviewer (QM) -------------------------------------------------------------------------

def r1_login(browser):
    section('R1 Login führt in den Überblick des QM, von dort in den Admin')
    context = new_context(browser)
    page = context.new_page()
    page.goto(BASE + '/login/')
    shot(page, 'R1-login-form')
    login(page, QM_USER, 'falsches-passwort')
    check('R1', 'Ein falsches Passwort wird abgelehnt, ohne zu sagen, was falsch war',
          'Benutzername oder Passwort stimmen nicht' in main_text(page) and '/login/' in page.url)
    shot(page, 'R1-login-failed')
    login(page, QM_USER, QM_PASSWORD)
    check('R1', 'Der Login des QM führt in den Überblick des QM-Bereichs', page.url == BASE + '/qm/', page.url)
    check('R1', 'Der Überblick hat eine Überschrift und im Kopf die drei Seiten des QM und den Link "Verwaltung"',
          page.locator('h1').count() == 1 and page.locator('h1').inner_text() == 'Überblick'
          and all(page.locator('nav.ui-nav').get_by_role('link', name=name, exact=True).count() == 1
                  for name in ('Überblick', 'Meldungen', 'Auswertungen', 'Verwaltung')))
    shot(page, 'R1-qm-overview')
    page.locator('nav.ui-nav').get_by_role('link', name='Verwaltung', exact=True).click()
    page.wait_for_load_state()
    check('R1', 'Der Link "Verwaltung" führt in den Admin, der "LabCIRS-Verwaltung" mit dem Konto des QM zeigt',
          page.url == BASE + '/admin/'
          and ('LabCIRS-Verwaltung' in page.inner_text('#content') or 'LabCIRS-Verwaltung' in page.inner_text('body')),
          page.url)
    shot(page, 'R1-admin-index')
    STATE['qm_context'] = context
    STATE['qm'] = page
    # an old reporter account must not log in here (the reporter reports anonymously)
    other = new_context(browser).new_page()
    login(other, 'reporter-demo', 'irgendwas')
    check('R1', 'Das technische Konto der Meldenden meldet nicht an (Meldungen gehen ohne Anmeldung)',
          '/login/' in other.url and 'admin' not in other.url)
    other.context.close()


def r5_config(browser):
    section('R5 LabCIRSConfig bearbeiten (Anmeldeinfo, Benachrichtigung)')
    page = STATE['qm']
    page.goto(BASE + '/admin/cirs/labcirsconfig/')
    shot(page, 'R5-config-list')
    page.locator('#result_list tbody tr th a').first.click()
    page.wait_for_load_state()
    page.fill('#id_login_info', 'Demo: Das Konto erhalten Sie vom Qualitätsmanagement (geändert im QM-Login).')
    page.check('#id_send_notification')
    page.click('input[name=_continue]')
    page.wait_for_load_state()
    check('R5', 'Benachrichtigung ohne Empfänger wird mit einer Fehlermeldung abgelehnt',
          page.locator('.errornote, .errorlist').count() > 0 and 'Empfänger' in page.inner_text('#content'))
    page.uncheck('#id_send_notification')
    page.select_option('#id_notification_recipients_from', label='qm-demo')
    page.click('#id_notification_recipients_add')
    page.click('input[name=_continue]')
    page.wait_for_load_state()
    page.check('#id_send_notification')
    page.click('input[name=_continue]')
    page.wait_for_load_state()
    check('R5', 'Benachrichtigung ohne Absenderadresse wird mit einer Fehlermeldung abgelehnt',
          page.locator('.errornote, .errorlist').count() > 0 and 'Absender' in page.inner_text('#content'))
    shot(page, 'R5-config-validation-error')
    page.fill('#id_notification_sender_email', 'cirs-qm@cirs.test')
    page.fill('#id_notification_text', 'Es gibt eine neue Aktivität im CIRS. Bitte im Admin nachsehen.')
    fits_width(page, 'R5', 'Einstellungen')
    shot(page, 'R5-config-filled')
    page.click('input[name=_continue]')
    page.wait_for_load_state()
    check('R5', 'Die Einstellungen werden gespeichert (Erfolgsmeldung)',
          page.locator('.messagelist .success').count() == 1,
          page.locator('.messagelist').inner_text() if page.locator('.messagelist').count() else '')
    shot(page, 'R5-config-saved')
    row = sql('select send_notification, notification_sender_email, notification_text from cirs_labcirsconfig')[0]
    recipients = sql('select u.username from cirs_labcirsconfig_notification_recipients r '
                     'join auth_user u on u.id = r.user_id')
    check('R5', 'Benachrichtigung, Absender, Text und Empfänger stehen in der Datenbank',
          row[0] is True and row[1] == 'cirs-qm@cirs.test' and row[2].startswith('Es gibt eine neue')
          and recipients == [('qm-demo',)], str(row[:2]) + ' ' + str(recipients))
    login_info = sql("select login_info from cirs_labcirsconfig_translation where language_code = 'de'")[0][0]
    anon = new_context(browser).new_page()
    anon.goto(BASE + '/login/?next=/incidents/%s/' % DEPT)
    check('R5', 'Die geänderte Anmeldeinfo steht auf der Anmeldeseite der Abteilung',
          login_info in anon.inner_text('main') and 'im QM-Login' in anon.inner_text('main'))
    shot(anon, 'R5-login-info-on-login-page')
    anon.context.close()


def open_incident(page, incident_id_):
    page.goto(BASE + '/admin/cirs/criticalincident/%d/change/' % incident_id_)


def r2_list_and_filters(browser):
    section('R2 Liste mit allen Filtern, inklusive Organisationseinheit')
    page = STATE['qm']
    page.goto(BASE + '/admin/cirs/criticalincident/')
    fits_width(page, 'R2', 'Meldungsliste')
    shot(page, 'R2-list-all')
    summaries = page.locator('#changelist-filter details summary').all_inner_texts()
    summaries = [s.strip() for s in summaries]
    expected = ['Nach Status', 'Nach Datum des Ereignisses', 'Nach Datum der Meldung', 'Nach Veröffentlichung',
                'Nach Risikoklasse', 'Nach Wo ist es passiert?', 'Nach mit publizierbaren Ereignissen']
    check('R2', 'Die Filterleiste hat Status, beide Datumsfilter, Veröffentlichung, Risiko, Organisationseinheit und publizierbar',
          summaries == expected, ', '.join(summaries))
    total = scalar('select count(*) from cirs_criticalincident')
    check('R2', 'Die Liste zeigt alle Meldungen der Abteilung', rows(page) == total, '%d Zeilen' % total)

    def apply(summary, option, href=None):
        page.goto(BASE + '/admin/cirs/criticalincident/')
        group = page.locator('#changelist-filter details', has=page.locator('summary', has_text=summary))
        if href:
            group.locator('a[href*="%s"]' % href).click()
        else:
            group.locator('a', has_text=re.compile('^\\s*' + re.escape(option) + '\\s*$')).click()
        page.wait_for_load_state()
        return rows(page)

    unit = scalar("select id from cirs_orgunit where name = 'Untereinheit B1'")
    today = date.today()
    cases = [
        ('Nach Status', 'in Bearbeitung', "status = 'in process'", (), 'R2-filter-status'),
        ('Nach Veröffentlichung', 'NICHT einverstanden', 'not public', (), 'R2-filter-consent', 'public__exact=0'),
        ('Nach Risikoklasse', 'mittel', "risk = 'middle'", (), 'R2-filter-risk'),
        ('Nach Wo ist es passiert?', 'Einheit B › Untereinheit B1', 'org_unit_id = %s', (unit,), 'R2-filter-org-unit'),
        ('Nach mit publizierbaren Ereignissen', 'Ja', 'id in (select critical_incident_id from cirs_publishableincident)', (), 'R2-filter-publishable-yes'),
        ('Nach mit publizierbaren Ereignissen', 'Nein', 'id not in (select critical_incident_id from cirs_publishableincident)', (), 'R2-filter-publishable-no'),
        ('Nach Datum der Meldung', 'Heute', 'reported = %s', (today,), 'R2-filter-reported-today'),
        ('Nach Datum des Ereignisses', 'Letzte 7 Tage', 'date >= %s and date < %s', (today - timedelta(days=7), today + timedelta(days=1)), 'R2-filter-date-7-days'),
    ]
    for summary, option, where, params, name, *href in cases:
        got = apply(summary, option, *href)
        want = scalar('select count(*) from cirs_criticalincident where ' + where, *params)
        check('R2', 'Filter "%s: %s" zeigt %d Meldungen wie die Datenbank' % (summary[5:], option[:30], want),
              got == want and want > 0, '%d von %d' % (got, total))
        shot(page, name)
    # two filters at once (status and organisational unit)
    page.goto(BASE + '/admin/cirs/criticalincident/?status__exact=in+process&org_unit__id__exact=%d' % unit)
    want = scalar("select count(*) from cirs_criticalincident where status = 'in process' and org_unit_id = %s", unit)
    check('R2', 'Zwei Filter zugleich (Status und Einheit) wirken zusammen', rows(page) == want, '%d' % want)
    # the filter "Abteilung" is part of the list but Django hides a filter with one choice: see S1


def r3_report_part_and_review(browser):
    section('R3 Meldeteil schreibgeschützt mit Foto, Review-Block vollständig')
    page = STATE['qm']
    open_incident(page, STATE['id'])
    for name in ('date', 'incident', 'reason', 'immediate_action', 'preventability', 'photo', 'public', 'reported'):
        if page.locator('[name="%s"]' % name).count():
            check('R3', 'Das Feld "%s" des Meldeteils ist ein Eingabefeld' % name, False)
            return
    check('R3', 'Der Meldeteil hat keine Eingabefelder (Datum, Texte, Vermeidbarkeit, Foto, Zustimmung sind Text)', True)
    text = page.inner_text('#content')
    check('R3', 'Der Meldeteil zeigt die gemeldeten Texte, die Einheit steht im Review-Block',
          STATE['marker'] in text and 'Untereinheit B1' in page.locator('#id_org_unit').evaluate('s => s.options[s.selectedIndex].text'))
    thumb = page.locator('img.labcirs-thumb')
    loaded = thumb.evaluate('i => i.complete && i.naturalWidth > 0') if thumb.count() else False
    check('R3', 'Das Foto wird als Vorschaubild gezeigt und geladen', loaded)
    page.locator('details > summary', has_text='Bewertung').click()
    fits_width(page, 'R3', 'Meldung im Admin (Meldeteil, Review, Inline, Kommentare)')
    shot(page, 'R3-incident-change-review-open')
    names = ['review_date', 'status', 'org_unit', 'risk', 'frequency', 'hazard', 'responsibilty', 'action']
    present = [n for n in names if page.locator('[name="%s"]' % n).count() == 1]
    categories = page.locator('input[name=category]').count()
    check('R3', 'Der Review-Block hat alle Felder: Reviewdatum, Status, Einheit, Risiko, Häufigkeit, Gefahr, Zuständigkeit, Maßnahme, 6 Kategorien',
          present == names and categories == 6, '%s, Kategorien %d' % (present, categories))
    # fill the block
    page.fill('#id_review_date', date.today().strftime('%d.%m.%Y'))
    page.select_option('#id_status', label='in Bearbeitung')
    page.select_option('#id_risk', 'middle')
    page.select_option('#id_frequency', 'occasional')
    page.select_option('#id_hazard', 'low')
    page.fill('#id_responsibilty', 'Demo-Station B')
    page.fill('#id_action', 'Demo: Ablauf mit dem Team besprechen.')
    page.check('#id_category_0')
    page.check('#id_category_1')
    # a forged field of the report part must not be taken over
    page.evaluate("""() => { document.querySelector('#content form').insertAdjacentHTML('beforeend',
        '<input type="hidden" name="incident" value="VERFAELSCHT"><input type="hidden" name="reason" value="VERFAELSCHT">'); }""")
    page.click('input[name=_continue]')
    page.wait_for_load_state()
    row = sql('select status, risk, frequency, hazard, responsibilty, action, category, review_date, '
              'incident, reason from cirs_criticalincident where id = %s', STATE['id'])[0]
    check('R3', 'Der Review-Block wird gespeichert (Status, Risiko, Häufigkeit, Gefahr, Zuständigkeit, Maßnahme, Kategorien, Datum)',
          row[:4] == ('in process', 'middle', 'occasional', 'low') and row[4] == 'Demo-Station B'
          and row[5].startswith('Demo: Ablauf') and len(row[6].split(',')) == 2 and row[7] == date.today(),
          str(row[:7]))
    check('R3', 'Ein untergeschobenes Feld des Meldeteils ändert den gemeldeten Text nicht',
          'VERFAELSCHT' not in row[8] and 'VERFAELSCHT' not in row[9] and STATE['marker'] in row[8])
    shot(page, 'R3-incident-saved')
    # the first seeded report (with the seed photo): the thumbnail again
    first = scalar("select id from cirs_criticalincident where incident like %s", 'Demo 1:%')
    open_incident(page, first)
    thumb = page.locator('img.labcirs-thumb')
    check('R3', 'Auch das Foto einer Demo-Meldung wird gezeigt',
          thumb.count() == 1 and thumb.evaluate('i => i.complete && i.naturalWidth > 0'))


def r4_publish(browser):
    section('R4 Veröffentlichen per Inline und per Liste, mit Prüfung der Pflichtsprachen')
    page = STATE['qm']
    marker = STATE['marker']
    title_de = 'Fall %s auf Station' % marker
    title_en = 'Case %s on the ward' % marker
    # --- inline in the incident
    open_incident(page, STATE['id'])
    page.fill('#id_publishableincident-0-incident', title_de)
    page.fill('#id_publishableincident-0-description', 'Beschreibung des Falls %s.' % marker)
    page.fill('#id_publishableincident-0-measures_and_consequences', 'Maßnahmen zum Fall %s.' % marker)
    page.check('#id_publishableincident-0-publish')
    page.click('input[name=_continue]')
    page.wait_for_load_state()
    error = page.inner_text('#content')
    check('R4', 'Inline: "Veröffentlichen" nur mit Deutsch wird abgelehnt, die Meldung nennt die Pflichtsprachen',
          'verpflichtenden Sprachen (Deutsch, Englisch)' in error
          and scalar('select count(*) from cirs_publishableincident where publish') == 2 + 30, '')
    shot(page, 'R4-inline-mandatory-languages-error')
    page.uncheck('#id_publishableincident-0-publish')
    page.click('input[name=_continue]')
    page.wait_for_load_state()
    check('R4', 'Inline: ohne Haken speichert die deutsche Fassung',
          page.locator('.messagelist .success').count() == 1)
    page.get_by_role('link', name='Englisch').first.click()
    page.wait_for_load_state()
    page.fill('#id_publishableincident-0-incident', title_en)
    page.fill('#id_publishableincident-0-description', 'Description of the case %s.' % marker)
    page.fill('#id_publishableincident-0-measures_and_consequences', 'Measures for the case %s.' % marker)
    page.check('#id_publishableincident-0-publish')
    fits_width(page, 'R4', 'Meldung mit Inline (englischer Tab)')
    shot(page, 'R4-inline-english-tab')
    page.click('input[name=_continue]')
    page.wait_for_load_state()
    check('R4', 'Inline: mit beiden Pflichtsprachen lässt sich veröffentlichen',
          page.locator('.messagelist .success').count() == 1)
    shot(page, 'R4-inline-published')
    published = sql('select p.publish, t.language_code, t.incident from cirs_publishableincident p '
                    'join cirs_publishableincident_translation t on t.master_id = p.id '
                    'where p.critical_incident_id = %s order by t.language_code', STATE['id'])
    check('R4', 'Der Fall ist veröffentlicht, mit deutschem und englischem Titel',
          [(r[0], r[1]) for r in published] == [(True, 'de'), (True, 'en')], str(published))

    # --- no consent: the report of the demo data without the reporter's agreement
    no_consent = scalar("select id from cirs_criticalincident where incident like %s", 'Demo 6:%')
    open_incident(page, no_consent)
    page.fill('#id_publishableincident-0-incident', 'Soll nicht erscheinen')
    page.check('#id_publishableincident-0-publish')
    page.click('input[name=_continue]')
    page.wait_for_load_state()
    check('R4', 'Ohne Zustimmung der Meldenden lässt sich nicht veröffentlichen',
          'nicht zugestimmt' in page.inner_text('#content')
          and scalar('select count(*) from cirs_publishableincident where critical_incident_id = %s', no_consent) == 0)
    shot(page, 'R4-inline-no-consent')

    # --- via the list of publishable incidents
    page.goto(BASE + '/admin/cirs/publishableincident/add/')
    options = [o.strip() for o in page.locator('#id_critical_incident option').all_inner_texts()]
    check('R4', 'Liste: zur Auswahl stehen nur Meldungen mit Zustimmung, nicht neu und noch nicht publizierbar',
          any(o.startswith('Demo 3') for o in options) and not any(o.startswith('Demo 6') for o in options)
          and not any(o.startswith('Demo 1:') for o in options), ' | '.join(options))
    page.select_option('#id_critical_incident', label=next(o for o in options if o.startswith('Demo 3')))
    page.fill('#id_incident', 'Lieferung im falschen Raum')
    page.fill('#id_description', 'Eine Lieferung wurde im falschen Raum abgestellt.')
    page.fill('#id_measures_and_consequences', 'Raumschilder sind angebracht.')
    page.check('#id_publish')
    page.click('input[name=_continue]')
    page.wait_for_load_state()
    check('R4', 'Liste: "Veröffentlichen" nur mit Deutsch wird abgelehnt (Pflichtsprachen)',
          'verpflichtenden Sprachen' in page.inner_text('#content'))
    fits_width(page, 'R4', 'Publizierbares Ereignis (Fehlermeldung)')
    shot(page, 'R4-list-mandatory-languages-error')
    page.uncheck('#id_publish')
    page.click('input[name=_continue]')
    page.wait_for_load_state()
    page.get_by_role('link', name='Englisch').first.click()
    page.wait_for_load_state()
    page.fill('#id_incident', 'Delivery in the wrong room')
    page.fill('#id_description', 'A delivery was left in the wrong room.')
    page.fill('#id_measures_and_consequences', 'Room signs are in place.')
    page.check('#id_publish')
    page.click('input[name=_save]')
    page.wait_for_load_state()
    check('R4', 'Liste: mit beiden Sprachen veröffentlicht, die Liste zeigt "vollständig"',
          page.locator('.messagelist .success').count() == 1 and 'Lieferung im falschen Raum' in page.inner_text('#content'))
    shot(page, 'R4-list-published')

    # --- the public list shows both new cases, in both languages
    anon = new_context(browser).new_page()
    anon.goto(BASE + '/incidents/%s/?q=%s' % (DEPT, marker))
    check('R4', 'Die öffentliche Fallliste zeigt den veröffentlichten Fall (Suche nach der Marke)',
          anon.locator('tbody tr').count() == 1 and title_de in anon.inner_text('tbody'))
    anon.goto(BASE + '/incidents/%s/?q=Lieferung' % DEPT)
    check('R4', 'Auch der Fall aus der Liste erscheint', anon.locator('tbody tr').count() == 1)
    shot(anon, 'R4-public-list-new-case')
    anon.context.close()


def r6_reporter_account(browser):
    section('R6 Name und Passwort des Reporter-Kontos ändern')
    page = STATE['qm']
    page.goto(BASE + '/admin/auth/user/')
    names = [n.strip() for n in page.locator('#result_list tbody tr th a').all_inner_texts()]
    check('R6', 'Das QM sieht in der Benutzerliste nur das Reporter-Konto seiner Abteilung', names == ['reporter-demo'], str(names))
    fits_width(page, 'R6', 'Benutzerliste')
    shot(page, 'R6-user-list')
    page.locator('#result_list tbody tr th a').first.click()
    page.wait_for_load_state()
    fields = [i.get_attribute('name') for i in page.locator('#content input[type=text], #content input[type=email]').all()]
    check('R6', 'Das QM darf nur Benutzername, Vor- und Nachname ändern (keine Rechte, kein Superuser)',
          fields == ['username', 'first_name', 'last_name'] and page.locator('input[name=is_superuser]').count() == 0,
          str(fields))
    page.fill('#id_first_name', 'Meldekonto')
    page.fill('#id_last_name', 'Station B')
    page.click('input[name=_continue]')
    page.wait_for_load_state()
    row = sql("select first_name, last_name, password from auth_user where username = 'reporter-demo'")[0]
    check('R6', 'Der Name des Reporter-Kontos ist geändert', row[:2] == ('Meldekonto', 'Station B'), str(row[:2]))
    shot(page, 'R6-name-changed')
    old_hash = row[2]
    user_id = scalar("select id from auth_user where username = 'reporter-demo'")
    new_password = secrets.token_urlsafe(18)
    page.goto(BASE + '/admin/auth/user/%d/password/' % user_id)
    shot(page, 'R6-password-form')
    page.fill('#id_password1', new_password)
    page.fill('#id_password2', new_password)
    page.locator('#content input[type=submit]').click()
    page.wait_for_load_state()
    new_hash = scalar("select password from auth_user where username = 'reporter-demo'")
    check('R6', 'Das Passwort des Reporter-Kontos ist geändert (neuer Hash passt zum neuen Passwort)',
          new_hash != old_hash and django_pbkdf2_matches(new_password, new_hash),
          new_hash.split('$')[0])
    shot(page, 'R6-password-changed')
    other = scalar("select id from auth_user where username = 'admin-demo'")
    response = page.goto(BASE + '/admin/auth/user/%d/change/' % other)
    check('R6', 'Andere Konten (Superuser, QM) sind für das QM nicht erreichbar',
          page.url == BASE + '/admin/' or (response and response.status in (403, 404)), page.url)
    shot(page, 'R6-other-user-refused')


def django_pbkdf2_matches(password, encoded):
    algorithm, iterations, salt, digest = encoded.split('$')
    check_hash = base64.b64encode(
        hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), int(iterations))).decode().strip()
    return algorithm == 'pbkdf2_sha256' and check_hash == digest


def r7_comments(browser):
    section('R7 Kommentare im Admin sehen, im Frontend antworten')
    page = STATE['qm']
    seeded = scalar("select id from cirs_criticalincident where incident like %s", 'Demo 2:%')
    open_incident(page, seeded)
    comments = page.locator('#comments-group tbody tr.form-row:not(.empty-form)')
    texts = [t.replace('\n', ' ') for t in comments.all_inner_texts()]
    check('R7', 'Im Admin steht der Kommentarverlauf der Meldung (QM und Reporter-Konto)',
          len(texts) == 2 and any('qm-demo' in t for t in texts) and any('Meldekonto' in t or 'reporter-demo' in t for t in texts),
          ' | '.join(texts))
    shot(page, 'R7-comments-in-admin')
    # the report of A3 has the reply of the reporter
    open_incident(page, STATE['id'])
    texts = [t.replace('\n', ' ') for t in page.locator('#comments-group tbody tr.form-row:not(.empty-form)').all_inner_texts()]
    check('R7', 'Die Antwort der Meldenden aus A4 steht im Admin', len(texts) == 1 and STATE['reply'] in texts[0], ' | '.join(texts))
    check('R7', 'Im Admin lassen sich keine Kommentare anlegen (nur im Frontend): kein Hinzufügen-Knopf, höchstens 0 neue Zeilen',
          not page.locator('#comments-group .add-row a').is_visible()
          and page.locator('#id_comments-MAX_NUM_FORMS').input_value() == '0')
    # "View on site" leads to the report in the frontend, where the QM replies
    page.locator('a.viewsitelink').click()
    page.wait_for_load_state()
    check('R7', '"Auf der Website anzeigen" führt zur Meldung im Frontend (als QM, Titel "Meldung")',
          page.url == BASE + '/incidents/%s/%d/' % (DEPT, STATE['id']) and page.locator('h1').inner_text() == 'Meldung', page.url)
    check('R7', 'Das QM sieht keine Hinweise für Meldende (kein "Zugang beenden", keine E-Mail-Karte)',
          page.get_by_role('button', name='Zugang beenden').count() == 0)
    STATE['qm_reply'] = 'Demo %s: erfundene Rückfrage des QM.' % STATE['marker']
    page.fill('#id_text', STATE['qm_reply'])
    page.get_by_role('button', name='Antwort senden').click()
    page.wait_for_load_state()
    cards = page.locator('article.ui-card h3').all_inner_texts()
    check('R7', 'Die Antwort des QM erscheint unter "Qualitätsmanagement", die der Meldenden unter "Meldende Person"',
          [c.split(' ·')[0] for c in cards] == ['Meldende Person', 'Qualitätsmanagement'], str(cards))
    shot(page, 'R7-qm-reply-frontend')
    author = scalar('select u.username from cirs_comment c join auth_user u on u.id = c.author_id '
                    'where c.text = %s', STATE['qm_reply'])
    check('R7', 'Die Antwort ist unter dem Konto des QM gespeichert', author == 'qm-demo', author)
    open_incident(page, STATE['id'])
    texts = [t.replace('\n', ' ') for t in page.locator('#comments-group tbody tr.form-row:not(.empty-form)').all_inner_texts()]
    check('R7', 'Die Antwort des QM steht danach auch im Admin', len(texts) == 2 and STATE['qm_reply'] in texts[1])
    shot(page, 'R7-comments-in-admin-after-reply')


def r8_org_units(browser):
    section('R8 Organisationseinheiten pflegen')
    page = STATE['qm']
    anon = new_context(browser).new_page()

    def form_options():
        anon.goto(BASE + '/incidents/%s/create/' % DEPT)
        return [o.strip() for o in anon.locator('#id_org_unit option').all_inner_texts()]

    before = form_options()
    page.goto(BASE + '/admin/cirs/orgunit/')
    fits_width(page, 'R8', 'Liste der Einheiten')
    shot(page, 'R8-orgunit-list')
    page.goto(BASE + '/admin/cirs/orgunit/add/')
    page.fill('#id_name', 'Einheit C')
    page.fill('#id_position', '7')
    page.click('input[name=_save]')
    page.wait_for_load_state()
    page.goto(BASE + '/admin/cirs/orgunit/add/')
    page.fill('#id_name', 'Untereinheit C1')
    page.select_option('#id_parent', label='Einheit C')
    page.fill('#id_position', '8')
    shot(page, 'R8-orgunit-add-child')
    page.click('input[name=_save]')
    page.wait_for_load_state()
    check('R8', 'Neue Einheit mit Untereinheit angelegt',
          scalar("select count(*) from cirs_orgunit where name in ('Einheit C', 'Untereinheit C1')") == 2)
    after_add = form_options()
    check('R8', 'Die neue Untereinheit steht im Meldeformular zur Auswahl',
          'Untereinheit C1' in after_add and 'Untereinheit C1' not in before,
          '%d -> %d Auswahlpunkte' % (len(before), len(after_add)))
    # deactivate and move in the list
    page.goto(BASE + '/admin/cirs/orgunit/')
    row_index = [t.strip() for t in page.locator('#result_list tbody tr th').all_inner_texts()].index('Untereinheit C1')
    page.uncheck('#id_form-%d-active' % row_index)
    page.fill('#id_form-%d-position' % row_index, '9')
    page.click('input[name=_save]')
    page.wait_for_load_state()
    row = sql("select active, position from cirs_orgunit where name = 'Untereinheit C1'")[0]
    check('R8', 'In der Liste ist die Untereinheit inaktiv gesetzt und verschoben', row == (False, 9), str(row))
    shot(page, 'R8-orgunit-list-after')
    after_off = form_options()
    check('R8', 'Eine inaktive Einheit steht nicht mehr im Meldeformular (die Meldungen daran bleiben)',
          'Untereinheit C1' not in after_off and 'Einheit C' in after_off)
    delete_links = page.locator('a.deletelink').count()
    page.goto(BASE + '/admin/cirs/orgunit/%d/change/' % scalar("select id from cirs_orgunit where name = 'Untereinheit C1'"))
    check('R8', 'Das QM kann keine Einheit löschen (nur inaktiv setzen)',
          page.locator('a.deletelink').count() == 0 and delete_links == 0)
    anon.context.close()


def r9_notifications(browser):
    section('R9 Benachrichtigungsmails bei neuer Meldung und neuem Kommentar, ohne Meldungsinhalt')
    marker = STATE['marker']
    mails = all_mails()
    to_qm = [m for m in mails if 'qm-demo@example.test' in m['to']]
    new_report = [m for m in to_qm if m['subject'] == 'Neues kritisches Ereignis']
    new_comment = [m for m in to_qm if m['subject'] == 'Neuer Kommentar im CIRS']
    check('R9', 'Zur neuen Meldung kam eine Mail an das QM (Betreff "Neues kritisches Ereignis")',
          len(new_report) >= 1, 'Mails an das QM: %d' % len(to_qm))
    check('R9', 'Zum neuen Kommentar der Meldenden kam eine Mail an das QM (Betreff "Neuer Kommentar im CIRS")',
          len(new_comment) == 1, '%d' % len(new_comment))
    first = new_report[0] if new_report else {'text': '', 'subject': '', 'from': ''}
    check('R9', 'Absender und Text sind die der Konfiguration',
          first['from'] == 'cirs-qm@cirs.test' and first['text'].strip() == 'Es gibt eine neue Aktivität im CIRS. Bitte im Admin nachsehen.',
          '%s / %s' % (first['from'], first['text'].strip()[:80]))
    leaks = [m['subject'] for m in to_qm if any(s in (m['text'] + m['subject'] + m['html']) for s in (
        marker, 'erfundenes Ereignis', 'erfundene Ursache', 'erfundene Antwort', 'Station B'))]
    check('R9', 'Keine Mail an das QM enthält Meldungsinhalt (Marke, Ereignis, Ursache, Antwort)', not leaks, str(leaks))
    quiet_period()
    after_qm_reply = [m for m in all_mails() if 'qm-demo@example.test' in m['to']]
    check('R9', 'Die eigene Antwort des QM löst keine Mail an das QM aus', len(after_qm_reply) == len(to_qm))
    save_mails('R9-notification-mails')
    mailpit_shot(browser, 'R9-mailpit-inbox')


# --- A6: report with e-mail ---------------------------------------------------------------------

def a6_reporter_email(browser):
    section('A6 Mit E-Mail melden: Bestätigung, Hinweise, Schlussmail, Adresse nicht sichtbar, löschen')
    qm = STATE['qm']
    marker = 'E' + secrets.token_hex(3)
    STATE['marker_mail'] = marker
    clear_mails()
    context = new_context(browser)
    page = context.new_page()
    page.goto(BASE + '/incidents/%s/create/' % DEPT)
    hint = page.locator('#id_reporter_email_helptext').inner_text()
    check('A6', 'Das Formular erklärt, dass die Meldung mit Adresse nicht mehr ganz anonym ist',
          'nicht mehr vollständig anonym' in hint, hint[:80])
    shot(page, 'A6-form-email-field')
    fill_report(page, marker, email=REPORTER_ADDRESS)
    code = submit_report(page)
    shot(page, 'A6-success-with-email')
    pk = incident_id(code)
    STATE.update({'mail_code': code, 'mail_id': pk})
    check('A6', 'Die Erfolgsseite verspricht den Code zusätzlich per E-Mail',
          'erhalten Sie den Code zusätzlich per E-Mail' in main_text(page))
    received = mails_to(REPORTER_ADDRESS, wait=10)
    check('A6', 'Die Bestätigung mit dem Code steht in Mailpit (Betreff "Ihre Meldung ist eingegangen")',
          len(received) == 1 and received[0]['subject'] == 'Ihre Meldung ist eingegangen'
          and code in received[0]['text'], str([m['subject'] for m in received]))
    check('A6', 'Die Bestätigung nennt keinen Inhalt der Meldung',
          not any(s in received[0]['text'] for s in (marker, 'erfundenes Ereignis', 'erfundene Ursache')))
    contact = scalar('select count(*) from cirs_reportercontact where incident_id = %s', pk)
    check('A6', 'Die Adresse liegt in ihrer eigenen Tabelle, getrennt von der Meldung', contact == 1)

    # the QM changes the status
    open_incident(qm, pk)
    qm.locator('details > summary', has_text='Bewertung').click()
    qm.select_option('#id_status', label='in Bearbeitung')
    qm.fill('#id_action', 'Demo: in Arbeit.')
    qm.fill('#id_review_date', date.today().strftime('%d.%m.%Y'))
    qm.click('input[name=_continue]')
    qm.wait_for_load_state()
    status_mails = [m for m in mails_to(REPORTER_ADDRESS, wait=10) if m['subject'] == 'Neuer Stand Ihrer Meldung']
    check('A6', 'Nach der Statusänderung kommt eine Hinweismail mit dem neuen Stand ("In Bearbeitung")',
          len(status_mails) == 1 and 'In Bearbeitung' in status_mails[0]['text'], str(len(status_mails)))
    check('A6', 'Die Hinweismail nennt keinen Inhalt der Meldung',
          marker not in status_mails[0]['text'] and 'Demo: in Arbeit' not in status_mails[0]['text'])

    # the address is not visible to the QM anywhere in the admin
    pages = ['/admin/', '/admin/cirs/criticalincident/', '/admin/cirs/criticalincident/%d/change/' % pk,
             '/admin/cirs/criticalincident/%d/history/' % pk, '/admin/auth/user/', '/admin/cirs/labcirsconfig/',
             '/admin/cirs/publishableincident/', '/admin/cirs/orgunit/', '/admin/cirs/reportercontact/']
    seen = []
    for path in pages:
        response = qm.goto(BASE + path)
        seen.append((path, response.status, REPORTER_ADDRESS in qm.content()))
    check('A6', 'Die Adresse steht auf keiner Seite des QM-Admins (9 Seiten, inkl. Meldung und Verlauf)',
          not any(s[2] for s in seen) and seen[-1][1] == 404, str([(s[0].split('/')[-2], s[1]) for s in seen]))
    open_incident(qm, pk)
    shot(qm, 'A6-admin-no-address')
    response = qm.goto(BASE + '/incidents/%s/%d/' % (DEPT, pk))
    check('A6', 'Auch die Frontend-Ansicht des QM zeigt weder Adresse noch den Hinweis auf eine Adresse',
          REPORTER_ADDRESS not in qm.content() and 'E-Mail-Benachrichtigung' not in main_text(qm))
    qm.fill('#id_text', 'Demo: erfundene Rückfrage per Mail.')
    qm.get_by_role('button', name='Antwort senden').click()
    qm.wait_for_load_state()
    reply_mails = [m for m in mails_to(REPORTER_ADDRESS, wait=10) if m['subject'] == 'Neue Rückmeldung zu Ihrer Meldung']
    check('A6', 'Auf die Antwort des QM kommt eine Hinweismail mit der Antwort, aber ohne Meldungsinhalt',
          len(reply_mails) == 1 and 'erfundene Rückfrage per Mail' in reply_mails[0]['text']
          and 'erfundenes Ereignis' not in reply_mails[0]['text'] and 'erfundene Ursache' not in reply_mails[0]['text'])

    # Only the code holder can remove the address, the QM gets 403
    token = csrf_token(qm.content())
    forbidden = qm.context.request.post(BASE + '/incidents/%s/%d/email/remove/' % (DEPT, pk), max_redirects=0,
                                        headers={'Referer': BASE + '/', 'Origin': ORIGIN},
                                        form={'csrfmiddlewaretoken': token, 'confirm': 'on'})
    check('A6', 'Das QM kann die Adresse nicht löschen (403), sie bleibt',
          forbidden.status == 403 and scalar('select count(*) from cirs_reportercontact where incident_id = %s', pk) == 1,
          str(forbidden.status))

    # completed: closing mail, address gone
    open_incident(qm, pk)
    qm.locator('details > summary', has_text='Bewertung').click()
    qm.select_option('#id_status', label='erledigt')
    qm.click('input[name=_continue]')
    qm.wait_for_load_state()
    closing = [m for m in mails_to(REPORTER_ADDRESS, wait=10) if m['subject'] == 'Ihre Meldung ist abgeschlossen']
    check('A6', 'Bei "erledigt" kommt die Schlussmail, die auch das Löschen der Adresse ankündigt',
          len(closing) == 1 and 'Ihre E-Mail-Adresse wurde gelöscht' in closing[0]['text'], str(len(closing)))
    check('A6', 'Danach ist die Adresse aus der Datenbank gelöscht',
          scalar('select count(*) from cirs_reportercontact where incident_id = %s', pk) == 0)
    count_before = len(all_mails())
    qm.goto(BASE + '/incidents/%s/%d/' % (DEPT, pk))
    qm.fill('#id_text', 'Demo %s: Antwort nach Abschluss.' % marker)
    qm.get_by_role('button', name='Antwort senden').click()
    qm.wait_for_load_state()
    quiet_period()
    check('A6', 'Nach dem Abschluss kommt keine Mail mehr an die frühere Adresse', len(all_mails()) == count_before)
    save_mails('A6-reporter-mails')
    to_reporter = mails_to(REPORTER_ADDRESS)
    leaked = [m['subject'] for m in to_reporter if any(
        s in m['text'] + m['html'] for s in (marker, 'erfundenes Ereignis', 'erfundene Ursache', 'erfundene Sofortmaßnahme'))]
    check('A6', 'Keine der %d Mails an die Adresse enthält Inhalt der Meldung (Marke, Ereignis, Ursache, Sofortmaßnahme)' % len(to_reporter),
          len(to_reporter) >= 4 and not leaked, str(leaked))
    mailpit_shot(browser, 'A6-mailpit-inbox')
    context.close()

    # a second report: the reporter deletes the address under "My report"
    context = new_context(browser)
    page = context.new_page()
    page.goto(BASE + '/incidents/%s/create/' % DEPT)
    fill_report(page, 'D' + secrets.token_hex(3), email=REPORTER_ADDRESS)
    code = submit_report(page)
    pk2 = incident_id(code)
    enter_code(page, code)
    check('A6', 'Unter "Meine Meldung" steht der Hinweis, dass die Benachrichtigung aktiv ist, ohne die Adresse zu zeigen',
          'E-Mail-Benachrichtigung ist aktiv' in main_text(page) and REPORTER_ADDRESS not in page.content())
    shot(page, 'A6-my-report-email-card')
    page.evaluate("document.querySelector('#id_confirm').removeAttribute('required')")  # the server must refuse too
    page.get_by_role('button', name='E-Mail-Adresse löschen').click()
    page.wait_for_load_state()
    check('A6', 'Ohne Bestätigungshaken wird nicht gelöscht, die Seite bittet um die Bestätigung',
          'Bitte bestätigen Sie' in main_text(page)
          and scalar('select count(*) from cirs_reportercontact where incident_id = %s', pk2) == 1)
    page.check('#id_confirm')
    page.get_by_role('button', name='E-Mail-Adresse löschen').click()
    page.wait_for_load_state()
    check('A6', 'Mit Bestätigung ist die Adresse gelöscht (Meldung der Seite, Datenbank, Karte verschwindet)',
          'Ihre E-Mail-Adresse wurde gelöscht' in main_text(page)
          and scalar('select count(*) from cirs_reportercontact where incident_id = %s', pk2) == 0
          and 'E-Mail-Benachrichtigung' not in main_text(page))
    shot(page, 'A6-address-deleted')
    count_before = len(mails_to(REPORTER_ADDRESS))
    open_incident(qm, pk2)
    qm.locator('details > summary', has_text='Bewertung').click()
    qm.select_option('#id_status', label='in Bearbeitung')
    qm.fill('#id_action', 'Demo: in Arbeit.')
    qm.fill('#id_review_date', date.today().strftime('%d.%m.%Y'))
    qm.click('input[name=_continue]')
    qm.wait_for_load_state()
    quiet_period()
    check('A6', 'Nach dem Löschen kommt bei einer Statusänderung keine Mail mehr',
          len(mails_to(REPORTER_ADDRESS)) == count_before)
    context.close()


# --- S: superuser --------------------------------------------------------------------------------

def s1_admin(browser):
    section('S1 Superuser: Benutzer, Abteilungen, Rollen, Konfiguration; keine Meldungen sichtbar')
    context = new_context(browser)
    page = context.new_page()
    login(page, ADMIN_USER, ADMIN_PASSWORD)
    check('S1', 'Der Login des Superusers führt in den Admin', page.url == BASE + '/admin/', page.url)
    models = [m.strip() for m in page.locator('#content-main th a').all_inner_texts()]
    check('S1', 'Der Admin-Index bietet Benutzer, Abteilungen, Rollen (Reporter, Reviewer) und Einstellungen',
          {'Benutzer', 'Abteilungen', 'Reporter', 'Reviewer', 'LabCIRS-Einstellungen'} <= set(models), ', '.join(models))
    shot(page, 'S1-admin-index')
    reports = scalar('select count(*) from cirs_criticalincident')
    for path, name in (('/admin/cirs/criticalincident/', 'Meldungen'), ('/admin/cirs/publishableincident/', 'publizierbare Fälle')):
        page.goto(BASE + path)
        check('S1', 'Der Superuser sieht keine %s (Liste leer, in der Datenbank sind %d Meldungen)' % (name, reports),
              rows(page) == 0 and reports > 0, 'Zeilen: %d' % rows(page))
    shot(page, 'S1-no-reports')
    page.goto(BASE + '/admin/cirs/criticalincident/%d/change/' % STATE['id'])
    check('S1', 'Auch der direkte Aufruf einer Meldung im Admin zeigt sie nicht',
          STATE['marker'] not in page.content(), page.url)
    # users, roles, departments: a second department with a new reporter account
    page.goto(BASE + '/admin/auth/user/')
    users = [n.strip() for n in page.locator('#result_list tbody tr th a').all_inner_texts()]
    check('S1', 'Die Benutzerliste zeigt alle Konten', {'admin-demo', 'qm-demo', 'reporter-demo'} <= set(users), ', '.join(users))
    shot(page, 'S1-user-list')
    page.goto(BASE + '/admin/auth/user/add/')
    page.fill('#id_username', 'reporter-demo2')
    page.fill('#id_password1', secrets.token_urlsafe(18))
    page.fill('#id_password2', page.input_value('#id_password1'))
    page.click('input[name=_save]')
    page.wait_for_load_state()
    check('S1', 'Ein neues Benutzerkonto lässt sich anlegen',
          scalar("select count(*) from auth_user where username = 'reporter-demo2'") == 1)
    page.goto(BASE + '/admin/cirs/reporter/add/')
    page.select_option('#id_user', label='reporter-demo2')
    page.click('input[name=_save]')
    page.wait_for_load_state()
    check('S1', 'Das Konto wird zum Reporter gemacht (Rolle)',
          scalar("select count(*) from cirs_reporter r join auth_user u on u.id = r.user_id where u.username = 'reporter-demo2'") == 1)
    shot(page, 'S1-role-added')
    page.goto(BASE + '/admin/cirs/department/add/')
    page.fill('#id_label', 'demo2')
    page.fill('#id_name', 'Demo-Abteilung 2')
    page.select_option('#id_reporter', label='reporter-demo2')
    page.select_option('#id_reviewers_from', label='qm-demo')
    page.click('#id_reviewers_add')
    page.check('#id_active')
    shot(page, 'S1-department-add')
    page.click('input[name=_save]')
    page.wait_for_load_state()
    check('S1', 'Eine zweite Abteilung ist angelegt, das QM zugewiesen, ihre Einstellungen entstehen von selbst',
          scalar("select count(*) from cirs_department where label = 'demo2' and active") == 1
          and scalar("select count(*) from cirs_labcirsconfig") == 2)
    page.goto(BASE + '/admin/cirs/labcirsconfig/')
    check('S1', 'Der Superuser sieht die Einstellungen aller Abteilungen (2)', rows(page) == 2)
    shot(page, 'S1-configs')
    page.goto(BASE + '/admin/cirs/department/')
    shot(page, 'S1-departments')
    # the home page now lists both departments
    anon = new_context(browser).new_page()
    anon.goto(BASE + '/')
    check('A1', 'Mit zwei aktiven Abteilungen zeigt die Startseite die Abteilungsliste statt der Fallliste',
          anon.url == BASE + '/' and 'Demo-Abteilung 2' in main_text(anon) and 'Demo-Abteilung' in main_text(anon))
    shot(anon, 'A1-department-list')
    anon.context.close()
    # the QM now has two departments: the department filter appears
    qm = STATE['qm']
    qm.goto(BASE + '/admin/cirs/criticalincident/')
    summaries = [s.strip() for s in qm.locator('#changelist-filter details summary').all_inner_texts()]
    check('R2', 'Mit zwei Abteilungen zeigt die Filterleiste des QM auch "Nach Abteilung"',
          'Nach Abteilung' in summaries, ', '.join(summaries))
    shot(qm, 'R2-filter-department')
    group = qm.locator('#changelist-filter details', has=qm.locator('summary', has_text='Nach Abteilung'))
    group.locator('a', has_text='demo2').click()
    qm.wait_for_load_state()
    check('R2', 'Der Abteilungsfilter wirkt (die zweite Abteilung hat keine Meldungen)', rows(qm) == 0)
    # deactivate it: reporting there is closed (inactive department)
    page.goto(BASE + '/admin/cirs/department/')
    page.locator('#result_list tbody tr th a', has_text='demo2').click()
    page.wait_for_load_state()
    page.uncheck('#id_active')
    page.click('input[name=_save]')
    page.wait_for_load_state()
    api = new_context(browser).request
    count = scalar('select count(*) from cirs_criticalincident')
    status = api.get(BASE + '/incidents/demo2/create/', max_redirects=0).status
    check('F4', 'Eine inaktive Abteilung gibt 404 auf Meldeformular, Liste und Codeeingabe',
          status == 404 and api.get(BASE + '/incidents/demo2/', max_redirects=0).status == 404
          and api.get(BASE + '/incidents/demo2/search/', max_redirects=0).status == 404
          and scalar('select count(*) from cirs_criticalincident') == count, str(status))
    anon = new_context(browser).new_page()
    anon.goto(BASE + '/')
    check('A1', 'Mit einer aktiven Abteilung führt die Startseite wieder zur Fallliste', anon.url == BASE + '/incidents/%s/' % DEPT)
    anon.context.close()
    context.close()


def s2_password_reset(browser):
    section('S2 Passwort-Reset per Mail')
    clear_mails()
    context = new_context(browser)
    page = context.new_page()
    login_page = page
    login_page.goto(BASE + '/login/')
    login_page.get_by_role('link', name='Passwort vergessen?').click()
    login_page.wait_for_load_state()
    shot(login_page, 'S2-reset-form')
    page.fill('#id_email', 'admin-demo@example.test')
    page.locator('#content input[type=submit], main button[type=submit]').first.click()
    page.wait_for_load_state()
    check('S2', 'Die Seite bestätigt den Versand, ohne zu verraten, ob es das Konto gibt',
          'sofern ein entsprechendes Konto existiert' in page.inner_text('body'), page.url)
    shot(page, 'S2-reset-sent')
    mails = mails_to('admin-demo@example.test', wait=10)
    check('S2', 'Die Reset-Mail steht in Mailpit', len(mails) == 1, str([m['subject'] for m in mails]))
    save_mails('S2-password-reset-mail')
    link = re.search(r'https?://\S+/accounts/reset/\S+', mails[0]['text']).group(0)
    check('S2', 'Der Link zeigt auf https://cirs.test (Schema und Host der Seite, nicht der Container)',
          link.startswith(BASE + '/accounts/reset/'), link.split('/accounts')[0])
    check('S2', 'Die Mail enthält keinen Meldungsinhalt', 'Demo' not in mails[0]['text'] and STATE['marker'] not in mails[0]['text'])
    page.goto(link)
    page.wait_for_load_state()
    new_password = secrets.token_urlsafe(18)
    page.fill('#id_new_password1', new_password)
    page.fill('#id_new_password2', new_password)
    shot(page, 'S2-reset-new-password')
    page.locator('#content input[type=submit], main button[type=submit]').first.click()
    page.wait_for_load_state()
    check('S2', 'Das neue Passwort wird gesetzt', 'Passwort zurücksetzen abgeschlossen' in page.inner_text('body'), page.url)
    shot(page, 'S2-reset-done')
    page.goto(link)
    check('S2', 'Der Link ist nach der Benutzung ungültig: die Seite bietet kein Passwortfeld mehr',
          page.locator('#id_new_password1').count() == 0 and 'ungültig' in page.inner_text('body'))
    shot(page, 'S2-reset-link-used')
    old = new_context(browser).new_page()
    login(old, ADMIN_USER, ADMIN_PASSWORD)
    check('S2', 'Mit dem alten Passwort geht der Login nicht mehr', '/login/' in old.url)
    old.context.close()
    fresh = new_context(browser).new_page()
    login(fresh, ADMIN_USER, new_password)
    check('S2', 'Mit dem neuen Passwort geht der Login (Superuser landet im Admin)', fresh.url == BASE + '/admin/', fresh.url)
    shot(fresh, 'S2-login-with-new-password')
    fresh.context.close()
    context.close()


def a4b_qm_reply_visible(browser):
    section('A4 (Fortsetzung) Antwort des QM ist für die Meldenden sichtbar')
    context = new_context(browser)
    page = context.new_page()
    enter_code(page, STATE['code'])
    cards = page.locator('article.ui-card h3').all_inner_texts()
    check('A4', 'Mit dem Code sehen die Meldenden die Antwort des QM unter "Qualitätsmanagement", den neuen Stand und können antworten',
          STATE['qm_reply'] in main_text(page) and [c.split(' ·')[0] for c in cards] == ['Meldende Person', 'Qualitätsmanagement']
          and 'In Bearbeitung' in main_text(page) and page.locator('#id_text').count() == 1, str(cards))
    shot(page, 'A4-qm-reply-seen-by-reporter')
    context.close()


# --- security -------------------------------------------------------------------------------------

def security(browser):
    section('SEC Sicherheit im Betrieb')
    context = new_context(browser)
    api = context.request
    evil = api.get(BASE + '/login/', headers={'Host': 'evil.test'}, max_redirects=0)
    check('SEC', 'Ein fremder Host-Header wird abgelehnt (ALLOWED_HOSTS): 400', evil.status == 400, str(evil.status))
    missing = api.get(BASE + '/incidents/%s/999999/' % DEPT, max_redirects=0)
    body = missing.text()
    check('SEC', 'Eine Fehlerseite verrät nichts (kein Traceback, keine Einstellungen, keine Django-Debugseite)',
          missing.status == 404 and 'Traceback' not in body and 'DEBUG' not in body and 'settings' not in body.lower(),
          str(missing.status))
    headers = api.get(BASE + '/incidents/%s/' % DEPT).headers
    check('SEC', 'Die Sicherheits-Header kommen vom Proxy (CSP ohne unsafe-inline, HSTS, nosniff)',
          "default-src 'self'" in headers.get('content-security-policy', '')
          and 'unsafe-inline' not in headers['content-security-policy']
          and 'max-age=' in headers.get('strict-transport-security', '')
          and headers.get('x-content-type-options') == 'nosniff', headers.get('content-security-policy', '')[:60])
    cookies = {c['name']: c for c in context.cookies()}
    check('SEC', 'Das CSRF-Cookie ist Secure', cookies.get('csrftoken', {}).get('secure') is True)
    context.close()


def whole_run_checks():
    section('Über den ganzen Ablauf: CSP-Meldungen und fremde Origins')
    check('SEC', 'Keine CSP-Meldung des Browsers in allen Abläufen (öffentliche Seiten und Admin)',
          not CSP_HITS, '%d Meldungen' % len(CSP_HITS))
    for url, message in CSP_HITS[:10]:
        print('      CSP: %s: %s' % (url, message[:200]))
    check('SEC', 'Keine Anfrage an eine fremde Origin in allen Abläufen (alle Seiten der Website und des Admins)',
          not FOREIGN, '%d Anfragen' % len(FOREIGN))
    for url, request in FOREIGN[:10]:
        print('      Origin: %s: %s' % (url, request))


# --- run ---------------------------------------------------------------------------------------------

FLOWS = [r1_login, r5_config, a1_start_page, a2_published_cases, a5_language, a3_report, a4_my_report,
         f1_foreign_report, f2_legacy_code, f3_photos, f4_unknown_department, r2_list_and_filters,
         r3_report_part_and_review, r4_publish, r6_reporter_account, r7_comments, r8_org_units,
         r9_notifications, a6_reporter_email, a4b_qm_reply_visible, s1_admin, s2_password_reset, security]


def main():
    if not (DSN and QM_PASSWORD and ADMIN_PASSWORD):
        print('Missing ACCEPT_DB_DSN, DEMO_QM_PASSWORD or DEMO_ADMIN_PASSWORD: run this through '
              'scripts/acceptance.sh (dc run --rm playwright python scripts/abnahme.py).')
        return 2
    os.makedirs(OUT + '/screens', exist_ok=True)
    clear_mails()
    only = set(sys.argv[1:])
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for flow in FLOWS:
            if only and flow.__name__.split('_')[0].upper() not in only:
                continue
            try:
                flow(browser)
            except Exception:
                traceback.print_exc()
                check(flow.__name__.split('_')[0].upper(), 'Ablauf %s ist mit einem Fehler abgebrochen' % flow.__name__, False,
                      traceback.format_exc().strip().splitlines()[-1][:200])
        qm = STATE.get('qm')
        if qm:
            collect_csp_events(qm)
        whole_run_checks()
        browser.close()
    failed = [r for r in RESULTS if not r[2]]
    print('\n%d checks, %d passed, %d FAILED' % (len(RESULTS), len(RESULTS) - len(failed), len(failed)))
    for point, text, _ok, detail in failed:
        print('  FAIL %s %s  [%s]' % (point, text, detail))
    with open(OUT + '/abnahme-result.json', 'w', encoding='utf-8') as f:
        json.dump([{'point': p, 'text': t, 'ok': ok, 'detail': d} for p, t, ok, d in RESULTS], f,
                  ensure_ascii=False, indent=1)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
