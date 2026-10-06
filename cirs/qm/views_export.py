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

"""The print view and the CSV file of the evaluations: the numbers of the page without the small ones.

Both are made from the Report that redact makes of the one that the page shows, and from nothing
else, so neither can say what the page says about a small number. The request is read as the page
reads it. A request that is not in order is not answered with a part of a file or a sheet of
paper: the browser is sent to the page, which names what is wrong.
"""

from typing import NamedTuple

from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.http import content_disposition_header, urlencode
from django.views.generic import TemplateView, View

from . import report_csv, report_page
from .access import QMAccessMixin, scoped_departments, scoped_incidents
from .report import Report, build_report, redact
from .views_reports import ReportForm, read_query, report_url


class Export(NamedTuple):
    """What a print view or a CSV file is made of: the redacted Report, the departments and the
    area it is about, and the day it is made."""
    report: Report
    departments: list
    area: str
    today: object
    first_month: object
    last_month: object
    group_id: object


def _page_url(request):
    """The page for the same request, with the parameters that its form knows as they were sent:
    it shows what is wrong with them."""
    query = [(name, request.GET[name]) for name in ReportForm.base_fields if name in request.GET]
    return reverse('qm_reports') + ('?' + urlencode(query) if query else '')


class ExportView(QMAccessMixin):
    """Reads the request like the page, redacts the Report and hands it to `respond`."""

    def get(self, request, *args, **kwargs):
        departments = list(scoped_departments(request.user))
        incidents = scoped_incidents(request.user)
        today = timezone.localdate()
        query = read_query(request, incidents, today) if departments else None
        if query is None or not query.valid:
            return HttpResponseRedirect(_page_url(request))
        report = build_report(incidents, query.first_month, query.last_month, query.group_id,
                              today)
        export = Export(redact(report, settings.REPORT_MIN_CELL), departments,
                        dict(query.areas).get(query.group_id, ''), today, query.first_month,
                        query.last_month, query.group_id)
        return self.respond(request, export)


class ReportPrintView(ExportView, TemplateView):
    template_name = 'cirs/qm/reports_print.html'

    def respond(self, request, export):
        report, min_cell = export.report, settings.REPORT_MIN_CELL
        period = report_page.period_text(report.period)
        development = report_page.development(report, period)
        if development:
            development['table_open'] = True  # a closed table is not printed
        return self.render_to_response({
            'report': report, 'period_text': period, 'min_cell': min_cell,
            'less_marker': '< %d' % min_cell,
            'created': date_format(export.today, 'SHORT_DATE_FORMAT'),
            'departments': export.departments,
            'department_names': ', '.join(department.name for department in export.departments),
            'area_name': export.area, 'figures': report_page.figures(report),
            'development': development,
            'distributions': report_page.distributions(report, period),
            'published_hidden': not isinstance(report.published, int),
            'notes': [{'title': title, 'text': text}
                      for title, text in report_page.notes(report, min_cell)],
            'back_url': report_url(export.first_month, export.last_month, export.group_id)})


class ReportCsvView(ExportView, View):
    """UTF-8 with a byte order mark, so that Excel reads the umlauts. Always a download."""

    def respond(self, request, export):
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = content_disposition_header(
            True, report_csv.filename(export.report.period))
        # never_cache says it too; this file is a download that stays on a disk, the headers
        # should not depend on a decorator
        response['Cache-Control'] = 'no-store'
        report_csv.write(response, export)
        return response
