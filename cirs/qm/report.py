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

"""All the numbers of an evaluation in one object, and the protection of small numbers in it.

build_report asks metrics for every number the evaluations show, once, and returns them as a
Report. A page renders from a Report and from nothing else, so that what one output shows can be
checked in one place. The incidents are a queryset that is already limited to the departments of
whoever looks; nothing here widens it, and a group only narrows it further.

redact is the one place where small numbers are withheld. What is printed or exported is rendered
from the redacted Report and from nothing else: its numbers are the ones that may be shown, so no
text, table, length or description made from it can say more.

A period is whole months, from the first day of its first month to the last day of its last. It
has at most MAX_MONTHS months: a longer one is a page with hundreds of rows and nobody's question.
"""

from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import NamedTuple

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from cirs.models import PublishableIncident

from . import metrics
from .metrics import SECONDARY_MARK, Bucket, Durations, MonthRow, redact_duration, redact_series

MAX_MONTHS = 36

# The distributions of an evaluation in their order: the field of metrics.distribution and the
# name of the characteristic.
DISTRIBUTION_TITLES = (
    ('org_unit_group', _('Area')), ('category', _('Category')),
    ('preventability', _('Preventability')), ('risk', _('Risk')),
    ('frequency', _('Frequency')), ('hazard', _('Hazard')))


class Period(NamedTuple):
    """The first and the last day of an evaluation, both included."""
    start: date
    end: date


@dataclass(frozen=True)
class Distribution:
    """The incidents of the period spread over the values of a field. `multiple_answers` is true
    where an incident can count in several cells, so that the cells add up to more than the
    incoming. `total` is what the cells add up to, None where they do not add up to anything
    (multiple answers); a redacted Report holds the total that may be shown, which can be a text."""
    field: str
    title: str
    buckets: list[Bucket]
    multiple_answers: bool
    total: int | str | None = None


@dataclass(frozen=True)
class Measure:
    """A published case of an incident reported in the period: the number of the incident, the
    address of its page, the day of the report, and the title and the measures of the case in the
    active language. In a redacted Report there is no number and no address, and the day is the
    first of its month, or the first of its year (`year_only`) where the month would give a hidden
    number of the months away."""
    number: int | None
    url: str
    reported: date
    title: str
    measures: str
    year_only: bool = False


@dataclass(frozen=True)
class Report:
    """What an evaluation says.

    `open_end` is None where the state at the end of the period is not recorded (see
    metrics.open_at_end). `completed`, `reaction` and `processing` come from the status log, so
    they are None for a period that ended before the log began: nothing was recorded then, which
    is not the same as nothing completed (`protocol_start` is the day the log began, None if it
    has no entry at all). `monthly` has a row for each month of the period. `unprocessed` is the
    number of incidents of the period that are still new. `status_changed` says whether the
    log holds any change of a status in the period.

    The numbers are whole numbers in a Report that build_report made. After redact a number can
    be a text: "< 3" for a small one and "*" for one that is withheld.
    """
    period: Period
    group_id: int | None
    incoming: int | str
    completed: int | str | None
    open_end: int | str | None
    published: int | str
    unprocessed: int | str
    reaction: Durations | None
    processing: Durations | None
    monthly: list[MonthRow]
    protocol_start: date | None
    status_changed: bool
    distributions: list[Distribution]
    measures: list[Measure]


def month_count(first_month, last_month):
    """The number of months from the month of `first_month` to the month of `last_month`, both
    included; 0 or less if the last comes before the first."""
    return (last_month.year - first_month.year) * 12 + last_month.month - first_month.month + 1


def build_report(qs, first_month, last_month, group_id=None, today=None):
    """The Report of the incidents `qs` for the months from `first_month` to `last_month` (a date
    in each, any day), narrowed to the group with the id `group_id` (None: no narrowing, which is
    not the cell "not specified" of metrics.group_filter). `today` defaults to the day of the
    installation. Raises ValueError for a period that ends before it begins or is longer than
    MAX_MONTHS months."""
    first, last = metrics.first_of_month(first_month), metrics.first_of_month(last_month)
    months = month_count(first, last)
    if not 1 <= months <= MAX_MONTHS:
        raise ValueError(f'A period of {months} months is not an evaluation')
    start, end = first, metrics.first_of_month(last, 1) - timedelta(days=1)
    today = today or timezone.localdate()
    # The log began when it began for all of the incidents, not for the group that is asked for:
    # a group without incidents in the first months did not record less than the others.
    started = metrics.protocol_start(qs)
    if group_id is not None:
        qs = metrics.group_filter(qs, group_id)
    # What the log decides is known from the day it began: for a period that ended before, there
    # is nothing to ask it, and the answer to "none" would be a wrong one.
    logged = started is not None and started <= end
    return Report(
        period=Period(start, end), group_id=group_id,
        incoming=metrics.incoming(qs, start, end),
        completed=metrics.completed(qs, start, end) if logged else None,
        open_end=metrics.open_at_end(qs, end, today),
        published=metrics.published(qs, start, end),
        unprocessed=metrics.unprocessed(qs, start, end),
        reaction=metrics.reaction_days(qs, start, end) if logged else None,
        processing=metrics.processing_days(qs, start, end) if logged else None,
        monthly=metrics.monthly(qs, first, months, started=started),
        protocol_start=started,
        status_changed=logged and metrics.has_status_change(qs, start, end),
        distributions=[_distribution(qs, field, title, start, end)
                       for field, title in DISTRIBUTION_TITLES],
        measures=_measures(qs, start, end))


def _distribution(qs, field, title, start, end):
    buckets = metrics.distribution(qs, field, start, end)
    multiple_answers = field in metrics.MULTIPLE_ANSWER_FIELDS
    total = None if multiple_answers else sum(bucket.count for bucket in buckets)
    return Distribution(field, str(title), buckets, multiple_answers, total)


def _measures(qs, start, end):
    """The published cases of the incidents reported in the period, the oldest report first. The
    incident, its department (for the address) and the translations come with the cases, so that a
    case costs no query of its own. The texts are in the active language, else in the first
    language that has them."""
    cases = (PublishableIncident.objects
             .filter(publish=True, critical_incident__in=metrics.in_period(qs, start, end))
             .select_related('critical_incident__department').prefetch_related('translations')
             .order_by('critical_incident__reported', 'critical_incident_id'))
    return [Measure(
        number=case.critical_incident_id, url=case.critical_incident.get_absolute_url(),
        reported=case.critical_incident.reported,
        title=case.safe_translation_getter('incident', any_language=True) or '',
        measures=case.safe_translation_getter('measures_and_consequences',
                                              any_language=True) or '')
        for case in cases]


def _with_total(values, min_cell, total):
    """The cells of a series and its total as they may be shown. A total that is not what the cells
    add up to cannot be shown: the numbers come from several queries, and an incident that is
    reported between two of them makes them disagree. Nothing is known then about what the total
    would give away, so it is withheld."""
    if total != sum(values):
        return redact_series(values, min_cell), SECONDARY_MARK
    *cells, shown = redact_series(values, min_cell, total)
    return cells, shown


def _withhold_smallest(cells):
    """The cells of a series with the smallest one above 0 withheld (SECONDARY_MARK)."""
    positive = [i for i, cell in enumerate(cells) if cell > 0]
    if not positive:
        return cells
    smallest = min(positive, key=lambda i: (cells[i], i))
    return [SECONDARY_MARK if i == smallest else cell for i, cell in enumerate(cells)]


class _Incoming:
    """The incoming over all the series that add up to it: the months, the distributions and the
    incidents still new next to those that are not. It is one number, shown in several places, and
    if one series has to withhold it, it is withheld in every place.

    With `guard`, the incoming is withheld, and then a printed series that shows every cell would
    give it back as the sum of its cells: such a series withholds its smallest cell as well."""

    def __init__(self, incoming, min_cell, guard=False):
        self.incoming, self.min_cell, self.guard, self.verdicts = incoming, min_cell, guard, []

    def cells(self, values, printed=True):
        """The cells of a series that adds up to the incoming. `printed` is False for a series
        of which only a part is shown anywhere."""
        cells, verdict = _with_total(values, self.min_cell, self.incoming)
        self.verdicts.append(verdict)
        if self.guard and printed and all(isinstance(cell, int) for cell in cells):
            cells = _withhold_smallest(cells)
        return cells

    def shown(self):
        if SECONDARY_MARK in self.verdicts:
            return SECONDARY_MARK
        return metrics.suppress(self.incoming, self.min_cell)


def _redact_months(report, min_cell, incoming):
    """The rows of the months. The incoming of the months is a series under the incoming, the
    completed of the months a series under the completed. A month that is not recorded stays
    so and is no cell of a series. Returns the rows and the completed as it may be shown."""
    rows = report.monthly
    in_cells = incoming.cells([row.incoming for row in rows])
    recorded = [row.completed for row in rows if row.completed is not None]
    if report.completed is None:
        done_cells, completed = [], None
    else:
        done_cells, completed = _with_total(recorded, min_cell, report.completed)
    done = iter(done_cells)
    return ([MonthRow(row.month, cell, None if row.completed is None else next(done))
             for row, cell in zip(rows, in_cells)], completed)


def _redact_distribution(distribution, min_cell, incoming):
    counts = [bucket.count for bucket in distribution.buckets]
    if distribution.multiple_answers:
        cells = redact_series(counts, min_cell)  # a report counts in several: no total to guard
    else:
        cells = incoming.cells(counts)
    return replace(distribution, buckets=[bucket._replace(count=cell) for bucket, cell
                                          in zip(distribution.buckets, cells)])


def _redact_measures(report, published, monthly):
    """The measures that may be listed: none if the number of published cases is hidden, since a
    list of one or two cases says what the number does not. A case has no number of its own and
    no address, and its day is the month: the numbers of the incidents run over all the
    departments, and the public list of the cases names the month as well. A month whose incoming
    is hidden is not named either: the cases that were reported in it would tell what the cell
    does not, so such a case names its year only."""
    if not isinstance(published, int):
        return []
    named = {row.month for row in monthly if isinstance(row.incoming, int)}
    cases = []
    for case in report.measures:
        month = metrics.first_of_month(case.reported)
        if month in named:
            cases.append(replace(case, number=None, url='', reported=month))
        else:
            cases.append(replace(case, number=None, url='', reported=date(month.year, 1, 1),
                                 year_only=True))
    return cases


def redact(report, min_cell):
    """The Report that may be printed or exported: every number that is above 0 and below
    `min_cell` is hidden ("< 3"), and so are the numbers that would give a hidden one away.

    A series of numbers that adds up to a total shown next to it (the months and each
    distribution to the incoming) hides one more number, the smallest that is not small, so that
    the hidden one cannot be worked out by subtraction, and if that does not suffice the total
    itself (see metrics.redact_series). The incoming is shown in several places, so the strictest
    verdict of the series counts in all of them. The incidents that are still new are the
    incoming without those that are not: the second number is no figure of the report, and still
    it has to be as hidden as one. A median over fewer than `min_cell` incidents is not stated
    (metrics.redact_duration).

    What is not recorded stays so. The report is not changed; a new one is returned. Two
    reports that differ only in numbers that are hidden give the same result.
    """
    if min_cell < 1:
        raise ValueError('The smallest number to show is at least 1')
    result = _redact(report, min_cell, guard=False)
    if result.incoming == SECONDARY_MARK:
        # Some series withholds the incoming: the others that add up to it have to stand for that.
        result = _redact(report, min_cell, guard=True)
    return result


def _redact(report, min_cell, guard):
    incoming = _Incoming(report.incoming, min_cell, guard)
    monthly, completed = _redact_months(report, min_cell, incoming)
    distributions = [_redact_distribution(one, min_cell, incoming)
                     for one in report.distributions]
    # The reports that are not new are no figure of the report: only the first number is printed.
    new, _processed = incoming.cells(
        [report.unprocessed, max(0, report.incoming - report.unprocessed)], printed=False)
    shown = incoming.shown()
    published = metrics.suppress(report.published, min_cell)
    processing = (None if report.processing is None
                  else redact_duration(report.processing, min_cell))
    if processing is not None and isinstance(completed, str):
        # The times are taken from the completions: their count would give back the number that
        # is hidden.
        processing = Durations(None, completed)
    return replace(
        report, incoming=shown, completed=completed,
        open_end=None if report.open_end is None else metrics.suppress(report.open_end, min_cell),
        published=published, unprocessed=new,
        reaction=None if report.reaction is None else redact_duration(report.reaction, min_cell),
        processing=processing, monthly=monthly,
        distributions=[replace(one, total=None if one.multiple_answers else shown)
                       for one in distributions],
        measures=_redact_measures(report, published, monthly))
