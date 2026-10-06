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

"""A search term can be a name. It must not be offered to the next person at the keyboard and it
must not be written to the access log of the proxy."""

import re
from pathlib import Path

from django.conf import settings
from django.test import Client, TestCase
from django.urls import reverse
from model_bakery import baker

NGINX_TEMPLATE = Path(settings.BASE_DIR) / 'deploy' / 'nginx.conf.template'


class SearchPrivacyTest(TestCase):

    def setUp(self):
        self.dept = baker.make_recipe('cirs.department')

    def search_input(self, client, url):
        response = client.get(url, {'q': 'x'})
        self.assertEqual(response.status_code, 200)
        match = re.search(r'<input[^>]*name="q"[^>]*>', response.content.decode())
        self.assertIsNotNone(match)
        return match.group(0)

    def test_the_search_field_of_the_published_cases_does_not_offer_earlier_searches(self):
        field = self.search_input(Client(), self.dept.get_absolute_url())
        self.assertIn('autocomplete="off"', field)

    def test_the_search_field_of_the_work_list_does_not_offer_earlier_searches(self):
        reviewer = baker.make_recipe('cirs.reviewer')
        self.dept.reviewers.add(reviewer)
        client = Client()
        client.force_login(reviewer.user)
        field = self.search_input(client, reverse('qm_incidents'))
        self.assertIn('autocomplete="off"', field)

    def test_the_proxy_log_has_no_query_string(self):
        log_format = re.search(r'^log_format anon (.+);$', NGINX_TEMPLATE.read_text(encoding='utf-8'),
                               re.M).group(1)
        self.assertIn('$request_method $uri', log_format)
        for variable in ('$request"', '$request_uri', '$args', '$query_string'):
            self.assertNotIn(variable, log_format)
