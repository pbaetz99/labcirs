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

"""Every page a role can reach answers without a server error and without inline code.

The crawl follows the links on the admin index, so a model registered later is covered
without touching this file. A failure lists every failing URL, not just the first one.
"""

import re
from django.db import transaction
from django.test import Client, TestCase
from django.urls import reverse
from model_bakery import baker

from cirs.models import Comment, OrgUnit, PublishableIncident, ReporterContact
from cirs.tests.helpers import create_user, csp_violations

ADMIN_LINK = re.compile(r'href="(/admin/[^"?#]*)')
QM_LINK = re.compile(r'href="(/qm/[^"?#]*)')
CHANGELIST = re.compile(r'^/admin/[^/]+/[^/]+/$')
OBJECT_PAGE = re.compile(r'^/admin/[^/]+/[^/]+/\d+/(change|history|delete)/$')
ADMIN_STATUSES = (200, 302, 403)
# The configuration comes with its department, so its add page answers 403.
CONFIG_ADD = '/admin/cirs/labcirsconfig/add/'


class PageCrawlTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        # Two departments, so that the department list renders instead of redirecting.
        cls.dept, cls.other_dept = baker.make_recipe('cirs.department', _quantity=2)
        cls.reviewer = baker.make_recipe('cirs.reviewer')
        cls.dept.reviewers.add(cls.reviewer)
        cls.superuser = create_user('crawl-admin', superuser=True)
        cls.org_unit = org_unit = baker.make(OrgUnit, name='Synthetic ward')
        cls.published = baker.make_recipe('cirs.public_ci', department=cls.dept,
                                          org_unit=org_unit, status='in process')
        cls.publishable = pi = PublishableIncident.objects.create(critical_incident=cls.published)
        for language in ('en', 'de'):
            pi.create_translation(language, incident='Synthetic incident',
                                  description='Synthetic description',
                                  measures_and_consequences='Synthetic measures')
        pi.publish = True
        pi.save()
        cls.commented = baker.make_recipe('cirs.public_ci', department=cls.dept)
        baker.make(Comment, critical_incident=cls.commented, author=cls.reviewer.user,
                   text='Synthetic comment')
        # the reporter's page then carries the form that removes the address
        ReporterContact.objects.create(incident=cls.commented, email='synthetic@example.org')

    def setUp(self):
        self.failures = []

    def fetch(self, client, role, url, method='get', allowed=(200,), follow=False):
        """Requests url and records a failure instead of stopping at the first one."""
        try:
            # a savepoint, so that one failing page leaves the next ones a usable connection
            with transaction.atomic():
                response = getattr(client, method)(url, follow=follow,
                                                   HTTP_ACCEPT_LANGUAGE='de')
        except Exception as error:
            self.failures.append('%s %s: %s' % (role, url, type(error).__name__))
            return None
        if response.status_code not in allowed:
            self.failures.append('%s %s: status %s' % (role, url, response.status_code))
        elif response.status_code == 200 and 'html' in response.get('Content-Type', ''):
            violations = csp_violations(response.content.decode())
            if violations:
                self.failures.append('%s %s: %s' % (role, url, ', '.join(violations)))
        return response

    def assertNoFailures(self):
        self.assertEqual(self.failures, [], '\n' + '\n'.join(self.failures))

    def client_for(self, user):
        client = Client()
        client.force_login(user)
        return client

    def test_guard_finds_inline_code(self):
        self.assertEqual(csp_violations('<p style="x"><style>p {}</style><script>go()</script>'
                                        '<a href="#" onclick="go()"><a href="javascript:go()">'),
                         ['style attribute', 'style block', 'inline script', 'event handler',
                          'javascript url'])
        self.assertEqual(csp_violations('<script src="/static/a.js" defer></script>'
                                        '<p>style="x" onclick="x" as text</p>'
                                        '<svg font-size="21"></svg>'), [])

    def test_public_pages_never_error(self):
        dept = {'dept': self.dept.label}
        urls = [reverse('labcirs_home'), reverse('departments_list'),
                reverse('create_incident', kwargs=dept), reverse('success', kwargs=dept),
                reverse('incident_search', kwargs=dept),
                reverse('incidents_for_department', kwargs=dept), reverse('login'),
                reverse('password_reset'), reverse('password_reset_done'),
                reverse('password_reset_confirm', kwargs={'uidb64': 'MQ', 'token': 'x-y'}),
                reverse('password_reset_complete')]
        client = Client()
        for url in urls:
            self.fetch(client, 'anonymous', url, follow=True)
        reviewer = self.client_for(self.reviewer.user)
        for url in (reverse('labcirs_home'), self.dept.get_absolute_url(),
                    self.published.get_absolute_url(), self.commented.get_absolute_url()):
            self.fetch(reviewer, 'QM', url, follow=True)
        self.fetch(self.client_for(self.superuser), 'superuser', reverse('labcirs_home'),
                   follow=True)
        self.assertNoFailures()

    def crawl_admin(self, client, role):
        """Returns the admin URLs fetched for role."""
        index = self.fetch(client, role, reverse('admin:index'))
        self.assertIsNotNone(index, self.failures)
        links = dict.fromkeys(url for url in ADMIN_LINK.findall(index.content.decode())
                              if 'logout' not in url and 'password' not in url)
        visited = [reverse('admin:index')]
        for url in links:
            response = self.fetch(client, role, url, allowed=ADMIN_STATUSES)
            visited.append(url)
            if not CHANGELIST.match(url):
                continue
            pages = [url + 'add/']
            if response is not None and response.status_code == 200:
                # the first object on the list: its change, history and delete page
                obj = re.search(r'href="(%s\d+/)change/' % re.escape(url),
                                response.content.decode())
                if obj:
                    pages += [obj.group(1) + page for page in ('change/', 'history/', 'delete/')]
            for page in pages:
                if page in links:
                    continue
                self.fetch(client, role, page, allowed=ADMIN_STATUSES)
                visited.append(page)
        return visited

    def test_admin_pages_never_error_for_each_role(self):
        visited = {role: self.crawl_admin(self.client_for(user), role)
                   for role, user in (('QM', self.reviewer.user),
                                      ('superuser', self.superuser))}
        self.assertNoFailures()
        # Floors near the real counts (QM 28, superuser 36), so that a crawl which
        # loses whole models or their object pages fails instead of passing on the list pages.
        self.assertGreaterEqual(len(visited['QM']), 25)
        self.assertGreaterEqual(len(visited['superuser']), 30)
        objects = {role: [url for url in urls if OBJECT_PAGE.match(url)]
                   for role, urls in visited.items()}
        self.assertGreaterEqual(len(objects['QM']), 12, visited['QM'])
        self.assertGreaterEqual(len(objects['superuser']), 15, visited['superuser'])
        # The fixture objects are in each role's lists (the superuser sees no incidents, qs.none()).
        self.assertIn(reverse('admin:cirs_publishableincident_change',
                              args=[self.publishable.pk]), visited['QM'])
        self.assertIn(reverse('admin:cirs_orgunit_change', args=[self.org_unit.pk]),
                      visited['superuser'])

    def test_qm_pages_never_error(self):
        names = ('qm_overview', 'qm_incidents', 'qm_reports', 'qm_reports_print', 'qm_reports_csv')
        urls = [reverse(name) for name in names]
        qm = self.client_for(self.reviewer.user)
        # the overview leads to the pages of the navigation: follow what it links to
        overview = self.fetch(qm, 'QM', urls[0])
        linked = dict.fromkeys(QM_LINK.findall(overview.content.decode()))
        self.assertTrue({urls[0], urls[1], urls[2]} <= set(linked), linked)
        for url in dict.fromkeys(urls + list(linked)):
            self.fetch(qm, 'QM', url)
        for url in urls:
            self.fetch(Client(), 'anonymous', url, allowed=(302,))
            self.fetch(self.client_for(self.superuser), 'superuser', url, allowed=(403,))
            self.fetch(self.client_for(self.dept.reporter.user), 'reporter', url, allowed=(403,))
        self.assertNoFailures()

    def test_config_add_page_is_forbidden(self):
        client = Client(raise_request_exception=False)
        client.force_login(self.superuser)
        self.assertEqual(client.get(CONFIG_ADD).status_code, 403)

    def test_reporter_tracking_pages_never_error(self):
        client = Client()
        session = client.session
        session['accessible_incident'] = self.commented.pk
        session.save()
        self.fetch(client, 'reporter', self.commented.get_absolute_url())
        # POST only: a GET answers 405, not a server error
        self.fetch(client, 'reporter',
                   reverse('remove_reporter_email',
                           kwargs={'dept': self.dept.label, 'pk': self.commented.pk}),
                   allowed=(405,))
        response = self.fetch(client, 'reporter',
                              reverse('end_incident_access',
                                      kwargs={'dept': self.dept.label, 'pk': self.commented.pk}),
                              method='post', follow=True)
        self.assertNoFailures()
        self.assertEqual(response.redirect_chain[-1][0],
                         reverse('incident_search', kwargs={'dept': self.dept.label}))
