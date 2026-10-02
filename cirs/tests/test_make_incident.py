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

"""The test helper that builds an incident with a status log of known times."""

from datetime import date, datetime, timezone as dt_timezone

from django.test import TestCase
from model_bakery import baker

from cirs.models import CriticalIncident
from cirs.tests.helpers import make_incident


def at(day, hour=12):
    return datetime(2026, 3, day, hour, 0, tzinfo=dt_timezone.utc)


def log(incident):
    return list(incident.status_changes.values_list('status', 'changed_at'))


class MakeIncidentTest(TestCase):

    def setUp(self):
        self.dept = baker.make_recipe('cirs.department')

    def test_without_history_the_incident_is_new_and_logged_once(self):
        incident = make_incident(self.dept)
        self.assertEqual(incident.department, self.dept)
        self.assertEqual(incident.status, 'new')
        self.assertEqual(incident.reported, date.today())
        self.assertEqual([status for status, _ in log(incident)], ['new'])

    def test_reported_and_other_fields_are_passed_on(self):
        incident = make_incident(self.dept, reported=date(2026, 2, 3), incident='Synthetic text',
                                 status='in process')
        saved = CriticalIncident.objects.get(pk=incident.pk)
        self.assertEqual((saved.reported, saved.incident, saved.status),
                         (date(2026, 2, 3), 'Synthetic text', 'in process'))

    def test_history_gives_status_and_time_of_every_entry(self):
        history = [('new', at(1)), ('in process', at(5, 9)), ('completed', at(9, 23))]
        incident = make_incident(self.dept, history=history)
        self.assertEqual(log(incident), history)
        self.assertEqual(CriticalIncident.objects.get(pk=incident.pk).status, 'completed')

    def test_history_may_start_with_another_status_than_new(self):
        history = [('in process', at(1)), ('under supervision', at(2))]
        incident = make_incident(self.dept, history=history)
        self.assertEqual(log(incident), history)

    def test_history_may_reopen_an_incident(self):
        history = [('new', at(1)), ('completed', at(2)), ('in process', at(3)),
                   ('completed', at(4))]
        self.assertEqual(log(make_incident(self.dept, history=history)), history)

    def test_entries_with_the_same_time_are_all_kept(self):
        history = [('new', at(1)), ('in process', at(1)), ('completed', at(1))]
        incident = make_incident(self.dept, history=history)
        self.assertEqual(log(incident), history)

    def test_legacy_incident_has_no_entry(self):
        incident = make_incident(self.dept, status='in process', legacy=True)
        self.assertEqual(log(incident), [])
        self.assertEqual(CriticalIncident.objects.get(pk=incident.pk).status, 'in process')

    def test_legacy_incident_keeps_the_entries_of_later_changes(self):
        incident = make_incident(
            self.dept, legacy=True,
            history=[('in process', at(1)), ('completed', at(7, 8))])
        self.assertEqual(log(incident), [('completed', at(7, 8))])

    def test_other_incidents_are_untouched(self):
        other = make_incident(self.dept)
        make_incident(self.dept, history=[('new', at(1)), ('completed', at(2))], legacy=True)
        self.assertEqual([status for status, _ in log(other)], ['new'])

    def test_comments_get_their_author_and_day(self):
        author = baker.make('auth.User')
        incident = make_incident(self.dept, comments=[(author, date(2026, 3, 4)),
                                                      (author, date(2026, 3, 4))])
        comments = incident.comments.order_by('pk')
        self.assertEqual([(c.author, c.created) for c in comments],
                         [(author, date(2026, 3, 4))] * 2)

    def test_status_and_history_together_are_refused(self):
        with self.assertRaises(TypeError):
            make_incident(self.dept, status='new', history=[('new', at(1))])

    def test_history_without_a_change_is_refused(self):
        with self.assertRaises(ValueError):
            make_incident(self.dept, history=[('new', at(1)), ('new', at(2))])
