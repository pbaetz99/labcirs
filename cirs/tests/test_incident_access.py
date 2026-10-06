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

"""Who gets to the page of an incident, and what whoever does not get there can learn from it.

Nobody who may not see an incident gets a different answer whether the number exists or not:
somebody who counts through the numbers must not learn how many reports there are or how fast they
come in. Only a label that is no department at all is a 404, labels are public.
"""

from django.test import Client, TestCase
from django.urls import reverse
from model_bakery import baker

from cirs.models import Comment, CriticalIncident

from .helpers import create_user

MISSING_PK = 987654


class AccessCase(TestCase):
    """Two departments with a reviewer each, and the incidents of the first one."""

    @classmethod
    def setUpTestData(cls):
        cls.dept = baker.make_recipe('cirs.department', label='eins')
        cls.other_dept = baker.make_recipe('cirs.department', label='zwei')
        cls.reviewer = baker.make_recipe('cirs.reviewer')
        cls.dept.reviewers.add(cls.reviewer)
        cls.outsider = baker.make_recipe('cirs.reviewer')
        cls.other_dept.reviewers.add(cls.outsider)
        cls.incident = baker.make_recipe('cirs.public_ci', department=cls.dept)
        cls.second = baker.make_recipe('cirs.public_ci', department=cls.dept)

    def url(self, pk, label=None):
        return reverse('incident_detail', kwargs={'dept': label or self.dept.label, 'pk': pk})

    def visitors(self):
        """The visitors who may not see self.incident, each with a client of their own."""
        anonymous = Client()
        with_code = Client()  # a real code search, as the reporter of another report does it
        response = with_code.post(reverse('incident_search', kwargs={'dept': self.dept.label}),
                                  {'incident_code': self.second.comment_code})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(with_code.session['accessible_incident'], self.second.pk)
        old_session = Client()  # the reporter account of an old session
        old_session.force_login(self.dept.reporter.user)
        no_role = Client()
        no_role.force_login(create_user('norole'))
        outsider = Client()
        outsider.force_login(self.outsider.user)
        return {'anonymous': anonymous, 'reporter with the code of another report': with_code,
                'reporter account': old_session, 'account without a role': no_role,
                'reviewer of another department': outsider}

    def answer(self, client, method, pk, label=None):
        data = {'text': 'Synthetic reply'} if method == 'post' else {}
        response = getattr(client, method)(self.url(pk, label), data)
        return response.status_code, response.get('Location')


class SameAnswerTest(AccessCase):

    def test_an_existing_and_a_missing_number_get_the_same_answer(self):
        for who, client in self.visitors().items():
            for method in ('get', 'post'):
                existing = self.answer(client, method, self.incident.pk)
                missing = self.answer(client, method, MISSING_PK)
                self.assertEqual(existing[0], 302, (who, method))
                self.assertEqual(existing, missing, (who, method))

    def test_a_number_under_the_label_of_another_department_is_like_a_missing_one(self):
        # the incident exists, but not under this label: nothing is there for this address
        for who, client in self.visitors().items():
            for method in ('get', 'post'):
                wrong = self.answer(client, method, self.incident.pk, self.other_dept.label)
                missing = self.answer(client, method, MISSING_PK, self.other_dept.label)
                self.assertEqual(wrong, missing, (who, method))

    def test_the_answer_leads_to_the_code_page_of_the_label_of_the_address(self):
        search = reverse('incident_search', kwargs={'dept': self.dept.label})
        other_search = reverse('incident_search', kwargs={'dept': self.other_dept.label})
        for who, client in self.visitors().items():
            if who == 'reviewer of another department':
                continue
            for pk, label, target in ((self.incident.pk, None, search), (MISSING_PK, None, search),
                                      (self.incident.pk, self.other_dept.label, other_search),
                                      (MISSING_PK, self.other_dept.label, other_search)):
                for method in ('get', 'post'):
                    self.assertEqual(self.answer(client, method, pk, label), (302, target),
                                     (who, method, pk, label))

    def test_a_reviewer_without_access_goes_to_the_start_page_in_every_case(self):
        client = self.visitors()['reviewer of another department']
        home = reverse('labcirs_home')
        for pk, label in ((self.incident.pk, None), (MISSING_PK, None),
                          (self.incident.pk, self.other_dept.label), (MISSING_PK, self.other_dept.label)):
            for method in ('get', 'post'):
                self.assertEqual(self.answer(client, method, pk, label), (302, home),
                                 (method, pk, label))

    def test_the_reviewer_of_the_department_goes_to_the_start_page_for_a_number_that_is_not_there(self):
        client = Client()
        client.force_login(self.reviewer.user)
        home = reverse('labcirs_home')
        for method in ('get', 'post'):
            self.assertEqual(self.answer(client, method, MISSING_PK), (302, home), method)
            # the answer for a report of the other department is the same
            foreign = baker.make_recipe('cirs.public_ci', department=self.other_dept)
            self.assertEqual(self.answer(client, method, foreign.pk, self.other_dept.label),
                             (302, home), method)
        self.assertEqual(client.get(self.url(self.incident.pk)).status_code, 200)

    def test_a_superuser_goes_to_the_admin_in_every_case(self):
        client = Client()
        client.force_login(create_user('boss', superuser=True))
        admin = reverse('admin:index')
        for pk, label in ((self.incident.pk, None), (MISSING_PK, None),
                          (self.incident.pk, self.other_dept.label)):
            for method in ('get', 'post'):
                self.assertEqual(self.answer(client, method, pk, label), (302, admin),
                                 (method, pk, label))

    def test_a_label_that_is_no_department_is_a_404_for_everybody(self):
        clients = dict(self.visitors())
        admin = Client()
        admin.force_login(create_user('boss', superuser=True))
        member = Client()
        member.force_login(self.reviewer.user)
        clients.update({'superuser': admin, 'reviewer': member})
        for who, client in clients.items():
            for method in ('get', 'post'):
                for pk in (self.incident.pk, MISSING_PK):
                    self.assertEqual(self.answer(client, method, pk, 'nobody')[0], 404,
                                     (who, method, pk))

    def test_an_inactive_department_is_still_a_department_for_the_reviewer(self):
        # work on old reports goes on when the department takes no new ones
        self.dept.active = False
        self.dept.save()
        client = Client()
        client.force_login(self.reviewer.user)
        self.assertEqual(client.get(self.url(self.incident.pk)).status_code, 200)
        # the label is public: the answer for the others does not depend on whether it is active
        anonymous = Client()
        search = reverse('incident_search', kwargs={'dept': self.dept.label})
        self.assertEqual(self.answer(anonymous, 'get', self.incident.pk), (302, search))
        self.assertEqual(self.answer(anonymous, 'get', MISSING_PK), (302, search))

    def test_nothing_is_changed_by_the_posts_that_were_turned_away(self):
        before = (Comment.objects.count(), CriticalIncident.objects.get(pk=self.incident.pk).status)
        for client in self.visitors().values():
            for pk in (self.incident.pk, MISSING_PK):
                self.answer(client, 'post', pk)
        after = (Comment.objects.count(), CriticalIncident.objects.get(pk=self.incident.pk).status)
        self.assertEqual(before, after)


class WhoGetsInTest(AccessCase):

    def test_the_reporter_with_the_code_of_this_report_gets_in(self):
        client = Client()
        client.post(reverse('incident_search', kwargs={'dept': self.dept.label}),
                    {'incident_code': self.incident.comment_code})
        self.assertEqual(client.get(self.url(self.incident.pk)).status_code, 200)
        self.assertEqual(client.get(self.url(self.second.pk)).status_code, 302)

    def test_the_reviewer_of_the_department_gets_in(self):
        client = Client()
        client.force_login(self.reviewer.user)
        for incident in (self.incident, self.second):
            self.assertEqual(client.get(self.url(incident.pk)).status_code, 200)
