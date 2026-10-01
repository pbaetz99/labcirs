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

from django.test import TestCase
from django.urls import reverse
from model_bakery import baker

from cirs.models import Comment, CriticalIncident

from .helpers import code_markup, create_user

VALID = {
    'date': '2026-09-01',
    'incident': 'Synthetic incident',
    'reason': 'Synthetic reason',
    'immediate_action': 'Synthetic action',
    'preventability': 'avoidable',
    'public': 'True',
}


class AnonymousCreateTest(TestCase):

    def setUp(self):
        self.dept = baker.make_recipe('cirs.department')

    def test_anonymous_create_stores_incident_for_url_department(self):
        response = self.client.post(reverse('create_incident', kwargs={'dept': self.dept.label}), VALID)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(CriticalIncident.objects.get().department, self.dept)

    def test_unknown_or_inactive_department_returns_404(self):
        inactive = baker.make_recipe('cirs.department', active=False)
        for label in ('unknown', inactive.label):
            for name in ('create_incident', 'incident_search', 'incidents_for_department'):
                url = reverse(name, kwargs={'dept': label})
                self.assertEqual(self.client.get(url).status_code, 404, url)
            url = reverse('create_incident', kwargs={'dept': label})
            self.assertEqual(self.client.post(url, VALID).status_code, 404, url)
        self.assertEqual(CriticalIncident.objects.count(), 0)

    def test_create_does_not_grant_detail_access(self):
        # Shared ward PCs: the next person must not see the report.
        response = self.client.post(reverse('create_incident', kwargs={'dept': self.dept.label}),
                                    VALID, follow=True)
        ci = CriticalIncident.objects.get()
        self.assertContains(response, code_markup(ci.comment_code))
        self.assertNotIn('accessible_incident', self.client.session)
        response = self.client.get(ci.get_absolute_url())
        self.assertRedirects(response, reverse('incident_search', kwargs={'dept': self.dept.label}),
                             fetch_redirect_response=False)

    def test_reviewer_cannot_create(self):
        reviewer = baker.make_recipe('cirs.reviewer')
        self.dept.reviewers.add(reviewer)
        self.client.force_login(reviewer.user)
        response = self.client.post(reverse('create_incident', kwargs={'dept': self.dept.label}), VALID)
        self.assertRedirects(response, reverse('labcirs_home'), fetch_redirect_response=False)
        self.assertEqual(CriticalIncident.objects.count(), 0)


class AnonymousAccessTest(TestCase):

    def setUp(self):
        self.dept = baker.make_recipe('cirs.department')
        self.ci = baker.make_recipe('cirs.public_ci', department=self.dept)
        self.search_url = reverse('incident_search', kwargs={'dept': self.dept.label})

    def grant_access(self, pk):
        session = self.client.session
        session['accessible_incident'] = pk
        session.save()

    def make_reviewer(self, dept):
        reviewer = baker.make_recipe('cirs.reviewer')
        dept.reviewers.add(reviewer)
        return reviewer

    def test_code_search_grants_access(self):
        response = self.client.post(self.search_url, {'incident_code': self.ci.comment_code})
        self.assertEqual(self.client.session['accessible_incident'], self.ci.pk)
        # fetching the target proves the anonymous session survives the next request
        self.assertRedirects(response, self.ci.get_absolute_url())

    def test_code_search_cycles_session_key(self):
        # Shared ward PCs: a fresh session key on granting access (session fixation).
        self.grant_access(baker.make_recipe('cirs.public_ci', department=self.dept).pk)
        old_key = self.client.session.session_key
        self.client.post(self.search_url, {'incident_code': self.ci.comment_code})
        self.assertNotEqual(self.client.session.session_key, old_key)
        self.assertEqual(self.client.session['accessible_incident'], self.ci.pk)

    def test_failed_search_keeps_session_key(self):
        self.grant_access(self.ci.pk)
        old_key = self.client.session.session_key
        self.client.post(self.search_url, {'incident_code': 'nosuchcodeatall2'})
        self.assertEqual(self.client.session.session_key, old_key)

    def test_end_access_requires_post(self):
        self.grant_access(self.ci.pk)
        response = self.client.get(reverse('end_incident_access',
                                           kwargs={'dept': self.dept.label, 'pk': self.ci.pk}))
        self.assertEqual(response.status_code, 405)
        self.assertEqual(self.client.session['accessible_incident'], self.ci.pk)

    def test_end_access_removes_session_key_and_redirects_to_search(self):
        self.grant_access(self.ci.pk)
        url = reverse('end_incident_access', kwargs={'dept': self.dept.label, 'pk': self.ci.pk})
        response = self.client.post(url)
        self.assertRedirects(response, self.search_url, fetch_redirect_response=False)
        self.assertNotIn('accessible_incident', self.client.session)
        response = self.client.get(self.ci.get_absolute_url())
        self.assertRedirects(response, self.search_url, fetch_redirect_response=False)

    def test_end_access_message_is_shown_on_search_page(self):
        self.grant_access(self.ci.pk)
        url = reverse('end_incident_access', kwargs={'dept': self.dept.label, 'pk': self.ci.pk})
        response = self.client.post(url, follow=True, HTTP_ACCEPT_LANGUAGE='de')
        self.assertContains(response, 'Der Zugang zu dieser Meldung ist beendet.')

    def test_end_access_without_session_is_harmless(self):
        url = reverse('end_incident_access', kwargs={'dept': self.dept.label, 'pk': self.ci.pk})
        self.assertRedirects(self.client.post(url), self.search_url, fetch_redirect_response=False)

    def test_detail_offers_end_access_to_reporters_only(self):
        url = reverse('end_incident_access', kwargs={'dept': self.dept.label, 'pk': self.ci.pk})
        self.grant_access(self.ci.pk)
        response = self.client.get(self.ci.get_absolute_url())
        self.assertContains(response, 'action="{}"'.format(url))
        self.assertContains(response, 'End access before you leave a shared computer.')
        response = self.client.get(self.ci.get_absolute_url(), HTTP_ACCEPT_LANGUAGE='de')
        self.assertContains(response, 'Zugang beenden')
        self.assertContains(response, 'Beenden Sie den Zugang, bevor Sie einen gemeinsam '
                                      'genutzten PC verlassen.')
        self.client.force_login(self.make_reviewer(self.dept).user)
        self.assertNotContains(self.client.get(self.ci.get_absolute_url()), url)

    def test_detail_without_code_redirects_to_search(self):
        response = self.client.get(self.ci.get_absolute_url())
        self.assertRedirects(response, self.search_url, fetch_redirect_response=False)

    def test_detail_with_other_pk_in_session_redirects_to_search(self):
        other = baker.make_recipe('cirs.public_ci', department=self.dept)
        self.grant_access(other.pk)
        response = self.client.get(self.ci.get_absolute_url())
        self.assertRedirects(response, self.search_url, fetch_redirect_response=False)

    def test_detail_under_wrong_department_label_is_404(self):
        other_dept = baker.make_recipe('cirs.department')
        self.grant_access(self.ci.pk)
        url = reverse('incident_detail', kwargs={'dept': other_dept.label, 'pk': self.ci.pk})
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, {'text': 'x'}).status_code, 404)

    def test_comment_post_without_access_is_rejected(self):
        before = Comment.objects.count()
        response = self.client.post(self.ci.get_absolute_url(), {'text': 'x'})
        self.assertRedirects(response, reverse('incident_search', kwargs={'dept': self.dept.label}),
                             fetch_redirect_response=False)
        self.assertEqual(Comment.objects.count(), before)

    def test_comment_post_with_other_pk_in_session_is_rejected(self):
        other = baker.make_recipe('cirs.public_ci', department=self.dept)
        self.grant_access(other.pk)
        response = self.client.post(self.ci.get_absolute_url(), {'text': 'x'})
        self.assertRedirects(response, self.search_url, fetch_redirect_response=False)
        self.assertEqual(Comment.objects.count(), 0)

    def test_anonymous_comment_author_is_department_reporter_user(self):
        self.grant_access(self.ci.pk)
        response = self.client.post(self.ci.get_absolute_url(), {'text': 'Synthetic comment'})
        self.assertRedirects(response, self.ci.get_absolute_url())
        comment = Comment.objects.get()
        self.assertEqual(comment.author, self.dept.reporter.user)
        self.assertEqual(comment.critical_incident, self.ci)

    def test_reviewer_comment_author_is_reviewer(self):
        reviewer = self.make_reviewer(self.dept)
        self.client.force_login(reviewer.user)
        self.assertEqual(self.client.get(self.ci.get_absolute_url()).status_code, 200)
        self.client.post(self.ci.get_absolute_url(), {'text': 'Synthetic reply'})
        self.assertEqual(Comment.objects.get().author, reviewer.user)

    def test_reviewer_of_other_department_is_redirected_home(self):
        reviewer = self.make_reviewer(baker.make_recipe('cirs.department'))
        self.client.force_login(reviewer.user)
        self.grant_access(self.ci.pk)  # a code in the session does not help a foreign reviewer
        for response in (self.client.get(self.ci.get_absolute_url()),
                         self.client.post(self.ci.get_absolute_url(), {'text': 'x'})):
            self.assertRedirects(response, reverse('labcirs_home'), fetch_redirect_response=False)
        self.assertEqual(Comment.objects.count(), 0)

    def test_superuser_comment_post_goes_to_admin(self):
        self.client.force_login(create_user('admin', superuser=True))
        response = self.client.post(self.ci.get_absolute_url(), {'text': 'x'})
        self.assertRedirects(response, reverse('admin:index'), fetch_redirect_response=False)
        self.assertEqual(Comment.objects.count(), 0)

    def test_old_reporter_session_needs_code_and_is_logged_out(self):
        self.client.force_login(self.dept.reporter.user)
        response = self.client.post(self.ci.get_absolute_url(), {'text': 'x'})
        self.assertRedirects(response, self.search_url, fetch_redirect_response=False)
        self.assertEqual(Comment.objects.count(), 0)
        self.client.get(self.search_url)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_published_list_visible_without_login(self):
        pi = baker.make_recipe('cirs.published_incident', critical_incident=self.ci)
        foreign = baker.make_recipe('cirs.published_incident',
                                    critical_incident__department=baker.make_recipe('cirs.department'))
        unpublished = baker.make_recipe('cirs.published_incident', publish=False,
                                        critical_incident__department=self.dept)
        response = self.client.get(self.dept.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['object_list']), [pi])
        self.assertNotIn(foreign, response.context['object_list'])
        self.assertNotIn(unpublished, response.context['object_list'])

    def test_anonymous_sees_navigation_with_add_incident(self):
        response = self.client.get(self.dept.get_absolute_url())
        self.assertContains(response, reverse('create_incident', kwargs={'dept': self.dept.label}))
        self.assertContains(response, self.search_url)
        self.assertContains(response, 'Add new incident')

    def test_reviewer_sees_no_add_incident(self):
        self.client.force_login(self.make_reviewer(self.dept).user)
        response = self.client.get(self.dept.get_absolute_url())
        self.assertNotContains(response, reverse('create_incident', kwargs={'dept': self.dept.label}))

    def test_login_ignores_next_to_other_host(self):
        create_user('admin', superuser=True)
        for target, expected in (('https://evil.example/', reverse('labcirs_home')),
                                 ('//evil.example/', reverse('labcirs_home')),
                                 ('/\\evil.example/', reverse('labcirs_home')),
                                 (reverse('admin:index'), reverse('admin:index'))):
            response = self.client.post(reverse('login') + '?next=' + target,
                                        {'username': 'admin', 'password': 'admin'})
            self.assertRedirects(response, expected, fetch_redirect_response=False)

    def test_reporter_account_cannot_log_in(self):
        rep_name = self.dept.reporter.user.username
        self.dept.reporter.user.set_password(rep_name)
        self.dept.reporter.user.save()
        response = self.client.post(reverse('login'), {'username': rep_name, 'password': rep_name})
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertContains(response, 'without logging in')

    def test_reporter_login_hint_is_translated(self):
        rep_name = self.dept.reporter.user.username
        self.dept.reporter.user.set_password(rep_name)
        self.dept.reporter.user.save()
        response = self.client.post(reverse('login'), {'username': rep_name, 'password': rep_name},
                                    HTTP_ACCEPT_LANGUAGE='de')
        self.assertContains(response, 'Melden funktioniert ohne Anmeldung.')
