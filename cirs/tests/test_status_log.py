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

"""The log of status changes of an incident: written by save(), shown read-only in the admin."""

import re
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from django.db import IntegrityError, connection, transaction
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone, translation
from model_bakery import baker

from cirs.admin import admin_site
from cirs.models import CriticalIncident, IncidentStatusChange
from cirs.status_log import log_rows, shows_time
from cirs.tests.helpers import make_incident

VIENNA = ZoneInfo('Europe/Vienna')


def logged(incident):
    return list(incident.status_changes.values_list('status', flat=True))


def cells(html, field):
    """The texts of the cells of one column in the status log of the admin page."""
    log = html.partition('id="status_changes-group"')[2]
    found = re.findall(r'<td class="field-%s">\s*<p>(.*?)</p>' % field, log, re.S)
    return [re.sub(r'\s+', ' ', text).strip() for text in found]


class StatusLogTest(TestCase):

    def setUp(self):
        self.dept = baker.make_recipe('cirs.department')

    def incident(self, **kwargs):
        return baker.make_recipe('cirs.public_ci', department=self.dept, **kwargs)

    def test_new_incident_gets_one_entry_with_its_initial_status(self):
        before = timezone.now()
        incident = self.incident()
        self.assertEqual(logged(incident), ['new'])
        self.assertTrue(before <= incident.status_changes.get().changed_at <= timezone.now())

    def test_initial_status_is_not_always_new(self):
        self.assertEqual(logged(self.incident(status='in process')), ['in process'])

    def test_each_change_adds_an_entry_in_order(self):
        incident = self.incident()
        for status in ('in process', 'completed'):
            incident.status = status
            incident.save()
        self.assertEqual(logged(incident), ['new', 'in process', 'completed'])

    def test_change_of_a_reloaded_incident_is_logged(self):
        incident = self.incident()
        reloaded = CriticalIncident.objects.get(pk=incident.pk)
        reloaded.status = 'under supervision'
        reloaded.save()
        self.assertEqual(logged(incident), ['new', 'under supervision'])

    def test_save_without_change_adds_nothing(self):
        incident = self.incident()
        incident.save()
        incident.action = 'Synthetic action'
        incident.save()
        reloaded = CriticalIncident.objects.get(pk=incident.pk)
        reloaded.save()
        reloaded.status = 'in process'
        reloaded.save()
        reloaded.save()
        self.assertEqual(logged(incident), ['new', 'in process'])

    def test_save_that_leaves_the_status_alone_adds_nothing(self):
        incident = self.incident()
        only = CriticalIncident.objects.only('incident').get(pk=incident.pk)
        only.save(update_fields=['incident'])
        self.assertEqual(logged(incident), ['new'])

    def test_save_of_other_fields_does_not_log_a_status_it_did_not_store(self):
        incident = self.incident()
        loaded = CriticalIncident.objects.get(pk=incident.pk)
        loaded.status = 'in process'
        with self.captureOnCommitCallbacks() as callbacks:
            loaded.save(update_fields=['action'])
        self.assertEqual(callbacks, [])  # nothing to tell the reporter either
        self.assertEqual(logged(incident), ['new'])
        self.assertEqual(CriticalIncident.objects.get(pk=incident.pk).status, 'new')
        # the change is still open: the save that stores it logs it
        loaded.save()
        self.assertEqual(logged(incident), ['new', 'in process'])

    def test_save_of_the_status_alone_logs_it(self):
        incident = self.incident()
        loaded = CriticalIncident.objects.get(pk=incident.pk)
        loaded.status = 'completed'
        with self.captureOnCommitCallbacks() as callbacks:
            loaded.save(update_fields=['status'])
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(logged(incident), ['new', 'completed'])

    def test_save_of_an_incident_loaded_by_its_id_only_logs_nothing(self):
        incident = self.incident()
        only = CriticalIncident.objects.only('id').get(pk=incident.pk)
        with self.captureOnCommitCallbacks() as callbacks:
            only.save()
        self.assertEqual(callbacks, [])
        self.assertNotIn('status', only.__dict__)  # not read just to be remembered
        self.assertEqual(logged(incident), ['new'])

    def test_incident_without_entries_gets_one_when_its_status_changes(self):
        incident = make_incident(self.dept, status='in process', legacy=True)
        self.assertEqual(logged(incident), [])
        reloaded = CriticalIncident.objects.get(pk=incident.pk)
        reloaded.save()
        self.assertEqual(logged(incident), [])
        reloaded.status = 'completed'
        reloaded.save()
        self.assertEqual(logged(incident), ['completed'])

    def test_rolled_back_save_leaves_no_entry(self):
        incident = self.incident()
        try:
            with transaction.atomic():
                incident.status = 'in process'
                incident.save()
                self.assertEqual(logged(incident), ['new', 'in process'])
                raise RuntimeError('roll back')
        except RuntimeError:
            pass
        self.assertEqual(logged(incident), ['new'])
        self.assertEqual(CriticalIncident.objects.get(pk=incident.pk).status, 'new')

    def test_rolled_back_creation_leaves_no_entry(self):
        try:
            with transaction.atomic():
                self.incident()
                raise RuntimeError('roll back')
        except RuntimeError:
            pass
        self.assertEqual(IncidentStatusChange.objects.count(), 0)

    def test_failing_log_entry_does_not_keep_the_status_change(self):
        # The status and its entry are stored together or not at all.
        incident = self.incident()
        incident.status = 'in process'
        with mock.patch.object(IncidentStatusChange, 'save', side_effect=IntegrityError('x')):
            with self.assertRaises(IntegrityError):
                incident.save()
        self.assertEqual(CriticalIncident.objects.get(pk=incident.pk).status, 'new')
        self.assertEqual(logged(incident), ['new'])

    def test_entries_are_ordered_by_time(self):
        history = [('new', datetime(2026, 3, 1, 8, tzinfo=VIENNA)),
                   ('in process', datetime(2026, 3, 9, 8, tzinfo=VIENNA)),
                   ('completed', datetime(2026, 3, 5, 8, tzinfo=VIENNA))]
        incident = make_incident(self.dept, history=history)
        self.assertEqual(logged(incident), ['new', 'completed', 'in process'])

    def test_entries_with_the_same_time_are_ordered_by_creation(self):
        self.assertEqual(IncidentStatusChange._meta.ordering, ['changed_at', 'id'])
        moment = datetime(2026, 3, 1, 8, tzinfo=VIENNA)
        history = [('new', moment), ('in process', moment), ('completed', moment),
                   ('in process', moment), ('completed', moment)]
        incident = make_incident(self.dept, history=history)
        self.assertEqual(logged(incident),
                         ['new', 'in process', 'completed', 'in process', 'completed'])

    def test_entries_go_with_the_incident(self):
        incident = self.incident()
        incident.delete()
        self.assertEqual(IncidentStatusChange.objects.count(), 0)

    def test_other_incidents_are_not_mixed_up(self):
        first, second = self.incident(), self.incident()
        first.status = 'completed'
        first.save()
        self.assertEqual(logged(first), ['new', 'completed'])
        self.assertEqual(logged(second), ['new'])


@override_settings(TIME_ZONE='Europe/Vienna')
class StatusLogAdminTest(TestCase):

    def setUp(self):
        self.dept = baker.make_recipe('cirs.department')
        self.reviewer = baker.make_recipe('cirs.reviewer')
        self.dept.reviewers.add(self.reviewer)
        self.incident = make_incident(self.dept, history=[
            ('new', datetime(2026, 1, 5, 9, 15, tzinfo=VIENNA)),
            ('in process', datetime(2026, 1, 12, 14, 40, tzinfo=VIENNA))])
        self.url = reverse('admin:cirs_criticalincident_change', args=[self.incident.pk])
        self.client.force_login(self.reviewer.user)

    def form_data(self, **extra):
        data = {'status': 'in process', 'review_date': '', 'org_unit': '', 'risk': '',
                'frequency': '', 'hazard': '', 'responsibilty': '', 'action': ''}
        for prefix in ('publishableincident', 'comments', 'status_changes'):
            data.update({prefix + '-TOTAL_FORMS': '0', prefix + '-INITIAL_FORMS': '0',
                         prefix + '-MIN_NUM_FORMS': '0', prefix + '-MAX_NUM_FORMS': '1000'})
        data.update(extra)
        return data

    def test_page_shows_the_log_in_german(self):
        html = self.client.get(self.url, HTTP_ACCEPT_LANGUAGE='de').content.decode()
        self.assertRegex(html, r'<h2[^>]*>\s*Statuswechsel\s*</h2>')
        self.assertEqual(cells(html, 'status'), ['neu', 'in Bearbeitung'])
        # the report itself has its day, the change of the QM keeps its time
        self.assertEqual(cells(html, 'moment'), ['5. Januar 2026', '12. Januar 2026 14:40'])
        self.assertRegex(html, r'<th[^>]*class="column-moment[^"]*"[^>]*>\s*Geändert am')

    def test_page_shows_the_log_in_english(self):
        html = self.client.get(self.url, HTTP_ACCEPT_LANGUAGE='en').content.decode()
        self.assertRegex(html, r'<h2[^>]*>\s*Status changes\s*</h2>')
        self.assertEqual(cells(html, 'status'), ['new', 'in process'])
        self.assertEqual(cells(html, 'moment'), ['Jan. 5, 2026', 'Jan. 12, 2026, 2:40 p.m.'])
        self.assertRegex(html, r'<th[^>]*class="column-moment[^"]*"[^>]*>\s*Changed at')

    def test_page_of_an_incident_without_entries_shows_an_empty_log(self):
        old = make_incident(self.dept, status='in process', legacy=True)
        url = reverse('admin:cirs_criticalincident_change', args=[old.pk])
        response = self.client.get(url, HTTP_ACCEPT_LANGUAGE='de')
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertRegex(html, r'<h2[^>]*>\s*Statuswechsel\s*</h2>')
        self.assertEqual(cells(html, 'status'), [])
        self.assertEqual(cells(html, 'moment'), [])

    def test_saving_an_incident_without_entries_works(self):
        old = make_incident(self.dept, status='in process', legacy=True)
        url = reverse('admin:cirs_criticalincident_change', args=[old.pk])
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(url, self.form_data(responsibilty='Ward'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(logged(old), [])
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(url, self.form_data(status='completed'))
        self.assertEqual(logged(old), ['completed'])

    def test_log_is_read_only_and_cannot_grow(self):
        response = self.client.get(self.url)
        formset = next(inline.formset for inline in response.context['inline_admin_formsets']
                       if inline.formset.model is IncidentStatusChange)
        self.assertEqual(len(formset.forms), 2)
        self.assertFalse(formset.can_delete)
        html = response.content.decode()
        # no row to add (the limit is 0), nothing to edit or remove
        self.assertIn('name="status_changes-MAX_NUM_FORMS" value="0"', html)
        self.assertNotIn('status_changes-0-status', html)
        self.assertNotIn('status_changes-0-DELETE', html)

    def test_forged_log_forms_are_ignored(self):
        first, second = self.incident.status_changes.all()
        data = self.form_data(**{
            'status_changes-TOTAL_FORMS': '3', 'status_changes-INITIAL_FORMS': '2',
            'status_changes-0-id': first.pk, 'status_changes-0-incident': self.incident.pk,
            'status_changes-0-status': 'completed', 'status_changes-0-DELETE': 'on',
            'status_changes-1-id': second.pk, 'status_changes-1-incident': self.incident.pk,
            'status_changes-2-incident': self.incident.pk, 'status_changes-2-status': 'completed'})
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(logged(self.incident), ['new', 'in process'])

    def test_log_has_no_admin_pages_of_its_own(self):
        self.assertFalse(admin_site.is_registered(IncidentStatusChange))

    def test_status_change_in_the_admin_is_logged(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.url, self.form_data(status='under supervision'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(logged(self.incident), ['new', 'in process', 'under supervision'])

    def test_admin_save_without_status_change_adds_nothing(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.url, self.form_data(action='', responsibilty='Ward'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(logged(self.incident), ['new', 'in process'])

    def test_the_time_of_the_report_is_not_on_the_page_in_any_language(self):
        # a report written at three in the morning, on a ward: the minute would say who was there
        early = make_incident(self.dept, history=[
            ('new', datetime(2026, 1, 5, 3, 12, 47, tzinfo=VIENNA)),
            ('in process', datetime(2026, 1, 6, 8, 5, tzinfo=VIENNA))])
        url = reverse('admin:cirs_criticalincident_change', args=[early.pk])
        for language in ('de', 'en'):
            html = self.client.get(url, HTTP_ACCEPT_LANGUAGE=language).content.decode()
            self.assertNotRegex(html, r'\b0?3[:.]12\b', language)
            self.assertNotIn('3:12', html, language)
            self.assertNotIn('47', ''.join(cells(html, 'moment')), language)
            self.assertIn('8:05' if language == 'en' else '08:05',
                          ''.join(cells(html, 'moment')), language)  # the QM's own change has it

    def test_the_day_of_the_report_is_the_day_of_the_clock_of_the_installation(self):
        # late in the evening in UTC is the next day in Vienna
        late = make_incident(self.dept, history=[
            ('new', datetime(2026, 1, 5, 23, 30, tzinfo=ZoneInfo('UTC')))])
        url = reverse('admin:cirs_criticalincident_change', args=[late.pk])
        html = self.client.get(url, HTTP_ACCEPT_LANGUAGE='de').content.decode()
        self.assertEqual(cells(html, 'moment'), ['6. Januar 2026'])

    def test_a_first_entry_that_is_a_change_of_the_qm_keeps_its_time(self):
        # an incident from before the log: its first entry is a change that somebody made
        old = make_incident(self.dept, legacy=True, history=[
            ('in process', datetime(2026, 1, 3, 10, 0, tzinfo=VIENNA)),
            ('completed', datetime(2026, 1, 9, 16, 20, tzinfo=VIENNA))])
        url = reverse('admin:cirs_criticalincident_change', args=[old.pk])
        html = self.client.get(url, HTTP_ACCEPT_LANGUAGE='de').content.decode()
        self.assertEqual(cells(html, 'moment'), ['9. Januar 2026 16:20'])

    def test_the_log_costs_the_same_queries_for_few_and_many_entries(self):
        def queries(incident):
            url = reverse('admin:cirs_criticalincident_change', args=[incident.pk])
            with CaptureQueriesContext(connection) as captured:
                self.assertEqual(self.client.get(url).status_code, 200)
            return len(captured)

        few = queries(self.incident)
        many = make_incident(self.dept, history=[('new', datetime(2026, 1, 1, 8, tzinfo=VIENNA))]
                             + [(status, datetime(2026, 1, day, 8, tzinfo=VIENNA))
                                for day, status in zip(range(2, 12), ['in process', 'completed'] * 5)])
        self.assertEqual(queries(many), few)


class LogRowsTest(TestCase):
    """What the pages show of the log: the entry of the report has its day only."""

    def setUp(self):
        # the names of the statuses are translated: the language that an earlier test left active
        # in the same process must not decide
        self.enterContext(translation.override('en'))
        self.dept = baker.make_recipe('cirs.department')

    def rows(self, **kwargs):
        incident = make_incident(self.dept, **kwargs)
        return log_rows(incident.status_changes.all())

    def test_the_first_entry_that_says_new_is_the_report_and_has_no_time(self):
        history = [('new', datetime(2026, 1, 5, 3, 12, tzinfo=VIENNA)),
                   ('in process', datetime(2026, 1, 5, 3, 40, tzinfo=VIENNA)),
                   ('completed', datetime(2026, 1, 9, 16, 20, tzinfo=VIENNA))]
        rows = self.rows(history=history)
        self.assertEqual([(str(row.status), row.moment, row.with_time) for row in rows],
                         [('new', history[0][1], False), ('in process', history[1][1], True),
                          ('completed', history[2][1], True)])

    def test_a_status_that_is_new_again_later_was_set_by_the_qm_and_keeps_its_time(self):
        history = [('new', datetime(2026, 1, 5, 3, 12, tzinfo=VIENNA)),
                   ('completed', datetime(2026, 1, 6, 9, tzinfo=VIENNA)),
                   ('new', datetime(2026, 1, 7, 9, 30, tzinfo=VIENNA))]
        self.assertEqual([row.with_time for row in self.rows(history=history)],
                         [False, True, True])

    def test_a_first_entry_that_does_not_say_new_is_no_report(self):
        history = [('in process', datetime(2026, 1, 5, 3, 12, tzinfo=VIENNA))]
        self.assertEqual([row.with_time for row in self.rows(history=history)], [True])

    def test_entries_with_the_same_time_keep_the_order_of_their_creation(self):
        moment = datetime(2026, 1, 5, 3, 12, tzinfo=VIENNA)
        rows = self.rows(history=[('new', moment), ('in process', moment), ('completed', moment)])
        self.assertEqual([row.with_time for row in rows], [False, True, True])

    def test_an_incident_from_before_the_log_has_no_rows(self):
        self.assertEqual(self.rows(legacy=True), [])

    def test_the_rule_is_one_function(self):
        self.assertFalse(shows_time('new', 7, 7))
        self.assertTrue(shows_time('new', 8, 7))
        self.assertTrue(shows_time('in process', 7, 7))
        self.assertTrue(shows_time('new', 7, None))
