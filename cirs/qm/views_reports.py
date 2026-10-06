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

"""The evaluations of the QM: the page and the form that asks for it.

The page asks for a period of whole months (from month and year to month and year) and, if wished,
for one area. Without parameters it shows this year up to the running month. A request that is not
in order is not answered with numbers: the page names what is wrong and shows none, whatever was
sent. All numbers come from a Report (build_report); this module only turns them into the texts
and the charts of the page.
"""

from datetime import date
from typing import NamedTuple
from urllib.parse import urlencode

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Min
from django.urls import reverse
from django.utils import timezone
from django.utils.dates import MONTHS
from django.utils.translation import gettext, gettext_lazy as _, pgettext_lazy

from . import metrics, report_page
from .access import QMPage, scoped_incidents
from .report import MAX_MONTHS, build_report, month_count

# The words of the period are generic: the catalogs of Django have words of their own for some
# short texts, and for the same text they win over the ones of this app. The context "report
# period" keeps them apart (it has to be written out each time for the extraction of the texts).
FROM = pgettext_lazy('report period', 'From')
UNTIL = pgettext_lazy('report period', 'Until')
LEGENDS = {'von_monat': FROM, 'von_jahr': FROM, 'bis_monat': UNTIL, 'bis_jahr': UNTIL}
CHOICE_ERRORS = {code: _('Please choose an entry from the list.')
                 for code in ('required', 'invalid_choice')}


def _select(label, choices=(), **kwargs):
    return forms.TypedChoiceField(
        label=label, choices=choices, coerce=int, error_messages=CHOICE_ERRORS,
        widget=forms.Select(attrs={'class': 'ui-select'}), **kwargs)


class ReportForm(forms.Form):
    """The period and the area of an evaluation. Every value is one of the choices, so whatever is
    sent can only be a month, a year that is offered or an area of the incidents."""
    von_monat = _select(pgettext_lazy('report period', 'Month'), list(MONTHS.items()))
    von_jahr = _select(pgettext_lazy('report period', 'Year'))
    bis_monat = _select(pgettext_lazy('report period', 'Month'), list(MONTHS.items()))
    bis_jahr = _select(pgettext_lazy('report period', 'Year'))
    bereich = _select(_('Area'), required=False, empty_value=None,
                      help_text=_('A group includes its units.'))

    def __init__(self, data, *, years, areas, today):
        """`years` are the years that can be chosen, `areas` the (id, name) of the areas. Without
        `data` the form holds this year up to the running month."""
        first, last = metrics.quick_ranges(today)['this_year']
        super().__init__(data, label_suffix='', initial={
            'von_monat': first.month, 'von_jahr': first.year,
            'bis_monat': last.month, 'bis_jahr': last.year})
        for name in ('von_jahr', 'bis_jahr'):
            self.fields[name].choices = [(year, str(year)) for year in years]
        self.fields['bereich'].choices = [('', gettext('All')), *areas]

    def period_groups(self):
        """The fields of the period in two groups, each with the legend that names it."""
        return [(FROM, [self['von_monat'], self['von_jahr']]),
                (UNTIL, [self['bis_monat'], self['bis_jahr']])]

    def clean(self):
        data = super().clean()
        try:
            first = date(data['von_jahr'], data['von_monat'], 1)
            last = date(data['bis_jahr'], data['bis_monat'], 1)
        except KeyError:
            return data  # a field is wrong, and says so
        months = month_count(first, last)
        if months < 1:
            self.add_error('bis_monat', _('The period ends before it begins.'))
        elif months > MAX_MONTHS:
            self.add_error('bis_monat', ValidationError(
                _('The period may cover at most %(months)d months.'),
                params={'months': MAX_MONTHS}))
        else:
            data['first_month'], data['last_month'] = first, last
        return data


def report_url(first_month, last_month, group_id=None, name='qm_reports'):
    """The address of the page (or, with the name of its address, of the print view or the CSV
    file) for the months from `first_month` to `last_month`."""
    query = [('von_monat', first_month.month), ('von_jahr', first_month.year),
             ('bis_monat', last_month.month), ('bis_jahr', last_month.year)]
    if group_id is not None:
        query.append(('bereich', group_id))
    return reverse(name) + '?' + urlencode(query)


def _quick_links(today, group_id):
    labels = {'last_quarter': _('Last quarter'), 'this_year': _('Current year'),
              'last_year': _('Last year')}
    ranges = metrics.quick_ranges(today)
    return [{'label': labels[name], 'url': report_url(*ranges[name], group_id)}
            for name in labels]


def _years(incidents, today):
    """The years that can be chosen: from the one of the oldest incident to this one. The year
    before is always among them, so that the quick choice of last year can be chosen too."""
    oldest = incidents.aggregate(oldest=Min('reported'))['oldest']
    return range(min(oldest.year if oldest else today.year, today.year - 1), today.year + 1)


def _error_items(form):
    """What the summary of the errors lists, in the order of the fields on the page: the field to
    go to, its name and what is wrong."""
    items = []
    for name in form.fields:
        field = form[name]
        label = f'{LEGENDS[name]}: {field.label}' if name in LEGENDS else str(field.label)
        items += [{'target': field.id_for_label, 'label': label, 'message': message}
                  for message in field.errors]
    return items


class Query(NamedTuple):
    """What the query of a request asks for: the form (with what was sent), the areas that can be
    chosen, and the months and the area. The months are None where the query is not in order."""
    form: ReportForm
    areas: list
    first_month: date | None
    last_month: date | None
    group_id: int | None

    @property
    def valid(self):
        return self.first_month is not None


def read_query(request, incidents, today):
    """The Query of the request, for the page and for the exports alike: without parameters this
    year up to the running month, and as soon as one parameter is there, all of them are checked."""
    areas = metrics.places(incidents)
    asked = any(name in request.GET for name in ReportForm.base_fields)
    form = ReportForm(request.GET if asked else None, years=_years(incidents, today),
                      areas=areas, today=today)
    if not asked:
        first, last = metrics.quick_ranges(today)['this_year']
        return Query(form, areas, first, last, None)
    if not form.is_valid():
        return Query(form, areas, None, None, form.cleaned_data.get('bereich'))
    return Query(form, areas, form.cleaned_data['first_month'], form.cleaned_data['last_month'],
                 form.cleaned_data['bereich'])


class ReportView(QMPage):
    template_name = 'cirs/qm/reports.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if not context['departments']:
            return context  # no incidents to evaluate: the page says so and shows nothing else
        incidents = scoped_incidents(self.request.user)
        today = timezone.localdate()
        query = read_query(self.request, incidents, today)
        context['form'] = query.form
        if not query.valid:
            context['error_items'] = _error_items(query.form)
            context['quick_links'] = _quick_links(today, query.group_id)
            return context
        report = build_report(incidents, query.first_month, query.last_month, query.group_id,
                              today)
        period = report_page.period_text(report.period)
        context.update(
            quick_links=_quick_links(today, query.group_id), report=report, period_text=period,
            area_name=dict(query.areas).get(query.group_id, ''),
            figures=report_page.figures(report), recorded_note=report_page.recorded_note(report),
            development=report_page.development(report, period),
            distributions=report_page.distributions(report, period),
            print_url=report_url(query.first_month, query.last_month, query.group_id,
                                 'qm_reports_print'),
            csv_url=report_url(query.first_month, query.last_month, query.group_id,
                               'qm_reports_csv'),
            min_cell=settings.REPORT_MIN_CELL)
        return context
