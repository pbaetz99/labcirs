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

"""The charts of the QM pages made from the numbers of metrics, with the texts that stand at them.

The overview and the evaluations draw the same two charts: the columns of the months and the bars
of a distribution. What is shared is here; what is said about a chart (its title and description)
is the business of the page, which knows its period.
"""

from django.utils.formats import date_format
from django.utils.translation import gettext

from .charts import Bar, Cell, Series, bar_chart, column_chart


def month_columns(rows, label_format='M'):
    """The columns of the months in `rows` (MonthRow): Incoming and Completed for each. A month
    before the status log began has no number of completions (None): its cell says "not recorded"
    and gets no column, so it is not taken for a month without completions. The months are named
    by `label_format`, a date format of Django."""
    not_recorded = gettext('not recorded')
    series = [
        Series(gettext('Incoming'), [Cell(row.incoming, str(row.incoming)) for row in rows]),
        Series(gettext('Completed'), [
            Cell(None, not_recorded) if row.completed is None
            else Cell(row.completed, str(row.completed)) for row in rows])]
    return column_chart([date_format(row.month, label_format) for row in rows], series)


def bucket_bars(buckets):
    """The bars of a distribution: one for each Bucket, in its order."""
    return bar_chart([Bar(bucket.label, bucket.count, str(bucket.count)) for bucket in buckets])
