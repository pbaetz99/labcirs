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

"""The work of the QM on the page of an incident, for IncidentDetailView.

Reporters and reviewers use one address. The reviewer of the department gets another template
(cirs/qm/incident.html) with the report to read, three forms and the history, and every form is
a POST of its own with a hidden field "aktion": bewertung (the review), antwort (the reply to the
reporting person) or veroeffentlichung (the published case). The page then answers with a
redirect to itself and a message, or, if the form is not in order, with the page and the summary
of the errors.

What a POST may do is decided here by the role and the permission of the account, never by the
address: the access mixin has let in only the reviewers of the department (and the reporter who
entered the code, who gets no action: 403), and each action needs the permission that the admin
asks for the same work. A permission the account lacks hides the form and refuses the action.
"""

import copy

from django.contrib import messages
from django.contrib.admin.models import CHANGE, LogEntry
from django.core.exceptions import BadRequest, PermissionDenied
from django.db import transaction
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _

from cirs.models import CriticalIncident
from cirs.status_log import log_rows

from .access import reviewer_of, scoped_departments
from .forms import PublicationForm, ReviewForm
from .params import incident_query, list_params, worklist_url

QM_TEMPLATE = 'cirs/qm/incident.html'
ACTIONS = ('bewertung', 'antwort', 'veroeffentlichung')
# The form of the reply had no such field before: a POST without it is the reply.
DEFAULT_ACTION = 'antwort'
# The name of the form in the context, by action; the reply is the "form" of the view.
CONTEXT_KEYS = {'bewertung': 'review_form', 'antwort': 'form',
                'veroeffentlichung': 'publication_form'}
CHANGE_INCIDENT = 'cirs.change_criticalincident'
ADD_CASE = 'cirs.add_publishableincident'
CHANGE_CASE = 'cirs.change_publishableincident'
SAVED = {'bewertung': _('The assessment has been saved.'),
         'antwort': _('Your reply has been saved and is the last one in the dialogue.')}
PUBLISHED = _('The publication has been saved. The case is now published.')
NOT_PUBLISHED = _('The publication has been saved. The case is not published.')


class QMIncidentMixin:
    """Needs the access mixin of the incident views: self.incident is an incident that the
    reviewer, if the request is one, may work on."""

    @cached_property
    def reviewer(self):
        return reviewer_of(self.request.user)

    def get_template_names(self):
        return [QM_TEMPLATE] if self.reviewer is not None else super().get_template_names()

    def get_form_kwargs(self):
        # The form of the reply is bound to the data only if the POST is the reply: after an error
        # in the review it shows no error of its own and no text that was not typed into it.
        kwargs = super().get_form_kwargs()
        if self.reviewer is not None and self.request.method == 'POST' and self.action() != 'antwort':
            kwargs.pop('data', None)
            kwargs.pop('files', None)
        return kwargs

    def action(self):
        return self.request.POST.get('aktion') or DEFAULT_ACTION

    def page_url(self):
        """The page of the incident again, with the list it was opened from."""
        return self.incident.get_absolute_url() + incident_query(**list_params(self.request.GET))

    def post_as_qm(self, request):
        action = self.action()
        if action not in ACTIONS:
            raise BadRequest('Unknown action')
        with transaction.atomic():
            form, permission = self._bind(action, request.POST)
            if not request.user.has_perm(permission):
                raise PermissionDenied
            saved = self._save(action, form) if form.is_valid() else None
            if saved is not None:
                self._log(request.user, action, form)
        if saved is None:
            return self.render_to_response(self.get_context_data(**{CONTEXT_KEYS[action]: form}))
        if action in SAVED:
            text = SAVED[action]
        else:
            text = PUBLISHED if saved.publish else NOT_PUBLISHED
        messages.success(request, text)
        return redirect(self.page_url())

    def _bind(self, action, data):
        """The form of the action with the data, and the permission that the action needs."""
        if action == 'bewertung':
            # Locked and read again: two requests that save at once must not both see the old
            # status (two entries in the log, two mails). It is a copy of the incident as well: a
            # form that is not in order has changed its instance, and the page above it shows the
            # incident as it is stored.
            incident = CriticalIncident.objects.select_for_update().get(pk=self.incident.pk)
            return ReviewForm(data, instance=incident), CHANGE_INCIDENT
        if action == 'antwort':
            return self.get_form_class()(data), CHANGE_INCIDENT
        form = PublicationForm(self.incident, data)
        return form, (CHANGE_CASE if form.case else ADD_CASE)

    def _save(self, action, form):
        if action == 'antwort':
            form.instance.author = self.request.user
            form.instance.critical_incident = self.incident
        return form.save()

    def _log(self, user, action, form):
        """The trail that the admin leaves for the same work: who, when and which fields, on the
        incident, and never what was written."""
        fields = ', '.join(form.changed_data) or '-'
        LogEntry.objects.log_actions(user.pk, [self.incident], CHANGE,
                                     f'QM page, {action}: {fields}', single_object=True)

    def work_context(self, review_form=None, publication_form=None):
        """What the page of the QM has besides what the page of the reporter has."""
        user = self.request.user
        incident = self.incident
        values = list_params(self.request.GET)
        can_review = user.has_perm(CHANGE_INCIDENT)
        context = {'can_review': can_review, 'can_reply': can_review,
                   'list_query': incident_query(**values),
                   'back_url': worklist_url(**values),
                   'departments': list(scoped_departments(user)),
                   'history': log_rows(incident.status_changes.all())}
        if can_review:
            context['review_form'] = review_form or ReviewForm(instance=copy.copy(incident))
        # a report that was not approved for publication gets no form: it could not be saved
        if incident.public is not False:
            form = publication_form or PublicationForm(incident)
            context['publication_form'] = form
            context['can_publish'] = user.has_perm(CHANGE_CASE if form.case else ADD_CASE)
            context['mandatory_languages'] = ', '.join(
                language.title for language in form.languages if language.mandatory)
            if form.published and incident.department.active:
                context['public_list_url'] = reverse('incidents_for_department',
                                                     kwargs={'dept': incident.department.label})
        return context
