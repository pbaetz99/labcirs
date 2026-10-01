from django.urls import re_path

from cirs.views import (DepartmentList, IncidentAccessEnd, IncidentCreate,
                        IncidentDetailView, IncidentSearch, IncidentSuccess,
                        PublishableIncidentList, ReporterContactDelete)

urlpatterns = [
    re_path(r'^$', DepartmentList.as_view(), name='departments_list'),
    re_path(r'^(?P<dept>.+)/create/$', IncidentCreate.as_view(), name='create_incident'),
    re_path(r'^(?P<dept>.+)/create/success/$', IncidentSuccess.as_view(), name='success'),
    re_path(r'^(?P<dept>.+)/search/$', IncidentSearch.as_view(), name='incident_search'),
    re_path(r'^(?P<dept>.+)/(?P<pk>[0-9]+)/$', IncidentDetailView.as_view(), name='incident_detail'),
    re_path(r'^(?P<dept>.+)/(?P<pk>[0-9]+)/access/end/$', IncidentAccessEnd.as_view(),
            name='end_incident_access'),
    re_path(r'^(?P<dept>.+)/(?P<pk>[0-9]+)/email/remove/$', ReporterContactDelete.as_view(),
            name='remove_reporter_email'),
    re_path(r'^(?P<dept>.+)/$', PublishableIncidentList.as_view(), name='incidents_for_department'),
]
