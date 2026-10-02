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

"""The evaluations of the QM: the page, the print view and the CSV file of the same numbers.

The page asks for a period of whole months (from month and year to month and year) and, if wished,
for one area. Without parameters it shows this year up to the running month. A request that is not
in order is not answered with numbers: the page names what is wrong and shows none, whatever was
sent. All numbers come from a Report (build_report); this module only turns them into the texts
and the charts of the page.
"""

import codecs
from datetime import date
from urllib.parse import urlencode

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Min
from django.http import HttpResponse
from django.template.defaultfilters import floatformat
from django.urls import reverse
from django.utils import timezone
from django.utils.dates import MONTHS
from django.utils.formats import date_format
from django.utils.translation import gettext, gettext_lazy as _, ngettext, pgettext_lazy
from django.views.generic import View

from . import chart_data, metrics
from .access import QMAccessMixin, QMPage, scoped_incidents
from .report import MAX_MONTHS, build_report, month_count

# The words of the period are generic: the catalogs of Django have words of their own for some
# short texts, and for the same text they win over the ones of this app. The context "report
# period" keeps them apart (it has to be written out each time for the extraction of the texts).
FROM = pgettext_lazy('report period', 'From')
UNTIL = pgettext_lazy('report period', 'Until')
LEGENDS = {'von_monat': FROM, 'von_jahr': FROM, 'bis_monat': UNTIL, 'bis_jahr': UNTIL}
CHOICE_ERRORS = {code: _('Please choose an entry from the list.')
                 for code in ('required', 'invalid_choice')}
MANY_MONTHS = 12  # a chart of more months opens its table: the columns are too thin for numbers
NO_DURATION = '–'


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


def report_url(first_month, last_month, group_id=None):
    """The address of the page for the months from `first_month` to `last_month`."""
    query = [('von_monat', first_month.month), ('von_jahr', first_month.year),
             ('bis_monat', last_month.month), ('bis_jahr', last_month.year)]
    if group_id is not None:
        query.append(('bereich', group_id))
    return reverse('qm_reports') + '?' + urlencode(query)


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


def _period_text(period):
    first, last = (date_format(day, 'F Y') for day in period)
    if first == last:
        return first
    return gettext('%(first)s to %(last)s') % {'first': first, 'last': last}


def _duration(durations):
    """The text "Median 7.5 days, count 4", or a dash where there is no incident to take it from."""
    if not durations.count:
        return NO_DURATION
    return ngettext('Median %(days)s day, count %(count)d',
                    'Median %(days)s days, count %(count)d',
                    1 if durations.median == 1 else 2) % {
        'days': floatformat(durations.median, '-1'), 'count': durations.count}


def _figures(report):
    """The key figures: label and value, and whether the value is a text (it is set smaller)."""
    open_end = report.open_end
    return [
        {'label': gettext('Incoming'), 'value': report.incoming},
        {'label': gettext('Completed'), 'value': report.completed},
        {'label': gettext('Open at the end of the period'),
         'value': gettext('not recorded') if open_end is None else open_end,
         'text': open_end is None},
        {'label': gettext('Published'), 'value': report.published},
        {'label': gettext('Reaction time'), 'value': _duration(report.reaction), 'text': True},
        {'label': gettext('Processing time'), 'value': _duration(report.processing),
         'text': True},
    ]


def _recorded_note(report):
    """What the page says where completions and times are not known for the whole period: since
    when the status log has them, or that it has none yet. Nothing if it covers the period."""
    started = report.protocol_start
    if started is None:
        return gettext('Completions and times are not recorded yet.')
    if started > report.period.start:
        return gettext('Completions and times have been recorded since %(date)s.') % {
            'date': date_format(started, 'SHORT_DATE_FORMAT')}
    return ''


def _development(report, period_text):
    """The columns of the months with their texts, None if no month holds a report or a
    completion. The table opens by itself where there are many months: the columns are then too
    thin for their numbers."""
    rows = report.monthly
    if not any(row.incoming or row.completed for row in rows):
        return None
    desc = gettext('Reports in %(period)s: %(incoming)d received, %(completed)d completed.') % {
        'period': period_text, 'incoming': sum(row.incoming for row in rows),
        'completed': sum(row.completed or 0 for row in rows)}
    note = _recorded_note(report) if any(row.completed is None for row in rows) else ''
    return {'chart': chart_data.month_columns(rows, 'M Y'),
            'title': gettext('Incoming and completed per month, %(period)s') % {
                'period': period_text},
            'desc': f'{desc} {note}'.strip(), 'label_header': gettext('Month'),
            'table_open': len(rows) > MANY_MONTHS}


def _most(distribution):
    """The key message of a distribution: the cell with the most incidents."""
    top = max(distribution.buckets, key=lambda bucket: bucket.count, default=None)
    if top is None or not top.count:
        return ''
    if distribution.multiple_answers:
        return gettext('Most: %(label)s with %(count)d.') % {'label': top.label,
                                                            'count': top.count}
    return gettext('Most: %(label)s with %(count)d of %(total)d.') % {
        'label': top.label, 'count': top.count,
        'total': sum(bucket.count for bucket in distribution.buckets)}


def _distributions(report, period_text):
    return [{'id': 'chart-' + distribution.field.replace('_', '-'), 'name': distribution.title,
             'chart': chart_data.bucket_bars(distribution.buckets),
             'title': f'{distribution.title}, {period_text}', 'desc': _most(distribution),
             'note': metrics.MULTIPLE_ANSWERS_NOTE if distribution.multiple_answers else ''}
            for distribution in report.distributions]


class ReportView(QMPage):
    template_name = 'cirs/qm/reports.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if not context['departments']:
            return context  # no incidents to evaluate: the page says so and shows nothing else
        incidents = scoped_incidents(self.request.user)
        today = timezone.localdate()
        areas = metrics.places(incidents)
        # Asked for as soon as one parameter is there, a wrong one too: it is checked, not ignored.
        asked = any(name in self.request.GET for name in ReportForm.base_fields)
        form = ReportForm(self.request.GET if asked else None, years=_years(incidents, today),
                          areas=areas, today=today)
        context['form'] = form
        if asked and not form.is_valid():
            context['error_items'] = _error_items(form)
            context['quick_links'] = _quick_links(today, form.cleaned_data.get('bereich'))
            return context
        if asked:
            first, last = form.cleaned_data['first_month'], form.cleaned_data['last_month']
            group_id = form.cleaned_data['bereich']
        else:
            (first, last), group_id = metrics.quick_ranges(today)['this_year'], None
        report = build_report(incidents, first, last, group_id, today)
        period_text = _period_text(report.period)
        context.update(
            quick_links=_quick_links(today, group_id), report=report, period_text=period_text,
            area_name=dict(areas).get(group_id, ''), figures=_figures(report),
            recorded_note=_recorded_note(report),
            development=_development(report, period_text),
            distributions=_distributions(report, period_text))
        return context


class ReportPrintView(QMPage):
    template_name = 'cirs/qm/reports_print.html'


class ReportCsvView(QMAccessMixin, View):
    """UTF-8 with a byte order mark, so that Excel reads the umlauts. Always a download."""

    def get(self, request):
        response = HttpResponse(codecs.BOM_UTF8, content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="auswertung.csv"'
        return response
