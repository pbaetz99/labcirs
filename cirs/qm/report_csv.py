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

"""The CSV file of an evaluation: the numbers of a redacted Report in the long form
Section;Characteristic;Value.

One row for each number, so that the file can be filtered and counted in a spreadsheet. The
separator is the semicolon and the file starts with a byte order mark, which is what Excel needs
to read the umlauts and to split the columns in German. A median is written in the number format of
the language. A month is a text that is no date ("Incoming, January 2026"), because Excel would
turn "Jan 2026" into a date. A cell that starts with a character that makes a spreadsheet read a
formula is neutralised. There is no text of any report in it, and no measures: only numbers.
"""

import codecs
import csv

from django.conf import settings
from django.utils.formats import date_format, number_format
from django.utils.translation import gettext

from . import report_page

BOM = codecs.BOM_UTF8
FORMULA_STARTS = ('=', '+', '-', '@', '\t', '\r')


def filename(period):
    """The name of the download: the period only, in ASCII ("auswertung-2026-01-bis-2026-10.csv").
    Neither an area nor a department is named in it."""
    first, last = period.start.strftime('%Y-%m'), period.end.strftime('%Y-%m')
    return 'auswertung-%s.csv' % (first if first == last else '%s-bis-%s' % (first, last))


def neutralise(cell):
    """The cell as text that no spreadsheet takes for a formula: a leading apostrophe stops it."""
    cell = str(cell)
    return "'" + cell if cell.startswith(FORMULA_STARTS) else cell


def _cell(value):
    """A number as a cell: a whole number as it is, a median in the number format of the language
    (one decimal if it has one), a text (hidden, withheld) as it is."""
    if isinstance(value, float):
        return number_format(value, decimal_pos=0 if value.is_integer() else 1)
    return str(value)


def _logged(label, value, suffix):
    if value is None:
        return [(label, gettext('not recorded'))]
    return [('%s %s' % (label, suffix) if suffix else label, _cell(value))]


def _times(label, durations, suffix):
    if durations is None:
        return [(label, gettext('not recorded'))]
    if not durations.count:
        median = report_page.NO_DURATION
    elif durations.median is None:
        median = gettext('not stated')
    else:
        median = _cell(durations.median)
    rows = [(gettext('%(label)s, median in days') % {'label': label}, median),
            (gettext('%(label)s, count') % {'label': label}, _cell(durations.count))]
    return [('%s %s' % (name, suffix) if suffix else name, value) for name, value in rows]


def _head(export):
    report = export.report
    rows = [(gettext('Organisation'), settings.ORGANIZATION), (gettext('System'), settings.SITE_NAME),
            (gettext('Period'), report_page.period_text(report.period))]
    rows += [(gettext('Department'), department.name) for department in export.departments]
    if export.area:
        rows.append((gettext('Area'), export.area))
    rows.append((gettext('Created on'), export.today.isoformat()))
    return [(gettext('Report'), name, value) for name, value in rows]


def _figures(report):
    suffix = report_page.from_when(report)
    open_end = gettext('not recorded') if report.open_end is None else report.open_end
    rows = [(gettext('Incoming'), report.incoming)]
    rows += _logged(gettext('Completed'), report.completed, suffix)
    rows += [(gettext('Open (as of today)'), open_end), (gettext('Published'), report.published),
             (gettext('Still without processing'), report.unprocessed)]
    rows += _times(gettext('Reaction time'), report.reaction, suffix)
    rows += _times(gettext('Processing time'), report.processing, suffix)
    return [(gettext('Key figures'), name, _cell(value)) for name, value in rows]


def _months(report):
    section, rows = gettext('Monthly trend'), []
    for row in report.monthly:
        month = date_format(row.month, 'F Y')
        completed = gettext('not recorded') if row.completed is None else _cell(row.completed)
        rows.append((section, gettext('Incoming, %(month)s') % {'month': month}, _cell(row.incoming)))
        rows.append((section, gettext('Completed, %(month)s') % {'month': month}, completed))
    return rows


def _distributions(report):
    return [(gettext('Distribution: %(name)s') % {'name': one.title}, bucket.label,
             _cell(bucket.count))
            for one in report.distributions for bucket in one.buckets]


def rows(export):
    """The rows of the file, the head of it first."""
    report = export.report
    found = (_head(export) + _figures(report) + _months(report) + _distributions(report))
    notes = report_page.notes(report, settings.REPORT_MIN_CELL)
    found += [(gettext('Notes'), title, text) for title, text in notes]
    return [(gettext('Section'), gettext('Characteristic'), gettext('Value'))] + found


def write(response, export):
    """The file: the byte order mark, then the rows with the semicolon as the separator."""
    response.write(BOM)
    writer = csv.writer(response, delimiter=';')
    for row in rows(export):
        writer.writerow([neutralise(cell) for cell in row])
