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

"""The overview of the QM: what is new, open, waiting, and how it develops.

The numbers are the real ones, not suppressed: the QM sees the incidents one by one anyway. Every
number comes from the incidents of the departments of the reviewer (scoped_incidents), and every
address into the work list from worklist_url.
"""

from django.conf import settings
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext

from cirs.models import STATUS_CHOICES

from . import metrics
from .access import QMPage, scoped_incidents
from .charts import Bar, Cell, Series, bar_chart, column_chart
from .params import worklist_url

LIST_LENGTH = 10  # rows of each list of what is waiting
MONTHS = 12       # months of the two charts; their texts say "12 months"


class OverviewView(QMPage):
    template_name = 'cirs/qm/overview.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        incidents = scoped_incidents(self.request.user)
        today = timezone.localdate()
        context.update(_new(incidents, today))
        context['open_states'] = _open_states(incidents)
        context.update(_waiting(incidents, today))
        context['development'] = _development(incidents, today)
        context['where'] = _where(incidents, today)
        return context


def _new(incidents, today):
    return {'new_week': metrics.incoming(incidents, *metrics.last_days(today, 7)),
            'new_month': metrics.incoming(incidents, metrics.first_of_month(today), today)}


def _open_states(incidents):
    labels = dict(STATUS_CHOICES)
    return [{'label': labels[status], 'count': count, 'url': worklist_url(stand=status)}
            for status, count in metrics.open_by_status(incidents).items()]


def _waiting(incidents, today):
    """The oldest incidents of each list. A row shows the place and links to the incident, which
    needs its department: both are fetched with the rows, not one by one."""
    rows = incidents.select_related('department', 'org_unit__parent').order_by('reported', 'pk')
    days = settings.QM_OVERDUE_DAYS
    return {'overdue_days': days,
            'overdue_rows': list(metrics.overdue(rows, today, days)[:LIST_LENGTH]),
            'overdue_url': worklist_url(ohne_bearbeitung=1),
            'awaiting_rows': list(metrics.awaiting_qm(rows)[:LIST_LENGTH]),
            'awaiting_url': worklist_url(wartet=1)}


def _development(incidents, today):
    """The columns of the last months, incoming and completed, with the texts that go with
    them. None if the months hold no report and no completion: there is nothing to draw.

    A month before the log began has no number of completions. Its cell says "not recorded" and
    the page says since when the log has them (or that it has none yet), so that it is not
    taken for a month without completions.
    """
    first, _last = metrics.last_months(today, MONTHS)
    # asked once: the months need it to tell what is not recorded, the note to say since when
    started = metrics.protocol_start(incidents)
    rows = metrics.monthly(incidents, first, MONTHS, started=started)
    if not any(row.incoming or row.completed for row in rows):
        return None
    not_recorded = gettext('not recorded')
    series = [
        Series(gettext('Incoming'), [Cell(row.incoming, str(row.incoming)) for row in rows]),
        Series(gettext('Completed'), [
            Cell(None, not_recorded) if row.completed is None
            else Cell(row.completed, str(row.completed)) for row in rows])]
    note = _recorded_note(started) if any(row.completed is None for row in rows) else ''
    desc = gettext('Reports in the last 12 months: %(incoming)d received, %(completed)d '
                   'completed.') % {'incoming': sum(row.incoming for row in rows),
                                    'completed': sum(row.completed or 0 for row in rows)}
    return {'chart': column_chart([date_format(row.month, 'M') for row in rows], series),
            'title': gettext('Incoming and completed per month, last 12 months'),
            'desc': f'{desc} {note}'.strip(), 'note': note,
            'label_header': gettext('Month')}


def _recorded_note(started):
    """What the page says about the months without completions: since when the log has them."""
    if started is None:
        return gettext('Completions are not recorded yet.')
    return gettext('Completions recorded since %(date)s.') % {
        'date': date_format(started, 'SHORT_DATE_FORMAT')}


def _where(incidents, today):
    """The bars of the groups of places over the last months, None if there is no report."""
    start, end = metrics.last_months(today, MONTHS)
    buckets = metrics.distribution(incidents, 'org_unit_group', start, end)
    if not buckets:
        return None
    most = max(buckets, key=lambda bucket: bucket.count)
    desc = gettext('Reports per area in the last 12 months; most: %(area)s with %(count)d of '
                   '%(total)d.') % {'area': most.label, 'count': most.count,
                                    'total': sum(bucket.count for bucket in buckets)}
    return {'chart': bar_chart([Bar(bucket.label, bucket.count, str(bucket.count))
                                for bucket in buckets]),
            'title': gettext('Reports per area, last 12 months'), 'desc': desc,
            'label_header': gettext('Where')}
