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

import os
import re
import runpy
from unittest import mock

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from model_bakery import baker

from cirs.models import CriticalIncident, PublishableIncident
from labcirs.settings import base

from .test_anonymous import VALID

WITHOUT_PUBLIC = {key: value for key, value in VALID.items() if key != 'public'}


class ConsentSettingTest(SimpleTestCase):

    def load(self, **env):
        # base.py is run again, the tests keep the settings they were started with.
        current = {k: v for k, v in os.environ.items() if k != 'LABCIRS_ASK_PUBLICATION_CONSENT'}
        with mock.patch.dict(os.environ, dict(current, **env), clear=True):
            return runpy.run_path(base.__file__)['ASK_PUBLICATION_CONSENT']

    def test_default_asks_for_consent(self):
        self.assertIs(self.load(), True)
        self.assertIs(self.load(LABCIRS_ASK_PUBLICATION_CONSENT=''), True)

    def test_false_switches_the_question_off(self):
        self.assertIs(self.load(LABCIRS_ASK_PUBLICATION_CONSENT='false'), False)


class ConsentFormTest(TestCase):

    def setUp(self):
        self.dept = baker.make_recipe('cirs.department')
        self.url = reverse('create_incident', kwargs={'dept': self.dept.label})

    def test_consent_shown_and_required_by_default(self):
        self.assertContains(self.client.get(self.url), 'name="public"')
        response = self.client.post(self.url, WITHOUT_PUBLIC, HTTP_ACCEPT_LANGUAGE='en')
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context['form'], 'public', 'This field is required.')
        self.assertEqual(CriticalIncident.objects.count(), 0)

    def test_declined_consent_is_stored(self):
        self.client.post(self.url, dict(VALID, public='False'))
        self.assertIs(CriticalIncident.objects.get().public, False)

    @override_settings(ASK_PUBLICATION_CONSENT=False)
    def test_consent_off_hides_field_and_publishes(self):
        self.assertNotContains(self.client.get(self.url), 'name="public"')
        response = self.client.post(self.url, WITHOUT_PUBLIC)
        self.assertEqual(response.status_code, 302)
        self.assertIs(CriticalIncident.objects.get().public, True)

    @override_settings(ASK_PUBLICATION_CONSENT=False)
    def test_consent_off_ignores_a_posted_value(self):
        self.client.post(self.url, dict(VALID, public='False'))
        self.assertIs(CriticalIncident.objects.get().public, True)

    def test_no_hidden_empty_public_input(self):
        for language in ('en', 'de'):
            html = self.client.get(self.url, HTTP_ACCEPT_LANGUAGE=language).content.decode()
            self.assertNotIn('value="Empty"', html)
            self.assertNotIn('value="Leer"', html)
            inputs = re.findall(r'<input[^>]*name="public"[^>]*>', html)
            self.assertEqual(len(inputs), 2, language)  # the two answers
            for tag in inputs:
                self.assertIn('type="radio"', tag)


class PublishableIncidentConsentTest(TestCase):

    def test_publishable_incident_blocked_for_public_false(self):
        ci = baker.make_recipe('cirs.public_ci', public=False)
        with self.assertRaises(ValidationError):
            PublishableIncident(critical_incident=ci).clean()
