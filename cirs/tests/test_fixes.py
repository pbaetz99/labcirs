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

import smtplib
from datetime import date
from unittest import mock

from django.contrib.auth.models import User
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import translation
from model_bakery import baker
from parameterized import parameterized

from cirs.admin import LabCIRSUserAdmin, admin_site
from cirs.models import (Comment, CriticalIncident, Department, LabCIRSConfig,
                         Reporter)

from .helpers import code_markup, create_user
from .test_anonymous import VALID


def grant_access(client, incident):
    session = client.session
    session['accessible_incident'] = incident.pk
    session.save()


class NotificationMailTest(TestCase):
    """Notifications are a courtesy: a mail error must not cost the reporter the code."""

    def setUp(self):
        self.dept = baker.make_recipe('cirs.department')
        self.create_url = reverse('create_incident', kwargs={'dept': self.dept.label})
        config = self.dept.labcirsconfig
        config.send_notification = True
        config.notification_sender_email = 'labcirs@localhost'
        config.save()
        config.notification_recipients.add(create_user('recipient'))

    @parameterized.expand([('smtp', smtplib.SMTPException('down')),
                           ('connection', ConnectionRefusedError('refused'))])
    def test_mail_failure_keeps_incident_and_shows_code(self, name, error):
        with mock.patch('cirs.forms.mail.send_mail', side_effect=error) as send:
            with self.assertLogs('cirs', level='ERROR') as logged:
                response = self.client.post(self.create_url, VALID, follow=True)
        send.assert_called_once()
        code = CriticalIncident.objects.get().comment_code
        self.assertContains(response, code_markup(code))
        # The log carries neither the code nor the report, reporters are anonymous.
        log = '\n'.join(logged.output)
        self.assertNotIn(code, log)
        self.assertNotIn(VALID['incident'], log)

    def test_mail_failure_on_comment_keeps_comment(self):
        ci = baker.make_recipe('cirs.public_ci', department=self.dept)
        grant_access(self.client, ci)
        with mock.patch('cirs.forms.mail.send_mail',
                        side_effect=smtplib.SMTPException('down')) as send:
            with self.assertLogs('cirs', level='ERROR') as logged:
                response = self.client.post(ci.get_absolute_url(), {'text': 'Synthetic comment'})
        send.assert_called_once()
        self.assertRedirects(response, ci.get_absolute_url())
        self.assertEqual(Comment.objects.get().text, 'Synthetic comment')
        log = '\n'.join(logged.output)
        for secret in (ci.comment_code, ci.incident, 'Synthetic comment'):
            self.assertNotIn(secret, log)

    # The QM reads the mails, so they follow the site language, not the reporter's browser.
    @parameterized.expand([('de', 'en', 'Neues kritisches Ereignis', 'Neuer Kommentar im CIRS'),
                           ('en', 'de', 'New critical incident', 'New LabCIRS comment')])
    def test_notification_subjects_follow_the_site_language(self, site, browser, incident, comment):
        ci = baker.make_recipe('cirs.public_ci', department=self.dept)
        with override_settings(LANGUAGE_CODE=site):
            self.client.post(self.create_url, VALID, HTTP_ACCEPT_LANGUAGE=browser)
            grant_access(self.client, ci)
            self.client.post(ci.get_absolute_url(), {'text': 'Synthetic comment'},
                             HTTP_ACCEPT_LANGUAGE=browser)
        self.assertEqual([message.subject for message in mail.outbox], [incident, comment])


class ReviewStatusCheckTest(TestCase):
    REVIEW_DATA = [('action', 'x'), ('responsibilty', 'x'), ('review_date', date.today()),
                   ('risk', 'low'), ('frequency', 'seldom')]

    def setUp(self):
        self.ci = baker.make_recipe('cirs.public_ci', date=date.today())

    def test_clean_allows_new_status_with_empty_review(self):
        self.assertEqual(self.ci.status, 'new')
        self.ci.full_clean()

    @parameterized.expand(REVIEW_DATA)
    def test_clean_rejects_new_status_with_review_data(self, field, value):
        setattr(self.ci, field, value)
        with self.assertRaises(ValidationError) as caught:
            self.ci.clean()
        self.assertIn('status', caught.exception.message_dict)

    def test_clean_allows_review_data_with_later_status(self):
        self.ci.action = 'x'
        self.ci.status = 'in process'
        self.ci.full_clean()


class SuccessPageTest(TestCase):

    def test_success_hint_translated(self):
        dept = baker.make_recipe('cirs.department')
        url = reverse('create_incident', kwargs={'dept': dept.label})
        response = self.client.post(url, VALID, follow=True, HTTP_ACCEPT_LANGUAGE='de')
        self.assertContains(response, 'Bitte notieren Sie diesen Code. Sie brauchen ihn, um den '
                                      'Stand Ihrer Meldung zu verfolgen und Rückfragen zu '
                                      'beantworten.')
        self.assertContains(response, code_markup(CriticalIncident.objects.get().comment_code))
        response = self.client.post(url, VALID, follow=True, HTTP_ACCEPT_LANGUAGE='en')
        self.assertContains(response, 'Please note this code. You need it to follow the status of '
                                      'your report and to answer questions.')


class TranslatedDisplayTest(TestCase):

    def test_detail_shows_translated_preventability(self):
        ci = baker.make_recipe('cirs.public_ci', preventability='avoidable')
        grant_access(self.client, ci)
        response = self.client.get(ci.get_absolute_url(), HTTP_ACCEPT_LANGUAGE='de')
        self.assertContains(response, 'Das Ereignis war vermeidbar')
        response = self.client.get(ci.get_absolute_url(), HTTP_ACCEPT_LANGUAGE='en')
        self.assertContains(response, 'The incident was avoidable')

    def test_help_texts_translated(self):
        fields = (Department._meta.get_field('reporter'), Department._meta.get_field('active'),
                  LabCIRSConfig._meta.get_field('notification_text'))
        with translation.override('de'):
            self.assertEqual([str(field.help_text) for field in fields], [
                'Reporter, die einer anderen Abteilung zugeordnet sind, sind hier nicht '
                'aufgelistet!',
                'Ereignisse können nur gemeldet werden, wenn die Abteilung aktiv ist.',
                'Geben Sie die Nachricht ein, die an die Reviewer gesendet wird, wenn ein '
                'neues Ereignis gemeldet wird.'])


class UserAdminFieldsetTest(TestCase):

    def test_reviewer_fieldset_title_is_translated(self):
        reviewer = baker.make_recipe('cirs.reviewer')
        request = RequestFactory().get('/')
        request.user = reviewer.user
        title = LabCIRSUserAdmin(User, admin_site).get_fieldsets(request)[1][0]
        with translation.override('en'):
            self.assertEqual(str(title), 'Personal info')
        with translation.override('de'):
            self.assertNotEqual(str(title), 'Personal info')


class RoleCleanTest(TestCase):

    @parameterized.expand([('en', 'Superuser cannot become reporter'),
                           ('de', 'Superuser kann nicht reporter werden')])
    def test_superuser_message_is_translated(self, language, expected):
        user = create_user('root', superuser=True)
        with translation.override(language):
            with self.assertRaises(ValidationError) as caught:
                Reporter(user=user).clean()
            self.assertEqual(caught.exception.messages, [expected])


class EndAccessDepartmentTest(TestCase):

    def test_unknown_or_inactive_department_is_404_and_keeps_access(self):
        ci = baker.make_recipe('cirs.public_ci')
        grant_access(self.client, ci)
        inactive = baker.make_recipe('cirs.department', active=False)
        for label in ('unknown', inactive.label):
            url = reverse('end_incident_access', kwargs={'dept': label, 'pk': ci.pk})
            self.assertEqual(self.client.post(url).status_code, 404, label)
        self.assertEqual(self.client.session['accessible_incident'], ci.pk)


class ConfigurationAdminAddTest(TestCase):
    """The config of a department is created with the department, never by hand."""

    def setUp(self):
        self.client.force_login(create_user('admin', superuser=True))

    def test_add_page_is_forbidden(self):
        url = reverse('admin:cirs_labcirsconfig_add')
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {}).status_code, 403)

    def test_change_page_lists_only_reviewers_of_the_department(self):
        dept = baker.make_recipe('cirs.department')
        rev1, rev2 = baker.make_recipe('cirs.reviewer', _quantity=2)
        dept.reviewers.add(rev1)
        response = self.client.get(
            reverse('admin:cirs_labcirsconfig_change', args=(dept.labcirsconfig.pk,)))
        self.assertEqual(response.status_code, 200)
        queryset = response.context['adminform'].form.fields['notification_recipients'].queryset
        self.assertIn(rev1.user, queryset)
        self.assertNotIn(rev2.user, queryset)
