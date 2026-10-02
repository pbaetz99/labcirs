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

"""The numbers of the QM pages, and the protection of small numbers in them.

Every counting function takes the incidents it is to count as a queryset that is already limited
to the departments of whoever looks. None of them knows a user or a department, so none can
widen that scope. A function needs a handful of queries at most, never one per incident.

Days are dates: ``reported`` is a date, so a period is a pair of dates and both days are in it.
Nothing here reads the clock; "today" is a parameter (``timezone.localdate()`` in the time zone
of the installation), so that the results can be tested at any day.

The status log has moments, not days. A day is a day of the clock on the wall in the time zone
of the installation (settings.TIME_ZONE): a period of days is the span of moments from the start
of its first day (included) to the start of the day after its last (excluded), and months are
cut at the same wall clock. A day on which the clocks change has 23 or 25 hours, and a change
at half past midnight belongs to the day it is on the wall, not the one in UTC.

The log began with its first entry: what happened before is not in it. A completion is only
known from then on, so a month that ended before is "not recorded" (None), which is not the
same as 0.

Small numbers: ``suppress`` hides one number, ``redact_series`` hides the numbers of a whole
series so that none of them can be worked out from the rest, ``redact_duration`` does the same
for a median. The pages that are printed or exported show only redacted numbers.
"""

from datetime import date, datetime, time, timedelta
from typing import NamedTuple
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import (BooleanField, Count, DateField, ExpressionWrapper, Min, OuterRef,
                              Q, Subquery)
from django.db.models.functions import Coalesce, Greatest, TruncDate, TruncMonth
from django.utils.translation import gettext_lazy as _

from cirs.models import (STATUS_CHOICES, Comment, CriticalIncident, IncidentStatusChange,
                         OrgUnit)

# The states in which an incident is open, in the order of the work.
OPEN_STATUSES = tuple(status for status, _label in STATUS_CHOICES if status != 'completed')

DISTRIBUTION_FIELDS = ('org_unit_group', 'category', 'preventability', 'risk', 'frequency',
                       'hazard')
# A report can name several of these, so the cells of such a distribution add up to more than the
# number of reports. The page says so with the note.
MULTIPLE_ANSWER_FIELDS = frozenset({'category'})
MULTIPLE_ANSWERS_NOTE = _('Several answers are possible: a report counts in each of its '
                          'categories.')
NOT_SPECIFIED = _('Not specified')

# Takes the place of a number that is withheld although it is not small, see redact_series.
SECONDARY_MARK = '*'


class Bucket(NamedTuple):
    """A cell of a distribution: the value as stored (None: not specified), its name and the
    number of incidents."""
    key: object
    label: str
    count: int


class Durations(NamedTuple):
    """Median in days and the number of incidents it is taken from. The count is a text
    ("< 3") after redact_duration has hidden a small one."""
    median: float | None
    count: int | str


class MonthRow(NamedTuple):
    """A month: its first day, the incidents reported in it and the incidents completed in it.
    `completed` is None for a month that ended before the status log began: nothing was recorded
    then, which is not the same as nothing completed."""
    month: date
    incoming: int
    completed: int | None


def first_of_month(day, months=0):
    """The first day of the month that lies `months` months after the one of `day` (before it,
    if negative)."""
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def last_days(today, days):
    """The span of the last `days` days, today included: (start, end)."""
    return today - timedelta(days=days - 1), today


def last_months(today, months):
    """The span of the last `months` months: the ones before the running month in full and the
    running month up to today (it is not over yet): (start, end)."""
    return first_of_month(today, 1 - months), today


def in_period(qs, start, end):
    """The incidents reported from the day `start` to the day `end`, both included."""
    return qs.filter(reported__range=(start, end))


def incoming(qs, start, end):
    """The number of incidents reported in the period."""
    return in_period(qs, start, end).count()


def open_by_status(qs):
    """The number of open incidents in each open state, the states without any too. Not
    limited to a period: it is the state of now."""
    counts = dict(qs.filter(status__in=OPEN_STATUSES).values_list('status')
                  .annotate(Count('pk')).order_by())
    return {status: counts.get(status, 0) for status in OPEN_STATUSES}


def published(qs, start, end):
    """The number of published cases of the incidents reported in the period. A case has no
    date of its own, so the day of the report decides; a case published later still counts
    where its incident was reported."""
    return in_period(qs, start, end).filter(publishableincident__publish=True).count()


def _zone():
    return ZoneInfo(settings.TIME_ZONE)


def _moments(start, end):
    """The days from `start` to `end`, both included, as the moments they span in the time zone
    of the installation: the start of the first day (included) and the start of the day after
    the last (excluded)."""
    zone = _zone()
    return (datetime.combine(start, time.min, zone),
            datetime.combine(end + timedelta(days=1), time.min, zone))


def protocol_start(qs):
    """The day (in the time zone of the installation) of the oldest entry in the status log of
    the incidents, None if they have no entry. Nothing is recorded before that day."""
    first = (IncidentStatusChange.objects.filter(incident__in=qs)
             .aggregate(first=Min('changed_at'))['first'])
    return first.astimezone(_zone()).date() if first else None


def _completions(qs):
    """The incidents that are completed now, each with `completed_at`: the moment of the last
    entry of the log that says completed. An incident that was reopened and completed again has
    that last one only, so it counts once; one that was reopened and is not completed again is
    not in the result. An incident completed before the log began has no entry (None) and
    belongs to no period."""
    last = (IncidentStatusChange.objects.filter(incident=OuterRef('pk'), status='completed')
            .order_by('-changed_at', '-pk').values('changed_at')[:1])
    return qs.filter(status='completed').annotate(completed_at=Subquery(last))


def completed(qs, start, end):
    """The number of incidents that are completed now and whose completion is in the period.
    The completion is the last one in the status log (see _completions)."""
    begin, after = _moments(start, end)
    return _completions(qs).filter(completed_at__gte=begin, completed_at__lt=after).count()


def _completed_by_month(qs, first, after):
    """{first day of the month: number} of the completions from the day `first` to the day before
    the day `after`, cut at the wall clock of the installation."""
    begin, end = _moments(first, after - timedelta(days=1))
    rows = (_completions(qs).filter(completed_at__gte=begin, completed_at__lt=end)
            .annotate(month=TruncMonth('completed_at', tzinfo=_zone()))
            .values_list('month').annotate(Count('pk')).order_by())
    return {month.date(): number for month, number in rows}


_ASK = object()  # "not given", for a parameter whose None is an answer


def monthly(qs, first_month, months, started=_ASK):
    """A MonthRow for each of `months` months from the month of `first_month` on.

    The incoming is counted by the day of the report. The completed comes from the status log,
    so it is None for a month that ended before the log began, and for all of them if there is
    no log. At most three queries, whatever the number of incidents.

    `started` is protocol_start(qs). A caller that has it already passes it (None if there is no
    log), so that it is not asked for twice; without it, the function asks.
    """
    first = first_of_month(first_month)
    after = first_of_month(first, months)
    reported = dict(in_period(qs, first, after - timedelta(days=1))
                    .annotate(month=TruncMonth('reported')).values_list('month')
                    .annotate(Count('pk')).order_by())
    if started is _ASK:
        started = protocol_start(qs)
    done = _completed_by_month(qs, first, after) if started else {}
    rows = []
    for number in range(months):
        month = first_of_month(first, number)
        # recorded unless the month was over before the day the log began
        recorded = started is not None and first_of_month(month, 1) > started
        rows.append(MonthRow(month, reported.get(month, 0),
                             done.get(month, 0) if recorded else None))
    return rows


def _is_overdue(today, days):
    """The condition of an incident without processing, see overdue."""
    return Q(status='new', reported__lt=today - timedelta(days=days))


def _latest_reporter():
    """The reporter role of the author of the latest comment of an incident, None if that author
    has none or there is no comment. The latest comment is the one of the latest day and, of
    several on one day, the one written last."""
    return Subquery(Comment.objects.filter(critical_incident=OuterRef('pk'))
                    .order_by('-created', '-pk').values('author__reporter')[:1])


def _is_awaiting_qm():
    """The condition of an incident that waits for the QM, see awaiting_qm. It reads the
    annotation latest_reporter."""
    return ~Q(status='completed') & Q(latest_reporter__isnull=False)


def _last_activity():
    """The latest of: the day of the report, the day of the newest comment and the day (on the
    wall clock of the installation) of the newest entry of the status log. The Coalesce gives an
    incident without comments or log the day of its report: on some databases Greatest is null
    as soon as one of its values is."""
    comment = (Comment.objects.filter(critical_incident=OuterRef('pk'))
               .order_by('-created').values('created')[:1])
    entry = (IncidentStatusChange.objects.filter(incident=OuterRef('pk')).order_by('-changed_at')
             .annotate(day=TruncDate('changed_at', tzinfo=_zone())).values('day')[:1])
    return Greatest('reported', Coalesce(Subquery(comment), 'reported'),
                    Coalesce(Subquery(entry), 'reported'), output_field=DateField())


def overdue(qs, today, days):
    """The incidents that are still new and were reported more than `days` days before `today`:
    on the day `days` an incident is not late yet, on the day after it is."""
    return qs.filter(_is_overdue(today, days))


def awaiting_qm(qs):
    """The incidents that are not completed and whose latest comment was written by a reporter
    account: the reporting person has the last word, the QM has not answered yet.

    The latest comment is the one of the latest day and, of several on one day, the one written
    last. A comment counts as the reporting person's if its author has the reporter role.
    """
    return qs.annotate(latest_reporter=_latest_reporter()).filter(_is_awaiting_qm())


def annotate_workflow(qs, today, days):
    """The incidents with what a list of them shows, as columns of the same query, so that a row
    costs no query of its own:

    is_overdue      the incident is without processing (see overdue)
    is_awaiting_qm  the incident waits for the QM (see awaiting_qm)
    last_activity   the day of the latest sign of life: the report, a comment or a change of the
                    status, whichever is the newest

    A list narrows itself with filter(is_overdue=True) and filter(is_awaiting_qm=True), so that
    it and the overview mean the same by them.
    """
    return qs.annotate(
        is_overdue=ExpressionWrapper(_is_overdue(today, days), output_field=BooleanField()),
        latest_reporter=_latest_reporter(),
        is_awaiting_qm=ExpressionWrapper(_is_awaiting_qm(), output_field=BooleanField()),
        last_activity=_last_activity())


def group_of(org_unit):
    """The group of an organisational unit: its parent, or the unit itself if it has none.
    None for no unit. Fetches the parent, so give lists of incidents select_related
    ('org_unit__parent')."""
    return (org_unit.parent or org_unit) if org_unit else None


def group_filter(qs, group_id):
    """The incidents of the group with this id: its own and those of its units.

    The id is a key of the distribution by group: None is the cell "not specified", the
    incidents without a unit. Only a unit without a parent is a group, so the id of a unit below
    another one has no incidents, like an id that no unit has. An id that is no number raises
    ValueError. The groups of a distribution and this filter thus always agree on the counts.
    """
    if group_id is None:
        return qs.filter(org_unit__isnull=True)
    return qs.filter(Q(org_unit=group_id, org_unit__parent__isnull=True)
                     | Q(org_unit__parent=group_id))


def distribution(qs, field, start, end):
    """How the incidents reported in the period are spread over the values of a field, as a
    list of Bucket. `field` is one of DISTRIBUTION_FIELDS.

    A field with choices lists every choice in its order, those with 0 too. The group of the
    place ("org_unit_group", see group_of) lists the groups with incidents in the order of the
    units. A value that is no choice (any more) comes after the choices, so that the sum is
    right. Incidents without a value come last as "not specified" (key None), if there are any.
    The categories are counted in Python, since they are stored as one text: a report counts in
    each of its categories, so the sum can exceed the incoming (MULTIPLE_ANSWER_FIELDS).
    """
    if field not in DISTRIBUTION_FIELDS:
        raise ValueError(f'No distribution by {field!r}')
    qs = in_period(qs, start, end)
    if field == 'org_unit_group':
        return _group_buckets(qs)
    if field == 'category':
        counts = {}
        for chosen in qs.values_list('category', flat=True):
            for key in set(chosen) or {''}:
                counts[key] = counts.get(key, 0) + 1
    else:
        counts = dict(qs.values_list(field).annotate(Count('pk')).order_by())
    choices = CriticalIncident._meta.get_field(field).choices
    found = [Bucket(key, str(label), counts.pop(key, 0)) for key, label in choices]
    no_choice = sorted((key, number) for key, number in counts.items() if key)
    found += [Bucket(key, key, number) for key, number in no_choice]
    return _with_not_specified(found, counts.get('', 0) + counts.get(None, 0))


def _group_buckets(qs):
    counts = {}
    rows = qs.values_list('org_unit', 'org_unit__parent').annotate(Count('pk')).order_by()
    for unit, parent, number in rows:
        counts[parent or unit] = counts.get(parent or unit, 0) + number
    none = counts.pop(None, 0)
    groups = OrgUnit.objects.filter(pk__in=list(counts))
    return _with_not_specified([Bucket(g.pk, g.name, counts[g.pk]) for g in groups], none)


def _with_not_specified(found, number):
    if number:
        found.append(Bucket(None, str(NOT_SPECIFIED), number))
    return found


def suppress(value, min_cell):
    """The number, or "< n" for a number above 0 and below the smallest cell n."""
    return f'< {min_cell}' if 0 < value < min_cell else value


def redact_series(values, min_cell, total=None):
    """The cells of a series with small numbers hidden so that none can be worked out.

    A cell above 0 and below `min_cell` shows as "< n". The cells of a series usually add up to
    a total that is shown too, and then one hidden number follows from the others: a total of 32
    over 10, 20 and a hidden cell can only mean 2. So pass `total`, the sum of `values`, whenever
    it is shown anywhere next to the series (the incoming over the cells of a distribution, a
    period over its months). Do not pass it for a series that has no total, such as the
    categories of reports that can name several.

    With a total, the result has one cell more: the total, last. If a hidden number could still
    be worked out (it is the only value that fits the sum of the hidden cells), the smallest
    other number above 0 is withheld too, shown as SECONDARY_MARK, until it cannot, and if that
    does not suffice, the total instead. A small total is shown as "< n", or as SECONDARY_MARK
    if the cells could be worked out from it (two cells of 1 under a total of 2). Someone who
    sees the result and knows which cells are hidden then always has two values left for each
    hidden small number. A minimum of 2 leaves no room for that: "< 2" says that the number is 1.

    Show the total from the result, never a copy of it from elsewhere: it may be withheld. The
    same cells in two reports with other periods or groups give no protection against taking one
    report from the other.
    """
    if total is not None and total != sum(values):
        raise ValueError(f'The total {total} is not the sum of the values')
    shown = [suppress(value, min_cell) for value in values]
    if total is None:
        return shown
    shown.append(suppress(total, min_cell))
    hidden = {i for i, value in enumerate(values) if 0 < value < min_cell}
    if not hidden:
        return shown
    # The candidates for the further number: not small, not 0, the smallest first.
    others = sorted((i for i, value in enumerate(values) if value >= min_cell),
                    key=lambda i: (values[i], i))
    extra = []
    while True:
        rest = sum(values[i] for i in (*hidden, *extra))
        low, high = (rest, rest) if total >= min_cell else (1, min_cell - 1)
        if not _fixes_a_small_number(low, high, len(hidden), len(extra), min_cell):
            break
        if not others:
            extra = []
            shown[-1] = SECONDARY_MARK
            break
        extra.append(others.pop(0))
    for i in extra:
        shown[i] = SECONDARY_MARK
    return shown


def _fixes_a_small_number(low, high, small, extra, min_cell):
    """Whether hidden cells that add up to a sum from `low` to `high` leave a single value for a
    small one: `small` hidden small cells (1 up to min_cell - 1 each) and `extra` withheld ones
    (min_cell or more each)."""
    other_small = small - 1
    least = 1 if extra else max(1, low - other_small * (min_cell - 1))
    most = min(min_cell - 1, high - other_small - extra * min_cell)
    return least >= most


def redact_duration(durations, min_cell):
    """The median and the count of durations, or neither of them if there are fewer than
    `min_cell` (and more than none): a median over one or two incidents is the duration of one
    of them. The count then shows as "< n". Without durations nothing is hidden."""
    if 0 < durations.count < min_cell:
        return Durations(None, suppress(durations.count, min_cell))
    return durations
