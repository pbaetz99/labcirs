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

"""Who gets into the QM pages and which incidents they see.

Only reviewers (the quality management) get in. Anonymous visitors are sent to the login and come
back to the page they asked for. Everybody else, the reporter accounts, accounts without a role
and the superusers (who see no incidents anywhere), are refused with 403.

The incidents of a page are always scoped_incidents(user), never the table. All the pages count
and list from that one query set, so a page cannot show a department the reviewer does not
belong to.
"""

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.generic import TemplateView

from cirs.models import CriticalIncident, Department


def reviewer_of(user):
    """The reviewer role of user, or None for anybody who is not a reviewer."""
    if user.is_superuser:
        return None  # a superuser is no QM, even with a reviewer role added behind the scenes
    return getattr(user, 'reviewer', None)  # missing for a user without the role, and anonymous


def scoped_departments(user):
    """The departments of the reviewer, inactive ones included, by name; none for others."""
    reviewer = reviewer_of(user)
    if reviewer is None:
        return Department.objects.none()
    return reviewer.departments.order_by('name')


def scoped_incidents(user):
    """
    The incidents of all departments of the reviewer. Inactive departments count, as in the
    admin: switching a department off stops new reports, not the work on the old ones. For
    somebody who is no reviewer it is empty.
    """
    return CriticalIncident.objects.filter(department__in=scoped_departments(user))


# Pages with incident data: on a shared PC the Back button after logging out must not bring them
# back, so never_cache (the same as for the pages of a report).
@method_decorator(never_cache, name='dispatch')
class QMAccessMixin:
    """The check of the account, before the method: GET and HEAD only, else 405."""
    http_method_names = ['get', 'head']

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), settings.LOGIN_URL)
        if reviewer_of(request.user) is None:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class QMPage(QMAccessMixin, TemplateView):
    """A page in cirs/qm/base.html. It names the departments whose incidents it covers."""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        departments = list(scoped_departments(self.request.user))
        context['departments'] = departments
        context['department_names'] = ', '.join(department.name for department in departments)
        # The top bar links the published cases of the department in the context. Here that is
        # the first active department of the reviewer, so the link stays on these pages too.
        context['department'] = next((d.label for d in departments if d.active), '')
        return context
