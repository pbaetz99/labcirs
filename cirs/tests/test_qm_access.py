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

"""Who gets into the QM pages, what they see of the frame, and how they get there."""

import codecs
import re
from urllib.parse import parse_qs, quote, urlsplit

from django.contrib.auth.models import AnonymousUser
from django.test import TestCase
from django.urls import reverse
from model_bakery import baker

from cirs.models import CriticalIncident, Reviewer
from cirs.qm.access import scoped_departments, scoped_incidents

from .canary import CANARY, CanaryMixin
from .helpers import create_role, create_user, csp_violations, make_incident

DE = {'HTTP_ACCEPT_LANGUAGE': 'de'}
# name of the address, path
ADDRESSES = (('qm_overview', '/qm/'), ('qm_incidents', '/qm/meldungen/'),
             ('qm_reports', '/qm/auswertungen/'), ('qm_reports_print', '/qm/auswertungen/druck/'),
             ('qm_reports_csv', '/qm/auswertungen/csv/'))
URLS = [reverse(name) for name, _ in ADDRESSES]
# the pages that are HTML, with their German title
PAGES = (('qm_overview', 'Überblick'), ('qm_incidents', 'Meldungen'),
         ('qm_reports', 'Auswertungen'), ('qm_reports_print', 'Auswertung (Druckansicht)'))
NO_DEPARTMENT_DE = 'Ihr Zugang gehört keiner Abteilung an.'


class QMTestCase(CanaryMixin, TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.dept = baker.make_recipe('cirs.department', name='Station Eins')
        cls.reviewer = baker.make_recipe('cirs.reviewer')
        cls.dept.reviewers.add(cls.reviewer)
        cls.own = make_incident(cls.dept, incident='Eigene Meldung')
        cls.make_canary()

    def login(self, user=None):
        self.client.force_login(user or self.reviewer.user)


class AddressesTest(TestCase):

    def test_the_five_addresses(self):
        for name, path in ADDRESSES:
            self.assertEqual(reverse(name), path)


class AccessTest(QMTestCase):

    def test_anonymous_is_sent_to_the_login_and_comes_back(self):
        for url in URLS:
            response = self.client.get(url + '?stand=new')
            self.assertEqual(response.status_code, 302, url)
            target = urlsplit(response.url)
            self.assertEqual(target.path, reverse('login'), url)
            self.assertEqual(parse_qs(target.query), {'next': [url + '?stand=new']}, url)

    def test_every_other_account_is_refused(self):
        superuser = create_user('boss', superuser=True)
        # an administrator who also got a reviewer role by a detour is still an administrator
        Reviewer.objects.create(user=superuser)
        accounts = (('reporter account', self.dept.reporter.user),
                    ('account without a role', create_user('norole')),
                    ('superuser', superuser))
        for who, user in accounts:
            self.login(user)
            for url in URLS:
                self.assertEqual(self.client.get(url).status_code, 403, '%s %s' % (who, url))

    def test_the_reviewer_gets_every_page(self):
        self.login()
        for url in URLS:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_a_reviewer_without_a_department_gets_the_pages_with_a_notice(self):
        self.login(baker.make_recipe('cirs.reviewer').user)
        for name, _ in PAGES:
            response = self.client.get(reverse(name), **DE)
            self.assertContains(response, NO_DEPARTMENT_DE, msg_prefix=name)
        self.assertEqual(self.client.get(reverse('qm_reports_csv')).status_code, 200)

    def test_only_get_and_head_are_allowed(self):
        self.login()
        for url in URLS:
            self.assertEqual(self.client.head(url).status_code, 200, url)
            for method in ('post', 'put', 'patch', 'delete', 'options'):
                self.assertEqual(getattr(self.client, method)(url).status_code, 405,
                                 '%s %s' % (method, url))

    def test_who_may_not_enter_cannot_post_either(self):
        # the check of the account comes before the check of the method
        self.assertEqual(self.client.post(reverse('qm_overview')).status_code, 302)
        self.login(create_user('boss', superuser=True))
        self.assertEqual(self.client.post(reverse('qm_overview')).status_code, 403)

    def test_nothing_is_kept_in_the_cache_of_the_browser(self):
        # a shared PC: the Back button after logging out must not bring the pages back
        for url in URLS:
            self.assertIn('no-store', self.client.get(url)['Cache-Control'], url)
        self.login()
        for url in URLS:
            self.assertIn('no-store', self.client.get(url)['Cache-Control'], url)
            self.assertIn('no-store', self.client.head(url)['Cache-Control'], url)

    def test_the_csv_is_a_download_in_utf8_with_a_byte_order_mark(self):
        self.login()
        response = self.client.get(reverse('qm_reports_csv'))
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        self.assertTrue(response.content.startswith(codecs.BOM_UTF8))
        disposition = response['Content-Disposition']
        self.assertTrue(disposition.startswith('attachment;'), disposition)
        self.assertTrue(disposition.isascii(), disposition)

    def test_no_page_shows_the_data_of_a_foreign_department(self):
        self.login()
        for url in URLS:
            self.assertNoCanary(self.client.get(url).content, url)

    def test_the_check_for_foreign_data_can_fail(self):
        for text in (CANARY, CANARY.upper(), '<td>x %s y</td>' % CANARY.lower(),
                     ('%s Meldung 0' % CANARY).encode()):
            with self.assertRaises(AssertionError, msg=text):
                self.assertNoCanary(text)
        self.assertNoCanary('Station Eins')


class ScopeTest(QMTestCase):

    def test_the_incidents_of_all_departments_of_the_reviewer_inactive_ones_included(self):
        inactive = baker.make_recipe('cirs.department', active=False)
        inactive.reviewers.add(self.reviewer)
        in_inactive = make_incident(inactive)
        incidents = scoped_incidents(self.reviewer.user)
        self.assertCountEqual(incidents, [self.own, in_inactive])
        self.assertCountEqual(scoped_departments(self.reviewer.user), [self.dept, inactive])

    def test_never_the_incidents_of_a_department_of_somebody_else(self):
        self.assertFalse(scoped_incidents(self.reviewer.user)
                         .filter(pk__in=[i.pk for i in self.canary_incidents]).exists())
        self.assertEqual(CriticalIncident.objects.count(), 4)

    def test_nobody_without_the_role_has_a_scope(self):
        superuser = create_user('boss', superuser=True)
        for user in (AnonymousUser(), superuser, create_user('norole'), self.dept.reporter.user,
                     baker.make_recipe('cirs.reviewer').user):
            self.assertFalse(scoped_incidents(user).exists(), user)
            self.assertFalse(scoped_departments(user).exists(), user)
        Reviewer.objects.create(user=superuser)
        self.assertFalse(scoped_incidents(superuser).exists())


class PageFrameTest(QMTestCase):

    def html(self, name, **extra):
        self.login()
        return self.client.get(reverse(name), **DE, **extra).content.decode()

    def test_title_and_one_heading(self):
        for name, title in PAGES:
            html = self.html(name)
            self.assertRegex(html, r'<title>%s · [^<]+</title>' % re.escape(title), name)
            self.assertEqual(re.findall(r'<h1[^>]*>(.*?)</h1>', html, re.S), [title], name)

    def test_no_inline_code(self):
        for name, _ in PAGES:
            self.assertEqual(csp_violations(self.html(name)), [], name)

    def test_the_page_says_whose_incidents_it_covers(self):
        for name, _ in PAGES:
            html = self.html(name)
            self.assertIn('Abteilung: Station Eins', html, name)
            self.assertNotIn('Abteilungen:', html, name)
        second = baker.make_recipe('cirs.department', name='Station Zwei')
        second.reviewers.add(self.reviewer)
        for name, _ in PAGES:
            self.assertIn('Abteilungen: Station Eins, Station Zwei', self.html(name), name)

    def test_the_departments_of_others_are_not_named(self):
        for name, _ in PAGES:
            self.assertNoCanary(self.html(name), name)

    def test_the_page_stays_empty_until_it_has_something_to_show(self):
        # the overview and the incident list have their content, and tests of their own
        for name, _ in PAGES:
            if name not in ('qm_overview', 'qm_incidents'):
                self.assertIn('Hier gibt es noch nichts anzuzeigen.', self.html(name), name)

    def test_the_pages_are_wide_and_light(self):
        html = self.html('qm_overview')
        self.assertIn('ui-main--wide', html)
        self.assertRegex(html, r'<html [^>]*data-theme="hell"')


class NavigationTest(QMTestCase):

    LABELS = (('qm_overview', 'Überblick'), ('qm_incidents', 'Meldungen'),
              ('qm_reports', 'Auswertungen'))

    def nav(self, url, **extra):
        html = self.client.get(url, **DE, **extra).content.decode()
        return re.search(r'<nav class="ui-nav".*?</nav>', html, re.S).group(0)

    def links(self, nav):
        """The links of the navigation: (path, text, is the current page)."""
        return [(path, text.strip(), 'aria-current="page"' in attrs)
                for path, attrs, text in re.findall(
                    r'<a class="ui-nav__link" href="([^"]*)"([^>]*)>([^<]*)</a>', nav)]

    def test_the_reviewer_has_the_three_pages_on_every_page(self):
        self.login()
        for url in (reverse('qm_overview'), self.dept.get_absolute_url(), reverse('login')):
            links = [(path, text) for path, text, _ in self.links(self.nav(url))]
            for name, label in self.LABELS:
                self.assertIn((reverse(name), label), links, url)

    def test_the_published_cases_stay_in_the_bar_on_the_qm_pages(self):
        self.login()
        for name, _label in self.LABELS:
            links = [(path, text) for path, text, _ in self.links(self.nav(reverse(name)))]
            self.assertIn((self.dept.get_absolute_url(), 'Veröffentlichte Fälle'), links, name)

    def test_the_current_page_and_only_it_is_marked(self):
        self.login()
        for name, label in self.LABELS:
            current = [text for path, text, current in self.links(self.nav(reverse(name)))
                       if current]
            self.assertEqual(current, [label], name)
        # on a page of the department none of the three is the current one
        marked = [text for path, text, current in self.links(self.nav(self.dept.get_absolute_url()))
                  if current]
        self.assertEqual(marked, ['Veröffentlichte Fälle'])

    def test_the_admin_link_and_the_other_entries_stay(self):
        self.login()
        links = [(path, text) for path, text, _ in self.links(self.nav(reverse('qm_overview')))]
        self.assertIn((reverse('admin:index'), 'Verwaltung'), links)
        links = [(path, text) for path, text, _ in self.links(self.nav(self.dept.get_absolute_url()))]
        self.assertIn((self.dept.get_absolute_url(), 'Veröffentlichte Fälle'), links)

    def test_nobody_else_sees_them(self):
        # an administrator who also got a reviewer role by a detour has no access, so no links
        boss_and_reviewer = create_user('boss-reviewer', superuser=True)
        Reviewer.objects.create(user=boss_and_reviewer)
        for user in (None, create_user('boss', superuser=True), create_user('norole'),
                     boss_and_reviewer):
            if user:
                self.login(user)
            nav = self.nav(reverse('login'))
            for name, _ in self.LABELS:
                self.assertNotIn('href="%s"' % reverse(name), nav, user)
        self.client.logout()
        nav = self.nav(self.dept.get_absolute_url())
        self.assertNotIn('/qm/', nav)


class LoginTest(QMTestCase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.qm = create_role(Reviewer, 'qm-login')
        cls.dept.reviewers.add(cls.qm)

    def log_in(self, next_url=None):
        url = reverse('login') + ('?next=' + next_url if next_url else '')
        return self.client.post(url, {'username': 'qm-login', 'password': 'qm-login'})

    def test_the_reviewer_lands_on_the_overview(self):
        self.assertRedirects(self.log_in(), reverse('qm_overview'), fetch_redirect_response=False)

    def test_a_valid_next_comes_first(self):
        # a bare word is no name of a view either: the redirect takes it as the address it is
        for target in ('/qm/meldungen/%3Fstand%3Dnew', self.dept.get_absolute_url(),
                       reverse('admin:index'), 'foo'):
            response = self.log_in(target)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.url, parse_qs('next=' + target)['next'][0], target)

    def test_a_next_to_another_host_is_ignored(self):
        for target in ('https://evil.example/', '//evil.example/', '/\\evil.example/',
                       'javascript:alert(1)'):
            self.assertRedirects(self.log_in(quote(target, safe='')), reverse('qm_overview'),
                                 fetch_redirect_response=False)

    def test_a_page_of_the_qm_needs_the_login_and_the_login_leads_back(self):
        path = '/qm/meldungen/?stand=in+process&page=1'  # a page that exists, the list is empty
        login_url = self.client.get(path).url
        response = self.client.post(login_url, {'username': 'qm-login', 'password': 'qm-login'})
        self.assertRedirects(response, path, fetch_redirect_response=False)
        self.assertEqual(self.client.get(path).status_code, 200)

    def test_the_login_page_carries_the_next_of_the_qm_page(self):
        response = self.client.get(self.client.get('/qm/auswertungen/').url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<input type="hidden" name="next" value="/qm/auswertungen/">')

    def test_a_superuser_still_goes_to_the_admin(self):
        create_user('boss', superuser=True)
        response = self.client.post(reverse('login'), {'username': 'boss', 'password': 'boss'})
        self.assertRedirects(response, reverse('labcirs_home'), fetch_redirect_response=False)
        response = self.client.post(reverse('login') + '?next=' + reverse('admin:index'),
                                    {'username': 'boss', 'password': 'boss'})
        self.assertRedirects(response, reverse('admin:index'), fetch_redirect_response=False)
