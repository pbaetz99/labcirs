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

"""The evaluations of the QM: the page, the print view and the CSV file of the same numbers."""

import codecs

from django.http import HttpResponse
from django.views.generic import View

from .access import QMAccessMixin, QMPage


class ReportView(QMPage):
    template_name = 'cirs/qm/reports.html'


class ReportPrintView(QMPage):
    template_name = 'cirs/qm/reports_print.html'


class ReportCsvView(QMAccessMixin, View):
    """UTF-8 with a byte order mark, so that Excel reads the umlauts. Always a download."""

    def get(self, request):
        response = HttpResponse(codecs.BOM_UTF8, content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="auswertung.csv"'
        return response
