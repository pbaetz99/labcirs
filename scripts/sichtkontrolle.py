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

"""Visual check of all public pages, the QM pages and the admin index at three widths (320, 768, 1440 px).

Runs with Playwright in the container mcr.microsoft.com/playwright/python (see the service
"playwright" in compose.dev.yaml and scripts/acceptance.sh):

    sh scripts/acceptance.sh dc run --rm playwright python scripts/sichtkontrolle.py

For every page and width it
  - saves a screenshot (full page) in the output directory,
  - measures horizontal scrolling (scrollWidth > clientWidth of the page),
  - lists the console messages that mention the Content Security Policy (and the violation events
    the browser raises, which are the same thing seen from the page),
  - lists every request to another origin than the site,
  - measures the size of every text inside an SVG as it is drawn (font size times the scale of
    the view box, getScreenCTM): below MIN_SVG_FONT_PX the type of a chart is too small to read.

The pages of the QM (overview, incident list, evaluations, print view) are opened after a login as
the reviewer of the demo data (qm-demo, its password from DEMO_QM_PASSWORD). The CSV file is a
download and has no page to look at.

The print view is also made into a PDF as the browser prints it (print media, the page size of the
style sheet): the file must be A4 and have at least one page and at most MAX_PDF_PAGES, and it is
saved next to the screenshots. The evaluations page is looked at in print media too: its results
must be left out and the note that sends to the print view must be there, because that page shows
every number, small ones too.

At the end it prints a table and exits with status 1 if anything was found. Pages that create data
(the report form, the success page) send invented text only.
"""

import argparse
import os
import re
import sys
from datetime import date, timedelta
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

WIDTHS = (320, 768, 1440)
HEIGHT = 900
MIN_SVG_FONT_PX = 11
A4_POINTS = (595.3, 841.9)  # the size of the page of the print view in PDF points (210 x 297 mm)
A4_TOLERANCE = 2
MAX_PDF_PAGES = 8
PRINT_VIEW = 'qm-reports-print'
EVALUATIONS = 'qm-reports'

# Collects the violation events of the Content Security Policy as the page sees them. The init
# script is injected by the browser, so the policy of the site does not block it.
COLLECT_CSP = """
window.__cspViolations = [];
document.addEventListener('securitypolicyviolation', (event) => {
    window.__cspViolations.push(event.violatedDirective + ' ' + event.blockedURI);
});
"""
# The argument is the smallest type size in px. An SVG text that is not drawn (display none) has
# no screen matrix and is left out.
MEASURE = """(minFontPx) => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
    csp: window.__cspViolations || [],
    smallText: Array.from(document.querySelectorAll('svg text')).flatMap((element) => {
        const matrix = element.getScreenCTM();
        if (!matrix) return [];
        const px = parseFloat(getComputedStyle(element).fontSize) * Math.hypot(matrix.a, matrix.b);
        return px < minFontPx ? [element.textContent.trim().slice(0, 24) + ' (' + px.toFixed(1) + ' px)'] : [];
    }),
})"""


class Run:
    """The settings of one run and what the pages need to reach their state."""

    def __init__(self, args):
        self.base = args.base.rstrip('/')
        self.origin = '{0.scheme}://{0.netloc}'.format(urlsplit(self.base))
        self.dept = args.dept
        self.code = args.code
        self.user = args.qm_user
        self.password = args.qm_password
        self.out = args.out

    def url(self, path):
        return self.base + path


def pdf_facts(data):
    """(number of pages, (width, height) of the first page in points) of a PDF that the browser
    made. The pages are the objects of the type Page (not Pages); the size is the media box."""
    pages = len(re.findall(rb'/Type\s*/Page(?![s\w])', data))
    box = re.search(rb'/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]', data)
    size = (float(box.group(1)), float(box.group(2))) if box else None
    return pages, size


def is_a4(size):
    return size is not None and all(abs(have - want) <= A4_TOLERANCE
                                    for have, want in zip(size, A4_POINTS))


def print_view_pdf(page, out, number, width):
    """The print view as the browser prints it: print media and the page size of the style sheet
    (no format is given, so that a style sheet without `@page` gives Letter and fails the check)."""
    page.emulate_media(media='print')
    data = page.pdf(prefer_css_page_size=True)
    path = os.path.join(out, '%02d-%s-%d.pdf' % (number, PRINT_VIEW, width))
    with open(path, 'wb') as handle:
        handle.write(data)
    pages, size = pdf_facts(data)
    return {'path': path, 'pages': pages, 'size': size,
            'ok': is_a4(size) and 1 <= pages <= MAX_PDF_PAGES}


def evaluations_on_paper(page):
    """Whether the evaluations page leaves its results out on paper and shows the note instead."""
    page.emulate_media(media='print')
    return page.evaluate("""() => {
        const results = document.querySelector('.ui-ergebnis');
        const note = document.querySelector('.ui-druckhinweis');
        return !!results && getComputedStyle(results).display === 'none'
            && !!note && getComputedStyle(note).display !== 'none';
    }""")


def fill_report(page, text):
    """A minimal valid report; the date is yesterday so that it never lies in the future."""
    page.fill('#id_date', (date.today() - timedelta(days=1)).isoformat())
    page.fill('#id_incident', text)
    page.fill('#id_reason', 'Demo: erfundene Ursache.')
    page.fill('#id_immediate_action', 'Demo: erfundene Sofortmaßnahme.')
    page.select_option('#id_preventability', 'avoidable')
    page.check('#id_public_0')


def step_empty_report(run, page):
    page.goto(run.url('/incidents/%s/create/' % run.dept))
    # The browser would stop the empty form itself; the page under test is the one the server sends.
    page.evaluate("document.querySelector('main form').noValidate = true")
    page.get_by_role('button', name='Meldung absenden').click()
    page.wait_for_selector('.ui-alert--danger')


def step_report_success(run, page):
    page.goto(run.url('/incidents/%s/create/' % run.dept))
    fill_report(page, 'Demo (Sichtkontrolle): erfundenes Ereignis.')
    page.get_by_role('button', name='Meldung absenden').click()
    page.wait_for_selector('.ui-meldecode')


def step_wrong_code(run, page):
    page.goto(run.url('/incidents/%s/search/' % run.dept))
    page.fill('#id_incident_code', 'gibt-es-nicht')
    page.get_by_role('button', name='Code prüfen').click()
    page.wait_for_selector('.ui-field__error')


def step_detail_with_code(run, page):
    page.goto(run.url('/incidents/%s/search/' % run.dept))
    page.fill('#id_incident_code', run.code)
    page.get_by_role('button', name='Code prüfen').click()
    page.wait_for_selector('#stand-titel')


def login(run, page, password=None, user=None):
    page.goto(run.url('/login/'))
    page.fill('#username', user or run.user)
    page.fill('#password', password if password is not None else run.password)
    page.get_by_role('button', name='Anmelden').click()


def step_failed_login(run, page):
    login(run, page, user='qm-demo', password='falsches-passwort')
    page.wait_for_selector('#anmeldemeldung')


def step_admin_index(run, page):
    login(run, page)
    page.wait_for_url('**/qm/')  # the reviewer lands on the QM overview
    page.goto(run.url('/admin/'))


def qm_step(path):
    """A step that logs in as the reviewer and opens the QM page at path."""
    def step(run, page):
        login(run, page)
        page.wait_for_url('**/qm/')
        if path != '/qm/':
            page.goto(run.url(path))
    return step


def step_selftest(run, page):
    """A public page into which the three kinds of finding are put by hand."""
    page.goto(run.url('/incidents/%s/' % run.dept))
    page.evaluate("""() => {
        // the policy has no 'unsafe-inline': the browser blocks the style attribute and says so
        document.body.insertAdjacentHTML('beforeend', '<p style="color:red">x</p>');
        // a request to another origin (the policy blocks it too, but it is still a request)
        const far = new Image();
        far.src = 'https://example.invalid/probe.png';
        document.body.appendChild(far);
        // horizontal scrolling: a table (width attribute, no style) wider than every viewport
        document.body.insertAdjacentHTML('beforeend',
            '<table width="3000"><tr><td>wide</td></tr></table>');
        // a chart whose type is far too small once the view box is scaled to the page
        document.body.insertAdjacentHTML('beforeend',
            '<svg width="120" height="20" viewBox="0 0 120 20"><text x="0" y="14" font-size="6">small</text></svg>');
    }""")
    page.wait_for_timeout(1000)


# name, path (None: the step reaches the page), step, expected status of the page
def pages(run):
    d = run.dept
    return [
        ('start', '/', None, 200),
        ('list', '/incidents/%s/' % d, None, 200),
        ('list-search', '/incidents/%s/?q=Wagen' % d, None, 200),
        ('list-page-2', '/incidents/%s/?page=2' % d, None, 200),
        ('report-form', '/incidents/%s/create/' % d, None, 200),
        ('report-form-errors', None, step_empty_report, 200),
        ('report-success', None, step_report_success, 200),
        ('my-report', '/incidents/%s/search/' % d, None, 200),
        ('my-report-wrong-code', None, step_wrong_code, 200),
        ('my-report-detail', None, step_detail_with_code, 200),
        ('login', '/login/', None, 200),
        ('login-failed', None, step_failed_login, 200),
        ('password-reset', '/accounts/password_reset/', None, 200),
        ('not-found', '/incidents/gibtsnicht/', None, 404),
        ('qm-overview', None, qm_step('/qm/'), 200),
        ('qm-incidents', None, qm_step('/qm/meldungen/'), 200),
        ('qm-reports', None, qm_step('/qm/auswertungen/'), 200),
        ('qm-reports-print', None, qm_step('/qm/auswertungen/druck/'), 200),
        ('admin-index', None, step_admin_index, 200),
    ]


def check_page(browser, run, name, path, step, expected, width, number):
    """One page at one width in a new browser context (no cookies from the page before)."""
    context = browser.new_context(ignore_https_errors=True, locale='de-DE',
                                  viewport={'width': width, 'height': HEIGHT})
    context.add_init_script(COLLECT_CSP)
    page = context.new_page()
    console, foreign, statuses = [], [], []

    def on_console(message):
        if 'content security policy' in message.text.lower():
            console.append(message.text)

    def on_request(request):
        parts = urlsplit(request.url)
        if parts.scheme in ('http', 'https') and '{0.scheme}://{0.netloc}'.format(parts) != run.origin:
            foreign.append(request.url)

    def on_response(response):
        if response.request.is_navigation_request() and response.request.frame == page.main_frame:
            statuses.append(response.status)

    page.on('console', on_console)
    page.on('request', on_request)
    page.on('response', on_response)
    if path is not None:
        page.goto(run.url(path))
    else:
        step(run, page)
    page.wait_for_load_state('networkidle')
    measured = page.evaluate(MEASURE, MIN_SVG_FONT_PX)
    shot = os.path.join(run.out, '%02d-%s-%d.png' % (number, name, width))
    page.screenshot(path=shot, full_page=True)
    pdf, paper = None, None
    if width == WIDTHS[-1] and name == PRINT_VIEW:
        pdf = print_view_pdf(page, run.out, number, width)
    if width == WIDTHS[-1] and name == EVALUATIONS:
        paper = evaluations_on_paper(page)
    context.close()
    status = statuses[-1] if statuses else None
    return {
        'page': name, 'width': width, 'shot': shot,
        'overflow': measured['scroll'] > measured['client'],
        'scroll': measured['scroll'], 'client': measured['client'],
        'csp': console + measured['csp'], 'foreign': sorted(set(foreign)),
        'small_text': measured['smallText'], 'pdf': pdf, 'paper': paper,
        'status': status, 'status_ok': status == expected,
    }


def paper_text(result):
    """What the table says of the paper: the pages of the PDF of the print view, or whether the
    evaluations page leaves its results out on paper; a dash for the other pages."""
    if result['pdf']:
        pdf = result['pdf']
        return '%d p. %s' % (pdf['pages'], 'A4' if is_a4(pdf['size']) else 'NOT A4 %s' % (pdf['size'],))
    if result['paper'] is not None:
        return 'results left out' if result['paper'] else 'RESULTS PRINTED'
    return '-'


def print_table(results):
    header = ('page', 'width', 'status', 'h-scroll', 'CSP', 'foreign origins', 'small SVG text',
              'paper')
    rows = [header]
    for r in results:
        rows.append((r['page'], str(r['width']), str(r['status']),
                     'YES %d>%d' % (r['scroll'], r['client']) if r['overflow'] else 'no',
                     str(len(r['csp'])), str(len(r['foreign'])), str(len(r['small_text'])),
                     paper_text(r)))
    widths = [max(len(row[i]) for row in rows) for i in range(len(header))]
    for index, row in enumerate(rows):
        print('  '.join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
        if index == 0:
            print('  '.join('-' * w for w in widths))


def selftest(run):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        result = check_page(browser, run, 'selftest', None, step_selftest, 200, 320, 99)
        browser.close()
    found = {'h-scroll': result['overflow'], 'CSP message': bool(result['csp']),
             'request to another origin': bool(result['foreign']),
             'SVG text under %d px' % MIN_SVG_FONT_PX: bool(result['small_text'])}
    for kind, hit in found.items():
        print('%-28s %s' % (kind, 'found' if hit else 'NOT FOUND'))
    if all(found.values()):
        print('SELFTEST OK: the check sees all four kinds of finding')
        return 0
    print('SELFTEST FAILED: the check misses a kind of finding')
    return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--base', default=os.environ.get('ACCEPT_URL', 'https://cirs.test'),
                        help='address of the site (env ACCEPT_URL)')
    parser.add_argument('--dept', default='demo', help='label of the department')
    parser.add_argument('--code', default='ab#d$f-9',
                        help='code of a demo report for the report page (the old code of '
                             'seed_demo_data)')
    parser.add_argument('--qm-user', default='qm-demo', help='reviewer for the QM pages and the admin index')
    parser.add_argument('--qm-password', default=os.environ.get('DEMO_QM_PASSWORD', ''),
                        help='password of the reviewer (env DEMO_QM_PASSWORD)')
    parser.add_argument('--out', default='artifacts/sicht', help='directory of the screenshots')
    parser.add_argument('--selftest', action='store_true',
                        help='proves that the check can fail: checks one page with a style attribute, '
                             'a request to another origin, a wide table and a small SVG text put in '
                             'by hand, and exits with 0 only if all four are found')
    args = parser.parse_args()
    run = Run(args)
    os.makedirs(run.out, exist_ok=True)
    if args.selftest:
        return selftest(run)

    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for number, (name, path, step, expected) in enumerate(pages(run), start=1):
            for width in WIDTHS:
                results.append(check_page(browser, run, name, path, step, expected, width, number))
        browser.close()

    print_table(results)
    findings = []
    for r in results:
        where = '%s at %d px' % (r['page'], r['width'])
        if r['overflow']:
            findings.append('%s: horizontal scrolling (%d > %d)' % (where, r['scroll'], r['client']))
        for message in r['csp']:
            findings.append('%s: CSP: %s' % (where, message))
        for url in r['foreign']:
            findings.append('%s: request to another origin: %s' % (where, url))
        for text in r['small_text']:
            findings.append('%s: SVG text under %d px: %s' % (where, MIN_SVG_FONT_PX, text))
        if r['pdf'] and not r['pdf']['ok']:
            findings.append('%s: the PDF is %s pages of %s points, not 1 to %d pages of A4 %s'
                            % (where, r['pdf']['pages'], r['pdf']['size'], MAX_PDF_PAGES, A4_POINTS))
        if r['paper'] is False:
            findings.append('%s: on paper the page prints its results and has no note' % where)
        if not r['status_ok']:
            findings.append('%s: unexpected status %s' % (where, r['status']))
    print()
    print('%d pages, %d screenshots in %s' % (len(pages(run)), len(results), run.out))
    if findings:
        print('%d FINDINGS:' % len(findings))
        for finding in findings:
            print('  ' + finding)
        return 1
    print('NO FINDINGS: no horizontal scrolling, no CSP messages, no requests to other origins, '
          'no SVG text under %d px, the print view is A4 and the evaluations page prints no '
          'numbers' % MIN_SVG_FONT_PX)
    return 0


if __name__ == '__main__':
    sys.exit(main())
