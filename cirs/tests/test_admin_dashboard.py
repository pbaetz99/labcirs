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

"""The start page of the admin: a superuser sees the system status above the list of the models
(and no number of any incident), a reviewer sees the way to the QM area instead, and nobody sees
the folder of the backup status."""

import html as html_lib
import logging
import os
import re
import tempfile
import time
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.staticfiles import finders
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from model_bakery import baker

import cirs
from cirs.models import LabCIRSConfig, Reviewer
from cirs.system_status import STALE_ACCOUNT_LIST_LENGTH

from .canary import CANARY, CanaryMixin
from .helpers import create_role, create_user, csp_violations, make_incident

DE = {'HTTP_ACCEPT_LANGUAGE': 'de'}
EN = {'HTTP_ACCEPT_LANGUAGE': 'en'}
STATUS_FILE_NAME = 'letzte-sicherung'
DAY = timedelta(days=1)
# Creating an account hashes its password, which takes long enough to be felt with dozens of them.
FAST_HASHERS = override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
INCIDENT_TABLES = ('cirs_criticalincident', 'cirs_comment', 'cirs_incidentstatuschange',
                   'cirs_publishableincident', 'cirs_reportercontact')
QM_LINK = 'Zum QM-Bereich'
SECTION = re.compile(r'<section[^>]*\sid="system-status".*?</section>', re.S)


def words(fragment):
    """The text of a piece of HTML: no tags, one space between the words."""
    return ' '.join(html_lib.unescape(re.sub(r'<[^>]+>', ' ', fragment)).split())


def section(html):
    return SECTION.search(html).group(0)


def block(html, heading):
    """The part of the status under the heading (h3) up to the next one."""
    start = re.search(r'<h3[^>]*>\s*%s\s*</h3>' % re.escape(heading), section(html)).end()
    rest = section(html)[start:]
    end = re.search(r'<h3[\s>]', rest)
    return rest[:end.start()] if end else rest


def tables(fragment):
    """The rows of each table of the fragment, as the text of their cells."""
    return [[[words(cell) for cell in re.findall(r'<t[hd][^>]*>(.*?)</t[hd]>', row, re.S)]
             for row in re.findall(r'<tr[^>]*>(.*?)</tr>', table, re.S)]
            for table in re.findall(r'<table.*?</table>', fragment, re.S)]


def hrefs(fragment):
    return [html_lib.unescape(href) for href in re.findall(r'href="([^"]*)"', fragment)]


def write_status_file(folder, content='labcirs-2026-10-06.dump 1234567\n', hours_ago=1):
    """The status file of the backup script, written `hours_ago` hours ago."""
    path = Path(folder) / STATUS_FILE_NAME
    path.write_text(content, encoding='utf-8')
    moment = time.time() - hours_ago * 3600
    os.utime(path, (moment, moment))
    return path


def age(user, last_login=None, joined=None):
    changes = {'last_login': last_login}
    if joined is not None:
        changes['date_joined'] = joined
    User.objects.filter(pk=user.pk).update(**changes)
    return user


@FAST_HASHERS
class DashboardTestCase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.superuser = create_user('boss', superuser=True)

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        # the warnings about a status file that cannot be used would fill the output of the run
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        # the mail of the development stack goes to a catcher: the status must not depend on it
        self.enterContext(override_settings(BACKUP_STATUS_DIR='', EMAIL_HOST='localhost',
                                            DEFAULT_FROM_EMAIL=''))

    def get(self, user=None, **extra):
        self.client.force_login(user or self.superuser)
        return self.client.get(reverse('admin:index'), **{**DE, **extra})

    def html(self, user=None, **extra):
        return self.get(user, **extra).content.decode()


class WhoSeesWhatTest(CanaryMixin, DashboardTestCase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.dept = baker.make_recipe('cirs.department', name='Station Eins')
        cls.reviewer = baker.make_recipe('cirs.reviewer')
        cls.dept.reviewers.add(cls.reviewer)
        cls.make_canary()

    def test_a_superuser_sees_the_system_status_above_the_list_of_the_models(self):
        html = self.html()
        self.assertEqual(len(SECTION.findall(html)), 1)
        self.assertRegex(section(html), r'<h2[^>]*>\s*Systemzustand\s*</h2>')
        self.assertLess(html.index('id="system-status"'), html.index('class="app-cirs'))
        # the models are still there, below it
        self.assertIn(reverse('admin:cirs_criticalincident_changelist'), html)

    def test_a_superuser_sees_every_part(self):
        status = words(section(self.html()))
        for part in ('Version', 'Letzte Sicherung', 'Abteilungen ohne QM', 'Mailversand',
                     'Konten', 'Konten ohne Anmeldung seit über 180 Tagen'):
            self.assertIn(part, status)

    def test_a_superuser_gets_no_way_to_the_qm_area(self):
        # the QM pages answer a superuser with 403: the page does not lead there
        self.assertNotIn(QM_LINK, self.html())
        self.assertNotIn(reverse('qm_overview'), hrefs(self.html()))

    def test_a_reviewer_sees_the_way_to_the_qm_area_and_no_system_status(self):
        html = self.html(self.reviewer.user)
        self.assertNotIn('system-status', html)
        self.assertNotIn('Systemzustand', html)
        link = re.search(r'<a [^>]*href="%s"[^>]*>\s*%s\s*</a>' % (reverse('qm_overview'), QM_LINK),
                         html)
        self.assertIsNotNone(link, 'no link to the QM area')
        self.assertIn('class="button"', link.group(0))
        # at the top: above the list of the models
        self.assertLess(link.start(), html.index('class="app-cirs'))

    def test_a_reviewer_sees_nothing_of_a_foreign_department_on_the_page(self):
        self.assertNoCanary(self.html(self.reviewer.user))

    def test_a_superuser_with_a_reviewer_role_is_a_superuser_here_too(self):
        boss = create_user('boss2', superuser=True)
        Reviewer.objects.create(user=boss)
        html = self.html(boss)
        self.assertIn('id="system-status"', html)
        self.assertNotIn(QM_LINK, html)

    def test_staff_without_a_role_sees_neither(self):
        user = create_user('helper')
        User.objects.filter(pk=user.pk).update(is_staff=True)
        response = self.get(user)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertNotIn('system-status', html)
        self.assertNotIn(QM_LINK, html)

    def test_who_is_not_staff_does_not_get_in(self):
        for user in (self.dept.reporter.user, create_user('norole')):
            response = self.get(user)
            self.assertEqual(response.status_code, 302)
            self.assertIn(reverse('admin:login'), response.url)

    def test_anonymous_is_sent_to_the_login(self):
        self.client.logout()
        response = self.client.get(reverse('admin:index'))
        self.assertRedirects(response, reverse('admin:login') + '?next=' + reverse('admin:index'),
                             fetch_redirect_response=False)

    def test_the_page_is_not_kept_in_the_cache_of_the_browser(self):
        self.assertIn('no-store', self.get()['Cache-Control'])

    def test_the_status_is_not_part_of_the_other_pages_of_the_admin(self):
        self.client.force_login(self.superuser)
        for name in ('admin:cirs_department_changelist', 'admin:auth_user_changelist'):
            self.assertNotIn('system-status', self.client.get(reverse(name)).content.decode())


class SectionTest(DashboardTestCase):

    def test_the_version_is_the_one_of_the_application(self):
        self.assertIn('Version %s' % cirs.__version__, words(section(self.html())))

    def test_english_too(self):
        status = words(section(self.html(**EN)))
        for part in ('System status', 'Version', 'Last backup', 'Departments without QM',
                     'Mail', 'Accounts', 'Accounts without login for over 180 days'):
            self.assertIn(part, status)

    def test_the_headings_follow_one_another_and_the_tables_have_caption_and_scope(self):
        html = self.html()
        self.assertEqual(len(re.findall(r'<h1[\s>]', html)), 1)
        text = section(html)
        self.assertEqual(re.findall(r'<h([1-6])[\s>]', text), ['2'] + ['3'] * 5)
        for table in re.findall(r'<table.*?</table>', text, re.S):
            self.assertRegex(table, r'<caption[^>]*>\s*\S')
            for head in re.findall(r'<th\b[^>]*>', table):
                self.assertRegex(head, r'scope="(row|col)"')

    def test_nothing_the_strict_policy_blocks(self):
        self.assertEqual(csp_violations(self.html()), [])


class AccountsTest(DashboardTestCase):

    def test_the_accounts_by_role(self):
        create_user('boss2', superuser=True)
        for name in ('qm1', 'qm2', 'qm3'):
            create_role(Reviewer, name)
        User.objects.filter(username='qm3').update(is_active=False)
        baker.make_recipe('cirs.department', _quantity=4)
        create_user('norole')
        rows = tables(block(self.html(), 'Konten'))[0]
        self.assertEqual(rows, [['Rolle', 'Konten', 'Davon inaktiv'],
                                ['Superuser', '2', '0'], ['QM', '3', '1'],
                                ['Reporter-Konto', '4', '0'], ['Ohne Rolle', '1', '0']])

    def test_it_says_why_the_reporter_accounts_are_never_in_the_list_below(self):
        self.assertIn('Reporter-Konten sind die technischen Konten der Abteilungen. '
                      'Sie melden sich nie an.', words(block(self.html(), 'Konten')))

    def test_nobody_has_been_away_for_long(self):
        self.assertEqual(words(block(self.html(), 'Konten ohne Anmeldung seit über 180 Tagen')),
                         'Keine.')

    def test_the_accounts_that_have_been_away_for_long(self):
        now = timezone.now()
        old = age(create_user('forgotten'), last_login=now - 400 * DAY)
        qm = create_role(Reviewer, 'qm-away').user
        age(qm, last_login=now - 200 * DAY)
        never = age(create_user('never'), last_login=None, joined=now - 300 * DAY)
        age(create_user('recent'), last_login=now - 30 * DAY)
        text = block(self.html(), 'Konten ohne Anmeldung seit über 180 Tagen')
        rows = tables(text)[0]
        self.assertEqual(rows[0], ['Konto', 'Rolle', 'Ohne Anmeldung seit'])
        self.assertEqual([row[:2] for row in rows[1:]],
                         [['forgotten', 'Ohne Rolle'], ['never', 'Ohne Rolle'],
                          ['qm-away', 'QM']])
        # the date as the language writes it, and the account that never logged in says so
        local = timezone.localtime
        self.assertEqual(rows[1][2], local(now - 400 * DAY).strftime('%d.%m.%Y'))
        self.assertEqual(rows[2][2], local(now - 300 * DAY).strftime('%d.%m.%Y')
                         + ' (angelegt, nie angemeldet)')
        self.assertEqual(rows[3][2], local(now - 200 * DAY).strftime('%d.%m.%Y'))
        # every account leads to its page in the admin
        self.assertEqual([url for url in hrefs(text) if '/auth/user/' in url],
                         [reverse('admin:auth_user_change', args=[user.pk])
                          for user in (old, never, qm)])

    def test_a_long_list_is_cut_and_says_how_many_are_left(self):
        users = [create_user('user%02d' % number)
                 for number in range(STALE_ACCOUNT_LIST_LENGTH + 3)]
        User.objects.filter(pk__in=[user.pk for user in users]).update(
            last_login=timezone.now() - 300 * DAY)
        text = block(self.html(), 'Konten ohne Anmeldung seit über 180 Tagen')
        self.assertEqual(len(tables(text)[0]), 1 + STALE_ACCOUNT_LIST_LENGTH)
        self.assertIn('3 weitere Konten werden nicht angezeigt.', words(text))
        self.assertIn(reverse('admin:auth_user_changelist'), hrefs(text))
        # one is a different sentence
        User.objects.filter(pk=users[-1].pk).update(last_login=timezone.now())
        User.objects.filter(pk=users[-2].pk).update(last_login=timezone.now())
        text = block(self.html(), 'Konten ohne Anmeldung seit über 180 Tagen')
        self.assertIn('Ein weiteres Konto wird nicht angezeigt.', words(text))

    def test_a_short_list_says_nothing_about_more(self):
        age(create_user('forgotten'), last_login=timezone.now() - 300 * DAY)
        text = block(self.html(), 'Konten ohne Anmeldung seit über 180 Tagen')
        self.assertNotIn('nicht angezeigt', words(text))
        self.assertNotIn(reverse('admin:auth_user_changelist'), hrefs(text))

    def test_names_are_text_and_never_markup(self):
        age(create_user('<i>x</i>'), last_login=timezone.now() - 300 * DAY)
        html = self.html()
        self.assertIn('&lt;i&gt;x&lt;/i&gt;', section(html))
        self.assertNotIn('<i>x</i>', html)


class DepartmentsTest(DashboardTestCase):

    def test_where_every_department_has_a_qm(self):
        department = baker.make_recipe('cirs.department')
        department.reviewers.add(create_role(Reviewer, 'qm'))
        self.assertEqual(words(block(self.html(), 'Abteilungen ohne QM')),
                         'Alle Abteilungen haben ein QM.')

    def test_without_departments(self):
        self.assertEqual(words(block(self.html(), 'Abteilungen ohne QM')),
                         'Es gibt noch keine Abteilungen.')

    def test_the_departments_without_a_qm_are_named_with_a_warning(self):
        covered = baker.make_recipe('cirs.department', name='Station Eins')
        covered.reviewers.add(create_role(Reviewer, 'qm'))
        open_one = baker.make_recipe('cirs.department', name='Station Zwei')
        closed = baker.make_recipe('cirs.department', name='Station Drei', active=False)
        text = block(self.html(), 'Abteilungen ohne QM')
        self.assertIn('Warnung: Diese Abteilungen haben kein QM. Niemand kann ihre Meldungen '
                      'bearbeiten.', words(text))
        items = [words(item) for item in re.findall(r'<li[^>]*>(.*?)</li>', text, re.S)]
        self.assertEqual(items, ['Station Drei (inaktiv)', 'Station Zwei'])
        # each leads to its page in the admin, where the QM is added
        self.assertEqual(hrefs(text), [reverse('admin:cirs_department_change', args=[closed.pk]),
                                       reverse('admin:cirs_department_change', args=[open_one.pk])])
        self.assertNotIn('Station Eins', text)

    def test_the_warning_is_a_word_a_symbol_and_a_tint_not_only_a_colour(self):
        baker.make_recipe('cirs.department')
        text = block(self.html(), 'Abteilungen ohne QM')
        warning = re.search(r'<p class="[^"]*labcirs-state--warning[^"]*">(.*?)</p>', text, re.S)
        self.assertIsNotNone(warning)
        self.assertTrue(words(warning.group(1)).startswith('Warnung:'))

    def test_names_are_text_and_never_markup(self):
        baker.make_recipe('cirs.department', name='<b>Haus</b>')
        html = self.html()
        self.assertIn('&lt;b&gt;Haus&lt;/b&gt;', section(html))
        self.assertNotIn('<b>Haus</b>', html)


class MailTest(DashboardTestCase):

    def mail(self, **settings_):
        with override_settings(**settings_):
            return block(self.html(), 'Mailversand')

    def test_mail_is_set_up(self):
        text = self.mail(EMAIL_HOST='mail.example.org', DEFAULT_FROM_EMAIL='cirs@example.org')
        self.assertIn('Mailversand eingerichtet: Ja', words(text))
        self.assertNotIn('Dafür braucht es', words(text))

    def test_mail_is_not_set_up_and_says_what_it_needs(self):
        for settings_ in ({}, {'EMAIL_HOST': 'mail.example.org'},
                          {'DEFAULT_FROM_EMAIL': 'cirs@example.org'}):
            with self.subTest(settings_):
                text = words(self.mail(**settings_))
                self.assertIn('Mailversand eingerichtet: Nein', text)
                self.assertIn('Dafür braucht es einen Mailserver außer localhost und eine '
                              'Absenderadresse (LABCIRS_EMAIL_HOST und LABCIRS_DEFAULT_FROM_EMAIL).',
                              text)

    def test_the_server_and_the_sender_are_never_shown(self):
        with override_settings(EMAIL_HOST='smtp.geheim.example',
                               DEFAULT_FROM_EMAIL='absender@geheim.example'):
            html = self.html()
        self.assertNotIn('geheim', html)

    def test_the_notification_of_the_qm_of_each_department(self):
        notifying = baker.make_recipe('cirs.department', name='Station Eins')
        baker.make_recipe('cirs.department', name='Station Zwei')
        LabCIRSConfig.objects.filter(department=notifying).update(send_notification=True)
        rows = tables(self.mail(EMAIL_HOST='mail.example.org',
                                DEFAULT_FROM_EMAIL='a@example.org'))[0]
        self.assertEqual(rows, [['Abteilung', 'Benachrichtigung des QM'],
                                ['Station Eins', 'An'], ['Station Zwei', 'Aus']])

    def test_off_is_a_word_too(self):
        baker.make_recipe('cirs.department', name='Station Eins')
        self.assertIn('Station Eins Aus', words(self.mail()))

    def test_without_departments_there_is_no_table(self):
        self.assertEqual(tables(self.mail()), [])


class BackupTest(DashboardTestCase):
    """The last backup in each of its states. The folder is a path of the server: it is not on
    the page in any of them, and neither is anything the status file says about places."""

    def backup(self, heading='Letzte Sicherung', **extra):
        with override_settings(BACKUP_STATUS_DIR=str(self.folder)):
            html = self.html(**extra)
        self.assertNotIn(str(self.folder), html)
        self.assertNotIn(self.folder.name, html)
        self.assertEqual(csp_violations(html), [])
        return block(html, heading)

    def state(self, text):
        return re.search(r'labcirs-state--(\w+)', text).group(1)

    def test_not_set_up(self):
        text = block(self.html(), 'Letzte Sicherung')
        self.assertIn('Nicht eingerichtet. Der Sicherungsstatus erscheint, sobald die Einstellung '
                      'LABCIRS_BACKUP_STATUS_DIR einen Ordner mit der Statusdatei des '
                      'Sicherungsskripts nennt.', words(text))

    def test_a_folder_that_does_not_exist(self):
        with override_settings(BACKUP_STATUS_DIR=str(self.folder / 'missing')):
            html = self.html()
        self.assertNotIn(str(self.folder), html)
        text = block(html, 'Letzte Sicherung')
        self.assertIn('Hinweis: Der Ordner für den Sicherungsstatus ist nicht vorhanden.',
                      words(text))
        self.assertEqual(self.state(text), 'notice')

    def test_an_empty_folder(self):
        text = self.backup()
        self.assertIn('Hinweis: Es wurde noch keine Sicherung gemeldet.', words(text))
        self.assertEqual(self.state(text), 'notice')

    def test_an_empty_status_file_is_as_good_as_none(self):
        write_status_file(self.folder, content='')
        self.assertIn('Es wurde noch keine Sicherung gemeldet.', words(self.backup()))

    def test_a_status_file_that_cannot_be_read(self):
        (self.folder / STATUS_FILE_NAME).mkdir()
        text = self.backup()
        self.assertIn('Hinweis: Die Statusdatei der letzten Sicherung konnte nicht gelesen werden.',
                      words(text))
        self.assertEqual(self.state(text), 'notice')

    def test_a_backup_of_an_hour_ago(self):
        path = write_status_file(self.folder, 'labcirs-2026-10-06.dump 1234567', hours_ago=1)
        text = self.backup()
        moment = timezone.localtime(datetime.fromtimestamp(path.stat().st_mtime, dt_timezone.utc))
        self.assertIn(moment.strftime('%d.%m.%Y %H:%M'), words(text))
        self.assertIn('(Alter: 1 Stunde)', words(text))
        self.assertIn('Datei: labcirs-2026-10-06.dump (1,2 MB)', words(text))
        self.assertEqual(self.state(text), 'ok')
        self.assertNotIn('Warnung', words(text))

    def test_the_file_and_the_size_in_english(self):
        write_status_file(self.folder, 'labcirs-2026-10-06.dump 1234567')
        self.assertIn('File: labcirs-2026-10-06.dump (1.2 MB)',
                      words(self.backup('Last backup', **EN)))

    def test_a_note_with_only_a_name_or_only_a_size_or_nothing_useful(self):
        for content, expected in (('labcirs.dump', 'Datei: labcirs.dump'),
                                  ('2048', 'Größe: 2,0 KB'), ('..\n', None)):
            with self.subTest(content=content):
                write_status_file(self.folder, content)
                text = words(self.backup())
                self.assertEqual('Datei:' in text or 'Größe:' in text, expected is not None)
                if expected:
                    self.assertIn(expected, text)
                self.assertNotIn('None', text)

    def test_a_backup_of_two_days_ago_is_a_warning(self):
        write_status_file(self.folder, hours_ago=50)
        text = self.backup()
        self.assertIn('Warnung: Die letzte Sicherung ist älter als 26 Stunden.', words(text))
        self.assertEqual(self.state(text), 'warning')
        # still says when it was and what
        self.assertIn('Alter: 2 Tage', words(text))
        self.assertIn('Datei: labcirs-2026-10-06.dump', words(text))

    def test_the_limit_is_26_hours(self):
        for hours, state in ((25, 'ok'), (27, 'warning')):
            with self.subTest(hours=hours):
                write_status_file(self.folder, hours_ago=hours)
                self.assertEqual(self.state(self.backup()), state)

    def test_a_time_in_the_future_is_a_notice(self):
        write_status_file(self.folder, hours_ago=-48)
        text = self.backup()
        self.assertIn('Hinweis: Der Zeitpunkt der letzten Sicherung liegt in der Zukunft. '
                      'Bitte prüfen Sie die Uhr des Servers.', words(text))
        self.assertEqual(self.state(text), 'notice')

    def test_a_warning_has_a_word_a_symbol_and_a_tint(self):
        write_status_file(self.folder, hours_ago=50)
        text = self.backup()
        warning = re.search(r'<p class="[^"]*labcirs-state--warning[^"]*">(.*?)</p>', text, re.S)
        self.assertTrue(words(warning.group(1)).startswith('Warnung:'))

    def test_the_name_in_the_status_file_is_a_name_and_never_a_way(self):
        for content, shown in (('../../etc/passwd 1234', 'passwd'),
                               ('/var/backups/labcirs/labcirs-2026-10-06.dump 99',
                                'labcirs-2026-10-06.dump'),
                               ('C:\\Backups\\labcirs.dump 99', 'labcirs.dump')):
            with self.subTest(content=content):
                write_status_file(self.folder, content)
                text = self.backup()
                self.assertIn('Datei: %s (' % shown, words(text))
                for way in ('..', '/var', '/etc', 'C:', 'Backups'):
                    self.assertNotIn(way, text)

    def test_the_name_in_the_status_file_is_text_and_never_markup(self):
        write_status_file(self.folder, '<img src=x onerror=alert(1)>.dump 7')
        text = self.backup()
        self.assertIn('&lt;img src=x onerror=alert(1)&gt;.dump', text)
        self.assertNotIn('<img', text)

    def test_a_link_in_place_of_the_status_file_is_not_followed(self):
        secret = self.folder.parent / (self.folder.name + '-secret')
        secret.write_text('GEHEIMER-INHALT.dump 99', encoding='utf-8')
        self.addCleanup(secret.unlink)
        (self.folder / STATUS_FILE_NAME).symlink_to(secret)
        text = self.backup()
        self.assertNotIn('GEHEIMER', text)
        self.assertIn('konnte nicht gelesen werden', words(text))
        self.assertNotIn(secret.name, text)

    def test_a_link_to_a_system_file_is_not_followed_either(self):
        (self.folder / STATUS_FILE_NAME).symlink_to('/etc/passwd')
        text = self.backup()
        self.assertNotIn('root', text)
        self.assertEqual(self.state(text), 'notice')

    def test_other_files_in_the_folder_change_nothing(self):
        write_status_file(self.folder, 'right.dump 10', hours_ago=3)
        for name in ('labcirs-new.dump', '.hidden', STATUS_FILE_NAME + '.tmp', STATUS_FILE_NAME + '.part'):
            (self.folder / name).write_text('wrong.dump 20', encoding='utf-8')
        text = words(self.backup())
        self.assertIn('Datei: right.dump', text)
        self.assertNotIn('wrong', text)

    def test_a_setting_with_dots_in_it_is_used_as_written_and_never_shown(self):
        (self.folder / 'x').mkdir()
        write_status_file(self.folder, 'labcirs.dump 5')
        with override_settings(BACKUP_STATUS_DIR=str(self.folder / 'x' / '..')):
            html = self.html()
        self.assertIn('Datei: labcirs.dump', words(block(html, 'Letzte Sicherung')))
        self.assertNotIn('..', block(html, 'Letzte Sicherung'))
        self.assertNotIn(self.folder.name, html)


class NoIncidentDataTest(CanaryMixin, DashboardTestCase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.dept = baker.make_recipe('cirs.department', name='Station Eins')
        cls.dept.reviewers.add(baker.make_recipe('cirs.reviewer'))
        cls.make_canary()
        for number in range(17):
            make_incident(cls.dept, incident='Eigene Meldung %d' % number)

    def test_the_page_has_no_number_and_no_text_of_any_incident(self):
        status = words(section(self.html()))
        # 17 incidents of the department and 3 of the other one
        for number in (r'\b17\b', r'\b20\b', r'\b3\b'):
            self.assertNotRegex(status, number)
        self.assertNotIn('Eigene Meldung', status)
        for text in ('Meldung', 'Titel', 'Beschreibung', 'Maßnahmen', 'Kommentar'):
            self.assertNotIn('%s %s' % (CANARY, text), status)
        for text in ('Gruppe', 'Station'):
            self.assertNotIn('%s-%s' % (CANARY, text), status)

    def test_no_query_of_the_page_reads_an_incident(self):
        self.client.force_login(self.superuser)
        with CaptureQueriesContext(connection) as queries:
            self.client.get(reverse('admin:index'))
        for query in queries.captured_queries:
            for table in INCIDENT_TABLES:
                self.assertNotIn(table, query['sql'])

    def test_the_departments_of_the_system_are_the_business_of_a_superuser(self):
        # all departments, not the ones of a QM: nobody is scoped here
        text = words(section(self.html()))
        self.assertIn(CANARY + '-Abteilung', text)


class QueriesTest(DashboardTestCase):

    def count_queries(self):
        self.client.force_login(self.superuser)
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.client.get(reverse('admin:index')).status_code, 200)
        return len(queries)

    def test_the_number_of_queries_does_not_grow_with_accounts_and_departments(self):
        create_role(Reviewer, 'qm0')
        few = self.count_queries()
        now = timezone.now()
        for number in range(30):
            age(create_user('away%d' % number), last_login=now - 300 * DAY)
            create_role(Reviewer, 'qm%d' % (number + 1))
        for department in baker.make_recipe('cirs.department', _quantity=10):
            if department.pk % 2:
                department.reviewers.add(Reviewer.objects.first())
        self.assertEqual(self.count_queries(), few)


class StylesTest(SimpleTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        css = Path(settings.BASE_DIR, 'static/css/admin-theme.css').read_text(encoding='utf-8')
        start = css.index('System status')
        cls.block = css[css.rindex('/*', 0, start):]

    def test_the_rules_use_only_variables_of_the_admin(self):
        # the values of the variables are in the :root block at the top of the file, each with
        # the token of the design system it repeats
        self.assertNotRegex(self.block, r'#[0-9a-fA-F]{3,8}\b')
        self.assertNotRegex(self.block, r'\b(rgb|rgba|hsl|hsla)\(')
        self.assertIn('var(--', self.block)

    def test_no_style_attribute_is_needed(self):
        template = Path(settings.BASE_DIR, 'templates/admin/system_status.html')
        self.assertNotRegex(template.read_text(encoding='utf-8'), r'\sstyle\s*=')

    def test_the_cells_of_the_status_wrap_again(self):
        # the stylesheet of Django's start page keeps every cell of a module on one line and gives
        # the first column the whole width, which suits a list of models and not a status: that
        # would scroll the page sideways on a phone
        self.assertRegex(self.block, r'\.dashboard \.module\.labcirs-system table td\s*\{[^}]*'
                                     r'white-space:\s*normal')
        self.assertRegex(self.block, r'\.dashboard \.module\.labcirs-system table th\s*\{[^}]*'
                                     r'width:\s*auto')

    def test_names_without_a_space_break_instead_of_widening_the_page(self):
        self.assertRegex(self.block, r'\.module\.labcirs-system\s*\{[^}]*overflow-wrap:\s*anywhere')

    def test_the_symbols_are_files_of_the_admin_that_exist(self):
        urls = re.findall(r'url\(([^)]+)\)', self.block)
        self.assertTrue(urls)
        for url in urls:
            self.assertTrue(url.startswith('../admin/img/'), url)
            self.assertIsNotNone(finders.find(url[len('../'):]), url)
