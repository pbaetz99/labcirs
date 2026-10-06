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

"""The texts and charts of an evaluation, made from a Report and from nothing else.

The page, the print view and the CSV file say the same things in the same words, so the words are
made here once. Every function reads the numbers it shows from the Report it is given and never
adds up numbers of its own: with a redacted Report (see report.redact) no text can say more than
the Report does. A number can be a text there ("< 3" for a small one, "*" for one that is
withheld), which is shown as it is and never counted with.
"""

from django.template.defaultfilters import floatformat
from django.utils.formats import date_format
from django.utils.translation import gettext, ngettext

from . import chart_data, metrics

NO_DURATION = '–'


def period_text(period):
    first, last = (date_format(day, 'F Y') for day in period)
    if first == last:
        return first
    return gettext('%(first)s to %(last)s') % {'first': first, 'last': last}


def said(number):
    """A number as it is read out in a sentence: the mark of a withheld number is no word."""
    return gettext('not stated') if number == metrics.SECONDARY_MARK else number


def duration_text(durations):
    """The text "Median 7.5 days, count 4", a dash where there is no incident to take it from, and
    for a median that is hidden (too few incidents) the count alone."""
    if not durations.count:
        return NO_DURATION
    if durations.median is None:
        return gettext('Median not stated, count %(count)s') % {'count': durations.count}
    return ngettext('Median %(days)s day, count %(count)d',
                    'Median %(days)s days, count %(count)d',
                    1 if durations.median == 1 else 2) % {
        'days': floatformat(durations.median, '-1'), 'count': durations.count}


def from_when(report):
    """"(since 20.12.2025)" for a period that the status log covers only in part, else nothing: the
    figures that come from the log count from that day on."""
    started = report.protocol_start
    if started is not None and report.period.start < started <= report.period.end:
        return gettext('(since %(date)s)') % {
            'date': date_format(started, 'SHORT_DATE_FORMAT')}
    return ''


def _logged(label, value, suffix, text=False):
    """A figure that comes from the status log. For a period that ended before the log began there
    is nothing in it: "not recorded", which is not the same as 0."""
    if value is None:
        return {'label': label, 'value': gettext('not recorded'), 'text': True}
    return {'label': label, 'value': value, 'text': text, 'suffix': suffix}


def figures(report):
    """The key figures: label and value, whether the value is a text (it is set smaller) and, for a
    figure from the log, from when it counts."""
    open_end, suffix = report.open_end, from_when(report)
    return [
        {'label': gettext('Incoming'), 'value': report.incoming},
        _logged(gettext('Completed'), report.completed, suffix),
        {'label': gettext('Open (as of today)'),
         'value': gettext('not recorded') if open_end is None else open_end,
         'text': open_end is None},
        {'label': gettext('Published'), 'value': report.published},
        _logged(gettext('Reaction time'),
                None if report.reaction is None else duration_text(report.reaction), suffix, True),
        _logged(gettext('Processing time'),
                None if report.processing is None else duration_text(report.processing), suffix,
                True),
        {'label': gettext('Still without processing'), 'value': report.unprocessed},
    ]


def logged_since(report):
    """What is said where completions and times are not known for the whole period: that the
    status log has no entry yet, or since when it has them. Nothing if it covers the period."""
    started = report.protocol_start
    if started is None:
        return gettext('The log is not running yet.')
    if started > report.period.start:
        return gettext('Completions and times have been recorded since %(date)s.') % {
            'date': date_format(started, 'SHORT_DATE_FORMAT')}
    return ''


def recorded_note(report):
    """logged_since, and where the log runs in the period but holds no change of a status, that
    too: a 0 or a dash is then no gap in the log but all there is."""
    sentences = [logged_since(report)]
    started = report.protocol_start
    if started is not None and started > report.period.start:
        sentences.append(gettext('Reaction times exist only for reports received since the log '
                                 'began.'))
    if started is not None and started <= report.period.end and not report.status_changed:
        sentences.append(gettext('So far no status change has been recorded.'))
    return ' '.join(sentence for sentence in sentences if sentence)


def development(report, period):
    """The columns of the months with their texts, None if no month holds a report or a
    completion. The table opens by itself where the chart has no room for the numbers over its
    columns: the numbers are then only in the table."""
    rows = report.monthly
    if not any(row.incoming or row.completed for row in rows):
        return None
    if report.completed is None:
        desc = gettext('Reports in %(period)s: %(incoming)s received.') % {
            'period': period, 'incoming': said(report.incoming)}
    else:
        desc = gettext('Reports in %(period)s: %(incoming)s received, %(completed)s '
                       'completed.') % {'period': period, 'incoming': said(report.incoming),
                                        'completed': said(report.completed)}
    note = logged_since(report) if any(row.completed is None for row in rows) else ''
    chart = chart_data.month_columns(rows, 'M Y')
    return {'chart': chart,
            'title': gettext('Incoming and completed per month, %(period)s') % {
                'period': period},
            'desc': f'{desc} {note}'.strip(), 'label_header': gettext('Month'),
            'table_open': not chart.show_values}


def most(distribution):
    """The key message of a distribution: the cell with the most incidents among the cells that
    are shown. A cell that is hidden or withheld has no number to be compared."""
    shown = [bucket for bucket in distribution.buckets if isinstance(bucket.count, int)]
    top = max(shown, key=lambda bucket: bucket.count, default=None)
    if top is None or not top.count:
        return ''
    if isinstance(distribution.total, int):
        return gettext('Most: %(label)s with %(count)d of %(total)d.') % {
            'label': top.label, 'count': top.count, 'total': distribution.total}
    return gettext('Most: %(label)s with %(count)d.') % {'label': top.label, 'count': top.count}


def distributions(report, period):
    return [{'id': 'chart-' + distribution.field.replace('_', '-'), 'name': distribution.title,
             'chart': chart_data.bucket_bars(distribution.buckets),
             'title': f'{distribution.title}, {period}', 'desc': most(distribution),
             'note': metrics.MULTIPLE_ANSWERS_NOTE if distribution.multiple_answers else ''}
            for distribution in report.distributions]


def notes(report, min_cell):
    """The notes under a printed or exported evaluation, as (title, text): the rule of the small
    numbers, what the times are, since when the status log has them (if it does not cover the
    period), and that evaluations can be compared."""
    found = [
        (gettext('Small numbers'), gettext(
            'Numbers above 0 and below %(min)d are shown as “< %(min)d”, so that single reports '
            'cannot be traced back. Where a hidden number could otherwise be worked out from the '
            'numbers shown, a star (“*”) stands in place of a further number.')
         % {'min': min_cell}),
        (gettext('Times'), gettext(
            'Reaction time and processing time are medians in days. If fewer than %(min)d reports '
            'are behind them, neither the median nor the count is stated.') % {'min': min_cell}),
    ]
    log = recorded_note(report)
    if log:
        found.append((gettext('Status log'), log))
    found.append((gettext('Comparisons'), gettext(
        'Whoever compares evaluations for periods of their own choice, or for one area and for '
        '“All”, can work out small numbers from the differences. Such evaluations should not be '
        'passed on together.')))
    return found
