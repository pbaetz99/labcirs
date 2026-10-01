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

"""A NUL byte in the path, the query or the form data is a bad request, not a server error.

PostgreSQL refuses text with a NUL byte ("text fields cannot contain NUL"), so a value that
reaches a query fails with a 500. One middleware answers 400 for every caller: the
published list, the login, the code search, the admin search and whatever comes later.
"""

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from model_bakery import baker

from .test_pages_list import make_case

BAD_REQUEST_HEADING = 'Anfrage nicht verständlich'  # the German 400 page


class NulByteTest(TestCase):

    def setUp(self):
        self.dept = baker.make_recipe('cirs.department')
        self.url = self.dept.get_absolute_url()
        # a server error must come back as a 500 response here, not as a raised exception
        self.client = Client(raise_request_exception=False)

    def assertBadRequest(self, response):
        self.assertEqual(response.status_code, 400)
        html = response.content.decode()
        self.assertIn(BAD_REQUEST_HEADING, html)  # the styled page, in the language of the visitor
        self.assertIn('Fehler 400', html)
        self.assertEqual(html.count('<h1'), 1)

    def test_nul_in_search_term_is_400(self):
        for query in ('?q=%00', '?q=a%00b', '?q=%00&page=1'):
            with self.subTest(query=query):
                self.assertBadRequest(self.client.get(self.url + query, HTTP_ACCEPT_LANGUAGE='de'))

    def test_nul_in_a_query_key_is_400(self):
        self.assertBadRequest(self.client.get(self.url + '?a%00b=1', HTTP_ACCEPT_LANGUAGE='de'))

    def test_nul_in_the_path_is_400(self):
        # the department label of the URL reaches a query as well
        for name in ('incidents_for_department', 'create_incident', 'incident_search'):
            with self.subTest(name=name):
                url = reverse(name, kwargs={'dept': 'a\x00b'})
                self.assertBadRequest(self.client.get(url, HTTP_ACCEPT_LANGUAGE='de'))

    def test_nul_in_login_form_is_400(self):
        for data in ({'username': 'a\x00b', 'password': 'x'},
                     {'username': 'a', 'password': 'x\x00y'},
                     {'username': 'a', 'password': 'x', 'next\x00': '/'}):
            with self.subTest(data=data):
                self.assertBadRequest(self.client.post(reverse('login'), data,
                                                       HTTP_ACCEPT_LANGUAGE='de'))

    def test_nul_in_urlencoded_form_is_400(self):
        response = self.client.post(reverse('login'), 'username=a%00b&password=x',
                                    content_type='application/x-www-form-urlencoded',
                                    HTTP_ACCEPT_LANGUAGE='de')
        self.assertBadRequest(response)

    def test_nul_in_admin_search_is_400(self):
        admin = User.objects.create_superuser('nul-admin', 'nul-admin@localhost', 'nul-admin')
        self.client.force_login(admin)
        response = self.client.get(reverse('admin:cirs_publishableincident_changelist') + '?q=%00',
                                   HTTP_ACCEPT_LANGUAGE='de')
        self.assertBadRequest(response)

    def test_the_400_page_follows_the_language_of_the_visitor(self):
        response = self.client.get(self.url + '?q=%00', HTTP_ACCEPT_LANGUAGE='en')
        self.assertEqual(response.status_code, 400)
        self.assertNotIn(BAD_REQUEST_HEADING, response.content.decode())
        self.assertIn('Error 400', response.content.decode())

    def test_normal_requests_are_not_touched(self):
        make_case(self.dept, 'Synthetic pump alarm')
        make_case(self.dept, 'Synthetic door handle')
        response = self.client.get(self.url, {'q': 'pump', 'x': 'ä ö ü ß “quote”'},
                                   HTTP_ACCEPT_LANGUAGE='de')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([case.incident for case in response.context['object_list']],
                         ['Synthetic pump alarm'])
        response = self.client.post(reverse('login'), {'username': 'nobody', 'password': 'x'},
                                    HTTP_ACCEPT_LANGUAGE='de')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'stimmen nicht')
