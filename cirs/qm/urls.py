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

"""The QM pages under /qm/."""

from django.urls import path

from .views_overview import OverviewView
from .views_reports import ReportCsvView, ReportPrintView, ReportView
from .views_worklist import WorklistView

urlpatterns = [
    path('', OverviewView.as_view(), name='qm_overview'),
    path('meldungen/', WorklistView.as_view(), name='qm_incidents'),
    path('auswertungen/', ReportView.as_view(), name='qm_reports'),
    path('auswertungen/druck/', ReportPrintView.as_view(), name='qm_reports_print'),
    path('auswertungen/csv/', ReportCsvView.as_view(), name='qm_reports_csv'),
]
