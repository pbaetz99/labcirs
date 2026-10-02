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

"""The times of the QM pages: the days until the first reaction and until the completion, the
open incidents at the end of a period, the periods of the quick choice, and the places. Test data
is constructed, never taken from a real report."""

from datetime import date, timedelta

from django.test import SimpleTestCase, override_settings
from model_bakery import baker
from parameterized import parameterized

from cirs.models import OrgUnit
from cirs.qm.metrics import (Durations, open_at_end, places, processing_days, quick_ranges,
                             reaction_days)
from cirs.tests.test_qm_metrics import FEB, JAN, WHOLE
from cirs.tests.test_qm_metrics_log import HOUR, LONG_AGO, LogTestCase, utc, vienna

TODAY = date(2026, 10, 2)


class ReactionDaysTest(LogTestCase):

    def reacted(self, reported, created, reacted, **fields):
        """An incident reported on the day `reported`, new at `created`, first changed at
        `reacted`."""
        return self.incident(reported, history=[('new', created), ('in process', reacted)],
                             **fields)

    def test_the_days_from_the_report_to_the_first_change(self):
        self.reacted(date(2026, 2, 1), utc(2026, 2, 1, 9), utc(2026, 2, 4, 10))
        self.assertEqual(reaction_days(self.scope, *FEB), Durations(3.0, 1))

    def test_the_median_and_the_number_of_incidents(self):
        for days in (1, 2, 4):
            self.reacted(date(2026, 2, 1), utc(2026, 2, 1, 9), utc(2026, 2, 1 + days, 10))
        self.assertEqual(reaction_days(self.scope, *FEB), Durations(2.0, 3))

    def test_the_median_of_an_even_number_is_the_mean_of_the_middle_two(self):
        for days in (1, 2):
            self.reacted(date(2026, 2, 1), utc(2026, 2, 1, 9), utc(2026, 2, 1 + days, 10))
        self.assertEqual(reaction_days(self.scope, *FEB), Durations(1.5, 2))

    def test_the_same_day_is_0_days(self):
        self.reacted(date(2026, 2, 1), utc(2026, 2, 1, 9), utc(2026, 2, 1, 15))
        self.assertEqual(reaction_days(self.scope, *FEB), Durations(0.0, 1))

    def test_the_first_change_counts_and_no_later_one(self):
        self.incident(date(2026, 2, 1), history=[
            ('new', utc(2026, 2, 1, 9)), ('in process', utc(2026, 2, 3, 9)),
            ('under supervision', utc(2026, 2, 10, 9)), ('completed', utc(2026, 2, 20, 9))])
        self.assertEqual(reaction_days(self.scope, *FEB), Durations(2.0, 1))

    def test_a_first_change_straight_to_completed_is_a_reaction(self):
        self.done(utc(2026, 2, 9, 12), reported=date(2026, 2, 5))
        self.assertEqual(reaction_days(self.scope, *FEB), Durations(4.0, 1))

    def test_a_reopened_incident_counts_once_with_its_first_change(self):
        self.incident(date(2026, 2, 1), history=[
            ('new', utc(2026, 2, 1, 9)), ('completed', utc(2026, 2, 2, 9)),
            ('in process', utc(2026, 2, 20, 9)), ('completed', utc(2026, 2, 25, 9))])
        self.assertEqual(reaction_days(self.scope, *FEB), Durations(1.0, 1))

    def test_an_incident_from_before_the_log_has_no_reaction(self):
        # no entry at all, and no entry "new" but the change after it: its first entry would be a
        # change that is not the first one
        self.incident(date(2026, 2, 1), status='in process', legacy=True)
        self.incident(date(2026, 2, 1), legacy=True, history=[
            ('new', utc(2026, 2, 1, 9)), ('in process', utc(2026, 2, 20, 9))])
        self.assertEqual(reaction_days(self.scope, *WHOLE), Durations(None, 0))

    def test_an_incident_that_is_still_new_has_no_reaction_yet(self):
        self.incident(date(2026, 2, 1), history=[('new', utc(2026, 2, 1, 9))])
        self.assertEqual(reaction_days(self.scope, *WHOLE), Durations(None, 0))

    def test_a_change_before_the_report_is_left_out(self):
        self.reacted(date(2026, 2, 10), utc(2026, 2, 1, 9), utc(2026, 2, 4, 10))
        self.reacted(date(2026, 2, 1), utc(2026, 2, 1, 9), utc(2026, 2, 6, 10))
        self.assertEqual(reaction_days(self.scope, *FEB), Durations(5.0, 1))

    def test_the_period_is_the_day_of_the_change_not_the_day_of_the_report(self):
        self.reacted(date(2026, 1, 20), utc(2026, 1, 20, 9), utc(2026, 2, 2, 9))
        self.assertEqual(reaction_days(self.scope, *FEB), Durations(13.0, 1))
        self.assertEqual(reaction_days(self.scope, *JAN), Durations(None, 0))

    def test_both_days_of_the_period_count(self):
        for moment in (utc(2026, 1, 31, 23, 59, 59), utc(2026, 2, 1, 0, 0, 0),
                       utc(2026, 2, 28, 23, 59, 59), utc(2026, 3, 1, 0, 0, 0)):
            self.reacted(LONG_AGO, moment - HOUR, moment)
        self.assertEqual(reaction_days(self.scope, *FEB).count, 2)
        self.assertEqual(reaction_days(self.scope, *WHOLE).count, 4)

    def test_incidents_of_another_department_never_count(self):
        self.reacted(date(2026, 2, 1), utc(2026, 2, 1, 9), utc(2026, 2, 4, 10))
        for day in range(2, 6):
            self.foreign(date(2026, 2, 1), history=[('new', utc(2026, 2, 1, 9)),
                                                    ('in process', utc(2026, 2, day, 10))])
        self.assertEqual(reaction_days(self.scope, *FEB), Durations(3.0, 1))

    def test_without_incidents_there_is_no_median(self):
        self.assertEqual(reaction_days(self.scope, *WHOLE), Durations(None, 0))

    def test_one_query_for_one_and_for_many_incidents(self):
        self.reacted(date(2026, 2, 1), utc(2026, 2, 1, 9), utc(2026, 2, 4, 10))
        with self.assertNumQueries(1):
            reaction_days(self.scope, *FEB)
        for day in range(2, 29):
            self.reacted(date(2026, 2, 1), utc(2026, 2, 1, 9), utc(2026, 2, day, 10))
        with self.assertNumQueries(1):
            self.assertEqual(reaction_days(self.scope, *FEB).count, 28)

    @override_settings(TIME_ZONE='Europe/Vienna')
    def test_00_30_in_vienna_on_the_first_is_in_that_month_and_that_day_ends_the_time(self):
        # 31 January 23:30 UTC is 1 February 00:30 in Vienna: February, and three days after the
        # 29th (by the day in UTC it would be two)
        self.reacted(date(2026, 1, 29), utc(2026, 1, 29, 9), utc(2026, 1, 31, 23, 30))
        self.assertEqual(reaction_days(self.scope, *FEB), Durations(3.0, 1))
        self.assertEqual(reaction_days(self.scope, *JAN), Durations(None, 0))

    @override_settings(TIME_ZONE='Europe/Vienna')
    def test_23_30_utc_on_the_last_day_of_july_is_in_august_in_vienna(self):
        self.reacted(date(2026, 7, 30), utc(2026, 7, 30, 9), utc(2026, 7, 31, 23, 30))
        self.assertEqual(reaction_days(self.scope, date(2026, 8, 1), date(2026, 8, 31)),
                         Durations(2.0, 1))
        self.assertEqual(reaction_days(self.scope, date(2026, 7, 1), date(2026, 7, 31)),
                         Durations(None, 0))

    @override_settings(TIME_ZONE='Europe/Vienna')
    def test_the_days_the_clocks_change_are_whole_days(self):
        # 29 March 2026 has 23 hours and 25 October 2026 has 25 hours in Vienna
        for day in (date(2026, 3, 29), date(2026, 10, 25)):
            before, after = day - timedelta(days=1), day + timedelta(days=1)
            for moment in (vienna(before.year, before.month, before.day, 23, 30),
                           vienna(day.year, day.month, day.day, 0, 30),
                           vienna(day.year, day.month, day.day, 23, 30),
                           vienna(after.year, after.month, after.day, 0, 30)):
                self.reacted(before, moment - HOUR, moment)
            self.assertEqual(reaction_days(self.scope, day, day), Durations(1.0, 2), day)
            self.assertEqual(reaction_days(self.scope, before, before), Durations(0.0, 1), before)
            self.assertEqual(reaction_days(self.scope, after, after), Durations(2.0, 1), after)
            self.assertEqual(reaction_days(self.scope, before, after).count, 4, day)

    @override_settings(TIME_ZONE='Europe/Vienna')
    def test_the_year_ends_in_the_time_zone_not_in_utc(self):
        self.reacted(date(2025, 12, 30), utc(2025, 12, 30, 9), vienna(2025, 12, 31, 23, 30))
        self.reacted(date(2025, 12, 30), utc(2025, 12, 30, 9), utc(2025, 12, 31, 23, 30))
        self.assertEqual(reaction_days(self.scope, date(2025, 1, 1), date(2025, 12, 31)),
                         Durations(1.0, 1))
        self.assertEqual(reaction_days(self.scope, date(2026, 1, 1), date(2026, 12, 31)),
                         Durations(2.0, 1))


class ProcessingDaysTest(LogTestCase):

    def test_the_days_from_the_report_to_the_completion(self):
        self.done(utc(2026, 2, 11, 12), reported=date(2026, 2, 1))
        self.assertEqual(processing_days(self.scope, *FEB), Durations(10.0, 1))

    def test_the_median_and_the_number_of_incidents(self):
        for days in (1, 2, 4):
            self.done(utc(2026, 2, 1 + days, 12), reported=date(2026, 2, 1))
        self.assertEqual(processing_days(self.scope, *FEB), Durations(2.0, 3))
        self.done(utc(2026, 2, 20, 12), reported=date(2026, 2, 1))
        self.assertEqual(processing_days(self.scope, *FEB), Durations(3.0, 4))

    def test_an_incident_that_was_reopened_counts_once_with_its_last_completion(self):
        self.incident(date(2026, 1, 1), history=[
            ('new', utc(2026, 1, 2, 9)), ('completed', utc(2026, 1, 10, 9)),
            ('in process', utc(2026, 1, 20, 9)), ('completed', utc(2026, 2, 5, 9))])
        self.assertEqual(processing_days(self.scope, *JAN), Durations(None, 0))
        self.assertEqual(processing_days(self.scope, *FEB), Durations(35.0, 1))
        self.assertEqual(processing_days(self.scope, *WHOLE), Durations(35.0, 1))

    def test_an_incident_that_was_reopened_and_not_completed_again_has_no_duration(self):
        self.incident(date(2026, 1, 1), history=[
            ('new', utc(2026, 1, 2, 9)), ('completed', utc(2026, 1, 10, 9)),
            ('in process', utc(2026, 1, 20, 9))])
        self.assertEqual(processing_days(self.scope, *WHOLE), Durations(None, 0))

    def test_an_incident_from_before_the_log_that_is_completed_after_it_has_a_duration(self):
        # no entry "new", but the entry of the completion: the duration needs nothing else
        self.incident(date(2026, 2, 1), legacy=True, history=[
            ('new', utc(2026, 2, 1, 9)), ('completed', utc(2026, 2, 11, 9))])
        self.assertEqual(processing_days(self.scope, *FEB), Durations(10.0, 1))

    def test_an_incident_that_was_completed_before_the_log_has_none(self):
        self.incident(date(2026, 2, 1), status='completed', legacy=True)
        self.assertEqual(processing_days(self.scope, *WHOLE), Durations(None, 0))

    def test_a_completion_before_the_report_is_left_out(self):
        self.done(utc(2026, 2, 4, 12), reported=date(2026, 2, 10))
        self.done(utc(2026, 2, 6, 12), reported=date(2026, 2, 1))
        self.assertEqual(processing_days(self.scope, *FEB), Durations(5.0, 1))

    def test_the_period_is_the_day_of_the_completion_not_the_day_of_the_report(self):
        self.done(utc(2026, 2, 2, 9), reported=date(2026, 1, 20))
        self.assertEqual(processing_days(self.scope, *FEB), Durations(13.0, 1))
        self.assertEqual(processing_days(self.scope, *JAN), Durations(None, 0))

    def test_both_days_of_the_period_count(self):
        for moment in (utc(2026, 1, 31, 23, 59, 59), utc(2026, 2, 1, 0, 0, 0),
                       utc(2026, 2, 28, 23, 59, 59), utc(2026, 3, 1, 0, 0, 0)):
            self.done(moment)
        self.assertEqual(processing_days(self.scope, *FEB).count, 2)
        self.assertEqual(processing_days(self.scope, *WHOLE).count, 4)

    def test_incidents_of_another_department_never_count(self):
        self.done(utc(2026, 2, 11, 12), reported=date(2026, 2, 1))
        for day in range(2, 6):
            self.foreign(date(2026, 2, 1), history=[('new', utc(2026, 2, 1, 9)),
                                                    ('completed', utc(2026, 2, day, 10))])
        self.assertEqual(processing_days(self.scope, *FEB), Durations(10.0, 1))

    def test_without_incidents_there_is_no_median(self):
        self.assertEqual(processing_days(self.scope, *WHOLE), Durations(None, 0))

    def test_one_query_for_one_and_for_many_incidents(self):
        self.done(utc(2026, 2, 11, 12), reported=date(2026, 2, 1))
        with self.assertNumQueries(1):
            processing_days(self.scope, *FEB)
        for day in range(2, 29):
            self.done(utc(2026, 2, day, 12), reported=date(2026, 2, 1))
        with self.assertNumQueries(1):
            self.assertEqual(processing_days(self.scope, *FEB).count, 28)

    @override_settings(TIME_ZONE='Europe/Vienna')
    def test_00_30_in_vienna_on_the_first_is_in_that_month_and_that_day_ends_the_time(self):
        self.done(utc(2026, 1, 31, 23, 30), reported=date(2026, 1, 29))
        self.assertEqual(processing_days(self.scope, *FEB), Durations(3.0, 1))
        self.assertEqual(processing_days(self.scope, *JAN), Durations(None, 0))

    @override_settings(TIME_ZONE='Europe/Vienna')
    def test_23_30_utc_on_the_last_day_of_july_is_in_august_in_vienna(self):
        self.done(utc(2026, 7, 31, 23, 30), reported=date(2026, 7, 30))
        self.assertEqual(processing_days(self.scope, date(2026, 8, 1), date(2026, 8, 31)),
                         Durations(2.0, 1))

    @override_settings(TIME_ZONE='Europe/Vienna')
    def test_the_days_the_clocks_change_are_whole_days(self):
        for day in (date(2026, 3, 29), date(2026, 10, 25)):
            before, after = day - timedelta(days=1), day + timedelta(days=1)
            for moment in (vienna(before.year, before.month, before.day, 23, 30),
                           vienna(day.year, day.month, day.day, 0, 30),
                           vienna(day.year, day.month, day.day, 23, 30),
                           vienna(after.year, after.month, after.day, 0, 30)):
                self.done(moment, reported=before)
            self.assertEqual(processing_days(self.scope, day, day), Durations(1.0, 2), day)
            self.assertEqual(processing_days(self.scope, before, before), Durations(0.0, 1), before)
            self.assertEqual(processing_days(self.scope, after, after), Durations(2.0, 1), after)

    @override_settings(TIME_ZONE='Europe/Vienna')
    def test_the_year_ends_in_the_time_zone_not_in_utc(self):
        self.done(vienna(2025, 12, 31, 23, 30), reported=date(2025, 12, 30))
        self.done(utc(2025, 12, 31, 23, 30), reported=date(2025, 12, 30))
        self.assertEqual(processing_days(self.scope, date(2025, 1, 1), date(2025, 12, 31)),
                         Durations(1.0, 1))
        self.assertEqual(processing_days(self.scope, date(2026, 1, 1), date(2026, 12, 31)),
                         Durations(2.0, 1))


class OpenAtEndTest(LogTestCase):

    def setUp(self):
        super().setUp()
        self.incident(date(2026, 9, 1), status='new')
        self.incident(date(2026, 9, 1), status='in process')
        self.incident(date(2026, 9, 1), status='under supervision')
        self.incident(date(2026, 9, 1), status='completed')
        self.foreign(date(2026, 9, 1), status='new')

    def test_a_period_that_reaches_today_has_the_state_of_now(self):
        self.assertEqual(open_at_end(self.scope, TODAY, TODAY), 3)
        self.assertEqual(open_at_end(self.scope, TODAY + timedelta(days=29), TODAY), 3)

    def test_a_period_that_ended_before_today_is_not_recorded(self):
        self.assertIsNone(open_at_end(self.scope, TODAY - timedelta(days=1), TODAY))
        self.assertIsNone(open_at_end(self.scope, date(2025, 12, 31), TODAY))

    def test_without_incidents_nothing_is_open(self):
        self.assertEqual(open_at_end(self.scope.none(), TODAY, TODAY), 0)

    def test_the_incidents_of_another_department_are_not_open_here(self):
        self.assertEqual(open_at_end(self.scope, TODAY, TODAY), 3)  # 4 if the foreign one counted


class QuickRangesTest(SimpleTestCase):

    @parameterized.expand([
        # today, last quarter, this year, last year
        (date(2026, 1, 1), (date(2025, 10, 1), date(2025, 12, 1)),
         (date(2026, 1, 1), date(2026, 1, 1)), (date(2025, 1, 1), date(2025, 12, 1))),
        (date(2026, 1, 31), (date(2025, 10, 1), date(2025, 12, 1)),
         (date(2026, 1, 1), date(2026, 1, 1)), (date(2025, 1, 1), date(2025, 12, 1))),
        (date(2026, 3, 31), (date(2025, 10, 1), date(2025, 12, 1)),
         (date(2026, 1, 1), date(2026, 3, 1)), (date(2025, 1, 1), date(2025, 12, 1))),
        (date(2026, 4, 1), (date(2026, 1, 1), date(2026, 3, 1)),
         (date(2026, 1, 1), date(2026, 4, 1)), (date(2025, 1, 1), date(2025, 12, 1))),
        (date(2026, 10, 2), (date(2026, 7, 1), date(2026, 9, 1)),
         (date(2026, 1, 1), date(2026, 10, 1)), (date(2025, 1, 1), date(2025, 12, 1))),
        (date(2026, 12, 31), (date(2026, 7, 1), date(2026, 9, 1)),
         (date(2026, 1, 1), date(2026, 12, 1)), (date(2025, 1, 1), date(2025, 12, 1))),
        (date(2027, 1, 1), (date(2026, 10, 1), date(2026, 12, 1)),
         (date(2027, 1, 1), date(2027, 1, 1)), (date(2026, 1, 1), date(2026, 12, 1))),
    ])
    def test_the_three_periods(self, today, last_quarter, this_year, last_year):
        self.assertEqual(quick_ranges(today), {'last_quarter': last_quarter,
                                               'this_year': this_year, 'last_year': last_year})

    def test_a_leap_day_is_a_day_like_any_other(self):
        self.assertEqual(quick_ranges(date(2028, 2, 29))['last_quarter'],
                         (date(2027, 10, 1), date(2027, 12, 1)))


class PlacesTest(LogTestCase):

    def test_the_groups_that_occur_in_the_incidents_in_the_order_of_the_units(self):
        late = baker.make(OrgUnit, name='Pflege', position=2)
        early = baker.make(OrgUnit, name='Labor', position=1)
        unit = baker.make(OrgUnit, name='Station A', parent=early)
        baker.make(OrgUnit, name='Leer')  # a group without incidents is not offered
        self.incident(date(2026, 2, 1), org_unit=unit)  # a group occurs through its units
        self.incident(date(2026, 2, 1), org_unit=late)
        self.incident(date(2026, 2, 1), org_unit=late)
        self.incident(date(2026, 2, 1))  # no place: no group
        self.assertEqual(places(self.scope), [(early.pk, 'Labor'), (late.pk, 'Pflege')])

    def test_the_groups_of_other_departments_are_not_offered(self):
        foreign = baker.make(OrgUnit, name='Fremd')
        self.foreign(date(2026, 2, 1), org_unit=foreign)
        self.assertEqual(places(self.scope), [])
