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

"""A reviewer chooses the incident of a publication only among the incidents of the own departments."""

from django.test import TestCase
from django.urls import reverse
from model_bakery import baker

from cirs.models import PublishableIncident


class PublicationChoiceTest(TestCase):

    def setUp(self):
        self.dept = baker.make_recipe('cirs.department')
        self.other_dept = baker.make_recipe('cirs.department')
        self.reviewer = baker.make_recipe('cirs.reviewer')
        self.dept.reviewers.add(self.reviewer)
        self.mine = baker.make_recipe('cirs.public_ci', department=self.dept, status='in process')
        self.foreign = baker.make_recipe('cirs.public_ci', department=self.other_dept,
                                         status='in process', incident='Canary incident title')
        self.client.force_login(self.reviewer.user)
        self.add_url = reverse('admin:cirs_publishableincident_add')

    def test_the_choice_holds_only_incidents_of_the_own_departments(self):
        response = self.client.get(self.add_url)
        self.assertEqual(response.status_code, 200)
        choices = response.context['adminform'].form.fields['critical_incident'].queryset
        self.assertEqual(list(choices), [self.mine])
        self.assertNotContains(response, 'Canary incident')

    def test_a_posted_number_of_a_foreign_incident_is_refused(self):
        response = self.client.post(self.add_url, {'critical_incident': self.foreign.pk,
                                                   'language_code': 'en', 'incident': 'Title',
                                                   'description': 'Text', 'measures': 'Text'})
        self.assertEqual(response.status_code, 200)  # the form comes back with its error
        self.assertTrue(response.context['adminform'].form.errors['critical_incident'])
        self.assertFalse(PublishableIncident.objects.filter(critical_incident=self.foreign).exists())

    def test_an_account_without_a_reviewer_role_has_no_choice(self):
        self.reviewer.delete()  # the user stays a staff member without a role
        self.client.force_login(self.reviewer.user)
        response = self.client.get(self.add_url)
        if response.status_code == 200:
            choices = response.context['adminform'].form.fields['critical_incident'].queryset
            self.assertEqual(list(choices), [])
        else:
            self.assertIn(response.status_code, (302, 403))

    def test_a_publication_without_an_incident_is_refused_without_an_error_page(self):
        response = self.client.post(self.add_url, {'language_code': 'en', 'incident': 'Title',
                                                   'description': 'Text', 'measures': 'Text'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['adminform'].form.errors['critical_incident'])
