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

"""The work list of the QM: all incidents of its departments, filtered and sorted.

The parameters of the address are read with parse_params, so a value out of range never reaches a
query: it is left out, and the page names it, so that nobody takes the list for filtered when it
is not. A page that does not exist is a 404, as in the list of the published cases.
"""

from datetime import date

from django.conf import settings
from django.core.paginator import EmptyPage, Paginator
from django.db.models import Q
from django.http import Http404
from django.utils import timezone
from django.utils.translation import gettext, gettext_lazy as _

from cirs.models import CATEGORY_CHOICES, RISK_CHOICES, STATUS_CHOICES

from . import metrics
from .access import QMPage, scoped_incidents
from .params import (FILTERS, MAX_SEARCH_LENGTH, NO_PLACE, PARAMS, SORTS, parse_params,
                     worklist_url)

PAGE_SIZE = 25
DEFAULT_SORT = '-gemeldet'  # the newest report first
# What each sort key orders by. The id always comes second, in the same direction: many incidents
# share a day, and without a second key the database may give a row on two pages or on none.
SORT_FIELDS = {'nr': 'pk', 'gemeldet': 'reported', 'aktivitaet': 'last_activity'}
# Why a parameter was left out, where "invalid value" says too little
IGNORED_REASONS = {'von': _('invalid date'), 'bis': _('invalid date'), 'wo': _('unknown area'),
                   'q': _('too long')}


def _in_category(rows, key):
    """A report can name several categories: the field holds their keys, commas between."""
    return rows.filter(Q(category=key) | Q(category__startswith=key + ',')
                       | Q(category__endswith=',' + key) | Q(category__contains=',' + key + ','))


# filter -> how it narrows the incidents, given its value from parse_params
NARROWINGS = {
    'stand': lambda rows, value: rows.filter(status=value),
    'wo': lambda rows, value: metrics.group_filter(rows, None if value == NO_PLACE else value),
    'kategorie': _in_category,
    'risiko': lambda rows, value: rows.filter(risk=value),
    'von': lambda rows, value: rows.filter(reported__gte=value),
    'bis': lambda rows, value: rows.filter(reported__lte=value),
    'wartet': lambda rows, flag: rows.filter(is_awaiting_qm=True),
    'ohne_bearbeitung': lambda rows, flag: rows.filter(is_overdue=True),
    'q': lambda rows, value: rows.filter(incident__icontains=value),
}


def _places(incidents):
    """The groups of places that occur in the incidents, as (id, name) in the order of the
    units. They are the values of wo that the list takes, besides NO_PLACE."""
    buckets = metrics.distribution(incidents, 'org_unit_group', date.min, date.max)
    return [(bucket.key, bucket.label) for bucket in buckets if bucket.key is not None]


def _read_params(query, place_ids):
    """(values, ignored): the parameters of the address that are in range, typed, and the names
    of those that were left out, in the order of PARAMS. A place that is no group among the
    incidents is out of range: the list shows nothing of a place that is not its own."""
    values, ignored = parse_params(query)
    place = values.get('wo')
    if place not in (None, NO_PLACE) and place not in place_ids:
        values = {name: value for name, value in values.items() if name != 'wo'}
        ignored = [*ignored, 'wo']
    return values, [name for name in PARAMS if name in ignored]


def _notes(names):
    """What the page says about each parameter it left out."""
    notes = []
    for name in names:
        if name == 'sort':
            notes.append(gettext('The sort order was ignored: invalid value.'))
        else:
            reason = IGNORED_REASONS.get(name, _('invalid value'))
            notes.append(gettext('The filter “%(name)s” was ignored: %(reason)s.')
                         % {'name': name, 'reason': reason})
    return notes


def _rows(incidents, values, today):
    """The incidents narrowed by the filters and in the order asked for. They come with what a
    row shows (department and place, the notes and the last activity), so the page asks the
    database once for all of them."""
    rows = metrics.annotate_workflow(incidents.select_related('department', 'org_unit__parent'),
                                     today, settings.QM_OVERDUE_DAYS)
    for name in FILTERS:
        if name in values:
            rows = NARROWINGS[name](rows, values[name])
    sort = values.get('sort', DEFAULT_SORT)
    direction = '-' if sort.startswith('-') else ''
    return rows.order_by(direction + SORT_FIELDS[sort.removeprefix('-')], direction + 'pk')


def _sorts(values):
    """The links of the three headings that sort. The heading the list is sorted by turns the
    order round, the others sort ascending. All keep the filters and start at the first page."""
    current = values.get('sort', DEFAULT_SORT)
    links = {}
    for key in SORTS:
        sorted_by = current.removeprefix('-') == key
        descending = sorted_by and current.startswith('-')
        target = '-' + key if sorted_by and not descending else key
        state = ('descending' if descending else 'ascending') if sorted_by else ''
        links[key] = {
            'url': worklist_url(**{**values, 'sort': target, 'page': None}),
            'aria_sort': state,
            'arrow': {'ascending': '↑', 'descending': '↓'}.get(state, ''),
            'action': _('sort descending') if target.startswith('-') else _('sort ascending')}
    return links


class WorklistView(QMPage):
    template_name = 'cirs/qm/worklist.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        incidents = scoped_incidents(self.request.user)
        places = _places(incidents)
        values, ignored = _read_params(self.request.GET, {place for place, _name in places})
        if 'page' in ignored:
            raise Http404('No such page')
        paginator = Paginator(_rows(incidents, values, timezone.localdate()), PAGE_SIZE)
        try:
            page = paginator.page(values.get('page', 1))
        except EmptyPage:
            raise Http404('No such page') from None
        context.update(
            selected=values, ignored=_notes(ignored), sorts=_sorts(values),
            filters_active=any(name in values for name in FILTERS),
            clear_url=worklist_url(sort=values.get('sort')),
            paginator=paginator, page_obj=page, is_paginated=paginator.num_pages > 1,
            previous_url=(worklist_url(**{**values, 'page': page.previous_page_number()})
                          if page.has_previous() else ''),
            next_url=(worklist_url(**{**values, 'page': page.next_page_number()})
                      if page.has_next() else ''),
            statuses=STATUS_CHOICES, places=places, no_place=NO_PLACE,
            categories=CATEGORY_CHOICES, risks=RISK_CHOICES,
            overdue_days=settings.QM_OVERDUE_DAYS, max_search=MAX_SEARCH_LENGTH)
        return context
