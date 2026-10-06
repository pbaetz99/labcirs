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

"""The geometry of the charts (cirs.qm.charts) and the markup the two partials draw from it."""

import dataclasses
import re
from pathlib import Path

from django.conf import settings
from django.template.loader import render_to_string
from django.test import SimpleTestCase
from django.utils import translation

from cirs.qm import charts
from cirs.qm.charts import Bar, Cell, Series, Tick, bar_chart, column_chart

from .helpers import csp_violations
from .test_pages_report import css_text

MONTHS = ['Jan', 'Feb', 'Mär', 'Apr', 'Mai', 'Jun', 'Jul', 'Aug', 'Sep', 'Okt', 'Nov', 'Dez']
NAMES = ('Incoming', 'Completed')


def bar(label, value, text=None):
    """A bar; a value that is not shown (None) needs its text."""
    return Bar(label, value, str(value) if text is None else text)


def cell(value, text=None):
    return Cell(value, str(value) if text is None else text)


def column_args(rows, names=NAMES):
    """The arguments of column_chart for rows of (label, first, second) with plain numbers."""
    return ([label for label, *_ in rows],
            [Series(name, [cell(values[number]) for _, *values in rows])
             for number, name in enumerate(names)])


def columns_of(rows, names=NAMES):
    return column_chart(*column_args(rows, names))


class BarGeometryTest(SimpleTestCase):

    def test_largest_value_gets_the_full_length(self):
        chart = bar_chart([bar('a', 8), bar('b', 4), bar('c', 0)])
        self.assertEqual([row.length for row in chart.bars],
                         [chart.width, chart.width / 2, 0])

    def test_all_zero_draws_nothing_and_does_not_divide_by_zero(self):
        chart = bar_chart([bar('a', 0), bar('b', 0)])
        self.assertEqual([row.length for row in chart.bars], [0, 0])

    def test_no_rows(self):
        self.assertEqual(bar_chart([]).bars, [])

    def test_a_small_value_stays_visible(self):
        chart = bar_chart([bar('a', 100000), bar('b', 1), bar('c', 0)])
        self.assertEqual([row.length for row in chart.bars[1:]], [charts.MIN_LENGTH, 0])

    def test_a_hidden_value_gets_no_bar_and_does_not_set_the_scale(self):
        chart = bar_chart([bar('a', None, '< 3'), bar('b', 4), bar('c', None, '< 3')])
        self.assertEqual([row.length for row in chart.bars], [0, chart.width, 0])
        self.assertEqual([row.text for row in chart.bars], ['< 3', '4', '< 3'])

    def test_only_hidden_values(self):
        chart = bar_chart([bar('a', None, '< 3'), bar('b', None, '< 3')])
        self.assertEqual([row.length for row in chart.bars], [0, 0])

    def test_the_geometry_holds_no_value(self):
        # what is not shown must not be in the result, not even for a bar that is
        chart = bar_chart([bar('a', 12), bar('b', None, '< 3')])
        for row in chart.bars:
            self.assertEqual({field.name for field in dataclasses.fields(row)},
                             {'label', 'text', 'length'})

    def test_labels_are_kept_whole(self):
        chart = bar_chart([bar('x' * 100, 1), bar('short', 1)])
        self.assertEqual([row.label for row in chart.bars], ['x' * 100, 'short'])


class ColumnGeometryTest(SimpleTestCase):

    def test_largest_value_gets_the_full_height_and_zero_none(self):
        chart = columns_of([('Jan', 10, 5), ('Feb', 0, 20)])
        (jan, feb) = chart.groups
        self.assertEqual([c.height for c in jan.columns], [chart.span / 2, chart.span / 4])
        self.assertEqual([c.height for c in feb.columns], [0, chart.span])
        self.assertEqual(feb.columns[0].y, chart.base)
        self.assertEqual(feb.columns[1].y, chart.top)

    def test_all_zero_draws_nothing_and_does_not_divide_by_zero(self):
        chart = columns_of([('Jan', 0, 0), ('Feb', 0, 0)])
        self.assertEqual([c.height for g in chart.groups for c in g.columns], [0] * 4)
        self.assertEqual(chart.ticks, [Tick(0, chart.base)])

    def test_no_rows(self):
        self.assertEqual(columns_of([]).groups, [])

    def test_a_small_value_stays_visible(self):
        chart = columns_of([('Jan', 100000, 1)])
        self.assertEqual(chart.groups[0].columns[1].height, charts.MIN_LENGTH)

    def test_a_hidden_value_gets_no_column_and_does_not_set_the_scale(self):
        chart = column_chart(['Jan', 'Feb'], [Series('Incoming', [cell(None, '< 3'), cell(4)]),
                                              Series('Completed', [cell(8), cell(None, '–')])])
        (jan, feb) = chart.groups
        self.assertEqual([c.height for c in jan.columns], [0, chart.span])
        self.assertEqual([c.height for c in feb.columns], [chart.span / 2, 0])
        self.assertEqual([c.text for c in jan.columns + feb.columns], ['< 3', '8', '4', '–'])
        # nothing is drawn, so the text sits on the axis
        self.assertEqual(jan.columns[0].y, chart.base)
        self.assertEqual([tick.value for tick in chart.ticks], [0, 2, 4, 6, 8])

    def test_the_geometry_holds_no_value(self):
        chart = column_chart(['Jan'], [Series('Incoming', [cell(None, '< 3')]),
                                       Series('Completed', [cell(12)])])
        for column in chart.groups[0].columns:
            self.assertEqual({field.name for field in dataclasses.fields(column)},
                             {'text', 'height', 'x', 'y', 'label_x', 'label_y', 'missing'})

    def test_a_cell_without_a_number_is_missing_and_a_zero_is_not(self):
        chart = column_chart(['Jan', 'Feb'], [Series('Incoming', [cell(None, '< 3'), cell(0)]),
                                              Series('Completed', [cell(5), cell(None, '–')])])
        (jan, feb) = chart.groups
        self.assertEqual([c.missing for c in jan.columns + feb.columns],
                         [True, False, False, True])

    def test_ticks_rise_with_the_value(self):
        chart = columns_of([('Jan', 8, 2)])
        self.assertEqual([tick.value for tick in chart.ticks], [0, 2, 4, 6, 8])
        positions = [tick.pos for tick in chart.ticks]
        self.assertEqual(positions, sorted(positions, reverse=True))
        self.assertEqual(positions[0], chart.base)
        self.assertEqual(positions[-1], chart.top)

    def test_ticks_are_round_numbers_up_to_the_largest_value(self):
        for top, values in ((0, [0]), (1, [0, 1]), (3, [0, 1, 2, 3]), (9, [0, 2, 4, 6, 8]),
                            (12, [0, 5, 10]), (120, [0, 50, 100]),
                            (1000, [0, 200, 400, 600, 800, 1000])):
            with self.subTest(top=top):
                self.assertEqual([t.value for t in columns_of([('a', top, 0)]).ticks], values)

    def test_the_view_box_is_about_as_wide_as_a_phone(self):
        # At 320 px the page leaves 288 px, 254 px inside a card. The type is 13 units: from a
        # view box of 288 units it stays at 11 px or more in the card.
        self.assertLessEqual(charts.WIDTH, 288)
        self.assertGreaterEqual(charts.WIDTH, 240)
        self.assertEqual(columns_of([('Jan', 1, 2)]).width, charts.WIDTH)

    def test_columns_lie_inside_the_plot_and_do_not_overlap(self):
        chart = columns_of([(month, 3, 4) for month in MONTHS])
        columns = [c for g in chart.groups for c in g.columns]
        self.assertGreaterEqual(columns[0].x, chart.left)
        self.assertLessEqual(columns[-1].x + chart.col_width, chart.right)
        for left, right in zip(columns, columns[1:]):
            self.assertLessEqual(left.x + chart.col_width, right.x)

    def test_one_group_does_not_get_huge_columns(self):
        self.assertLessEqual(columns_of([('Jan', 3, 4)]).col_width, charts.MAX_COL_WIDTH)

    def test_wide_columns_carry_horizontal_values(self):
        chart = columns_of([('Jan', 12, 15), ('Feb', 3, 4), ('Mär', 1, 2)])
        self.assertTrue(chart.show_values)
        self.assertFalse(chart.vertical)
        # the text of a column is no wider than the space it has
        self.assertGreaterEqual(chart.col_width + 1, 2 * charts.CHAR)

    def test_narrow_columns_turn_the_values(self):
        chart = columns_of([('M%d' % number, 12, 15) for number in range(8)])
        self.assertTrue(chart.show_values)
        self.assertTrue(chart.vertical)
        # a label never starts below the top of its column, so it never covers the column ...
        for group in chart.groups:
            for column in group.columns:
                self.assertLessEqual(column.label_y, column.y)
        # ... and the tallest one has room for it above, below the legend
        self.assertGreaterEqual(chart.top - charts.MARGIN - 2 * charts.CHAR, charts.COL_LEGEND_H)

    def test_values_that_fit_neither_way_are_left_out(self):
        chart = columns_of([(month, 3, 4) for month in MONTHS])
        self.assertFalse(chart.show_values)
        # and the room above the columns is not kept free for them
        self.assertLessEqual(chart.top, charts.COL_LEGEND_H + 2 * charts.MARGIN)

    def test_the_longest_text_decides_how_the_values_stand(self):
        short = column_chart(['Jan', 'Feb', 'Mär'], [Series('Incoming', [cell(1)] * 3),
                                                     Series('Completed', [cell(2)] * 3)])
        long = column_chart(['Jan', 'Feb', 'Mär'], [Series('Incoming', [cell(1, 'n/a')] * 3),
                                                    Series('Completed', [cell(2, 'not recorded')] * 3)])
        self.assertFalse(short.vertical)
        self.assertTrue(long.vertical)
        # the turned text of 12 characters needs room above the plot
        self.assertGreaterEqual(long.top - charts.COL_LEGEND_H, len('not recorded') * charts.CHAR)

    def test_axis_labels_are_thinned_out_when_they_do_not_fit(self):
        long = columns_of([('%s 2026' % month, 3, 4) for month in MONTHS])
        shown = [g.label for g in long.groups if g.show_label]
        self.assertTrue(0 < len(shown) < len(MONTHS))
        self.assertEqual(shown[0], 'Jan 2026')
        short = columns_of([(month, 3, 4) for month in 'ABCDEFGHIJKL'])
        self.assertTrue(all(g.show_label for g in short.groups))

    def test_shown_axis_labels_do_not_touch_and_stay_inside_the_view_box(self):
        for labels in (['%s 2026' % month for month in MONTHS], MONTHS, ['Januar 2026'] * 12,
                       ['Jan 2026', 'Feb 2026'], ['Jan']):
            chart = columns_of([(label, 3, 4) for label in labels])
            shown = [g for g in chart.groups if g.show_label]
            with self.subTest(labels=labels[:2], count=len(labels)):
                self.assertTrue(shown)
                for group in shown:
                    half = len(group.label) * charts.CHAR / 2
                    self.assertGreaterEqual(group.label_x - half, 0)
                    self.assertLessEqual(group.label_x + half, chart.width)
                for left, right in zip(shown, shown[1:]):
                    self.assertLessEqual(left.label_x + len(left.label) * charts.CHAR / 2,
                                         right.label_x - len(right.label) * charts.CHAR / 2)

    def test_legend_entries_do_not_overlap(self):
        chart = columns_of([('Jan', 3, 4)], ('Incoming reports', 'Completed'))
        first, second = chart.legend
        self.assertEqual(first.name, 'Incoming reports')
        self.assertGreater(second.x, first.x + len('Incoming reports') * charts.CHAR)
        self.assertLess(second.x + len('Completed') * charts.CHAR, chart.width)

    def test_two_series_of_the_length_of_the_categories_are_required(self):
        with self.assertRaises(ValueError):
            column_chart(['Jan'], [Series('Incoming', [cell(1)])])
        with self.assertRaises(ValueError):
            column_chart(['Jan'], [Series('a', [cell(1)]), Series('b', [cell(1)]),
                                   Series('c', [cell(1)])])
        with self.assertRaises(ValueError):
            column_chart(['Jan', 'Feb'], [Series('a', [cell(1), cell(2)]), Series('b', [cell(1)])])
        with self.assertRaises(ValueError):
            column_chart(['Jan', 'Feb'], [Series('a', [cell(1)]), Series('b', [cell(1)])])


def rendered(template, **context):
    context.setdefault('chart_id', 'diagramm')
    context.setdefault('title', 'Synthetic title')
    context.setdefault('desc', 'Synthetic key message')
    context.setdefault('label_header', 'Label')
    return render_to_string('cirs/qm/_%s.html' % template, context)


def bars(rows, **context):
    return rendered('bars', chart=bar_chart(rows), **context)


def columns(rows, names=NAMES, **context):
    return rendered('columns', chart=columns_of(rows, names), **context)


def ids(html):
    return re.findall(r'\sid="([^"]*)"', html)


def svgs(html):
    return re.findall(r'<svg\b.*?</svg>', html, re.S)


class RenderTest(SimpleTestCase):

    def check_named(self, html, tag):
        """The element of the given tag is an image with a name and a description in the page."""
        self.assertRegex(html, r'<%s\b[^>]*\srole="img"' % tag)
        names = re.search(r'<%s\b[^>]*\saria-labelledby="([^"]*)"' % tag, html).group(1).split()
        self.assertEqual(len(names), 2)
        title = re.search(r'<[^>]* id="%s"[^>]*>(.*?)</' % names[0], html, re.S)
        desc = re.search(r'<[^>]* id="%s"[^>]*>(.*?)</' % names[1], html, re.S)
        self.assertEqual(title.group(1), 'Synthetic title')
        self.assertEqual(desc.group(1), 'Synthetic key message')

    def table(self, html):
        details = re.search(r'<details\b[^>]*>(.*?)</details>', html, re.S).group(1)
        self.assertRegex(details, r'^\s*<summary>[^<]+(?:<span class="ui-visually-hidden">[^<]*</span>)?</summary>')
        return re.search(r'<table\b.*?</table>', details, re.S).group(0)

    def cells(self, table):
        return [[re.sub(r'<[^>]+>', '', cell).strip()
                 for cell in re.findall(r'<t[hd]\b[^>]*>.*?</t[hd]>', row, re.S)]
                for row in re.findall(r'<tr\b.*?</tr>', table, re.S)]

    def test_bars_are_an_image_with_the_numbers_as_a_table(self):
        html = bars([bar('Ward A', 12), bar('Ward B', 0), bar('No entry', 3)],
                    label_header='Where', value_header='Reports')
        self.check_named(html, 'div')
        table = self.table(html)
        self.assertRegex(table, r'<caption[^>]*>Synthetic title</caption>')
        self.assertEqual(self.cells(table), [['Where', 'Reports'], ['Ward A', '12'],
                                             ['Ward B', '0'], ['No entry', '3']])
        self.assertEqual(len(re.findall(r'<th scope="col"', table)), 2)
        self.assertEqual(len(re.findall(r'<th scope="row"', table)), 3)
        self.assertEqual(csp_violations(html), [])

    def test_bars_write_labels_and_values_as_text_and_draw_only_the_areas(self):
        html = bars([bar('Ward A', 12), bar('Ward B', 0), bar('No entry', 3)])
        drawing = html[:html.index('<details')]
        labels = re.findall(r'<[^>]* class="ui-diagramm__zeilenname"[^>]*>([^<]*)<', drawing)
        values = re.findall(r'<[^>]* class="ui-diagramm__zahl"[^>]*>([^<]*)<', drawing)
        self.assertEqual(labels, ['Ward A', 'Ward B', 'No entry'])
        self.assertEqual(values, ['12', '0', '3'])
        self.assertEqual(len(svgs(drawing)), 3)
        for svg in svgs(drawing):
            self.assertNotIn('<text', svg)
            self.assertNotIn('<title', svg)
            self.assertRegex(svg, r'<svg\b[^>]*\saria-hidden="true"')
        # a bar for each value above 0, none for 0
        self.assertEqual(len(re.findall(r'<rect\b', drawing)), 2)
        self.assertNotRegex(html, r'<rect[^>]*\s(?:width|height)="0(?:\.0)?"')

    def test_a_long_label_is_not_shortened(self):
        label = 'Department of ' + 'long ' * 20
        html = bars([bar(label, 1)])
        self.assertEqual(html.count(label), 2)  # drawing and table

    def test_a_hidden_value_shows_only_its_text(self):
        html = bars([bar('Ward A', None, '< 3'), bar('Ward B', 9)], label_header='Where')
        drawing = html[:html.index('<details')]
        self.assertEqual(len(re.findall(r'<rect\b', drawing)), 1)
        self.assertEqual(re.findall(r'<[^>]* class="ui-diagramm__zahl"[^>]*>([^<]*)<', drawing),
                         ['&lt; 3', '9'])
        self.assertEqual(self.cells(self.table(html))[1:], [['Ward A', '&lt; 3'], ['Ward B', '9']])

    def test_all_zero_renders(self):
        html = bars([bar('Ward A', 0), bar('Ward B', 0)])
        self.check_named(html, 'div')
        self.assertNotIn('<rect', html)

    def test_no_rows_render_nothing(self):
        self.assertEqual(bars([]).strip(), '')
        self.assertEqual(columns([]).strip(), '')

    def test_a_German_page_gets_dots_in_the_coordinates(self):
        with translation.override('de'):
            html = (bars([bar('Ward A', 7), bar('Ward B', 3)])
                    + columns([('Jan', 7, 3), ('Feb', 3, 1)]))
        self.assertIn('Zahlen als Tabelle', html)
        self.assertNotRegex(html, r'="[^"]*\d,\d[^"]*"')
        self.assertRegex(html, r'<rect[^>]*\swidth="\d+\.\d"')

    def test_summary_and_default_value_header_in_English(self):
        with translation.override('en'):
            html = bars([bar('Ward A', 7)])
        self.assertIn('<summary>Numbers as a table<span class="ui-visually-hidden">: Synthetic title</span>'
                      '</summary>', html)
        self.assertEqual(self.cells(self.table(html))[0], ['Label', 'Number'])

    def test_the_table_is_closed_unless_asked_otherwise(self):
        self.assertNotRegex(bars([bar('A', 1)]), r'<details\b[^>]*\sopen')
        self.assertRegex(bars([bar('A', 1)], table_open=True), r'<details\b[^>]*\sopen')
        self.assertRegex(columns([('A', 1, 2)], table_open=True), r'<details\b[^>]*\sopen')

    def test_labels_and_texts_from_the_database_are_escaped(self):
        for html in (bars([bar('<b>"&', 1, '<i>')]),
                     rendered('columns', chart=column_chart(
                         ['<b>"&'], [Series('<u>', [cell(1, '<i>')]), Series('x', [cell(2)])]))):
            self.assertNotRegex(html, r'<[biu]>')
            self.assertIn('&lt;b&gt;&quot;&amp;', html)
            self.assertEqual(csp_violations(html), [])

    def test_charts_on_one_page_share_no_ids_and_every_name_resolves(self):
        page = (bars([bar('A', 1)], chart_id='wo') + columns([('A', 1, 2)], chart_id='verlauf')
                + bars([bar('B', 2)], chart_id='kategorie')
                + columns([('B', 3, 4)], chart_id='verlauf-2'))
        found = ids(page)
        self.assertEqual(len(found), len(set(found)))
        self.assertTrue(all(i.startswith(('wo-', 'verlauf-', 'kategorie-')) for i in found))
        names = [name for value in re.findall(r'aria-labelledby="([^"]*)"', page)
                 for name in value.split()]
        self.assertEqual(len(names), 8)
        for name in names:
            self.assertIn(name, found)

    def test_columns_are_an_image_with_the_numbers_as_a_table(self):
        html = columns([('Jan', 4, 2), ('Feb', 0, 7)], label_header='Month')
        self.check_named(html, 'svg')
        self.assertEqual(self.cells(self.table(html)),
                         [['Month', 'Incoming', 'Completed'], ['Jan', '4', '2'], ['Feb', '0', '7']])
        self.assertEqual(csp_violations(html), [])

    def test_hidden_cells_show_only_their_text(self):
        html = rendered('columns', chart=column_chart(
            ['Jan', 'Feb'], [Series('Incoming', [cell(None, '< 3'), cell(5)]),
                             Series('Completed', [cell(6), cell(None, '–')])]), label_header='Month')
        self.assertEqual(self.cells(self.table(html)),
                         [['Month', 'Incoming', 'Completed'], ['Jan', '&lt; 3', '6'],
                          ['Feb', '5', '–']])
        svg = svgs(html)[0]
        self.assertEqual(re.findall(r'<text class="ui-diagramm__wert"[^>]*>([^<]*)</text>', svg),
                         ['&lt; 3', '6', '5', '–'])
        # two columns and the two swatches of the legend
        self.assertEqual(len(re.findall(r'<rect class="ui-diagramm__reihe-\d"', svg)), 4)

    def test_a_missing_cell_is_marked_even_when_no_values_are_drawn(self):
        # twelve months: the texts over the columns do not fit and are left to the table, so
        # a month without a number must not look like a month with 0
        chart = column_chart(MONTHS, [Series('Incoming', [cell(None, '< 3'), cell(0)] + [cell(4)] * 10),
                                      Series('Completed', [cell(2)] * 11 + [cell(None, '–')])])
        self.assertFalse(chart.show_values)
        svg = svgs(rendered('columns', chart=chart))[0]
        self.assertNotIn('ui-diagramm__wert', svg)
        # one marker for each missing cell, in the class of its series, along the zero line
        self.assertEqual(len(re.findall(r'<path class="ui-diagramm__fehlt ui-diagramm__fehlt-1"', svg)), 1)
        self.assertEqual(len(re.findall(r'<path class="ui-diagramm__fehlt ui-diagramm__fehlt-2"', svg)), 1)
        jan, feb = chart.groups[:2]
        self.assertIn('d="M%s %sh%s"' % (jan.columns[0].x, chart.base, chart.col_width), svg)
        # the month with 0 (second column of Jan, first of Feb) has nothing of its own
        self.assertNotIn('d="M%s ' % feb.columns[0].x, svg)
        self.assertNotIn('d="M%s ' % jan.columns[1].x, svg)

    def test_a_missing_cell_and_a_zero_do_not_look_alike(self):
        def drawing(value):
            chart = column_chart(MONTHS, [Series('Incoming', [cell(value, '–')] + [cell(4)] * 11),
                                          Series('Completed', [cell(2)] * 12)])
            return svgs(rendered('columns', chart=chart))[0]
        self.assertNotEqual(drawing(None), drawing(0))
        self.assertIn('ui-diagramm__fehlt', drawing(None))
        self.assertNotIn('ui-diagramm__fehlt', drawing(0))

    def test_the_second_series_differs_by_outline_and_not_only_by_colour(self):
        html = columns([('Jan', 4, 2)])
        # no pattern, no reference to another element: only classes
        self.assertNotIn('<pattern', html)
        self.assertNotIn('<defs', html)
        self.assertNotIn('url(', html)
        self.assertEqual(html.count('ui-diagramm__reihe-2'), 2)  # column and legend entry
        self.assertEqual(html.count('ui-diagramm__reihe-1'), 2)

    def test_columns_have_a_legend_with_both_names(self):
        svg = svgs(columns([('Jan', 4, 2)]))[0]
        for name in NAMES:
            self.assertRegex(svg, r'<text class="ui-diagramm__beschriftung"[^>]*>%s</text>' % name)

    def test_columns_show_values_and_thin_axis_labels(self):
        html = columns([('A 2026', 3, 4), ('B 2026', 5, 6), ('C 2026', 1, 2)])
        self.assertEqual(len(re.findall(r'<text class="ui-diagramm__wert"', svgs(html)[0])), 6)
        self.assertEqual(len(self.cells(self.table(html))), 4)
        html = columns([('%s 2026' % month, 3, 4) for month in MONTHS])
        shown = re.findall(r'<text class="ui-diagramm__marke"[^>]*>([A-Z][^<]*)</text>',
                           svgs(html)[0])
        self.assertTrue(0 < len(shown) < 12)
        # the table has all twelve
        self.assertEqual(len(self.cells(self.table(html))), 13)

    def test_values_that_do_not_fit_are_left_to_the_table(self):
        html = columns([(month, 3, 4) for month in MONTHS])
        self.assertNotIn('ui-diagramm__wert', svgs(html)[0])
        self.assertEqual(self.cells(self.table(html))[1], ['Jan', '3', '4'])

    def test_rotated_value_labels_stay_in_the_view_box(self):
        html = columns([('M%d' % number, 12, 15) for number in range(8)])
        self.assertEqual(len(re.findall(r'transform="rotate\(-90 ', html)), 16)

    def test_every_class_has_a_rule(self):
        css = css_text()
        html = (bars([bar('A', 1)]) + columns([('A', 1, 2)])
                + rendered('columns', chart=column_chart(
                    ['A'], [Series('a', [cell(None, '–')]), Series('b', [cell(None, '–')])])))
        self.assertIn('ui-diagramm__fehlt-2', html)
        for token in set(' '.join(re.findall(r'\sclass="([^"]*)"', html)).split()):
            self.assertRegex(css, r'\.%s(?![\w-])' % re.escape(token), token)

    def test_the_parts_set_their_own_fill_and_stroke(self):
        # core.css draws every svg as a line icon (fill none, stroke in the text colour)
        css = css_text()
        for name in ('beschriftung', 'wert', 'marke', 'reihe-1'):
            rule = re.search(r'\.ui-diagramm__%s[^{]*\{([^}]*)\}' % name, css).group(1)
            self.assertRegex(rule, r'fill:\s*var\(--ui-', name)
            self.assertRegex(rule, r'stroke:\s*none', name)
        rule = re.search(r'\.ui-diagramm__reihe-2[^{]*\{([^}]*)\}', css).group(1)
        self.assertRegex(rule, r'fill:\s*var\(--ui-')
        self.assertRegex(rule, r'stroke:\s*var\(--ui-')
        self.assertRegex(rule, r'stroke-width:\s*[\d.]+')
        # the mark of a missing number is a dashed line in the colour of its series, no area
        rule = re.search(r'\.ui-diagramm__fehlt\s*\{([^}]*)\}', css).group(1)
        self.assertRegex(rule, r'fill:\s*none')
        self.assertRegex(rule, r'stroke-dasharray:\s*[\d. ]+')
        self.assertRegex(rule, r'stroke-width:\s*[\d.]+')
        for number in '12':
            rule = re.search(r'\.ui-diagramm__fehlt-%s\s*\{([^}]*)\}' % number, css).group(1)
            self.assertRegex(rule, r'stroke:\s*var\(--ui-')

    def test_the_charts_have_rules_for_print(self):
        # a black-and-white printer: series 1 solid, series 2 white with a black outline
        css = css_text()
        for name in ('reihe-1', 'reihe-2', 'beschriftung', 'fehlt'):
            self.assertRegex(css, r'@media print \{[^@]*\.ui-diagramm__%s' % name, name)

    def test_the_section_for_the_charts_names_no_colours(self):
        # colours come from the tokens (and the system colours of forced colours and print);
        # the pages are always light, so there is no theme to switch
        source = Path(settings.BASE_DIR, 'static/css/labcirs.css').read_text(encoding='utf-8')
        section = source[source.index('/* --- Charts of the QM pages') + 3:]
        section = re.split(r'\n/\* --- ', section, maxsplit=1)[0]
        section = re.sub(r'/\*.*?\*/', '', section, flags=re.S)
        self.assertIn('.ui-diagramm__reihe-1', section)
        self.assertNotRegex(section, r'#[0-9a-fA-F]{3,8}\b')
        self.assertNotRegex(section, r'\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\(')
        self.assertNotIn('prefers-color-scheme', section)
        self.assertNotIn('data-theme', section)
