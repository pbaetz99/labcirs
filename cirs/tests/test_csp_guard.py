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

"""The CSP guard (cirs.tests.helpers.csp_violations) and the public pages measured against it.

The page crawl runs the guard over every page a role can reach. This file tests the guard itself
and names the public pages, so that a page that gains an inline script, an inline
style or a foreign URL fails here with its address.
"""

import re

from django.conf import settings
from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse
from model_bakery import baker

from .helpers import csp_violations
from .test_pages_list import make_case

STATIC_REFERENCE = re.compile(r'<(?:script|img|link)\b[^>]*\s(?:src|href)="(%s[^"?#]+)' %
                              re.escape(settings.STATIC_URL))


class GuardTest(TestCase):

    def test_finds_foreign_hosts_for_script_img_and_link(self):
        for html in ('<script src="https://cdn.example.org/x.js" defer></script>',
                     '<img alt="" src="//cdn.example.org/a.png">',
                     '<link rel="stylesheet" href="http://fonts.example.org/c.css">',
                     '<link href=\'https://fonts.example.org/c.css\' rel="stylesheet">',
                     '<SCRIPT SRC="HTTPS://cdn.example.org/x.js"></SCRIPT>'):
            with self.subTest(html=html):
                self.assertEqual(csp_violations(html), ['external url'])

    def test_allows_own_and_inline_data_urls_and_links_to_other_sites(self):
        self.assertEqual(csp_violations(
            '<script src="/static/js/formular.js" defer></script>'
            '<img alt="" src="/media/photos/a.jpg"><img alt="" src="data:image/png;base64,AAAA">'
            '<link rel="stylesheet" href="static/css/core.css">'
            '<a href="https://example.org/imprint">Imprint</a>'
            '<form action="https://example.org/x"></form>'), [])

    def test_finds_style_attribute_and_block_inline_script_handler_and_javascript_url(self):
        self.assertEqual(
            csp_violations('<p style="color: red"><style>p {}</style><script>go()</script>'
                           '<a href="#" onclick="go()"><a href="javascript:go()">'),
            ['style attribute', 'style block', 'inline script', 'event handler',
             'javascript url'])

    def test_ignores_text_about_attributes(self):
        self.assertEqual(csp_violations('<p>style="x" onclick="x" https://a.example/x.js</p>'),
                         [])


class PublicPagesTest(TestCase):

    def setUp(self):
        self.dept = baker.make_recipe('cirs.department')
        self.other = baker.make_recipe('cirs.department')  # the department list renders
        make_case(self.dept, 'Synthetic case', photo='photos/2026/09/30/synthetic.jpg')
        self.incident = baker.make_recipe('cirs.public_ci', department=self.dept)
        session = self.client.session
        session['accessible_incident'] = self.incident.pk
        session.save()

    def public_urls(self):
        dept = {'dept': self.dept.label}
        return [reverse('departments_list'), self.dept.get_absolute_url(),
                self.dept.get_absolute_url() + '?q=synthetic',
                reverse('create_incident', kwargs=dept), reverse('success', kwargs=dept),
                reverse('incident_search', kwargs=dept), self.incident.get_absolute_url(),
                reverse('login'), reverse('password_reset'), reverse('password_reset_done'),
                reverse('password_reset_confirm', kwargs={'uidb64': 'MQ', 'token': 'x-y'}),
                reverse('password_reset_complete')]

    def pages(self):
        for url in self.public_urls():
            response = self.client.get(url, HTTP_ACCEPT_LANGUAGE='de')
            self.assertEqual(response.status_code, 200, url)
            yield url, response.content.decode()

    def test_public_pages_have_no_csp_violations(self):
        for url, html in self.pages():
            self.assertEqual(csp_violations(html), [], url)

    def test_public_pages_reference_only_static_files_that_exist(self):
        # The production storage refuses a file that does not exist (manifest); a template that
        # still links a removed library would break every page there.
        for url, html in self.pages():
            for path in STATIC_REFERENCE.findall(html):
                name = path[len(settings.STATIC_URL):]
                self.assertTrue(finders.find(name), '%s links the missing static file %s' % (url, name))
