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

"""The page of an incident for the QM: the report to read, the review, the dialogue with the
reporting person, the published case and the history, at the address that the reporter uses too.

Who may do what (the role of the department and the permission, never the address), what each of
the three actions does and refuses, that neither the code nor the address of the reporting person
is anywhere on the page, and that the way back to the list can lead nowhere else. Test data is
constructed, never taken from a real report."""

import html as html_lib
import re
from datetime import date, datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

from django.contrib.auth.models import Permission
from django.core import mail
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from model_bakery import baker

from cirs.models import (Comment, CriticalIncident, IncidentStatusChange, OrgUnit,
                         PublishableIncident, ReporterContact, group_code)
from cirs.qm.params import incident_query, worklist_url

from .canary import CanaryMixin
from .helpers import create_user, csp_violations, make_incident
from .test_pages_report import DU_FORM, css_text

EN = {'HTTP_ACCEPT_LANGUAGE': 'en'}
VIENNA = ZoneInfo('Europe/Vienna')
ADDRESS = 'melder.kanarienvogel@example.org'
REVIEW = {'status': 'in process', 'org_unit': '', 'risk': 'high', 'frequency': '', 'hazard': '',
          'category': ['other'], 'responsibilty': 'Station', 'action': 'Synthetic measure',
          'review_date': ''}
CASE = {'en-incident': 'Title', 'en-description': 'Description',
        'en-measures_and_consequences': 'Measures', 'de-incident': 'Titel',
        'de-description': 'Beschreibung', 'de-measures_and_consequences': 'Massnahmen'}
ALL_PERMISSIONS = ('change_criticalincident', 'add_publishableincident',
                   'change_publishableincident')


def section(html, name):
    return re.search(r'<section[^>]*aria-labelledby="%s".*?</section>' % name, html, re.S).group(0)


def words(fragment):
    return ' '.join(html_lib.unescape(re.sub(r'<[^>]+>', ' ', fragment)).split())


def forms_of(html):
    """The POST forms of the page itself, not those of the top bar (log out, language)."""
    main = re.search(r'<main.*?</main>', html, re.S).group(0)
    return re.findall(r'<form\b[^>]*method="post"[^>]*>.*?</form>', main, re.S)


@override_settings(DEFAULT_FROM_EMAIL='cirs@example.org', TIME_ZONE='Europe/Vienna')
class PageCase(CanaryMixin, TestCase):
    """A department with a reviewer, an incident that is new (its report was written at a quarter
    past one in the morning) with a reporting person who left an address, and a foreign department."""

    @classmethod
    def setUpTestData(cls):
        cls.dept = baker.make_recipe('cirs.department', name='Station Eins')
        cls.reviewer = baker.make_recipe('cirs.reviewer')
        cls.dept.reviewers.add(cls.reviewer)
        cls.incident = make_incident(
            cls.dept, reported=date(2026, 9, 30), incident='Synthetic incident text',
            reason='Synthetic reason', immediate_action='Synthetic immediate action',
            preventability='avoidable', history=[('new', datetime(2026, 9, 30, 1, 15, tzinfo=VIENNA))])
        cls.contact = ReporterContact.objects.create(incident=cls.incident, email=ADDRESS)
        cls.url = cls.incident.get_absolute_url()
        cls.make_canary()
        ReporterContact.objects.create(incident=cls.canary_incidents[0],
                                       email='fremd.kanarienvogel@example.org')

    def setUp(self):
        self.client.force_login(self.reviewer.user)

    def get(self, query='', **extra):
        return self.client.get(self.url + query, **{**EN, **extra})

    def post(self, action, data, query='', **extra):
        payload = dict(data) if action is None else {'aktion': action, **data}
        return self.client.post(self.url + query, payload, **{**EN, **extra})

    def html(self, query=''):
        response = self.get(query)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def stored(self):
        return CriticalIncident.objects.get(pk=self.incident.pk)

    # the foreign department has a case and a comment of its own: count what belongs to this incident
    def cases(self):
        return PublishableIncident.objects.filter(critical_incident=self.incident)

    def comments(self):
        return Comment.objects.filter(critical_incident=self.incident)

    def reporter_mails(self):
        return [m for m in mail.outbox if m.to == [ADDRESS]]

    def reviewer_without(self, *codenames):
        """The reviewer, but without the permissions of these codenames."""
        self.reviewer.user.user_permissions.remove(*Permission.objects.filter(codename__in=codenames))
        self.client.force_login(type(self.reviewer.user).objects.get(pk=self.reviewer.user.pk))


class WhoMayDoWhatTest(PageCase):

    def test_the_reviewer_of_the_department_gets_the_page_of_the_qm(self):
        self.assertTemplateUsed(self.get(), 'cirs/qm/incident.html')

    def test_the_reporter_with_the_code_keeps_the_page_of_the_reporter(self):
        client = Client()
        client.post(reverse('incident_search', kwargs={'dept': self.dept.label}),
                    {'incident_code': self.incident.comment_code})
        response = client.get(self.url, **EN)
        self.assertTemplateUsed(response, 'cirs/criticalincident_detail.html')
        self.assertTemplateNotUsed(response, 'cirs/qm/incident.html')
        self.assertNotIn('name="aktion"', response.content.decode())

    def test_the_reporter_with_the_code_can_start_no_action(self):
        client = Client()
        client.post(reverse('incident_search', kwargs={'dept': self.dept.label}),
                    {'incident_code': self.incident.comment_code})
        for action, data in (('bewertung', REVIEW), ('veroeffentlichung', dict(CASE, publish='on')),
                             ('antwort', {'text': 'Synthetic reply'})):
            response = client.post(self.url, {'aktion': action, **data}, **EN)
            self.assertEqual(response.status_code, 403, action)
        self.assertEqual(self.stored().status, 'new')
        self.assertEqual(self.stored().risk, '')
        self.assertFalse(self.cases().exists())
        self.assertEqual(self.comments().count(), 0)
        self.assertEqual(mail.outbox, [])

    def test_the_reporter_still_replies_with_the_form_that_has_no_action(self):
        client = Client()
        client.post(reverse('incident_search', kwargs={'dept': self.dept.label}),
                    {'incident_code': self.incident.comment_code})
        self.assertEqual(client.post(self.url, {'text': 'Synthetic reply'}).status_code, 302)
        self.assertEqual(self.comments().get().author, self.dept.reporter.user)

    def test_a_reviewer_of_another_department_can_start_no_action(self):
        outsider = baker.make_recipe('cirs.reviewer')
        baker.make_recipe('cirs.department').reviewers.add(outsider)
        self.client.force_login(outsider.user)
        for action, data in (('bewertung', REVIEW), ('veroeffentlichung', dict(CASE, publish='on')),
                             ('antwort', {'text': 'Synthetic reply'})):
            response = self.post(action, data)
            self.assertRedirects(response, reverse('labcirs_home'), fetch_redirect_response=False)
        self.assertEqual(self.stored().risk, '')
        self.assertFalse(self.cases().exists())
        self.assertEqual(self.comments().count(), 0)

    def test_without_the_permission_to_change_incidents_the_forms_are_gone_and_the_actions_refused(self):
        self.reviewer_without('change_criticalincident')
        html = self.html()
        self.assertEqual(html.count('name="aktion" value="bewertung"'), 0)
        self.assertEqual(html.count('name="aktion" value="antwort"'), 0)
        self.assertIn('Your account is not allowed to change the assessment.', html)
        self.assertIn('Your account is not allowed to reply.', html)
        self.assertIn('Synthetic incident text', html)  # reading is the role's, not a permission
        for action, data in (('bewertung', REVIEW), ('antwort', {'text': 'Synthetic reply'}),
                             (None, {'text': 'Synthetic reply'})):
            self.assertEqual(self.post(action, data).status_code, 403, action)
        self.assertEqual(self.stored().risk, '')
        self.assertEqual(self.comments().count(), 0)

    def test_the_case_needs_the_permission_to_add_and_then_the_one_to_change(self):
        self.reviewer_without('add_publishableincident')
        self.assertEqual(self.post('veroeffentlichung', CASE).status_code, 403)
        self.assertNotIn('name="aktion" value="veroeffentlichung"', self.html())
        self.assertFalse(self.cases().exists())
        # with the right to add it works; the case is there, so the right to change counts now
        self.reviewer.user.user_permissions.add(Permission.objects.get(codename='add_publishableincident'))
        self.client.force_login(type(self.reviewer.user).objects.get(pk=self.reviewer.user.pk))
        self.assertEqual(self.post('veroeffentlichung', CASE).status_code, 302)
        self.reviewer_without('change_publishableincident')
        self.assertEqual(self.post('veroeffentlichung', dict(CASE, **{'en-incident': 'X'})).status_code, 403)
        self.assertEqual(self.cases().get().translations.get(
            language_code='en').incident, 'Title')

    def test_an_unknown_action_is_a_bad_request_and_changes_nothing(self):
        for action in ('loeschen', 'BEWERTUNG', 'bewertung ', '<script>'):
            self.assertEqual(self.post(action, REVIEW).status_code, 400, action)
        self.assertEqual(self.stored().risk, '')

    def test_the_old_form_of_the_reply_without_an_action_is_still_the_reply(self):
        response = self.post(None, {'text': 'Synthetic reply'})
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertEqual(self.comments().get().author, self.reviewer.user)

    def test_a_post_without_the_csrf_token_is_refused(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.reviewer.user)
        for action, data in (('bewertung', REVIEW), ('antwort', {'text': 'x'})):
            self.assertEqual(client.post(self.url, {'aktion': action, **data}).status_code, 403)
        self.assertEqual(self.stored().risk, '')
        self.assertEqual(self.comments().count(), 0)

    def test_the_page_and_every_answer_are_not_stored_by_the_browser(self):
        for response in (self.get(), self.post('bewertung', REVIEW),
                         self.post('bewertung', dict(REVIEW, status='bogus'))):
            self.assertIn('no-store', response['Cache-Control'])

    def test_a_reviewer_of_two_departments_sees_the_department_of_the_incident_only_in_the_head(self):
        self.assertNotIn('Department: Station Eins', words(self.html()))
        second = baker.make_recipe('cirs.department', name='Station Zwei')
        second.reviewers.add(self.reviewer)
        text = words(self.html())
        self.assertIn('Department: Station Eins', text)
        self.assertNotIn('Station Zwei', text)


class PageTest(PageCase):

    def test_one_h1_and_a_title_with_the_number(self):
        html = self.html()
        self.assertEqual(re.findall(r'<h1[^>]*>(.*?)</h1>', html, re.S), ['Report %d' % self.incident.pk])
        self.assertRegex(html, r'<title>Report %d · [^<]+</title>' % self.incident.pk)

    def test_the_parts_come_in_the_order_of_the_work(self):
        html = self.html()
        names = ['meldung-titel', 'bewertung-titel', 'rueckmeldungen-titel',
                 'veroeffentlichung-titel', 'verlauf-titel']
        positions = [html.index('id="%s"' % name) for name in names]
        self.assertEqual(positions, sorted(positions))
        for name in names:  # and every link of the row at the top leads to a part
            self.assertIn('href="#%s"' % name, html)

    def test_the_report_is_there_to_read_with_its_dates_and_nothing_to_edit_in_it(self):
        text = words(section(self.html(), 'meldung-titel'))
        for expected in ('Synthetic incident text', 'Synthetic reason', 'Synthetic immediate action',
                         'The incident was avoidable'):
            self.assertIn(expected, text)
        self.assertNotIn('<input', section(self.html(), 'meldung-titel'))

    def test_the_head_names_the_status_in_a_badge_and_the_way_back(self):
        head = section(self.html(), 'stand-titel')
        self.assertRegex(head, r'<span class="ui-badge ui-badge--info">New</span>')
        self.assertIn('href="%s">Back to the list' % worklist_url(), self.html())

    def test_a_completed_incident_has_the_badge_of_success(self):
        incident = make_incident(self.dept, history=[('new', datetime(2026, 9, 1, 8, tzinfo=VIENNA)),
                                                     ('completed', datetime(2026, 9, 2, 8, tzinfo=VIENNA))])
        html = self.client.get(incident.get_absolute_url(), **EN).content.decode()
        self.assertIn('<span class="ui-badge ui-badge--success">Completed</span>', html)

    def test_every_part_has_its_form_with_its_action_its_token_and_its_guard_against_a_double_click(self):
        forms = forms_of(self.html())
        self.assertEqual([re.search(r'name="aktion" value="(\w+)"', f).group(1) for f in forms],
                         ['bewertung', 'antwort', 'veroeffentlichung'])
        for form in forms:
            self.assertIn('data-einmal-absenden', form)
            self.assertIn('csrfmiddlewaretoken', form)
            self.assertRegex(form, r'<button class="ui-btn [^"]*" type="submit" data-busy-text="[^"]+">')
            self.assertNotIn(' action=', form.split('>')[0])  # it posts to this page, list and all

    def test_no_inline_code_and_no_script_but_the_shared_one(self):
        for query in ('', incident_query(stand='new'), '?liste=x'):
            html = self.html(query)
            self.assertEqual(csp_violations(html), [], query)
            self.assertEqual(re.findall(r'<script[^>]*src="([^"]*)"', html), ['/static/js/formular.js'])

    def test_every_class_has_a_rule_and_none_comes_from_somewhere_else(self):
        css = css_text()
        main = re.search(r'<main.*?</main>', self.html(), re.S).group(0)
        for token in set(' '.join(re.findall(r'\sclass="([^"]*)"', main)).split()):
            self.assertTrue(token.startswith(('ui-', 'labcirs-')), token)
            self.assertRegex(css, r'\.%s(?![\w-])' % re.escape(token), token)

    def test_ids_are_unique_and_every_reference_leads_to_one(self):
        html = self.html()
        ids = re.findall(r'\sid="([^"]+)"', html)
        self.assertEqual(len(ids), len(set(ids)), sorted(ids))
        for target in re.findall(r'aria-labelledby="([^"]+)"', html):
            self.assertIn(target, ids)
        for target in re.findall(r'aria-describedby="([^"]+)"', html):
            for single in target.split():
                self.assertIn(single, ids)

    def test_every_field_has_a_label(self):
        main = re.search(r'<main.*?</main>', self.html(), re.S).group(0)
        labels = set(re.findall(r'<label[^>]*for="([^"]+)"', main))
        groups = set(re.findall(r'<fieldset[^>]*id="([^"]+)"', main))
        for attributes in re.findall(r'<(?:input|select|textarea)\b([^>]*)>', main):
            if 'type="hidden"' in attributes:
                continue
            field_id = re.search(r'\sid="([^"]+)"', attributes).group(1)
            self.assertTrue(field_id in labels or re.sub(r'_\d+$', '', field_id) in groups, field_id)

    def test_the_tables_have_a_caption_and_every_head_cell_a_scope(self):
        html = self.html()
        self.assertEqual(html.count('<table'), html.count('<caption'))
        self.assertEqual(len(re.findall(r'<th[\s>]', html)), html.count('<th scope="col"'))

    def test_the_review_is_in_groups_and_the_status_a_group_of_choices(self):
        form = forms_of(self.html())[0]
        self.assertEqual(form.count('class="ui-fieldset"'), 4)
        self.assertRegex(form, r'<fieldset class="ui-fieldset" id="id_status"')
        self.assertIn('name="category"', form)
        self.assertNotIn('multiselectfield', form)

    def test_the_hint_for_the_reply_is_once_there_and_says_nothing_of_an_address(self):
        text = words(self.html())
        self.assertEqual(text.count('Do not write names or patient data.'), 1)
        for forbidden in ('E-mail notification is active', 'Delete e-mail address', 'End access'):
            self.assertNotIn(forbidden, text)

    def test_the_replies_are_roles_oldest_first_and_the_last_article_is_the_last_reply(self):
        qm = self.reviewer.user
        for author, text, day in ((qm, 'Synthetic second', date(2026, 10, 2)),
                                  (self.dept.reporter.user, 'Synthetic first', date(2026, 10, 1))):
            baker.make(Comment, critical_incident=self.incident, author=author, text=text, created=day)
        dialogue = section(self.html(), 'rueckmeldungen-titel')
        self.assertLess(dialogue.index('Synthetic first'), dialogue.index('Synthetic second'))
        titles = [words(t).split(' · ')[0] for t in re.findall(r'<h3[^>]*>(.*?)</h3>', dialogue, re.S)]
        self.assertEqual(titles[:2], ['Reporting person', 'Quality management'])
        self.assertNotIn(qm.username, dialogue)
        self.assertNotIn(self.dept.reporter.user.username, dialogue)
        self.assertIn('Synthetic second', re.findall(r'<article.*?</article>', dialogue, re.S)[-1])

    def test_the_photo_is_a_link_without_a_script(self):
        CriticalIncident.objects.filter(pk=self.incident.pk).update(photo='photos/2026/01/01/x.jpg')
        report = section(self.html(), 'meldung-titel')
        self.assertIn('href="/media/photos/2026/01/01/x.jpg"', report)
        self.assertIn('target="_blank" rel="noopener"', report)

    def test_the_cost_in_queries_does_not_depend_on_the_number_of_replies(self):
        def queries():
            with CaptureQueriesContext(connection) as captured:
                self.assertEqual(self.get().status_code, 200)
            return len(captured)
        self.get()  # what the first request fills
        baker.make(Comment, critical_incident=self.incident, author=self.reviewer.user, text='One')
        few = queries()
        for number in range(20):
            baker.make(Comment, critical_incident=self.incident,
                       author=self.dept.reporter.user if number % 2 else self.reviewer.user, text='n')
        self.assertEqual(queries(), few)

    def test_the_texts_are_translatable(self):
        html = self.html()
        for text in ('Details of the report', 'Assessment', 'Dialogue with the reporting person',
                     'Publication', 'History', 'Back to the list', 'Save assessment', 'Send reply',
                     'Save publication', 'On this page'):
            self.assertIn(text, html)


class WayBackTest(PageCase):

    def back(self, query):
        match = re.search(r'<a class="ui-btn ui-btn--secondary" href="([^"]*)">Back to the list', self.html(query))
        return html_lib.unescape(match.group(1))

    def test_without_a_list_it_is_the_plain_list(self):
        self.assertEqual(self.back(''), worklist_url())

    def test_it_keeps_the_filters_of_the_list_it_came_from(self):
        values = {'stand': 'in process', 'risiko': 'high', 'wartet': True, 'sort': '-aktivitaet',
                  'page': 2, 'q': 'Etikett & Co'}
        self.assertEqual(self.back(incident_query(**values)), worklist_url(**values))

    def test_a_value_that_was_made_up_leads_nowhere_but_to_the_list(self):
        attacks = ('https://evil.example/', '//evil.example/', 'javascript:alert(1)',
                   'http://evil.example/?stand=new', 'stand=bogus&page=-1', 'q=%00', '"><b>x</b>',
                   '&'.join('a=%d' % n for n in range(400)))
        for attack in attacks:
            value = quote(attack, safe='')  # all of it is the value of the one parameter
            for query in ('?liste=' + value, '?liste=' + value + '&liste=' + value):
                response = self.get(query)
                self.assertEqual(response.status_code, 200, attack)
                html = response.content.decode()
                # the page, not the language switch of the top bar, which carries the address as asked
                main = re.search(r'<main.*?</main>', html, re.S).group(0)
                self.assertNotIn('evil', main)
                self.assertNotIn('<b>x</b>', html)
                link = re.search(r'href="([^"]*)">Back to the list', html).group(1)
                self.assertTrue(html_lib.unescape(link).startswith(reverse('qm_incidents')), link)

    def test_every_post_comes_back_to_the_same_list(self):
        query = incident_query(stand='new', page=3)
        for action, data in (('bewertung', REVIEW), ('antwort', {'text': 'Synthetic reply'}),
                             ('veroeffentlichung', CASE)):
            response = self.post(action, data, query)
            self.assertEqual(response.status_code, 302, action)
            self.assertEqual(response['Location'], self.url + query, action)
        # the page that follows carries it in its way back too
        self.assertEqual(self.back(query), worklist_url(stand='new', page=3))


class ReviewActionTest(PageCase):

    def test_a_valid_review_is_saved_and_the_page_says_so(self):
        response = self.post('bewertung', REVIEW, follow=True)
        self.assertRedirects(response, self.url)
        self.assertContains(response, 'The assessment has been saved.')
        self.assertRegex(response.content.decode(),
                         r'<div class="ui-alert ui-alert--success" role="status">')
        saved = self.stored()
        self.assertEqual((saved.status, saved.risk, saved.action, saved.category),
                         ('in process', 'high', 'Synthetic measure', ['other']))
        self.assertIn('In process', section(response.content.decode(), 'stand-titel'))

    def test_a_new_status_is_logged_once_and_the_reporting_person_gets_one_mail_with_the_status_only(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.post('bewertung', REVIEW)
        self.assertEqual(list(self.incident.status_changes.values_list('status', flat=True)),
                         ['new', 'in process'])
        message, = self.reporter_mails()
        self.assertEqual(message.subject, 'New status of your report')
        self.assertIn('In progress', message.body)
        for secret in ('Synthetic incident text', 'Synthetic reason', 'Synthetic measure'):
            self.assertNotIn(secret, message.body)
        self.assertEqual(len(mail.outbox), 1)

    def test_a_review_that_leaves_the_status_alone_logs_nothing_and_sends_nothing(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.post('bewertung', REVIEW)
        mail.outbox.clear()
        before = IncidentStatusChange.objects.count()
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.post('bewertung', dict(REVIEW, risk='low')).status_code, 302)
        self.assertEqual(self.stored().risk, 'low')
        self.assertEqual(IncidentStatusChange.objects.count(), before)
        self.assertEqual(mail.outbox, [])

    def test_the_same_review_twice_is_one_change_and_one_mail(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.post('bewertung', REVIEW)
            self.post('bewertung', REVIEW)
        self.assertEqual(self.incident.status_changes.count(), 2)
        self.assertEqual(len(self.reporter_mails()), 1)

    def test_a_review_that_is_not_in_order_shows_the_page_with_the_errors_and_saves_nothing(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.post('bewertung', dict(REVIEW, status='new'))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertEqual(html.count('role="alert"'), 1)  # the summary, nothing else
        self.assertIn('Please check your entries.', html)
        self.assertIn('<a href="#id_status">', html)
        self.assertRegex(html, r'<div class="ui-field__error" id="id_status_error">[^<]*in process')
        self.assertNotIn('The assessment has been saved.', html)
        # what the person typed is still in the form, and the page shows what is stored
        self.assertIn('<option value="high" selected>', html)
        self.assertIn('Synthetic measure', forms_of(html)[0])
        self.assertIn('<span class="ui-badge ui-badge--info">New</span>', section(html, 'stand-titel'))
        self.assertEqual(self.stored().risk, '')
        self.assertEqual(self.incident.status_changes.count(), 1)
        self.assertEqual(mail.outbox, [])

    def test_the_other_forms_are_fresh_after_an_error(self):
        html = self.post('bewertung', dict(REVIEW, status='new')).content.decode()
        self.assertEqual(html.count('Please check your entries.'), 1)
        self.assertEqual(len(forms_of(html)), 3)

    def test_the_parts_of_the_report_cannot_be_changed_through_this_form(self):
        self.post('bewertung', dict(REVIEW, incident='forged', reason='forged', public='False',
                                    comment_code='forged', preventability='not avoidable',
                                    reported='2000-01-01'))
        saved = self.stored()
        self.assertEqual((saved.incident, saved.reason, saved.preventability, saved.comment_code,
                          saved.reported, saved.public),
                         (self.incident.incident, self.incident.reason, 'avoidable',
                          self.incident.comment_code, date(2026, 9, 30), True))

    def test_the_place_is_offered_as_the_report_form_offers_it(self):
        unit = baker.make(OrgUnit, name='Synthetic ward')
        self.assertIn('>Synthetic ward</option>', self.html())
        self.post('bewertung', dict(REVIEW, org_unit=str(unit.pk)))
        self.assertEqual(self.stored().org_unit, unit)


class ReplyActionTest(PageCase):

    def test_a_reply_is_saved_by_the_reviewer_and_mailed_to_the_reporting_person(self):
        response = self.post('antwort', {'text': 'Synthetic reply'}, follow=True)
        self.assertRedirects(response, self.url)
        self.assertContains(response, 'Your reply has been saved and is the last one in the dialogue.')
        comment = self.comments().get()
        self.assertEqual((comment.author, comment.critical_incident), (self.reviewer.user, self.incident))
        message, = self.reporter_mails()
        self.assertEqual(message.subject, 'New reply to your report')
        self.assertIn('Synthetic reply', message.body)
        self.assertNotIn('Synthetic incident text', message.body)
        self.assertIn('Synthetic reply', section(response.content.decode(), 'rueckmeldungen-titel'))

    def test_an_empty_reply_shows_the_page_with_the_error_and_sends_nothing(self):
        response = self.post('antwort', {'text': ''})
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('<a href="#id_text">', html)
        self.assertIn('id="id_text_error"', html)
        self.assertEqual(self.comments().count(), 0)
        self.assertEqual(mail.outbox, [])

class PublicationActionTest(PageCase):

    def case(self):
        return self.cases().get()

    def test_a_case_is_made_and_published_from_the_page_and_the_staff_see_it(self):
        response = self.post('veroeffentlichung', dict(CASE, publish='on'), follow=True)
        self.assertRedirects(response, self.url)
        self.assertContains(response, 'The publication has been saved. The case is now published.')
        self.assertTrue(self.case().publish)
        listed = self.client.get(reverse('incidents_for_department', kwargs={'dept': self.dept.label}), **EN)
        self.assertContains(listed, 'Title')
        # the link shows how the staff see it
        self.assertIn('href="%s">This is how the staff see it' % reverse(
            'incidents_for_department', kwargs={'dept': self.dept.label}), response.content.decode())

    def test_a_draft_is_saved_without_publishing_and_says_so(self):
        response = self.post('veroeffentlichung', {'en-incident': 'Title'}, follow=True)
        self.assertContains(response, 'The publication has been saved. The case is not published.')
        self.assertFalse(self.case().publish)
        self.assertNotIn('This is how the staff see it', response.content.decode())
        self.assertIn('Mandatory languages incomplete', section(response.content.decode(), 'veroeffentlichung-titel'))

    def test_publishing_with_a_text_missing_is_refused_with_the_error_at_the_switch(self):
        response = self.post('veroeffentlichung', dict(CASE, publish='on', **{'de-description': ''}))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('<a href="#id_publish">Publish</a>', html)
        self.assertRegex(html, r'id="id_publish_error">[^<]*mandatory languages')
        self.assertFalse(self.cases().exists())

    def test_a_language_with_a_text_but_no_title_is_refused_at_the_field(self):
        html = self.post('veroeffentlichung', {'de-description': 'Only a text'}).content.decode()
        self.assertIn('<a href="#id_de-incident">', html)
        self.assertFalse(self.cases().exists())

    def test_a_report_that_was_not_approved_for_publication_offers_no_form_and_gets_no_case(self):
        CriticalIncident.objects.filter(pk=self.incident.pk).update(public=False)
        html = self.html()
        self.assertIn('did not agree to the publication', words(section(html, 'veroeffentlichung-titel')))
        self.assertNotIn('name="aktion" value="veroeffentlichung"', html)
        response = self.post('veroeffentlichung', dict(CASE, publish='on'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.cases().exists())

    def test_a_case_that_is_taken_back_keeps_its_texts(self):
        self.post('veroeffentlichung', dict(CASE, publish='on'))
        self.post('veroeffentlichung', CASE)
        self.assertFalse(self.case().publish)
        self.assertEqual(self.case().translations.count(), 2)

    def test_the_form_shows_the_stored_texts_and_the_languages(self):
        self.post('veroeffentlichung', dict(CASE, publish='on'))
        html = section(self.html(), 'veroeffentlichung-titel')
        self.assertRegex(html, r'>\s*Massnahmen</textarea>')
        self.assertIn('English (mandatory language)', html)
        self.assertIn('Deutsch (mandatory language)', html)
        self.assertRegex(html, r'type="checkbox" name="publish"[^>]*checked')
        self.assertIn('Published cases are visible to every visitor', words(html))

    def test_the_link_to_the_staff_view_is_not_offered_for_a_department_that_takes_no_reports(self):
        self.post('veroeffentlichung', dict(CASE, publish='on'))
        self.dept.active = False
        self.dept.save()
        self.assertNotIn('This is how the staff see it', self.html())


class HistoryTest(PageCase):

    def rows(self, html):
        return [(words(cells[0]), cells[1]) for cells in
                (re.findall(r'<td[^>]*>(.*?)</td>', row, re.S) for row in
                 re.findall(r'<tr>(.*?)</tr>', section(html, 'verlauf-titel'), re.S)[1:])]

    def test_the_report_has_its_day_and_no_time_anywhere_on_the_page(self):
        html = self.html()
        self.assertNotRegex(html, r'\b0?1[:.]15\b')
        self.assertNotIn('1:15', html)
        rows = self.rows(html)
        self.assertEqual(rows[0][0], 'New')
        self.assertEqual(rows[0][1], '<time datetime="2026-09-30">09/30/2026</time>')

    def test_a_change_of_the_qm_keeps_its_time(self):
        incident = make_incident(self.dept, history=[
            ('new', datetime(2026, 9, 1, 3, 12, tzinfo=VIENNA)),
            ('in process', datetime(2026, 9, 2, 14, 40, tzinfo=VIENNA))])
        html = self.client.get(incident.get_absolute_url(), **EN).content.decode()
        self.assertNotRegex(html, r'\b0?3[:.]12\b')
        self.assertRegex(self.rows(html)[1][1], r'<time datetime="2026-09-02T14:40:00\+02:00">[^<]*2:40')

    def test_an_incident_from_before_the_log_says_that_nothing_is_recorded(self):
        incident = make_incident(self.dept, legacy=True)
        html = self.client.get(incident.get_absolute_url(), **EN).content.decode()
        self.assertIn('No status change is recorded for this report.', html)
        self.assertNotIn('<table', section(html, 'verlauf-titel'))


class SecretsTest(PageCase):
    """The code and the address of the reporting person, the one fact that ties a report to a
    person, are on no page of the QM: not in the text, an attribute, a link or a title, not in the
    page after an error, and the foreign department is not there either."""

    def secrets(self):
        out = []
        for incident, email in ((self.incident, ADDRESS),
                                (self.canary_incidents[0], 'fremd.kanarienvogel@example.org')):
            code = incident.comment_code
            out += [code, code.upper(), group_code(code), group_code(code).upper(), email,
                    email.split('@')[0]]
        return [secret.lower() for secret in out]

    def assertClean(self, response, what):
        text = response.content.decode().lower() + str(response.headers).lower()
        for secret in self.secrets():
            self.assertNotIn(secret, text, '%s: %s' % (what, secret))
        # the units of places are shared by all departments, the select of the places is theirs
        page = re.sub(r'<select[^>]*name="org_unit".*?</select>', '', response.content.decode(), flags=re.S)
        self.assertNoCanary(page, what)

    def test_no_state_of_the_page_shows_them(self):
        self.assertClean(self.get(), 'plain')
        self.assertClean(self.get(incident_query(stand='new', q='x', page=2)), 'with a list')
        for action, data in (('bewertung', dict(REVIEW, status='new')), ('antwort', {'text': ''}),
                             ('veroeffentlichung', {'de-description': 'Only a text'})):
            response = self.post(action, data)
            self.assertEqual(response.status_code, 200, action)
            self.assertClean(response, 'error of ' + action)
        for action, data in (('bewertung', REVIEW), ('antwort', {'text': 'Synthetic reply'}),
                             ('veroeffentlichung', dict(CASE, publish='on'))):
            self.assertClean(self.post(action, data, follow=True), 'after ' + action)

    def test_the_pages_that_turn_somebody_away_show_nothing_either(self):
        self.reviewer_without('change_criticalincident')
        self.assertClean(self.post('bewertung', REVIEW), 'forbidden')
        outsider = baker.make_recipe('cirs.reviewer')
        baker.make_recipe('cirs.department').reviewers.add(outsider)
        self.client.force_login(outsider.user)
        self.assertClean(self.get(), 'outsider')
        self.assertClean(self.get(follow=True), 'outsider followed')

    def test_the_check_can_fail(self):
        class Fake:
            content = ('<a title="%s">' % self.incident.comment_code).encode()
            headers = {}
        with self.assertRaises(AssertionError):
            self.assertClean(Fake(), 'a code in an attribute')
        Fake.content = ('<a href="mailto:%s">' % ADDRESS).encode()
        with self.assertRaises(AssertionError):
            self.assertClean(Fake(), 'an address in a link')


class GermanTest(PageCase):
    """The texts of the page in German, in the form of address that the QM is spoken to."""

    DE = {'HTTP_ACCEPT_LANGUAGE': 'de'}

    def page(self, response=None):
        response = response or self.client.get(self.url, **self.DE)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_the_headings_buttons_and_hints(self):
        html = self.page()
        self.assertEqual(re.findall(r'<h1[^>]*>(.*?)</h1>', html, re.S), ['Meldung %d' % self.incident.pk])
        self.assertRegex(html, r'<title>Meldung %d · ' % self.incident.pk)
        text = words(re.search(r'<main.*?</main>', html, re.S).group(0))
        for expected in ('Zurück zur Liste', 'Angaben zur Meldung', 'Bewertung',
                         'Dialog mit der meldenden Person', 'Veröffentlichung', 'Verlauf',
                         'Bewertung speichern', 'Antwort senden', 'Veröffentlichung speichern',
                         'Einordnung', 'Maßnahme', 'Pflichtsprache', 'Neu',
                         'Veröffentlichte Fälle sind für alle Besucher der Seite sichtbar'):
            self.assertIn(expected, text)
        self.assertNotRegex(text, DU_FORM)
        self.assertIn('aria-label="Auf dieser Seite"', html)
        self.assertNotIn('Geschichte', html)  # the word of the admin for the same English one

    def test_the_messages_after_saving(self):
        with self.captureOnCommitCallbacks(execute=True):
            for action, data, expected in (
                    ('bewertung', REVIEW, 'Die Bewertung wurde gespeichert.'),
                    ('antwort', {'text': 'Synthetische Antwort'},
                     'Ihre Antwort wurde gespeichert und steht am Ende des Dialogs.'),
                    ('veroeffentlichung', dict(CASE, publish='on'),
                     'Die Veröffentlichung wurde gespeichert. Der Fall ist jetzt veröffentlicht.')):
                response = self.client.post(self.url, {'aktion': action, **data}, follow=True, **self.DE)
                self.assertContains(response, expected)

    def test_the_summary_of_the_errors(self):
        response = self.client.post(self.url, {'aktion': 'bewertung', **dict(REVIEW, status='new')}, **self.DE)
        html = self.page(response)
        self.assertIn('Bitte prüfen Sie Ihre Eingaben.', html)
        self.assertIn('<a href="#id_status">Stand</a>', html)

    def test_the_history_has_the_day_of_the_report_and_no_time(self):
        html = self.page()
        rows = re.findall(r'<td[^>]*>(.*?)</td>', section(html, 'verlauf-titel'), re.S)
        self.assertEqual(rows, ['Neu', '<time datetime="2026-09-30">30.09.2026</time>'])

