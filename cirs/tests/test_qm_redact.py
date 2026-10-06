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

"""The protection of small numbers in an evaluation that is printed or exported: what redact
makes of a Report. Everything printed or exported is made from a redacted Report, so what must
not be said is tested here, on Reports that are made by hand, and in the tests of the print view
and the CSV file on what is rendered from them."""

from dataclasses import FrozenInstanceError, replace
from datetime import date

from django.test import SimpleTestCase
from django.utils import translation

from cirs.qm import chart_data
from cirs.qm.metrics import SECONDARY_MARK, Bucket, Durations, MonthRow, first_of_month
from cirs.qm.report import Distribution, Measure, Period, Report, redact

STAR = SECONDARY_MARK
FIRST = date(2026, 1, 1)


def distribution(field, counts, multiple_answers=False):
    buckets = [Bucket('k%d' % number, '%s %d' % (field, number), count)
               for number, count in enumerate(counts)]
    return Distribution(field, field.title(), buckets, multiple_answers,
                        None if multiple_answers else sum(counts))


def months(incoming, completed, first=FIRST):
    return [MonthRow(first_of_month(first, number), one, done)
            for number, (one, done) in enumerate(zip(incoming, completed))]


def measures(number=4):
    return [Measure(number=100 + case, url='/incidents/station/%d/' % (100 + case),
                    reported=date(2026, 1, 10 + case), title='Titel %d' % case,
                    measures='Text %d' % case) for case in range(number)]


def make_report(**changes):
    """An evaluation of the three months of the first quarter in which nothing is small: 40
    incoming (13, 14, 13), 30 completed (10 a month), every distribution adds up to the 40."""
    report = Report(
        period=Period(FIRST, date(2026, 3, 31)), group_id=None, incoming=40, completed=30,
        open_end=5, published=4, unprocessed=6, reaction=Durations(2.5, 20),
        processing=Durations(10.0, 18), monthly=months([13, 14, 13], [10, 10, 10]),
        protocol_start=date(2025, 12, 1), status_changed=True,
        distributions=[distribution('area', [20, 20]),
                       distribution('category', [30, 25, 10], multiple_answers=True),
                       distribution('preventability', [10, 10, 20]),
                       distribution('risk', [5, 15, 20]), distribution('frequency', [0, 10, 30]),
                       distribution('hazard', [8, 12, 20])],
        measures=measures(4))
    return replace(report, **changes)


def counts(report, field):
    [found] = [one for one in report.distributions if one.field == field]
    return [bucket.count for bucket in found.buckets]


class NoSmallNumberTest(SimpleTestCase):

    def test_numbers_that_are_not_small_stay_numbers(self):
        shown = redact(make_report(), 3)
        self.assertEqual((shown.incoming, shown.completed, shown.open_end, shown.published,
                          shown.unprocessed), (40, 30, 5, 4, 6))
        self.assertEqual((shown.reaction, shown.processing),
                         (Durations(2.5, 20), Durations(10.0, 18)))
        self.assertEqual([(row.incoming, row.completed) for row in shown.monthly],
                         [(13, 10), (14, 10), (13, 10)])
        self.assertEqual(counts(shown, 'area'), [20, 20])
        self.assertEqual(counts(shown, 'frequency'), [0, 10, 30])

    def test_the_total_of_a_distribution_is_the_one_that_is_shown(self):
        shown = redact(make_report(), 3)
        self.assertEqual([one.total for one in shown.distributions],
                         [40, None, 40, 40, 40, 40])

    def test_the_rest_of_the_report_is_what_it_was(self):
        report = make_report()
        shown = redact(report, 3)
        for name in ('period', 'group_id', 'protocol_start', 'status_changed'):
            self.assertEqual(getattr(shown, name), getattr(report, name), name)
        self.assertEqual([(one.field, one.title, one.multiple_answers)
                          for one in shown.distributions],
                         [(one.field, one.title, one.multiple_answers)
                          for one in report.distributions])
        self.assertEqual([[(b.key, b.label) for b in one.buckets] for one in shown.distributions],
                         [[(b.key, b.label) for b in one.buckets] for one in report.distributions])

    def test_the_report_is_not_changed_but_a_new_one_returned(self):
        report = make_report()
        before = repr(report)
        shown = redact(report, 3)
        self.assertEqual(repr(report), before)
        self.assertIsNot(shown, report)
        with self.assertRaises(FrozenInstanceError):
            shown.incoming = 0

    def test_a_minimum_of_1_hides_nothing(self):
        report = make_report(incoming=3, completed=2, published=1, unprocessed=1,
                             monthly=months([1, 1, 1], [1, 1, 0]),
                             distributions=[distribution('area', [1, 1, 1])],
                             reaction=Durations(4.0, 1), processing=Durations(9.0, 2),
                             measures=measures(1))
        shown = redact(report, 1)
        self.assertEqual((shown.incoming, shown.completed, shown.published, shown.unprocessed),
                         (3, 2, 1, 1))
        self.assertEqual(counts(shown, 'area'), [1, 1, 1])
        self.assertEqual((shown.reaction, shown.processing),
                         (Durations(4.0, 1), Durations(9.0, 2)))
        self.assertEqual(len(shown.measures), 1)

    def test_a_larger_minimum_hides_more(self):
        shown = redact(make_report(open_end=4, published=6), 5)
        self.assertEqual(shown.open_end, '< 5')
        self.assertEqual(shown.published, 6)

    def test_a_number_that_is_a_text_after_redact_is_never_a_number_again(self):
        shown = redact(make_report(open_end=2), 3)
        self.assertIsInstance(shown.open_end, str)
        self.assertIs(type(shown.incoming), int)


class SingleFiguresTest(SimpleTestCase):

    def test_open_is_hidden_when_small_and_0_stays_0(self):
        self.assertEqual(redact(make_report(open_end=2), 3).open_end, '< 3')
        self.assertEqual(redact(make_report(open_end=1), 3).open_end, '< 3')
        self.assertEqual(redact(make_report(open_end=0), 3).open_end, 0)
        self.assertEqual(redact(make_report(open_end=3), 3).open_end, 3)

    def test_open_is_not_recorded_where_it_is_not(self):
        self.assertIsNone(redact(make_report(open_end=None), 3).open_end)

    def test_the_times_of_fewer_than_the_minimum_incidents_give_neither_median_nor_count(self):
        shown = redact(make_report(reaction=Durations(7.0, 2), processing=Durations(1.5, 1)), 3)
        self.assertEqual(shown.reaction, Durations(None, '< 3'))
        self.assertEqual(shown.processing, Durations(None, '< 3'))

    def test_the_times_of_enough_incidents_stay(self):
        shown = redact(make_report(reaction=Durations(7.0, 3), processing=Durations(None, 0)), 3)
        self.assertEqual(shown.reaction, Durations(7.0, 3))
        self.assertEqual(shown.processing, Durations(None, 0))

    def test_times_that_are_not_recorded_stay_not_recorded(self):
        shown = redact(make_report(completed=None, reaction=None, processing=None,
                                   monthly=months([13, 14, 13], [None, None, None])), 3)
        self.assertEqual((shown.completed, shown.reaction, shown.processing), (None, None, None))
        self.assertEqual([row.completed for row in shown.monthly], [None] * 3)

    def test_the_incidents_still_without_processing_are_hidden_when_few(self):
        self.assertEqual(redact(make_report(unprocessed=2), 3).unprocessed, '< 3')
        self.assertEqual(redact(make_report(unprocessed=0), 3).unprocessed, 0)
        self.assertEqual(redact(make_report(unprocessed=20), 3).unprocessed, 20)
        self.assertEqual(redact(make_report(unprocessed=40), 3).unprocessed, 40)

    def test_the_incidents_still_without_processing_do_not_give_away_those_that_were_processed(self):
        # 40 incoming and 38 still new: the 2 that are not follow from the two numbers
        shown = redact(make_report(unprocessed=38), 3)
        self.assertEqual(shown.unprocessed, STAR)
        self.assertEqual(shown.incoming, 40)
        # 39 of 40: a 1 that is not new would be worked out the same way
        self.assertEqual(redact(make_report(unprocessed=39), 3).unprocessed, STAR)

    def test_a_small_complement_that_is_withheld_with_the_total_withholds_the_incoming(self):
        # 2 incoming, 1 of them new: both numbers are 1
        report = make_report(
            incoming=2, completed=0, unprocessed=1, monthly=months([1, 1, 0], [0, 0, 0]),
            distributions=[distribution('area', [2])], published=0, measures=[])
        shown = redact(report, 3)
        self.assertEqual(shown.incoming, STAR)
        self.assertEqual(shown.unprocessed, '< 3')


class PublishedTest(SimpleTestCase):

    def test_a_published_number_below_the_minimum_is_hidden_and_so_is_the_list(self):
        for number in (1, 2):
            shown = redact(make_report(published=number, measures=measures(number)), 3)
            self.assertEqual(shown.published, '< 3', number)
            self.assertEqual(shown.measures, [], number)

    def test_with_enough_published_cases_the_list_stays(self):
        shown = redact(make_report(published=3, measures=measures(3)), 3)
        self.assertEqual(shown.published, 3)
        self.assertEqual([one.title for one in shown.measures], ['Titel 0', 'Titel 1', 'Titel 2'])

    def test_without_a_published_case_there_is_none_to_list(self):
        shown = redact(make_report(published=0, measures=[]), 3)
        self.assertEqual((shown.published, shown.measures), (0, []))

    def test_the_number_of_the_report_and_its_address_and_the_day_are_gone(self):
        # the numbers of the reports run over all departments, the day is more than the public
        # list of cases says: month and year
        shown = redact(make_report(published=4, measures=measures(4)), 3)
        for original, case in zip(measures(4), shown.measures):
            self.assertIsNone(case.number)
            self.assertEqual(case.url, '')
            self.assertEqual(case.reported, first_of_month(original.reported))
            self.assertEqual((case.title, case.measures), (original.title, original.measures))

    def test_the_figure_and_the_list_always_agree(self):
        for published in range(0, 8):
            shown = redact(make_report(published=published, measures=measures(published)), 3)
            if isinstance(shown.published, int):
                self.assertEqual(len(shown.measures), shown.published, published)
            else:
                self.assertEqual(shown.measures, [], published)


class SeriesTest(SimpleTestCase):

    def test_a_small_month_costs_the_smallest_other_month_too(self):
        # 40 - 20 - 18 would give the 2 away
        report = make_report(monthly=months([2, 18, 20], [10, 10, 10]))
        shown = redact(report, 3)
        self.assertEqual([row.incoming for row in shown.monthly], ['< 3', STAR, 20])
        self.assertEqual(shown.incoming, 40)

    def test_the_completed_months_are_a_series_of_their_own_with_the_total_of_the_completed(self):
        report = make_report(completed=6, monthly=months([13, 14, 13], [1, 0, 5]))
        shown = redact(report, 3)
        self.assertEqual([row.completed for row in shown.monthly], ['< 3', 0, STAR])
        self.assertEqual(shown.completed, 6)

    def test_months_that_are_not_recorded_are_no_part_of_the_series(self):
        report = make_report(completed=6, monthly=months([13, 14, 13], [None, 1, 5]))
        shown = redact(report, 3)
        self.assertEqual([row.completed for row in shown.monthly], [None, '< 3', STAR])
        self.assertEqual(shown.completed, 6)

    def test_a_distribution_with_one_small_cell_hides_another_one_as_well(self):
        report = make_report(distributions=[distribution('risk', [2, 18, 20])])
        shown = redact(report, 3)
        self.assertEqual(counts(shown, 'risk'), ['< 3', STAR, 20])

    def test_two_small_cells_that_cannot_be_told_apart_are_all_that_goes(self):
        report = make_report(distributions=[distribution('risk', [1, 2, 37])])
        self.assertEqual(counts(redact(report, 3), 'risk'), ['< 3', '< 3', 37])

    def test_zero_cells_are_shown(self):
        report = make_report(distributions=[distribution('risk', [0, 2, 0, 18, 20])])
        self.assertEqual(counts(redact(report, 3), 'risk'), [0, '< 3', 0, STAR, 20])

    def test_the_categories_have_no_total_so_only_the_small_ones_are_hidden(self):
        report = make_report(distributions=[distribution('category', [2, 10, 20], True)])
        shown = redact(report, 3)
        self.assertEqual(counts(shown, 'category'), ['< 3', 10, 20])
        self.assertIsNone(shown.distributions[0].total)

    def test_a_total_that_one_series_withholds_is_withheld_in_every_place_that_shows_it(self):
        # three cells of 1 under a total of 3 can only be 1, 1 and 1: the total goes
        report = make_report(
            incoming=3, completed=0, published=0, unprocessed=0, measures=[],
            monthly=months([1, 1, 1], [0, 0, 0]),
            distributions=[distribution('area', [1, 1, 1]), distribution('risk', [3]),
                           distribution('category', [1, 1, 1], True)])
        shown = redact(report, 3)
        self.assertEqual(shown.incoming, STAR)
        self.assertEqual([row.incoming for row in shown.monthly], ['< 3'] * 3)
        self.assertEqual([one.total for one in shown.distributions], [STAR, STAR, None])
        # the other distribution had no reason to hide it, and must not show it now
        self.assertEqual(counts(shown, 'risk'), [3])

    def test_a_small_total_is_hidden_like_a_small_number(self):
        report = make_report(
            incoming=2, completed=0, published=0, unprocessed=0, measures=[],
            monthly=months([2, 0, 0], [0, 0, 0]), distributions=[distribution('area', [2, 0])])
        shown = redact(report, 3)
        self.assertEqual(shown.incoming, '< 3')
        self.assertEqual(shown.distributions[0].total, '< 3')
        self.assertEqual([row.incoming for row in shown.monthly], ['< 3', 0, 0])

    def test_a_report_whose_series_do_not_add_up_to_the_incoming_is_still_redacted(self):
        # the numbers come from several queries: an incident that is reported between them
        # makes the incoming one more than the cells. That is no reason to fail, and nothing is
        # said that the cells would have kept back
        report = make_report(incoming=41, monthly=months([2, 18, 20], [10, 10, 10]))
        shown = redact(report, 3)
        self.assertEqual(shown.incoming, STAR)
        self.assertEqual([row.incoming for row in shown.monthly], ['< 3', 18, 20])
        self.assertEqual([one.total for one in shown.distributions], [STAR, None, STAR, STAR,
                                                                       STAR, STAR])


class SameInOutputTest(SimpleTestCase):
    """Two Reports that differ only in cells that are hidden say the same."""

    def pair(self):
        # 1 and 2 swap places, the totals stay: months, a distribution, the categories, the
        # published cases and the incidents that are still new
        a = make_report(
            monthly=months([1, 2, 37], [10, 10, 10]), published=1, measures=measures(1),
            unprocessed=1, distributions=[
                distribution('area', [1, 2, 37]), distribution('category', [1, 12, 20], True),
                distribution('risk', [2, 1, 37])])
        b = make_report(
            monthly=months([2, 1, 37], [10, 10, 10]), published=2, measures=measures(2),
            unprocessed=2, distributions=[
                distribution('area', [2, 1, 37]), distribution('category', [2, 12, 20], True),
                distribution('risk', [1, 2, 37])])
        return a, b

    def test_the_reports_differ(self):
        a, b = self.pair()
        self.assertNotEqual(a, b)

    def test_what_is_made_of_them_is_the_same(self):
        a, b = self.pair()
        self.assertEqual(redact(a, 3), redact(b, 3))

    def test_so_are_the_charts(self):
        a, b = (redact(report, 3) for report in self.pair())
        self.assertEqual(chart_data.month_columns(a.monthly), chart_data.month_columns(b.monthly))
        for one, other in zip(a.distributions, b.distributions):
            self.assertEqual(chart_data.bucket_bars(one.buckets),
                             chart_data.bucket_bars(other.buckets))

    def test_a_cell_that_differs_in_what_is_shown_is_no_longer_the_same(self):
        a, _ = self.pair()
        other = replace(a, monthly=months([1, 2, 37], [10, 10, 9]), completed=29)
        self.assertNotEqual(redact(a, 3), redact(other, 3))


class ChartsOfRedactedNumbersTest(SimpleTestCase):

    def setUp(self):
        # the texts of the charts are in the active language, and an earlier test may have left
        # German active in this thread
        self.enterContext(translation.override('en'))

    def test_a_hidden_number_gets_no_bar_and_no_say_in_the_scale(self):
        chart = chart_data.bucket_bars([Bucket(1, 'a', '< 3'), Bucket(2, 'b', 10),
                                        Bucket(3, 'c', STAR), Bucket(4, 'd', 5)])
        self.assertEqual([(row.label, row.text, row.length) for row in chart.bars],
                         [('a', '< 3', 0), ('b', '10', 100), ('c', '*', 0), ('d', '5', 50)])

    def test_with_nothing_but_hidden_numbers_there_is_no_bar_and_no_division_by_zero(self):
        chart = chart_data.bucket_bars([Bucket(1, 'a', '< 3'), Bucket(2, 'b', '< 3')])
        self.assertEqual([row.length for row in chart.bars], [0, 0])

    def test_a_hidden_column_is_a_gap_in_the_scale_and_marked_as_missing(self):
        chart = chart_data.month_columns(months(['< 3', 20, STAR], [0, 5, '< 3']))
        columns = [column for group in chart.groups for column in group.columns]
        self.assertEqual([(column.text, column.missing) for column in columns],
                         [('< 3', True), ('0', False), ('20', False), ('5', False),
                          ('*', True), ('< 3', True)])
        self.assertEqual([column.height for column in columns if column.missing], [0, 0, 0])
        self.assertEqual(max(tick.value for tick in chart.ticks), 20)

    def test_the_scale_is_made_of_what_is_shown_only(self):
        # a hidden cell cannot be higher than a shown one, and cannot set the axis
        hidden = chart_data.month_columns(months(['< 3', 6, 6], [6, 6, 6]))
        none = chart_data.month_columns(months([0, 6, 6], [6, 6, 6]))
        self.assertEqual(hidden.ticks, none.ticks)
        self.assertEqual(hidden.span, none.span)

    def test_not_recorded_is_still_a_gap(self):
        chart = chart_data.month_columns(months([1, 1], [None, 3]))
        self.assertEqual([(c.text, c.missing) for c in chart.groups[0].columns],
                         [('1', False), ('not recorded', True)])
