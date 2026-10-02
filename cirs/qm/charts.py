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

"""The geometry of the charts on the QM pages: horizontal bars and grouped columns.

Pure functions that turn rows of numbers into lengths and positions. The partials
cirs/templates/cirs/qm/_bars.html and _columns.html draw them: the bars as HTML text with a thin
inline SVG for each bar area, the columns as one inline SVG. The drawing scales with its
container, so all numbers here are view box units, not pixels.

What is shown and what is not: every element carries the number (``value``) and the text that
stands for it (``text``). The number only sets the length. ``value=None`` means the number must
not be shown (suppressed, not recorded): the element gets no length and does not count for the
scale. The geometry never holds a number, only the text; the labels, the table and the title
and description of the image are made from ``text`` alone, so what is hidden cannot be read from
the markup. The caller builds ``title`` and ``desc`` from texts too.

Scale and legibility: the longest bar, the tallest column fills its plot (the axis ends at the
data, not at a round number), a value of 0 gets no length, a value above 0 at least MIN_LENGTH so
that it cannot be mistaken for 0. Coordinates are rounded to one decimal to keep the markup
short. The columns are drawn in a view box of WIDTH units, which is about what a phone leaves
for a chart: its type (FONT units) is then at least 11 px there. Values that do not fit over the
columns are turned, and left out of the drawing if they do not fit turned either; the table below
the chart has them all. Axis labels that would touch their neighbours are left out of the drawing
as well.

A column without a number (``Column.missing``) is drawn as a dashed line along the zero line, in
the colour of its series, whether the texts stand over the columns or not. A month with 0 has
nothing there, so with the table closed (print) a month that is not shown cannot be taken for a
month without events. The line says only that there is no number; the page tells what it means.

Include contract of the two partials:

    {% include "cirs/qm/_bars.html" with chart=chart chart_id="where" title=title desc=desc label_header=header %}

    chart         the result of bar_chart or column_chart; without rows nothing is drawn
    chart_id      unique on the page: the ids of the elements that name the chart start with it
    title         what the chart shows: the name of the image and the caption of the table
    desc          the key message in one sentence: the description of the image
    label_header  heading of the first column of the table
    value_header  _bars.html only: heading of the second column (default "Number")
    table_open    optional: shows the table without a click, for print
"""

from dataclasses import dataclass
from itertools import count
from math import ceil

WIDTH = 288          # Width of the view box of the columns.
FONT = 13            # Height of a label (--ui-fs-xs in labcirs.css) ...
CHAR = 7.8           # ... and the width of one of its characters, estimated. Placement only.
INK = 10             # What a turned digit takes in width.
MIN_LENGTH = 2       # Shortest bar or column for a value above 0.
MAX_COL_WIDTH = 30   # A chart with one or two groups does not get slabs.
MARGIN = 4           # Keeps strokes and the first label inside the view box.
TICK = 4             # Length of an axis mark.
AXIS_H = 24          # Room below the plot for the labels.


@dataclass(frozen=True)
class Tick:
    """An axis mark: the value and its height."""
    value: int
    pos: float


def _round(number):
    return round(number, 1)


def _top(values):
    """The largest value that is shown."""
    return max((value for value in values if value is not None), default=0)


def _length(value, top, span):
    """The length of a bar for value when top fills span."""
    if value is None or value <= 0 or top <= 0:
        return 0
    return max(MIN_LENGTH, _round(value / top * span))


def _ticks(top, span, base):
    """Axis marks at round values from 0 up to top; base is where 0 lies, the marks rise from it."""
    # 1, 2, 5, 10, 20, 50 ...: the first step that needs at most five intervals
    candidates = (factor * 10 ** power for power in count() for factor in (1, 2, 5))
    step = next(candidate for candidate in candidates if top <= 5 * candidate)
    return [Tick(value, _round(base - value / top * span) if top else base)
            for value in range(0, top + 1, step)]


# --- Horizontal bars -----------------------------------------------------------------------

BAR_WIDTH = 100  # The view box of the area of one bar: the longest bar is this long ...
BAR_HEIGHT = 10  # ... and CSS gives the area its height.


@dataclass(frozen=True)
class Bar:
    label: str
    value: int | None  # None: no bar, no say in the scale; the text is all there is.
    text: str          # What stands at the bar and in the table: "12", "< 3".


@dataclass(frozen=True)
class BarRow:
    label: str
    text: str
    length: float


@dataclass(frozen=True)
class BarChart:
    width: int   # View box of the area of one bar.
    height: int
    bars: list


def bar_chart(bars):
    """Horizontal bars, one for each Bar in the given order."""
    bars = list(bars)
    top = _top(bar.value for bar in bars)
    return BarChart(BAR_WIDTH, BAR_HEIGHT,
                    [BarRow(bar.label, bar.text, _length(bar.value, top, BAR_WIDTH))
                     for bar in bars])


# --- Grouped columns -----------------------------------------------------------------------

COL_PLOT_H = 140   # Height of the tallest column.
COL_LEGEND_H = 22  # Legend row above the plot.
COL_SWATCH = 12
COL_SHARE = 0.85   # Share of a category's width that its two columns take.


@dataclass(frozen=True)
class Cell:
    value: int | None  # As for Bar.
    text: str          # Short: it stands over the column ("12", "< 3", "–").


@dataclass(frozen=True)
class Series:
    name: str
    cells: list  # One Cell for each category.


@dataclass(frozen=True)
class Column:
    text: str
    height: float
    x: float
    y: float        # Top of the column.
    label_x: float  # Centre of the column.
    label_y: float  # Text: just above the column (it starts there if it is turned).
    missing: bool   # No number to show: the column is marked, so it does not read as 0.


@dataclass(frozen=True)
class Group:
    label: str
    label_x: float
    show_label: bool  # An axis label that would touch its neighbour is left out of the drawing.
    columns: tuple    # One Column for each series.


@dataclass(frozen=True)
class LegendEntry:
    x: float       # Swatch.
    text_x: float
    name: str


@dataclass(frozen=True)
class ColumnChart:
    width: int
    height: int
    left: int            # Axis line, and where the plot starts.
    right: int
    base: int            # Zero line.
    span: int            # Height of the column for the largest value.
    top: int             # End of the axis line.
    col_width: float
    show_values: bool    # The texts over the columns are drawn.
    vertical: bool       # They are turned by 90 degrees: the columns are too narrow.
    label_y: int         # Baseline of the axis labels.
    tick_start: int
    tick_label_x: int
    swatch: int
    legend_y: int
    legend_text_y: int   # Centre of the swatch.
    names: tuple
    legend: list
    groups: list
    ticks: list


def column_chart(categories, series):
    """Grouped columns: one group for each category, one column in it for each of two Series."""
    categories = list(categories)
    if len(series) != 2 or any(len(one.cells) != len(categories) for one in series):
        raise ValueError('Two series with one cell for each category are required.')
    top = _top(cell.value for one in series for cell in one.cells)
    longest = max((len(cell.text) for one in series for cell in one.cells), default=0)
    left = round(len(str(top)) * CHAR) + 5 * MARGIN
    right = WIDTH - MARGIN
    pitch = (right - left) / max(len(categories), 1)
    col_width = _round(min((pitch * COL_SHARE - 1) / 2, MAX_COL_WIDTH))
    group_width = 2 * col_width + 1
    # The texts stand upright if the longest fits over a column, else turned, else not at all.
    vertical = col_width < longest * CHAR + 2
    show_values = not vertical or col_width + 1 >= INK
    if not show_values:
        headroom = 2 * MARGIN
    elif vertical:
        headroom = ceil(longest * CHAR) + MARGIN
    else:
        headroom = FONT + MARGIN
    base = COL_LEGEND_H + headroom + COL_PLOT_H
    # Axis labels: every step-th one, so that neighbours do not touch.
    widest = max((len(label) for label in categories), default=0)
    step = max(1, ceil((widest * CHAR + 2) / pitch))

    def column(cell, x):
        height = _length(cell.value, top, COL_PLOT_H)
        y = _round(base - height)
        return Column(cell.text, height, _round(x), y, _round(x + col_width / 2), _round(y - MARGIN),
                      cell.value is None)

    groups = []
    for number, label in enumerate(categories):
        centre = _round(left + (number + 0.5) * pitch)
        half = len(label) * CHAR / 2
        shown = number % step == 0 and centre - half >= 0 and centre + half <= WIDTH
        x = left + number * pitch + (pitch - group_width) / 2
        groups.append(Group(label, centre, shown,
                            (column(series[0].cells[number], x),
                             column(series[1].cells[number], x + col_width + 1))))
    legend_y = (COL_LEGEND_H - COL_SWATCH) // 2
    legend = []
    x = MARGIN
    for one in series:
        legend.append(LegendEntry(x, x + COL_SWATCH + MARGIN, one.name))
        x += COL_SWATCH + MARGIN + _round(len(one.name) * CHAR) + 5 * MARGIN
    return ColumnChart(WIDTH, base + AXIS_H, left, right, base, COL_PLOT_H, base - COL_PLOT_H,
                       col_width, show_values, vertical, base + FONT + TICK + 2, left - TICK,
                       left - TICK - 2, COL_SWATCH, legend_y, legend_y + COL_SWATCH // 2,
                       tuple(one.name for one in series), legend, groups,
                       _ticks(top, COL_PLOT_H, base))
