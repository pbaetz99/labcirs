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

"""Optional e-mail notifications for reporters.

The address is the only thing that ties a report to a person, so the tests care as much
about where it must not show up (QM pages, admin, logs, other mails) as about the mails.
"""

import logging
import re
import smtplib
from unittest import mock

from django.contrib import admin
from django.core import mail
from django.db import transaction
from django.contrib.staticfiles import finders
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from model_bakery import baker
from parameterized import parameterized

from cirs.admin import admin_site
from cirs.models import Comment, CriticalIncident, ReporterContact, group_code
from cirs.reporter_mail import notify_reporter

from .helpers import code_markup, create_user
from .test_anonymous import VALID

ADDRESS = 'melder@example.org'
CLOSING_DE = ('Antworten können Sie im Haus unter „Meine Meldung“ mit Ihrem Code. '
              'Antworten Sie nicht auf diese E-Mail.')
REMOVED_DE = ('Ihre E-Mail-Adresse wurde gelöscht. '
              'Sie erhalten zu dieser Meldung keine E-Mails mehr.')
CONFIRM_ERROR_DE = ('Bitte bestätigen Sie, dass Sie Ihre E-Mail-Adresse endgültig '
                    'löschen möchten.')
REVIEWER_HINT_DE = ('Ihre Antwort geht zusätzlich per E-Mail an die meldende Person, '
                    'wenn sie Benachrichtigungen eingerichtet hat.')


def reporter_mails():
    return [message for message in mail.outbox if message.to == [ADDRESS]]


def grant_access(client, incident):
    session = client.session
    session['accessible_incident'] = incident.pk
    session.save()


# The reporter's field is offered (and mails are sent from) a configured sender only.
@override_settings(DEFAULT_FROM_EMAIL='cirs@example.org')
class ReporterMailBase(TestCase):

    def save_committed(self, incident):
        """Saves incident and runs what waits for the commit (the test runs in a transaction)."""
        with self.captureOnCommitCallbacks(execute=True):
            incident.save()

    def setUp(self):
        self.dept = baker.make_recipe('cirs.department')
        self.create_url = reverse('create_incident', kwargs={'dept': self.dept.label})
        self.reviewer = baker.make_recipe('cirs.reviewer')
        self.dept.reviewers.add(self.reviewer)

    def incident(self, email=ADDRESS, **fields):
        ci = baker.make_recipe('cirs.public_ci', department=self.dept, **fields)
        if email:
            ReporterContact.objects.create(incident=ci, email=email)
        # as loaded by a view or the admin, not as created
        return CriticalIncident.objects.get(pk=ci.pk)

    def remove_url(self, ci):
        return reverse('remove_reporter_email',
                       kwargs={'dept': ci.department.label, 'pk': ci.pk})


class ReporterContactCreateTest(ReporterMailBase):

    @parameterized.expand([('missing', {}), ('empty', {'reporter_email': ''})])
    def test_without_email_no_contact_and_no_reporter_mail(self, name, extra):
        self.client.post(self.create_url, dict(VALID, **extra))
        self.assertEqual(CriticalIncident.objects.count(), 1)
        self.assertEqual(ReporterContact.objects.count(), 0)
        self.assertEqual(mail.outbox, [])

    def test_email_creates_contact_and_mails_code(self):
        self.client.post(self.create_url, dict(VALID, reporter_email=ADDRESS))
        ci = CriticalIncident.objects.get()
        self.assertEqual(ci.reporter_contact.email, ADDRESS)
        mails = reporter_mails()
        self.assertEqual(len(mails), 1)
        self.assertIn(group_code(ci.comment_code), mails[0].body)
        self.assertNotIn(ci.comment_code, mails[0].body)  # only in groups, as on the success page
        for text in (VALID['incident'], VALID['reason'], VALID['immediate_action']):
            self.assertNotIn(text, mails[0].body)
            self.assertNotIn(text, mails[0].subject)

    @override_settings(LANGUAGE_CODE='de')
    def test_received_mail_wording(self):
        # the browser asks for English, the reporter's mail follows the site language
        self.client.post(self.create_url, dict(VALID, reporter_email=ADDRESS),
                         HTTP_ACCEPT_LANGUAGE='en')
        code = CriticalIncident.objects.get().comment_code
        message, = reporter_mails()
        self.assertEqual(message.subject, 'Ihre Meldung ist eingegangen')
        self.assertEqual(message.body, (
            'Ihre Meldung ist eingegangen. Ihr Code lautet: %s. Mit diesem Code sehen Sie den '
            'Stand Ihrer Meldung und können Rückfragen beantworten. Bewahren Sie diese '
            'Nachricht sicher auf.\n\n%s' % (group_code(code), CLOSING_DE)))
        self.assertRegex(message.body, r'Ihr Code lautet: (\w{4} ){3}\w{4}\. Mit')

    @override_settings(LANGUAGE_CODE='de')
    def test_received_mail_shows_an_old_code_in_two_groups(self):
        ci = self.incident(comment_code='ab#d$f-9')
        notify_reporter(ci, 'received')
        message, = reporter_mails()
        self.assertIn('Ihr Code lautet: ab#d $f-9. Mit diesem Code', message.body)

    @parameterized.expand([('de', 'en', 'Ihre Meldung ist eingegangen'),
                           ('en', 'de', 'Your report has been received')])
    def test_mail_language_is_the_site_language(self, site, browser, subject):
        with override_settings(LANGUAGE_CODE=site):
            self.client.post(self.create_url, dict(VALID, reporter_email=ADDRESS),
                             HTTP_ACCEPT_LANGUAGE=browser)
        self.assertEqual(reporter_mails()[0].subject, subject)

    def test_invalid_email_is_form_error(self):
        response = self.client.post(self.create_url, dict(VALID, reporter_email='not-an-address'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('reporter_email', response.context['form'].errors)
        self.assertEqual(CriticalIncident.objects.count(), 0)
        self.assertEqual(ReporterContact.objects.count(), 0)
        self.assertEqual(mail.outbox, [])

    def test_form_field_and_hint_in_german(self):
        response = self.client.get(self.create_url, HTTP_ACCEPT_LANGUAGE='de')
        self.assertContains(response, 'E-Mail für Benachrichtigungen')
        # the warning stands out (bold, error colour) and nothing else follows it
        self.assertContains(response, 'Freiwillig. <strong class="labcirs-warnung">Mit Adresse ist '
                                      'Ihre Meldung nicht mehr vollständig anonym.</strong>')
        self.assertNotContains(response, 'Mail-Protokolle')

    def test_warning_style_exists(self):
        with open(finders.find('css/labcirs.css'), encoding='utf-8') as css:
            self.assertRegex(css.read(), r'\.labcirs-warnung \{[^}]*color: var\(--ui-danger\)'
                                         r'[^}]*font-weight: 700')

    def test_field_asks_the_browser_not_to_offer_an_address(self):
        # The browser must not offer the work address
        response = self.client.get(self.create_url)
        tag = re.search(r'<input[^>]*name="reporter_email"[^>]*>', response.content.decode())
        self.assertIn('autocomplete="off"', tag.group(0))

    @override_settings(DEFAULT_FROM_EMAIL='')
    def test_without_sender_the_field_is_not_offered_and_a_posted_address_is_ignored(self):
        # No mail can be sent without a sender, so nobody is asked for an address
        response = self.client.get(self.create_url, HTTP_ACCEPT_LANGUAGE='de')
        self.assertNotIn('reporter_email', response.context['form'].fields)
        self.assertNotContains(response, 'reporter_email')
        self.assertNotContains(response, 'E-Mail für Benachrichtigungen')
        self.client.post(self.create_url, dict(VALID, reporter_email=ADDRESS))
        self.assertEqual(CriticalIncident.objects.count(), 1)
        self.assertEqual(ReporterContact.objects.count(), 0)
        self.assertEqual(mail.outbox, [])

    def test_with_sender_the_field_is_offered(self):
        response = self.client.get(self.create_url)
        self.assertIn('reporter_email', response.context['form'].fields)

    def test_address_reaches_no_other_mail(self):
        config = self.dept.labcirsconfig
        config.send_notification = True
        config.notification_sender_email = 'labcirs@localhost'
        config.save()
        config.notification_recipients.add(create_user('recipient'))
        self.client.post(self.create_url, dict(VALID, reporter_email=ADDRESS))
        others = [message for message in mail.outbox if message.to != [ADDRESS]]
        self.assertEqual(len(others), 1)
        for message in others:
            self.assertNotIn(ADDRESS, message.subject + message.body + ''.join(message.to))

    def test_contact_is_deleted_with_its_incident(self):
        ci = self.incident()
        ci.delete()
        self.assertEqual(ReporterContact.objects.count(), 0)

    def test_string_form_has_no_address(self):
        contact = ReporterContact.objects.create(incident=self.incident(email=None),
                                                 email=ADDRESS)
        self.assertNotIn(ADDRESS, str(contact))


@override_settings(LANGUAGE_CODE='de')
class ReporterStatusMailTest(ReporterMailBase):

    def test_status_change_notifies_with_label_only(self):
        ci = self.incident()
        ci.status = 'in process'
        self.save_committed(ci)
        message, = reporter_mails()
        self.assertEqual(message.subject, 'Neuer Stand Ihrer Meldung')
        self.assertEqual(message.body, ('Der Stand Ihrer Meldung hat sich geändert: '
                                        'In Bearbeitung.\n\n' + CLOSING_DE))
        for text in (ci.incident, ci.reason, ci.immediate_action):
            self.assertNotIn(text, message.body)

    def test_save_without_status_change_sends_nothing(self):
        ci = self.incident()
        self.save_committed(ci)
        ci.action = ''
        ci.risk = 'low'
        self.save_committed(ci)
        self.assertEqual(reporter_mails(), [])

    def test_second_save_after_change_sends_nothing_more(self):
        ci = self.incident()
        ci.status = 'in process'
        self.save_committed(ci)
        self.save_committed(ci)
        self.assertEqual(len(reporter_mails()), 1)

    def test_new_incident_sends_no_status_mail(self):
        ci = baker.make_recipe('cirs.public_ci', department=self.dept)
        ReporterContact.objects.create(incident=ci, email=ADDRESS)
        self.save_committed(ci)
        self.assertEqual(reporter_mails(), [])

    def test_status_change_without_contact_sends_nothing(self):
        ci = self.incident(email=None)
        ci.status = 'in process'
        self.save_committed(ci)
        self.assertEqual(mail.outbox, [])

    def test_completed_sends_final_mail_and_deletes_contact(self):
        ci = self.incident(status='in process')
        ci.status = 'completed'
        self.save_committed(ci)
        message, = reporter_mails()
        self.assertEqual(message.subject, 'Ihre Meldung ist abgeschlossen')
        self.assertEqual(message.body, (
            'Ihre Meldung ist abgeschlossen. Ihre E-Mail-Adresse wurde gelöscht, Sie erhalten '
            'keine weiteren Nachrichten.\n\n' + CLOSING_DE))
        self.assertFalse(ReporterContact.objects.filter(incident=ci).exists())
        # nothing follows, whoever writes or changes something afterwards
        ci.status = 'in process'
        self.save_committed(ci)
        self.assertEqual(len(reporter_mails()), 1)

    def test_completed_deletes_contact_although_the_mail_fails(self):
        ci = self.incident()
        ci.status = 'completed'
        with mock.patch('cirs.reporter_mail.send_mail', side_effect=OSError('down')):
            with self.assertLogs('cirs', level='ERROR'):
                self.save_committed(ci)
        self.assertFalse(ReporterContact.objects.filter(incident=ci).exists())

    def test_status_mail_and_deletion_wait_for_the_commit(self):
        ci = self.incident()
        ci.status = 'completed'
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            ci.save()
            self.assertEqual(reporter_mails(), [])
            self.assertTrue(ReporterContact.objects.filter(incident=ci).exists())
        self.assertEqual(len(callbacks), 1)
        callbacks[0]()  # the commit: first the mail, then the address goes
        self.assertEqual(len(reporter_mails()), 1)
        self.assertFalse(ReporterContact.objects.filter(incident=ci).exists())

    def test_rolled_back_save_tells_the_reporter_nothing(self):
        ci = self.incident()
        ci.status = 'completed'
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            with self.assertRaises(RuntimeError):
                with transaction.atomic():
                    ci.save()
                    raise RuntimeError('the admin form failed after the save')
        self.assertEqual(callbacks, [])
        self.assertEqual(reporter_mails(), [])
        self.assertEqual(CriticalIncident.objects.get().status, 'new')
        self.assertTrue(ReporterContact.objects.filter(incident=ci).exists())

    @override_settings(SITE_URL='https://cirs.example.org')
    def test_mail_names_site_url_as_text_without_link_markup(self):
        ci = self.incident()
        ci.status = 'in process'
        self.save_committed(ci)
        message, = reporter_mails()
        self.assertTrue(message.body.endswith(
            CLOSING_DE + '\nAdresse im Hausnetz: https://cirs.example.org'), message.body)
        self.assertNotIn('<a ', message.body)
        self.assertEqual(message.alternatives, [])

    @override_settings(SITE_URL='')
    def test_no_site_url_no_address_line(self):
        ci = self.incident()
        ci.status = 'in process'
        self.save_committed(ci)
        self.assertNotIn('Hausnetz', reporter_mails()[0].body)


@override_settings(LANGUAGE_CODE='de')
class ReporterReplyMailTest(ReporterMailBase):

    def test_reviewer_comment_notifies_reporter_with_text(self):
        ci = self.incident()
        self.client.force_login(self.reviewer.user)
        response = self.client.post(ci.get_absolute_url(), {'text': 'Synthetic reply'})
        self.assertRedirects(response, ci.get_absolute_url(), fetch_redirect_response=False)
        message, = reporter_mails()
        self.assertEqual(message.subject, 'Neue Rückmeldung zu Ihrer Meldung')
        self.assertEqual(message.body, ('Das Qualitätsmanagement hat Ihnen geantwortet:\n\n'
                                        'Synthetic reply\n\n' + CLOSING_DE))
        for text in (ci.incident, ci.reason, ci.immediate_action):
            self.assertNotIn(text, message.body)

    def test_reporter_comment_does_not_notify_reporter(self):
        ci = self.incident()
        grant_access(self.client, ci)
        self.client.post(ci.get_absolute_url(), {'text': 'Synthetic question'})
        self.assertEqual(Comment.objects.get().author, self.dept.reporter.user)
        self.assertEqual(reporter_mails(), [])

    def test_reviewer_comment_without_contact_sends_nothing(self):
        ci = self.incident(email=None)
        self.client.force_login(self.reviewer.user)
        self.client.post(ci.get_absolute_url(), {'text': 'Synthetic reply'})
        self.assertEqual(mail.outbox, [])

    def test_reply_of_another_incident_reaches_only_its_reporter(self):
        ci = self.incident()
        other = self.incident(email='other@example.org')
        self.client.force_login(self.reviewer.user)
        self.client.post(other.get_absolute_url(), {'text': 'Synthetic reply'})
        self.assertEqual(reporter_mails(), [])
        self.assertEqual([message.to for message in mail.outbox], [['other@example.org']])
        self.assertTrue(ReporterContact.objects.filter(incident=ci).exists())


class ReporterMailFailureTest(ReporterMailBase):

    @parameterized.expand([
        ('smtp', smtplib.SMTPException('down')),
        ('refused', smtplib.SMTPRecipientsRefused({ADDRESS: (550, b'no such user')})),
        ('data', smtplib.SMTPDataError(550, ('rejected <%s>' % ADDRESS).encode())),
        ('connection', ConnectionRefusedError('refused')),
        # what Django raises for a sender it cannot parse, and any other surprise
        ('sender', ValueError('Invalid address ""')),
        ('other', RuntimeError('unexpected ' + ADDRESS)),
    ])
    def test_reporter_mail_failure_is_logged_not_raised(self, name, error):
        with mock.patch('cirs.reporter_mail.send_mail', side_effect=error) as send:
            with self.assertLogs('cirs', level='ERROR') as logged:
                response = self.client.post(self.create_url,
                                            dict(VALID, reporter_email=ADDRESS), follow=True)
        send.assert_called_once()
        ci = CriticalIncident.objects.get()
        self.assertContains(response, code_markup(ci.comment_code))
        self.assertEqual(ci.reporter_contact.email, ADDRESS)
        # Neither the address (servers echo it in their answers) nor the code nor the report
        # may reach the log, the traceback included.
        formatter = logging.Formatter()
        log = '\n'.join(formatter.format(record) for record in logged.records)
        for secret in (ADDRESS, 'example.org', ci.comment_code, group_code(ci.comment_code),
                       ci.incident):
            self.assertNotIn(secret, log)

    def test_failing_status_mail_does_not_stop_the_save(self):
        ci = self.incident()
        ci.status = 'in process'
        with mock.patch('cirs.reporter_mail.send_mail',
                        side_effect=smtplib.SMTPException('down')):
            with self.assertLogs('cirs', level='ERROR'):
                self.save_committed(ci)
        self.assertEqual(CriticalIncident.objects.get().status, 'in process')

    def test_failing_reply_mail_keeps_the_comment(self):
        ci = self.incident()
        self.client.force_login(self.reviewer.user)
        with mock.patch('cirs.reporter_mail.send_mail', side_effect=OSError('timeout')):
            with self.assertLogs('cirs', level='ERROR'):
                response = self.client.post(ci.get_absolute_url(), {'text': 'Synthetic reply'})
        self.assertRedirects(response, ci.get_absolute_url(), fetch_redirect_response=False)
        self.assertEqual(Comment.objects.get().text, 'Synthetic reply')

    # The real SMTP backend raises ValueError for a sender it cannot parse, outside its own
    # error handling: without a catch-all the reporter would get a 500 instead of the code and
    # the QM a rolled-back status change.
    @override_settings(EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
                       DEFAULT_FROM_EMAIL='not valid <')
    def test_invalid_sender_does_not_cost_the_reporter_the_code(self):
        with mock.patch('smtplib.SMTP'):
            with self.assertLogs('cirs', level='ERROR') as logged:
                response = self.client.post(self.create_url,
                                            dict(VALID, reporter_email=ADDRESS), follow=True)
        ci = CriticalIncident.objects.get()
        self.assertContains(response, code_markup(ci.comment_code))
        self.assertEqual(ci.reporter_contact.email, ADDRESS)
        log = '\n'.join(logging.Formatter().format(record) for record in logged.records)
        self.assertIn('ValueError', log)
        self.assertNotIn(ADDRESS, log)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
                       DEFAULT_FROM_EMAIL='')
    def test_empty_sender_does_not_break_status_change_and_reply(self):
        # a contact can outlive a sender setting that was removed later
        ci = self.incident()
        ci.status = 'in process'
        self.client.force_login(self.reviewer.user)
        with mock.patch('smtplib.SMTP'):
            with self.assertLogs('cirs', level='ERROR'):
                self.save_committed(ci)
            with self.assertLogs('cirs', level='ERROR'):
                response = self.client.post(ci.get_absolute_url(), {'text': 'Synthetic reply'})
        self.assertEqual(CriticalIncident.objects.get().status, 'in process')
        self.assertRedirects(response, ci.get_absolute_url(), fetch_redirect_response=False)
        self.assertEqual(Comment.objects.get().text, 'Synthetic reply')

    def test_unknown_event_is_a_programming_error_not_a_mail_failure(self):
        for ci in (self.incident(), self.incident(email=None)):
            with self.assertRaises(ValueError):
                notify_reporter(ci, 'nonsense')
        self.assertEqual(mail.outbox, [])


class ReporterContactRemoveTest(ReporterMailBase):

    def test_reporter_can_remove_email_with_code_access(self):
        ci = self.incident()
        grant_access(self.client, ci)
        response = self.client.post(self.remove_url(ci), {'confirm': 'on'},
                                    HTTP_ACCEPT_LANGUAGE='de', follow=True)
        self.assertRedirects(response, ci.get_absolute_url())
        self.assertFalse(ReporterContact.objects.filter(incident=ci).exists())
        self.assertContains(response, REMOVED_DE)
        self.assertNotContains(response, 'E-Mail-Benachrichtigung ist aktiv.')

    @parameterized.expand([('missing', {}), ('empty', {'confirm': ''})])
    def test_remove_email_needs_the_confirmation(self, name, data):
        # WCAG 3.3.4: a permanent deletion must be confirmed
        ci = self.incident()
        grant_access(self.client, ci)
        response = self.client.post(self.remove_url(ci), data, HTTP_ACCEPT_LANGUAGE='de',
                                    follow=True)
        self.assertRedirects(response, ci.get_absolute_url())
        self.assertTrue(ReporterContact.objects.filter(incident=ci).exists())
        self.assertContains(response, CONFIRM_ERROR_DE)
        self.assertNotContains(response, REMOVED_DE)
        self.assertContains(response, 'E-Mail-Benachrichtigung ist aktiv.')

    def test_remove_email_without_access_is_rejected(self):
        ci = self.incident()
        response = self.client.post(self.remove_url(ci), {'confirm': 'on'})
        self.assertRedirects(response, reverse('incident_search', kwargs={'dept': self.dept.label}),
                             fetch_redirect_response=False)
        self.assertTrue(ReporterContact.objects.filter(incident=ci).exists())

    def test_access_to_another_incident_does_not_help(self):
        ci, other = self.incident(), self.incident(email='other@example.org')
        grant_access(self.client, other)
        response = self.client.post(self.remove_url(ci), {'confirm': 'on'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(ReporterContact.objects.filter(incident=ci).exists())
        self.assertTrue(ReporterContact.objects.filter(incident=other).exists())

    def test_remove_email_of_unknown_incident_is_answered_like_one_without_access(self):
        # whoever may not touch a report gets the same answer whether it exists or not
        ci = self.incident()
        answers = []
        for pk in (ci.pk, 999999):
            response = self.client.post(reverse('remove_reporter_email',
                                                kwargs={'dept': self.dept.label, 'pk': pk}),
                                        {'confirm': 'on'})
            answers.append((response.status_code, response.get('Location')))
        self.assertEqual(answers[0], answers[1])
        self.assertEqual(answers[0][0], 302)
        self.assertTrue(ReporterContact.objects.filter(incident=ci).exists())

    def test_remove_url_answers_get_with_405(self):
        ci = self.incident()
        grant_access(self.client, ci)
        self.assertEqual(self.client.get(self.remove_url(ci)).status_code, 405)
        self.assertTrue(ReporterContact.objects.filter(incident=ci).exists())

    def test_only_the_code_holder_may_remove_the_address(self):
        # A QM or an administrator gets 403 and nothing is deleted
        ci = self.incident()
        other_dept = baker.make_recipe('cirs.department')
        outsider = baker.make_recipe('cirs.reviewer')
        other_dept.reviewers.add(outsider)
        superuser = create_user('root', superuser=True)
        for user in (self.reviewer.user, outsider.user, superuser):
            client = Client()
            client.force_login(user)
            grant_access(client, ci)  # even with the code in the session
            response = client.post(self.remove_url(ci), {'confirm': 'on'})
            self.assertEqual(response.status_code, 403, user)
            self.assertTrue(ReporterContact.objects.filter(incident=ci).exists(), user)

    def test_remove_email_needs_csrf_token(self):
        ci = self.incident()
        client = Client(enforce_csrf_checks=True)
        grant_access(client, ci)
        self.assertEqual(client.post(self.remove_url(ci), {'confirm': 'on'}).status_code, 403)
        self.assertTrue(ReporterContact.objects.filter(incident=ci).exists())


class ReporterDetailPageTest(ReporterMailBase):

    def test_reviewer_sees_mail_warning_reporter_does_not(self):
        ci = self.incident(email=None)
        self.client.force_login(self.reviewer.user)
        response = self.client.get(ci.get_absolute_url(), HTTP_ACCEPT_LANGUAGE='de')
        self.assertContains(response, REVIEWER_HINT_DE)
        self.assertContains(response, 'Schreiben Sie keine Namen oder Patientendaten.')
        self.client.logout()
        grant_access(self.client, ci)
        response = self.client.get(ci.get_absolute_url(), HTTP_ACCEPT_LANGUAGE='de')
        self.assertNotContains(response, REVIEWER_HINT_DE)

    def test_reviewer_hint_is_the_same_with_and_without_contact(self):
        # It must not tell the QM whether a reporter left an address.
        with_contact, without = self.incident(), self.incident(email=None)
        self.client.force_login(self.reviewer.user)
        for ci in (with_contact, without):
            response = self.client.get(ci.get_absolute_url(), HTTP_ACCEPT_LANGUAGE='de')
            self.assertContains(response, REVIEWER_HINT_DE)
            self.assertNotContains(response, 'E-Mail-Benachrichtigung ist aktiv.')
            self.assertNotContains(response, 'E-Mail-Adresse löschen')
            self.assertNotContains(response, ADDRESS)

    def test_reporter_sees_notice_and_remove_form_only_with_contact(self):
        with_contact, without = self.incident(), self.incident(email=None)
        grant_access(self.client, with_contact)
        response = self.client.get(with_contact.get_absolute_url(), HTTP_ACCEPT_LANGUAGE='de')
        self.assertContains(response, 'E-Mail-Benachrichtigung ist aktiv.')
        self.assertContains(response, 'E-Mail-Adresse löschen')
        self.assertContains(response, 'Ich möchte meine E-Mail-Adresse endgültig löschen.')
        self.assertContains(response, self.remove_url(with_contact))
        self.assertNotContains(response, ADDRESS)
        grant_access(self.client, without)
        response = self.client.get(without.get_absolute_url(), HTTP_ACCEPT_LANGUAGE='de')
        self.assertNotContains(response, 'E-Mail-Benachrichtigung ist aktiv.')
        self.assertNotContains(response, self.remove_url(without))

    def test_remove_form_texts_in_english(self):
        ci = self.incident()
        grant_access(self.client, ci)
        response = self.client.get(ci.get_absolute_url(), HTTP_ACCEPT_LANGUAGE='en')
        self.assertContains(response, 'E-mail notification is active.')
        self.assertContains(response, 'I want to delete my e-mail address permanently.')
        self.assertContains(response, 'Delete e-mail address')


class ReporterAddressHiddenFromQmTest(ReporterMailBase):

    def test_email_not_shown_in_admin(self):
        ci = self.incident()
        self.client.force_login(self.reviewer.user)
        self.assertFalse(admin.site.is_registered(ReporterContact))
        self.assertFalse(admin_site.is_registered(ReporterContact))
        pages = [reverse('admin:index'), reverse('admin:cirs_criticalincident_changelist'),
                 reverse('admin:cirs_criticalincident_change', args=[ci.pk]),
                 reverse('admin:cirs_criticalincident_history', args=[ci.pk])]
        for url in pages:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertNotContains(response, ADDRESS)
            self.assertNotContains(response, 'example.org')
            self.assertNotContains(response, 'eporter contact')

    def test_admin_status_change_notifies_the_reporter(self):
        # The QM changes the status where it always did, the reporter gets the mail.
        ci = self.incident()
        self.client.force_login(self.reviewer.user)
        data = {'status': 'in process', 'review_date': '', 'org_unit': '', 'risk': '',
                'frequency': '', 'hazard': '', 'responsibilty': '', 'action': ''}
        for prefix in ('publishableincident', 'comments', 'status_changes'):
            data.update({prefix + '-TOTAL_FORMS': '0', prefix + '-INITIAL_FORMS': '0',
                         prefix + '-MIN_NUM_FORMS': '0', prefix + '-MAX_NUM_FORMS': '1000'})
        with self.captureOnCommitCallbacks(execute=True):  # the admin saves in a transaction
            response = self.client.post(
                reverse('admin:cirs_criticalincident_change', args=[ci.pk]), data)
        self.assertRedirects(response, reverse('admin:cirs_criticalincident_changelist'),
                             fetch_redirect_response=False)
        self.assertEqual(CriticalIncident.objects.get().status, 'in process')
        self.assertEqual(len(reporter_mails()), 1)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
                       DEFAULT_FROM_EMAIL='')
    def test_admin_status_change_survives_a_broken_mail_setup(self):
        ci = self.incident()
        self.client.force_login(self.reviewer.user)
        data = {'status': 'in process', 'review_date': '', 'org_unit': '', 'risk': '',
                'frequency': '', 'hazard': '', 'responsibilty': '', 'action': ''}
        for prefix in ('publishableincident', 'comments', 'status_changes'):
            data.update({prefix + '-TOTAL_FORMS': '0', prefix + '-INITIAL_FORMS': '0',
                         prefix + '-MIN_NUM_FORMS': '0', prefix + '-MAX_NUM_FORMS': '1000'})
        with mock.patch('smtplib.SMTP'), self.assertLogs('cirs', level='ERROR'):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    reverse('admin:cirs_criticalincident_change', args=[ci.pk]), data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(CriticalIncident.objects.get().status, 'in process')
