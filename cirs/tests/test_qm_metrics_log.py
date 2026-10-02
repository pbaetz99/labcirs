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

"""The numbers of the QM pages that read the status log and the comments: when incidents were
completed, how the months add up, which incidents are late or wait for the QM. Test data is
constructed, never taken from a real report."""

from datetime import date, datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from django.db.models import QuerySet
from django.test import override_settings

from cirs.models import CriticalIncident
from cirs.qm.metrics import (MonthRow, awaiting_qm, completed, monthly, overdue,
                             protocol_start)
from cirs.tests.helpers import create_user
from cirs.tests.test_qm_metrics import FEB, JAN, WHOLE, DataTestCase

VIENNA = ZoneInfo('Europe/Vienna')
HOUR = timedelta(hours=1)
LONG_AGO = date(2000, 1, 1)  # reported before every period looked at


def utc(*moment):
    return datetime(*moment, tzinfo=dt_timezone.utc)


def vienna(*moment):
    return datetime(*moment, tzinfo=VIENNA)


class LogTestCase(DataTestCase):

    def done(self, at, reported=LONG_AGO, **fields):
        """An incident that is completed at the moment `at`."""
        return self.incident(reported, history=[('new', at - HOUR), ('completed', at)], **fields)

    def start_log(self, day):
        """The log begins on `day`: an old incident that is logged then (it is in no period)."""
        return self.incident(LONG_AGO, history=[('new', utc(day.year, day.month, day.day, 9))])


class ProtocolStartTest(LogTestCase):

    def test_it_is_the_day_of_the_oldest_entry(self):
        self.incident(date(2026, 3, 1), history=[('new', utc(2026, 3, 5, 9)),
                                                 ('in process', utc(2026, 3, 9, 9))])
        self.incident(date(2026, 1, 1), history=[('new', utc(2026, 2, 20, 9))])
        self.assertEqual(protocol_start(self.scope), date(2026, 2, 20))

    def test_without_a_log_there_is_no_start(self):
        self.assertIsNone(protocol_start(self.scope))
        self.incident(date(2026, 3, 1), legacy=True)
        self.assertIsNone(protocol_start(self.scope))

    @override_settings(TIME_ZONE='Europe/Vienna')
    def test_the_day_is_the_one_of_the_time_zone(self):
        # 31 January 23:30 UTC is 1 February 00:30 in Vienna
        self.incident(date(2026, 1, 31), history=[('new', utc(2026, 1, 31, 23, 30))])
        self.assertEqual(protocol_start(self.scope), date(2026, 2, 1))

    def test_other_departments_do_not_set_the_start(self):
        self.foreign(date(2026, 1, 1), history=[('new', utc(2026, 1, 2, 9))])
        self.assertIsNone(protocol_start(self.scope))
        self.incident(date(2026, 3, 1), history=[('new', utc(2026, 3, 5, 9))])
        self.assertEqual(protocol_start(self.scope), date(2026, 3, 5))

    def test_one_query_for_one_and_for_many_incidents(self):
        self.incident(date(2026, 2, 1), history=[('new', utc(2026, 2, 1, 9))])
        with self.assertNumQueries(1):
            protocol_start(self.scope)
        for day in range(2, 29):
            self.incident(date(2026, 2, 1), history=[('new', utc(2026, 2, day, 9))])
        with self.assertNumQueries(1):
            protocol_start(self.scope)


class CompletedTest(LogTestCase):

    def test_both_days_of_the_period_count(self):
        for at in (utc(2026, 1, 31, 12), utc(2026, 2, 1, 12), utc(2026, 2, 28, 12),
                   utc(2026, 3, 1, 12)):
            self.done(at)
        self.assertEqual(completed(self.scope, *FEB), 2)
        self.assertEqual(completed(self.scope, *JAN), 1)
        self.assertEqual(completed(self.scope, *WHOLE), 4)

    def test_the_first_and_the_last_second_of_a_period(self):
        self.done(utc(2026, 2, 1, 0, 0, 0))
        self.done(utc(2026, 2, 28, 23, 59, 59))
        self.done(utc(2026, 3, 1, 0, 0, 0))
        self.done(utc(2026, 1, 31, 23, 59, 59))
        self.assertEqual(completed(self.scope, *FEB), 2)

    def test_the_day_of_the_report_does_not_matter(self):
        self.done(utc(2026, 2, 10, 12), reported=date(2025, 5, 5))
        self.assertEqual(completed(self.scope, *FEB), 1)

    def test_a_period_that_ends_before_it_starts_is_empty(self):
        self.done(utc(2026, 2, 10, 12))
        self.assertEqual(completed(self.scope, date(2026, 2, 28), date(2026, 2, 1)), 0)

    def test_without_incidents_the_number_is_0(self):
        self.assertEqual(completed(self.scope, *WHOLE), 0)

    def test_an_incident_that_is_not_completed_does_not_count(self):
        self.incident(date(2026, 2, 1), history=[('new', utc(2026, 2, 1, 9)),
                                                 ('in process', utc(2026, 2, 5, 9))])
        self.assertEqual(completed(self.scope, *FEB), 0)

    def test_an_incident_that_was_completed_twice_counts_once_with_its_last_completion(self):
        self.incident(date(2026, 1, 1), history=[('new', utc(2026, 1, 2, 9)),
                                                 ('completed', utc(2026, 1, 10, 9)),
                                                 ('in process', utc(2026, 1, 20, 9)),
                                                 ('completed', utc(2026, 2, 5, 9))])
        self.assertEqual(completed(self.scope, *JAN), 0)  # not the first completion any more
        self.assertEqual(completed(self.scope, *FEB), 1)
        self.assertEqual(completed(self.scope, *WHOLE), 1)  # once, though it was completed twice

    def test_an_incident_that_was_reopened_and_is_not_completed_again_does_not_count(self):
        self.incident(date(2026, 1, 1), history=[('new', utc(2026, 1, 2, 9)),
                                                 ('completed', utc(2026, 1, 10, 9)),
                                                 ('in process', utc(2026, 1, 20, 9))])
        self.assertEqual(completed(self.scope, *WHOLE), 0)

    def test_an_incident_without_log_does_not_count(self):
        self.incident(date(2026, 2, 1), status='completed', legacy=True)
        self.assertEqual(completed(self.scope, *WHOLE), 0)

    def test_incidents_of_another_department_never_count(self):
        self.done(utc(2026, 2, 10, 12))
        for day in range(1, 5):
            self.foreign(LONG_AGO, history=[('new', utc(2026, 2, day, 9)),
                                            ('completed', utc(2026, 2, day, 10))])
        self.assertEqual(completed(self.scope, *FEB), 1)

    @override_settings(TIME_ZONE='Europe/Vienna')
    def test_00_30_in_vienna_on_the_first_counts_in_that_month(self):
        # 31 January 23:30 UTC is 1 February 00:30 in Vienna
        self.done(utc(2026, 1, 31, 23, 30))
        self.assertEqual(completed(self.scope, *FEB), 1)
        self.assertEqual(completed(self.scope, *JAN), 0)

    @override_settings(TIME_ZONE='Europe/Vienna')
    def test_23_30_utc_on_the_last_day_of_july_counts_in_august_in_vienna(self):
        # 1 August 01:30 in Vienna
        self.done(utc(2026, 7, 31, 23, 30))
        self.assertEqual(completed(self.scope, date(2026, 8, 1), date(2026, 8, 31)), 1)
        self.assertEqual(completed(self.scope, date(2026, 7, 1), date(2026, 7, 31)), 0)

    @override_settings(TIME_ZONE='Europe/Vienna')
    def test_the_year_ends_in_the_time_zone_not_in_utc(self):
        self.done(vienna(2025, 12, 31, 23, 30))  # 22:30 UTC
        self.done(utc(2025, 12, 31, 23, 30))  # 1 January 00:30 in Vienna
        self.assertEqual(completed(self.scope, date(2025, 1, 1), date(2025, 12, 31)), 1)
        self.assertEqual(completed(self.scope, date(2026, 1, 1), date(2026, 12, 31)), 1)

    @override_settings(TIME_ZONE='Europe/Vienna')
    def test_the_days_the_clocks_change_are_whole_days(self):
        # 29 March 2026 has 23 hours and 25 October 2026 has 25 hours in Vienna
        for day in (date(2026, 3, 29), date(2026, 10, 25)):
            before, after = day - timedelta(days=1), day + timedelta(days=1)
            for moment in (vienna(before.year, before.month, before.day, 23, 30),
                           vienna(day.year, day.month, day.day, 0, 30),
                           vienna(day.year, day.month, day.day, 23, 30),
                           vienna(after.year, after.month, after.day, 0, 30)):
                self.done(moment)
            self.assertEqual(completed(self.scope, day, day), 2, day)
            self.assertEqual(completed(self.scope, before, before), 1, before)
            self.assertEqual(completed(self.scope, after, after), 1, after)
            self.assertEqual(completed(self.scope, before, after), 4, day)

    def test_one_query_for_one_and_for_many_incidents(self):
        self.done(utc(2026, 2, 10, 12))
        with self.assertNumQueries(1):
            completed(self.scope, *FEB)
        for day in range(1, 29):
            self.done(utc(2026, 2, day, 12))
        with self.assertNumQueries(1):
            self.assertEqual(completed(self.scope, *FEB), 29)


class MonthlyTest(LogTestCase):

    def test_one_row_for_each_month_in_order(self):
        rows = monthly(self.scope, date(2026, 11, 1), 4)
        self.assertEqual([row.month for row in rows], [date(2026, 11, 1), date(2026, 12, 1),
                                                       date(2027, 1, 1), date(2027, 2, 1)])
        self.assertTrue(all(isinstance(row, MonthRow) for row in rows))

    def test_incoming_and_completed_of_every_month(self):
        self.start_log(date(2026, 1, 1))
        self.incident(date(2026, 1, 31), history=[('new', utc(2026, 1, 31, 9)),
                                                  ('completed', utc(2026, 2, 3, 9))])
        self.incident(date(2026, 2, 1), history=[('new', utc(2026, 2, 1, 9))])
        self.incident(date(2026, 2, 28), history=[('new', utc(2026, 2, 28, 9)),
                                                  ('completed', utc(2026, 2, 28, 10))])
        self.done(utc(2026, 3, 31, 12))
        rows = monthly(self.scope, date(2026, 1, 1), 3)
        self.assertEqual(rows, [MonthRow(date(2026, 1, 1), 1, 0), MonthRow(date(2026, 2, 1), 2, 2),
                                MonthRow(date(2026, 3, 1), 0, 1)])

    def test_a_month_without_anything_is_0_where_it_is_recorded(self):
        self.start_log(date(2026, 1, 1))
        self.assertEqual(monthly(self.scope, date(2026, 1, 1), 2),
                         [MonthRow(date(2026, 1, 1), 0, 0), MonthRow(date(2026, 2, 1), 0, 0)])

    def test_months_that_end_before_the_log_began_are_not_recorded(self):
        self.start_log(date(2026, 3, 15))
        self.incident(date(2026, 1, 10), legacy=True)
        rows = monthly(self.scope, date(2026, 1, 1), 4)
        # incoming is a count of reports and needs no log, completed does
        self.assertEqual([row.incoming for row in rows], [1, 0, 0, 0])
        self.assertEqual([row.completed for row in rows], [None, None, 0, 0])

    def test_the_month_the_log_began_in_is_recorded_from_the_first_of_it_on(self):
        self.start_log(date(2026, 3, 1))
        rows = monthly(self.scope, date(2026, 2, 1), 2)
        self.assertEqual([row.completed for row in rows], [None, 0])

    def test_without_a_log_no_month_is_recorded(self):
        self.incident(date(2026, 1, 10), legacy=True)
        self.incident(date(2026, 2, 3), status='completed', legacy=True)
        rows = monthly(self.scope, date(2026, 1, 1), 3)
        self.assertEqual([(row.incoming, row.completed) for row in rows],
                         [(1, None), (1, None), (0, None)])

    def test_an_empty_database_has_the_rows_with_nothing_recorded(self):
        rows = monthly(self.scope, date(2026, 1, 1), 2)
        self.assertEqual([(row.incoming, row.completed) for row in rows], [(0, None), (0, None)])

    def test_the_log_of_another_department_records_nothing_here(self):
        self.foreign(LONG_AGO, history=[('new', utc(2026, 1, 1, 9))])
        self.incident(date(2026, 1, 10), legacy=True)
        rows = monthly(self.scope, date(2026, 1, 1), 2)
        self.assertEqual([row.completed for row in rows], [None, None])

    def test_a_reopened_incident_counts_once_in_the_month_of_its_last_completion(self):
        self.start_log(date(2026, 1, 1))
        self.incident(date(2026, 1, 1), history=[('new', utc(2026, 1, 2, 9)),
                                                 ('completed', utc(2026, 1, 10, 9)),
                                                 ('in process', utc(2026, 1, 20, 9)),
                                                 ('completed', utc(2026, 3, 5, 9))])
        rows = monthly(self.scope, date(2026, 1, 1), 3)
        self.assertEqual([row.completed for row in rows], [0, 0, 1])

    def test_incidents_of_another_department_never_count(self):
        self.start_log(date(2026, 1, 1))
        for day in range(1, 5):
            self.foreign(date(2026, 2, day), history=[('new', utc(2026, 2, day, 9)),
                                                      ('completed', utc(2026, 2, day, 10))])
        rows = monthly(self.scope, date(2026, 1, 1), 3)
        self.assertEqual([(row.incoming, row.completed) for row in rows],
                         [(0, 0), (0, 0), (0, 0)])

    def test_the_year_change(self):
        self.start_log(date(2025, 11, 1))
        self.done(utc(2025, 12, 31, 12))
        self.done(utc(2026, 1, 1, 12))
        self.incident(date(2025, 12, 31), history=[('new', utc(2025, 12, 31, 9))])
        self.incident(date(2026, 1, 1), history=[('new', utc(2026, 1, 1, 9))])
        rows = monthly(self.scope, date(2025, 11, 1), 4)
        self.assertEqual([(row.month, row.incoming, row.completed) for row in rows],
                         [(date(2025, 11, 1), 0, 0), (date(2025, 12, 1), 1, 1),
                          (date(2026, 1, 1), 1, 1), (date(2026, 2, 1), 0, 0)])

    @override_settings(TIME_ZONE='Europe/Vienna')
    def test_the_months_are_those_of_the_time_zone(self):
        self.start_log(date(2025, 11, 1))
        self.done(utc(2026, 1, 31, 23, 30))  # 1 February 00:30 in Vienna
        self.done(utc(2026, 7, 31, 23, 30))  # 1 August 01:30 in Vienna
        self.done(utc(2025, 12, 31, 23, 30))  # 1 January 00:30 in Vienna
        self.done(vienna(2025, 12, 31, 23, 30))  # still December
        rows = {row.month: row.completed for row in monthly(self.scope, date(2025, 12, 1), 9)}
        self.assertEqual(rows[date(2025, 12, 1)], 1)
        self.assertEqual(rows[date(2026, 1, 1)], 1)
        self.assertEqual(rows[date(2026, 2, 1)], 1)
        self.assertEqual(rows[date(2026, 7, 1)], 0)
        self.assertEqual(rows[date(2026, 8, 1)], 1)

    @override_settings(TIME_ZONE='Europe/Vienna')
    def test_a_month_is_recorded_by_the_local_day_the_log_began(self):
        # 28 February 23:30 UTC is 1 March 00:30 in Vienna: February is over by then
        self.incident(LONG_AGO, history=[('new', utc(2026, 2, 28, 23, 30))])
        rows = monthly(self.scope, date(2026, 2, 1), 2)
        self.assertEqual([row.completed for row in rows], [None, 0])

    def test_the_same_number_of_queries_for_one_and_for_many_incidents(self):
        self.start_log(date(2026, 1, 1))
        with self.assertNumQueries(3):
            monthly(self.scope, date(2026, 1, 1), 12)
        for day in range(1, 30):
            self.incident(date(2026, 2, 1 + day % 28),
                          history=[('new', utc(2026, 2, 1 + day % 28, 9)),
                                   ('completed', utc(2026, 3, 1 + day % 28, 9))])
        with self.assertNumQueries(3):
            rows = monthly(self.scope, date(2026, 1, 1), 12)
        self.assertEqual(sum(row.incoming for row in rows), 29)
        self.assertEqual(sum(row.completed for row in rows), 29)


class OverdueTest(LogTestCase):
    TODAY = date(2026, 10, 15)

    def test_day_14_is_not_overdue_and_day_15_is(self):
        self.incident(date(2026, 10, 1))  # 14 days old
        late = self.incident(date(2026, 9, 30))  # 15 days old
        self.assertEqual(list(overdue(self.scope, self.TODAY, 14)), [late])

    def test_the_limit_is_a_parameter(self):
        self.incident(date(2026, 10, 8))
        late = self.incident(date(2026, 10, 7))
        self.assertEqual(list(overdue(self.scope, self.TODAY, 7)), [late])
        self.assertEqual(len(overdue(self.scope, self.TODAY, 1)), 2)

    def test_only_incidents_that_are_still_new(self):
        for status in ('in process', 'under supervision', 'completed'):
            self.incident(date(2026, 1, 1), status=status)
        late = self.incident(date(2026, 1, 1))
        self.assertEqual(list(overdue(self.scope, self.TODAY, 14)), [late])

    def test_the_result_can_be_narrowed_further(self):
        self.incident(date(2026, 1, 1))
        found = overdue(self.scope, self.TODAY, 14)
        self.assertIsInstance(found, QuerySet)
        self.assertEqual(found.filter(reported__year=2025).count(), 0)
        self.assertEqual(found.count(), 1)

    def test_incidents_of_another_department_never_count(self):
        self.foreign(date(2026, 1, 1))
        self.assertEqual(list(overdue(self.scope, self.TODAY, 14)), [])

    def test_one_query_for_one_and_for_many_incidents(self):
        self.incident(date(2026, 1, 1))
        with self.assertNumQueries(1):
            list(overdue(self.scope, self.TODAY, 14))
        for day in range(1, 29):
            self.incident(date(2026, 2, day))
        with self.assertNumQueries(1):
            self.assertEqual(len(list(overdue(self.scope, self.TODAY, 14))), 29)


class AwaitingQMTest(LogTestCase):

    def setUp(self):
        super().setUp()
        self.reporter = self.dept.reporter.user
        self.qm = create_user('qm-user')

    def waiting(self, comments, **fields):
        return self.incident(date(2026, 2, 1), comments=comments, **fields)

    def test_the_reporting_person_wrote_the_last_comment(self):
        incident = self.waiting([(self.qm, date(2026, 2, 3)), (self.reporter, date(2026, 2, 4))])
        self.assertEqual(list(awaiting_qm(self.scope)), [incident])

    def test_the_qm_wrote_the_last_comment(self):
        self.waiting([(self.reporter, date(2026, 2, 3)), (self.qm, date(2026, 2, 4))])
        self.assertEqual(list(awaiting_qm(self.scope)), [])

    def test_a_reporter_comment_is_waiting_only_while_nobody_has_replied(self):
        incident = self.waiting([(self.reporter, date(2026, 2, 3))])
        self.assertEqual(list(awaiting_qm(self.scope)), [incident])
        self.waiting([(self.reporter, date(2026, 2, 3)), (self.qm, date(2026, 2, 3))])
        self.assertEqual(list(awaiting_qm(self.scope)), [incident])

    def test_no_comments_wait_for_nobody(self):
        self.waiting([])
        self.assertEqual(list(awaiting_qm(self.scope)), [])

    def test_the_last_comment_is_the_one_of_the_last_day(self):
        # written first, but dated last
        incident = self.waiting([(self.reporter, date(2026, 2, 9)), (self.qm, date(2026, 2, 5))])
        self.assertEqual(list(awaiting_qm(self.scope)), [incident])

    def test_of_two_comments_of_one_day_the_one_written_last_counts(self):
        day = date(2026, 2, 4)
        incident = self.waiting([(self.qm, day), (self.reporter, day)])
        self.waiting([(self.reporter, day), (self.qm, day)])
        self.assertEqual(list(awaiting_qm(self.scope)), [incident])

    def test_a_completed_incident_waits_for_nobody(self):
        self.waiting([(self.reporter, date(2026, 2, 4))], status='completed')
        self.assertEqual(list(awaiting_qm(self.scope)), [])

    def test_every_state_but_completed_can_wait(self):
        found = [self.waiting([(self.reporter, date(2026, 2, 4))], status=status)
                 for status in ('new', 'in process', 'under supervision')]
        self.assertCountEqual(awaiting_qm(self.scope), found)

    def test_only_a_reporter_role_counts_as_the_reporting_person(self):
        other = create_user('somebody')  # no role at all
        self.waiting([(self.reporter, date(2026, 2, 3)), (other, date(2026, 2, 4))])
        self.assertEqual(list(awaiting_qm(self.scope)), [])

    def test_the_comments_of_another_department_never_count(self):
        foreign = self.foreign(date(2026, 2, 1))
        foreign.comments.create(author=self.other.reporter.user, text='Synthetic reply')
        self.assertEqual(list(awaiting_qm(self.scope)), [])
        self.assertEqual(list(awaiting_qm(CriticalIncident.objects.filter(department=self.other))),
                         [foreign])

    def test_the_result_can_be_narrowed_further(self):
        self.waiting([(self.reporter, date(2026, 2, 4))])
        found = awaiting_qm(self.scope)
        self.assertIsInstance(found, QuerySet)
        self.assertEqual(found.filter(reported__year=2025).count(), 0)

    def test_one_query_for_one_and_for_many_incidents(self):
        self.waiting([(self.reporter, date(2026, 2, 4))])
        with self.assertNumQueries(1):
            list(awaiting_qm(self.scope))
        for day in range(1, 29):
            self.waiting([(self.qm, date(2026, 2, 1)), (self.reporter, date(2026, 2, day))])
        with self.assertNumQueries(1):
            self.assertEqual(len(list(awaiting_qm(self.scope))), 29)
