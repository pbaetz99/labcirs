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

"""All the numbers of an evaluation in one object.

build_report asks metrics for every number the evaluations show, once, and returns them as a
Report. A page renders from a Report and from nothing else, so that what one output shows can be
checked, and later changed (small numbers withheld for print and export), in one place. The
incidents are a queryset that is already limited to the departments of whoever looks; nothing here
widens it, and a group only narrows it further.

A period is whole months, from the first day of its first month to the last day of its last. It
has at most MAX_MONTHS months: a longer one is a page with hundreds of rows and nobody's question.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import NamedTuple

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from cirs.models import PublishableIncident

from . import metrics
from .metrics import Bucket, Durations, MonthRow

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
    incoming."""
    field: str
    title: str
    buckets: list[Bucket]
    multiple_answers: bool


@dataclass(frozen=True)
class Measure:
    """A published case of an incident reported in the period: the number of the incident, the
    address of its page, the day of the report, and the title and the measures of the case in the
    active language."""
    number: int
    url: str
    reported: date
    title: str
    measures: str


@dataclass(frozen=True)
class Report:
    """What an evaluation says. `open_end` is None where the state at the end of the period is not
    recorded (see metrics.open_at_end). `monthly` has a row for each month of the period and
    `protocol_start` is the day the status log began (None: it has no entry), which tells which
    completions can be known at all."""
    period: Period
    group_id: int | None
    incoming: int
    completed: int
    open_end: int | None
    published: int
    reaction: Durations
    processing: Durations
    monthly: list[MonthRow]
    protocol_start: date | None
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
    return Report(
        period=Period(start, end), group_id=group_id,
        incoming=metrics.incoming(qs, start, end),
        completed=metrics.completed(qs, start, end),
        open_end=metrics.open_at_end(qs, end, today),
        published=metrics.published(qs, start, end),
        reaction=metrics.reaction_days(qs, start, end),
        processing=metrics.processing_days(qs, start, end),
        monthly=metrics.monthly(qs, first, months, started=started),
        protocol_start=started,
        distributions=[
            Distribution(field, str(title), metrics.distribution(qs, field, start, end),
                         field in metrics.MULTIPLE_ANSWER_FIELDS)
            for field, title in DISTRIBUTION_TITLES],
        measures=_measures(qs, start, end))


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
