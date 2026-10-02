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

"""The numbers of the QM pages that do not need the status log, and the suppression of small
numbers. Test data is constructed, never taken from a real report."""

from datetime import date
from itertools import product

from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import translation
from model_bakery import baker
from parameterized import parameterized

from cirs.models import (FREQUENCY_CHOICES, HAZARD_CHOICES, PREVENTABILITY_CHOICES,
                         CriticalIncident, OrgUnit, PublishableIncident)
from cirs.qm import metrics
from cirs.qm.metrics import (SECONDARY_MARK, Bucket, Durations, distribution, first_of_month,
                             group_filter, group_of, in_period, incoming, last_days, last_months,
                             open_by_status, published, redact_duration, redact_series, suppress)
from cirs.tests.helpers import make_incident

JAN = (date(2026, 1, 1), date(2026, 1, 31))
FEB = (date(2026, 2, 1), date(2026, 2, 28))
MAR = (date(2026, 3, 1), date(2026, 3, 31))
WHOLE = (date(2026, 1, 1), date(2026, 12, 31))
NOT_SPECIFIED = 'Not specified'


class SuppressTest(SimpleTestCase):

    @parameterized.expand([(0, 3, 0), (1, 3, '< 3'), (2, 3, '< 3'), (3, 3, 3), (10, 3, 10),
                           (4, 5, '< 5'), (5, 5, 5), (1, 1, 1), (1, 2, '< 2')])
    def test_a_number_above_0_and_below_the_minimum_is_hidden(self, value, min_cell, expected):
        self.assertEqual(suppress(value, min_cell), expected)

    def test_a_hidden_number_is_text_and_a_shown_one_stays_a_number(self):
        self.assertIsInstance(suppress(2, 3), str)
        self.assertIs(type(suppress(7, 3)), int)


class RedactSeriesTest(SimpleTestCase):

    def test_without_a_small_number_nothing_changes(self):
        self.assertEqual(redact_series([3, 10, 0, 20], 3, 33), [3, 10, 0, 20, 33])
        self.assertEqual(redact_series([3, 10, 0, 20], 3, None), [3, 10, 0, 20])

    def test_an_empty_series_stays_empty(self):
        self.assertEqual(redact_series([], 3, None), [])
        self.assertEqual(redact_series([], 3, 0), [0])
        self.assertEqual(redact_series([0, 0], 3, 0), [0, 0, 0])

    def test_one_small_number_with_its_total_costs_the_smallest_other_number_too(self):
        # 32 - 10 - 20 would give the 2 away
        self.assertEqual(redact_series([2, 10, 20], 3, 32), ['< 3', SECONDARY_MARK, 20, 32])

    def test_the_smallest_other_number_is_hidden_wherever_it_stands(self):
        self.assertEqual(redact_series([20, 2, 10], 3, 32), [20, '< 3', SECONDARY_MARK, 32])
        self.assertEqual(redact_series([10, 2, 20, 10], 3, 42),
                         [SECONDARY_MARK, '< 3', 20, 10, 42])

    def test_a_month_row_with_exactly_one_small_month(self):
        self.assertEqual(redact_series([0, 1, 0, 8, 9], 3, 18),
                         [0, '< 3', 0, SECONDARY_MARK, 9, 18])

    def test_zeros_are_neither_hidden_nor_chosen_as_the_other_number(self):
        self.assertEqual(redact_series([0, 0, 2, 0, 5], 3, 7),
                         [0, 0, '< 3', 0, SECONDARY_MARK, 7])

    def test_equal_candidates_lose_the_first_one(self):
        self.assertEqual(redact_series([2, 5, 5, 20], 3, 32),
                         ['< 3', SECONDARY_MARK, 5, 20, 32])

    def test_without_a_total_nothing_can_be_worked_out_and_only_small_numbers_go(self):
        # a question with several answers per report has no total that its cells add up to
        self.assertEqual(redact_series([2, 10, 20], 3, None), ['< 3', 10, 20])

    def test_two_small_numbers_that_cannot_be_told_apart_stay_as_they_are(self):
        self.assertEqual(redact_series([1, 2, 10, 20], 3, 33), ['< 3', '< 3', 10, 20, 33])

    def test_small_numbers_that_the_total_would_give_away_cost_another_number(self):
        # 12 - 10 = 2 can only be 1 + 1
        self.assertEqual(redact_series([1, 1, 10], 3, 12),
                         ['< 3', '< 3', SECONDARY_MARK, 12])

    def test_all_cells_1_hide_the_total(self):
        # a total of 3 over three cells can only be 1 + 1 + 1
        self.assertEqual(redact_series([1, 1, 1], 3, 3),
                         ['< 3', '< 3', '< 3', SECONDARY_MARK])

    def test_when_hiding_another_number_does_not_help_only_the_total_goes(self):
        # without the 1, the 3 would be 4 - 1; hidden, 4 - 3 still limits the 1 to a single value
        self.assertEqual(redact_series([1, 3], 3, 4), ['< 3', 3, SECONDARY_MARK])

    def test_a_small_total_is_hidden_as_a_small_number(self):
        self.assertEqual(redact_series([1, 0], 3, 1), ['< 3', 0, '< 3'])
        self.assertEqual(redact_series([0, 2, 0], 3, 2), [0, '< 3', 0, '< 3'])

    def test_the_minimum_is_a_parameter(self):
        self.assertEqual(redact_series([4, 9, 20], 5, 33), ['< 5', SECONDARY_MARK, 20, 33])
        self.assertEqual(redact_series([4, 9, 20], 4, 33), [4, 9, 20, 33])

    def test_a_total_that_the_cells_do_not_add_up_to_is_refused(self):
        with self.assertRaises(ValueError):
            redact_series([2, 10], 3, 20)

    def test_input_is_left_alone(self):
        values = [2, 10, 20]
        redact_series(values, 3, 32)
        self.assertEqual(values, [2, 10, 20])

    def test_a_small_total_that_would_fix_the_cells_is_withheld(self):
        # 1 + 1 = 2: "< 3" for the cells and the total would tell that each cell is a 1
        self.assertEqual(redact_series([1, 1, 0], 3, 2), ['< 3', '< 3', 0, SECONDARY_MARK])
        self.assertEqual(redact_series([1, 0, 1], 3, 2), ['< 3', 0, '< 3', SECONDARY_MARK])

    def test_a_small_total_over_cells_that_stay_open_is_shown_as_small(self):
        self.assertEqual(redact_series([2, 0, 0], 3, 2), ['< 3', 0, 0, '< 3'])
        self.assertEqual(redact_series([1, 1, 1], 4, 3), ['< 4', '< 4', '< 4', SECONDARY_MARK])
        self.assertEqual(redact_series([1, 1, 0], 4, 2), ['< 4', '< 4', 0, '< 4'])

    def test_no_small_number_can_be_worked_out_from_what_is_shown(self):
        """Whoever sees an output has at least two values left for every small number in it.
        Checked for every series of 3 and 4 cells up to a size, with and without a total, and
        for several minimums. The possible series are those that fit what is shown: a number
        stands for itself, a small mark for 1 up to the minimum less 1, the other mark for the
        minimum or more, and the total, if there is one, for the sum of the cells."""
        for min_cell, cells, limit in ((3, 3, 9), (4, 3, 9), (5, 3, 9), (3, 4, 7), (4, 4, 6)):
            for values in product(range(limit + 1), repeat=cells):
                if sum(values) > limit:
                    continue
                for total in (sum(values), None):
                    with self.subTest(values=values, total=total, min_cell=min_cell):
                        shown = redact_series(list(values), min_cell, total)
                        possible = self.possible_series(shown, cells, min_cell, 2 * limit)
                        self.assertIn(values, possible)
                        for i in range(cells):
                            if shown[i] == f'< {min_cell}':
                                self.assertGreaterEqual(len({w[i] for w in possible}), 2)

    @staticmethod
    def possible_series(shown, cells, min_cell, cap):
        ranges = []
        for cell in shown[:cells]:
            if cell == SECONDARY_MARK:
                ranges.append(range(min_cell, cap + 1))
            elif isinstance(cell, str):
                ranges.append(range(1, min_cell))
            else:
                ranges.append((cell,))
        if len(shown) == cells:
            return list(product(*ranges))
        total = shown[cells]
        if total == SECONDARY_MARK:
            fits = lambda number: number >= 1
        elif isinstance(total, str):
            fits = lambda number: 1 <= number < min_cell
        else:
            fits = lambda number: number == total
        return [w for w in product(*ranges) if fits(sum(w))]

    def test_what_is_shown_is_true(self):
        """A number is the real one, a small mark stands for a number from 1 up to the minimum
        less 1, and the other mark for a number above 0 that is withheld."""
        for min_cell in (2, 3, 4):
            for values in product(range(8), repeat=3):
                for total in (sum(values), None):
                    shown = redact_series(list(values), min_cell, total)
                    real = values + ((total,) if total is not None else ())
                    for cell, number in zip(shown, real, strict=True):
                        with self.subTest(values=values, total=total, min_cell=min_cell):
                            if cell == SECONDARY_MARK:
                                self.assertGreater(number, 0)
                            elif isinstance(cell, str):
                                self.assertEqual(cell, f'< {min_cell}')
                                self.assertTrue(0 < number < min_cell)
                            else:
                                self.assertEqual(cell, number)


class RedactDurationTest(SimpleTestCase):

    @parameterized.expand([(1,), (2,)])
    def test_a_median_over_fewer_than_the_minimum_is_one_real_duration_or_two(self, count):
        hidden = redact_duration(Durations(8.5, count), 3)
        self.assertIsNone(hidden.median)
        self.assertEqual(hidden.count, '< 3')

    def test_from_the_minimum_on_median_and_count_are_shown(self):
        self.assertEqual(redact_duration(Durations(8.5, 3), 3), Durations(8.5, 3))
        self.assertEqual(redact_duration(Durations(2.0, 40), 3), Durations(2.0, 40))

    def test_no_durations_at_all_stay_none_and_0(self):
        self.assertEqual(redact_duration(Durations(None, 0), 3), Durations(None, 0))

    def test_the_minimum_is_a_parameter(self):
        self.assertEqual(redact_duration(Durations(4.0, 4), 5), Durations(None, '< 5'))
        self.assertEqual(redact_duration(Durations(4.0, 5), 5), Durations(4.0, 5))


class DataTestCase(TestCase):
    """Two departments: the numbers are always those of the first. The incidents of the other
    one are in the data of every test and must never show up."""

    def setUp(self):
        # Labels come out in the active language. A German request in an earlier test leaves
        # German active in this thread, so the tests set their language themselves.
        self.enterContext(translation.override('en'))
        self.dept = baker.make_recipe('cirs.department')
        self.other = baker.make_recipe('cirs.department')
        self.scope = CriticalIncident.objects.filter(department=self.dept)

    def incident(self, reported, **fields):
        return make_incident(self.dept, reported=reported, **fields)

    def foreign(self, reported, **fields):
        return make_incident(self.other, reported=reported, **fields)


def counted(buckets):
    return [(bucket.key, bucket.count) for bucket in buckets]


class InPeriodTest(DataTestCase):

    def test_both_days_of_the_period_count(self):
        for day in (date(2026, 1, 31), date(2026, 2, 1), date(2026, 2, 14), date(2026, 2, 28),
                    date(2026, 3, 1)):
            self.incident(day)
        self.assertEqual(incoming(self.scope, *FEB), 3)
        self.assertEqual(incoming(self.scope, *JAN), 1)
        self.assertEqual(incoming(self.scope, *MAR), 1)
        self.assertEqual(incoming(self.scope, *WHOLE), 5)

    def test_a_single_day_is_a_period(self):
        self.incident(date(2026, 2, 1))
        self.incident(date(2026, 2, 2))
        self.assertEqual(incoming(self.scope, date(2026, 2, 1), date(2026, 2, 1)), 1)

    def test_the_last_day_of_a_leap_february(self):
        self.incident(date(2028, 2, 29))
        self.incident(date(2028, 3, 1))
        self.assertEqual(incoming(self.scope, date(2028, 2, 1), date(2028, 2, 29)), 1)

    def test_year_end_and_start_belong_to_their_own_year(self):
        self.incident(date(2025, 12, 31))
        self.incident(date(2026, 1, 1))
        self.assertEqual(incoming(self.scope, *WHOLE), 1)

    def test_a_period_that_ends_before_it_starts_is_empty(self):
        self.incident(date(2026, 2, 10))
        self.assertEqual(incoming(self.scope, date(2026, 2, 28), date(2026, 2, 1)), 0)

    def test_without_incidents_the_number_is_0(self):
        self.assertEqual(incoming(self.scope, *WHOLE), 0)
        self.assertEqual(list(in_period(self.scope, *WHOLE)), [])

    def test_in_period_keeps_the_scope_of_the_incidents_it_is_given(self):
        mine = self.incident(date(2026, 2, 10))
        self.foreign(date(2026, 2, 10))
        self.assertEqual(list(in_period(self.scope, *FEB)), [mine])
        self.assertEqual(incoming(self.scope.none(), *FEB), 0)

    def test_incidents_of_another_department_never_count(self):
        self.incident(date(2026, 2, 10))
        for _ in range(4):
            self.foreign(date(2026, 2, 10))
        self.assertEqual(incoming(self.scope, *FEB), 1)
        self.assertEqual(incoming(CriticalIncident.objects.filter(department=self.other), *FEB), 4)

    @override_settings(TIME_ZONE='Europe/Vienna')
    def test_the_day_of_a_report_is_its_date_whatever_the_time_zone(self):
        self.incident(date(2026, 2, 1))
        self.incident(date(2026, 1, 31))
        self.assertEqual(incoming(self.scope, *FEB), 1)

    def test_one_query(self):
        for day in range(1, 29):
            self.incident(date(2026, 2, day))
        with self.assertNumQueries(1):
            incoming(self.scope, *FEB)


class OpenByStatusTest(DataTestCase):

    def test_every_open_state_is_listed_even_with_0(self):
        self.assertEqual(open_by_status(self.scope),
                         {'new': 0, 'in process': 0, 'under supervision': 0})

    def test_the_states_come_in_the_order_of_the_work(self):
        self.assertEqual(list(open_by_status(self.scope)),
                         ['new', 'in process', 'under supervision'])

    def test_counts_per_state_and_completed_is_not_open(self):
        for status in ('new', 'new', 'in process', 'completed', 'completed', 'completed'):
            self.incident(date(2026, 2, 1), status=status)
        self.assertEqual(open_by_status(self.scope),
                         {'new': 2, 'in process': 1, 'under supervision': 0})

    def test_the_day_of_the_report_does_not_matter(self):
        self.incident(date(2020, 5, 1), status='under supervision')
        self.incident(date(2026, 9, 30), status='under supervision')
        self.assertEqual(open_by_status(self.scope)['under supervision'], 2)

    def test_incidents_of_another_department_never_count(self):
        self.incident(date(2026, 2, 1))
        for status in ('new', 'in process', 'under supervision'):
            self.foreign(date(2026, 2, 1), status=status)
        self.assertEqual(open_by_status(self.scope),
                         {'new': 1, 'in process': 0, 'under supervision': 0})

    def test_one_query(self):
        for status in ('new', 'in process', 'under supervision', 'completed') * 5:
            self.incident(date(2026, 2, 1), status=status)
        with self.assertNumQueries(1):
            open_by_status(self.scope)


class DistributionTest(DataTestCase):

    def test_every_choice_is_listed_in_its_order_and_not_specified_comes_last(self):
        for risk in ('high', 'high', 'low', ''):
            self.incident(date(2026, 2, 10), risk=risk)
        with translation.override('en'):
            self.assertEqual(distribution(self.scope, 'risk', *FEB), [
                Bucket('low', 'low', 1), Bucket('middle', 'middle', 0),
                Bucket('high', 'high', 2), Bucket(None, NOT_SPECIFIED, 1)])

    def test_labels_are_in_the_active_language(self):
        self.incident(date(2026, 2, 10), risk='high')
        self.incident(date(2026, 2, 10), risk='')
        with translation.override('de'):
            found = distribution(self.scope, 'risk', *FEB)
        self.assertEqual([(b.key, b.label) for b in found if b.count],
                         [('high', 'hoch'), (None, 'Keine Angabe')])

    def test_not_specified_is_left_out_when_every_incident_has_a_value(self):
        self.incident(date(2026, 2, 10), risk='low')
        self.assertEqual([b.key for b in distribution(self.scope, 'risk', *FEB)],
                         ['low', 'middle', 'high'])

    def test_an_empty_period_lists_the_choices_with_0(self):
        self.assertEqual(counted(distribution(self.scope, 'risk', *FEB)),
                         [('low', 0), ('middle', 0), ('high', 0)])

    def test_the_period_includes_both_days_and_nothing_else(self):
        for day in (date(2026, 1, 31), date(2026, 2, 1), date(2026, 2, 28), date(2026, 3, 1)):
            self.incident(day, risk='high')
        self.assertEqual(dict(counted(distribution(self.scope, 'risk', *FEB)))['high'], 2)

    def test_incidents_of_another_department_never_count(self):
        self.incident(date(2026, 2, 10), risk='low')
        self.foreign(date(2026, 2, 10), risk='high')
        self.foreign(date(2026, 2, 10), risk='')
        self.assertEqual(counted(distribution(self.scope, 'risk', *FEB)),
                         [('low', 1), ('middle', 0), ('high', 0)])

    @parameterized.expand([('preventability', PREVENTABILITY_CHOICES),
                           ('frequency', FREQUENCY_CHOICES), ('hazard', HAZARD_CHOICES)])
    def test_other_choice_fields(self, field, choices):
        for key, _label in choices:
            self.incident(date(2026, 2, 10), **{field: key})
        self.incident(date(2026, 2, 11), **{field: ''})
        self.incident(date(2026, 2, 11), **{field: ''})
        found = distribution(self.scope, field, *FEB)
        self.assertEqual([b.key for b in found], [key for key, _label in choices] + [None])
        self.assertEqual([b.count for b in found], [1] * len(choices) + [2])
        self.assertEqual(sum(b.count for b in found), incoming(self.scope, *FEB))

    def test_a_value_that_is_no_choice_any_more_keeps_the_sum_right(self):
        self.incident(date(2026, 2, 10), risk='extreme')
        self.incident(date(2026, 2, 10), risk='')
        found = distribution(self.scope, 'risk', *FEB)
        self.assertEqual(counted(found), [('low', 0), ('middle', 0), ('high', 0),
                                          ('extreme', 1), (None, 1)])
        self.assertEqual(sum(b.count for b in found), 2)

    def test_an_unknown_field_is_refused(self):
        for field in ('status', 'department', 'incident', 'org_unit', ''):
            with self.subTest(field), self.assertRaises(ValueError):
                distribution(self.scope, field, *FEB)

    def test_every_field_of_the_overview_is_known(self):
        self.assertEqual(metrics.DISTRIBUTION_FIELDS, ('org_unit_group', 'category',
                         'preventability', 'risk', 'frequency', 'hazard'))
        for field in metrics.DISTRIBUTION_FIELDS:
            distribution(self.scope, field, *FEB)


class CategoryDistributionTest(DataTestCase):

    def test_a_report_counts_in_each_of_its_categories(self):
        self.incident(date(2026, 2, 10), category=['technique/methods', 'infrastructure'])
        self.incident(date(2026, 2, 11), category=['infrastructure'])
        self.incident(date(2026, 2, 12), category=[])
        self.incident(date(2026, 3, 12), category=['other'])
        self.foreign(date(2026, 2, 10), category=['knowledge/training', 'infrastructure'])
        found = distribution(self.scope, 'category', *FEB)
        self.assertEqual(counted(found), [
            ('organisation/communication', 0), ('technique/methods', 1), ('knowledge/training', 0),
            ('concentration/attention (mistake/slip)', 0), ('infrastructure', 2), ('other', 0),
            (None, 1)])
        # three reports, four answers
        self.assertEqual(sum(b.count for b in found if b.key), 3)
        self.assertEqual(incoming(self.scope, *FEB), 3)

    def test_the_same_category_twice_in_one_report_counts_once(self):
        self.incident(date(2026, 2, 10), category=['other', 'other'])
        self.assertEqual(dict(counted(distribution(self.scope, 'category', *FEB)))['other'], 1)

    def test_labels_are_the_names_of_the_categories(self):
        self.incident(date(2026, 2, 10), category=['infrastructure'])
        self.incident(date(2026, 2, 10), category=[])
        with translation.override('en'):
            found = {b.key: b.label for b in distribution(self.scope, 'category', *FEB)}
        self.assertEqual(found['infrastructure'], 'infrastructure')
        self.assertEqual(found[None], NOT_SPECIFIED)

    def test_the_page_can_tell_that_several_answers_are_possible(self):
        self.assertEqual(metrics.MULTIPLE_ANSWER_FIELDS, {'category'})
        with translation.override('de'):
            self.assertIn('Mehrfachnennung', str(metrics.MULTIPLE_ANSWERS_NOTE))
        with translation.override('en'):
            self.assertIn('Several answers', str(metrics.MULTIPLE_ANSWERS_NOTE))


class GroupTest(DataTestCase):

    def setUp(self):
        super().setUp()
        self.ward = OrgUnit.objects.create(name='Ward', position=2)
        self.room = OrgUnit.objects.create(name='Room', parent=self.ward)
        self.lab = OrgUnit.objects.create(name='Lab', position=1)
        self.empty = OrgUnit.objects.create(name='Archive', position=3)
        self.foreign_group = OrgUnit.objects.create(name='Foreign Unit', position=0)

    def test_the_group_of_a_unit_is_its_parent_and_of_a_top_unit_the_unit_itself(self):
        self.assertEqual(group_of(self.room), self.ward)
        self.assertEqual(group_of(self.ward), self.ward)
        self.assertIsNone(group_of(None))

    def test_a_group_has_its_units_and_its_own_incidents(self):
        in_room = self.incident(date(2026, 2, 10), org_unit=self.room)
        in_ward = self.incident(date(2026, 2, 10), org_unit=self.ward)
        self.incident(date(2026, 2, 10), org_unit=self.lab)
        self.incident(date(2026, 2, 10))
        self.foreign(date(2026, 2, 10), org_unit=self.ward)
        self.assertCountEqual(group_filter(self.scope, self.ward.pk), [in_room, in_ward])

    def test_a_group_without_units_has_only_its_own_incidents(self):
        in_lab = self.incident(date(2026, 2, 10), org_unit=self.lab)
        self.incident(date(2026, 2, 10), org_unit=self.ward)
        self.assertEqual(list(group_filter(self.scope, self.lab.pk)), [in_lab])

    def test_an_unknown_group_has_no_incidents(self):
        self.incident(date(2026, 2, 10), org_unit=self.lab)
        self.assertEqual(group_filter(self.scope, 999999).count(), 0)

    def test_no_group_is_the_incidents_without_a_unit_and_not_those_of_every_top_unit(self):
        without = self.incident(date(2026, 2, 10))
        self.incident(date(2026, 2, 10), org_unit=self.lab)
        self.incident(date(2026, 2, 10), org_unit=self.room)
        self.foreign(date(2026, 2, 10))
        self.assertEqual(list(group_filter(self.scope, None)), [without])

    def test_the_id_of_a_unit_below_another_is_no_group(self):
        self.incident(date(2026, 2, 10), org_unit=self.room)
        self.incident(date(2026, 2, 10), org_unit=self.ward)
        self.assertEqual(group_filter(self.scope, self.room.pk).count(), 0)

    def test_an_id_that_is_no_number_is_refused(self):
        for group_id in ('Ward', '', '1; DROP TABLE'):
            with self.subTest(group_id), self.assertRaises(ValueError):
                group_filter(self.scope, group_id)

    def test_the_id_of_a_request_is_a_text_of_digits(self):
        in_room = self.incident(date(2026, 2, 10), org_unit=self.room)
        self.assertEqual(list(group_filter(self.scope, str(self.ward.pk))), [in_room])

    def test_every_cell_of_the_distribution_has_its_incidents_and_together_all_of_them(self):
        for unit in (self.room, self.room, self.ward, self.lab, self.empty, None, None, None):
            self.incident(date(2026, 2, 10), org_unit=unit)
        self.foreign(date(2026, 2, 10), org_unit=self.lab)
        self.foreign(date(2026, 2, 10))
        found = distribution(self.scope, 'org_unit_group', *FEB)
        self.assertEqual(len(found), 4)
        for bucket in found:
            with self.subTest(bucket.label):
                self.assertEqual(group_filter(self.scope, bucket.key).count(), bucket.count)
        self.assertEqual(sum(group_filter(self.scope, b.key).count() for b in found),
                         incoming(self.scope, *FEB))

    def test_the_distribution_by_group_folds_units_into_their_group(self):
        for unit in (self.room, self.room, self.ward, self.lab, None, None, None):
            self.incident(date(2026, 2, 10), org_unit=unit)
        self.incident(date(2026, 3, 10), org_unit=self.lab)
        self.foreign(date(2026, 2, 10), org_unit=self.foreign_group)
        self.foreign(date(2026, 2, 10), org_unit=self.lab)
        self.assertEqual(distribution(self.scope, 'org_unit_group', *FEB), [
            Bucket(self.lab.pk, 'Lab', 1), Bucket(self.ward.pk, 'Ward', 3),
            Bucket(None, NOT_SPECIFIED, 3)])

    def test_the_label_is_the_name_of_the_group_alone(self):
        self.incident(date(2026, 2, 10), org_unit=self.room)
        self.assertEqual(distribution(self.scope, 'org_unit_group', *FEB)[0].label, 'Ward')

    def test_groups_nobody_reported_in_are_left_out(self):
        self.incident(date(2026, 2, 10), org_unit=self.lab)
        keys = [b.key for b in distribution(self.scope, 'org_unit_group', *FEB)]
        self.assertEqual(keys, [self.lab.pk])

    def test_without_any_unit_only_not_specified_is_left(self):
        self.incident(date(2026, 2, 10))
        self.assertEqual(distribution(self.scope, 'org_unit_group', *FEB),
                         [Bucket(None, NOT_SPECIFIED, 1)])

    def test_an_empty_period_has_no_groups(self):
        self.assertEqual(distribution(self.scope, 'org_unit_group', *FEB), [])

    def test_the_sum_is_the_incoming(self):
        for unit in (self.room, self.ward, self.lab, None):
            self.incident(date(2026, 2, 10), org_unit=unit)
        found = distribution(self.scope, 'org_unit_group', *FEB)
        self.assertEqual(sum(b.count for b in found), incoming(self.scope, *FEB))


class PublishedTest(DataTestCase):

    def publish(self, incident, publish=True):
        return PublishableIncident.objects.create(critical_incident=incident, publish=publish)

    def test_published_incidents_reported_in_the_period(self):
        self.publish(self.incident(date(2026, 2, 1)))
        self.publish(self.incident(date(2026, 2, 28)))
        self.publish(self.incident(date(2026, 1, 31)))
        self.publish(self.incident(date(2026, 3, 1)))
        self.assertEqual(published(self.scope, *FEB), 2)

    def test_a_case_that_is_not_published_or_has_no_case_does_not_count(self):
        self.publish(self.incident(date(2026, 2, 10)), publish=False)
        self.incident(date(2026, 2, 10))
        self.assertEqual(published(self.scope, *FEB), 0)

    def test_the_cases_of_another_department_never_count(self):
        self.publish(self.incident(date(2026, 2, 10)))
        self.publish(self.foreign(date(2026, 2, 10)))
        self.assertEqual(published(self.scope, *FEB), 1)

    def test_the_date_is_that_of_the_report_not_of_the_publishing(self):
        self.publish(self.incident(date(2026, 1, 10)))
        self.assertEqual((published(self.scope, *JAN), published(self.scope, *FEB)), (1, 0))

    def test_without_incidents_the_number_is_0(self):
        self.assertEqual(published(self.scope, *WHOLE), 0)

    def test_one_query(self):
        for day in range(1, 11):
            self.publish(self.incident(date(2026, 2, day)))
        with self.assertNumQueries(1):
            published(self.scope, *FEB)


class QueryCountTest(DataTestCase):
    """The number of queries does not grow with the number of incidents: none per incident."""

    def fill(self, count):
        groups = [OrgUnit.objects.create(name=f'Group {n}') for n in range(3)]
        units = groups + [OrgUnit.objects.create(name='Sub', parent=groups[0]), None]
        for n in range(count):
            self.incident(date(2026, 2, 1 + n % 28), org_unit=units[n % 5],
                          risk=('low', 'middle', 'high', '')[n % 4],
                          category=['infrastructure', 'other'][:1 + n % 2])

    def counts(self):
        found = {}
        for field in metrics.DISTRIBUTION_FIELDS:
            with CaptureQueriesContext(connection) as queries:
                distribution(self.scope, field, *FEB)
            found[field] = len(queries)
        return found

    def test_the_same_number_of_queries_for_one_and_for_many_incidents(self):
        self.fill(1)
        few = self.counts()
        self.fill(30)
        self.assertEqual(self.counts(), few)
        self.assertLessEqual(max(few.values()), 2)


class SpanTest(SimpleTestCase):

    def test_the_last_7_days_end_today_and_start_6_days_before(self):
        self.assertEqual(last_days(date(2026, 10, 1), 7), (date(2026, 9, 25), date(2026, 10, 1)))

    def test_the_last_day_is_one_day(self):
        self.assertEqual(last_days(date(2026, 10, 1), 1), (date(2026, 10, 1), date(2026, 10, 1)))

    def test_days_cross_months_years_and_a_leap_day(self):
        self.assertEqual(last_days(date(2026, 1, 3), 7), (date(2025, 12, 28), date(2026, 1, 3)))
        self.assertEqual(last_days(date(2028, 3, 1), 7), (date(2028, 2, 24), date(2028, 3, 1)))

    def test_the_last_12_months_are_11_whole_months_and_the_running_one(self):
        self.assertEqual(last_months(date(2026, 10, 17), 12),
                         (date(2025, 11, 1), date(2026, 10, 17)))

    def test_in_january_they_start_in_february_of_the_year_before(self):
        self.assertEqual(last_months(date(2026, 1, 15), 12), (date(2025, 2, 1), date(2026, 1, 15)))

    def test_in_december_they_start_in_january(self):
        self.assertEqual(last_months(date(2026, 12, 31), 12),
                         (date(2026, 1, 1), date(2026, 12, 31)))

    def test_the_last_month_is_the_running_one(self):
        self.assertEqual(last_months(date(2026, 10, 17), 1),
                         (date(2026, 10, 1), date(2026, 10, 17)))
        self.assertEqual(last_months(date(2026, 10, 1), 1), (date(2026, 10, 1), date(2026, 10, 1)))

    def test_more_than_a_year_back(self):
        self.assertEqual(last_months(date(2026, 3, 5), 36), (date(2023, 4, 1), date(2026, 3, 5)))

    @parameterized.expand([
        (date(2026, 10, 17), 0, date(2026, 10, 1)), (date(2026, 10, 17), 1, date(2026, 11, 1)),
        (date(2026, 10, 17), 3, date(2027, 1, 1)), (date(2026, 10, 17), -10, date(2025, 12, 1)),
        (date(2026, 1, 31), -1, date(2025, 12, 1)), (date(2026, 1, 31), 1, date(2026, 2, 1)),
        (date(2026, 12, 31), 1, date(2027, 1, 1)), (date(2026, 1, 1), -13, date(2024, 12, 1))])
    def test_first_of_month(self, day, months, expected):
        self.assertEqual(first_of_month(day, months), expected)
