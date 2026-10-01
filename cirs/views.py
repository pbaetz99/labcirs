# Copyright (C) 2016-2025 Sebastian Major
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

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import (REDIRECT_FIELD_NAME, authenticate, login,
                                 logout)
from django.contrib.messages.views import SuccessMessageMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template import loader
from django.urls import get_script_prefix, resolve, reverse
from django.utils.html import format_html
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from django.views.generic import ListView, TemplateView, View
from django.views.generic.edit import CreateView, FormView

from .context_processors import cirs_data
from .forms import CommentForm, IncidentCreateForm, IncidentSearchForm
from .models import (Comment, CriticalIncident, Department, LabCIRSConfig,
                     PublishableIncident, ReporterContact, group_code)


def get_active_department(label):
    return get_object_or_404(Department, label=label, active=True)


class RedirectMixin(object):
       
    def dispatch(self, request, *args, **kwargs):
        user = self.request.user
        if user.is_superuser:
            return redirect('admin:index')
        elif user.is_authenticated and not hasattr(user, 'reviewer'):
            # Only reviewers log in here. Old sessions of reporter accounts or
            # users without role end, the request goes on anonymously.
            logout(self.request)
        return super(RedirectMixin, self).dispatch(request, *args, **kwargs)


class ContextAndRedirectMixin(RedirectMixin):
    
    def get_context_data(self, **kwargs):
        context = super(ContextAndRedirectMixin, self).get_context_data(**kwargs)
        context['department'] = self.kwargs['dept']
        return context

class DepartmentList(RedirectMixin, ListView):
    model = Department
    
    def dispatch(self, *args, **kwargs):
        if self.request.user.is_superuser:
            return redirect('admin:index')
        departments = self.get_queryset()
        if departments.count() == 1:
            return redirect('incidents_for_department', dept=departments.get().label)
        return super(DepartmentList, self).dispatch(*args, **kwargs)

    def get_queryset(self):
        if hasattr(self.request.user, 'reviewer'):
            return self.request.user.reviewer.departments.filter(active=True)#all()
        else:
            return Department.objects.filter(active=True)
            #return super(DepartmentList, self).get_queryset()


# Where the code of a new report waits for the success page, which shows it once. Not a message:
# any message pending in the session would be taken for the code.
NEW_CODE_SESSION_KEY = 'new_incident_code'


class IncidentCreate(ContextAndRedirectMixin, CreateView):
    model = CriticalIncident
    form_class = IncidentCreateForm
    success_url = 'success'

    def dispatch(self, request, *args, **kwargs):
        self.department = get_active_department(kwargs['dept'])
        if hasattr(self.request.user, 'reviewer'):
            return redirect('labcirs_home')
        else:
            return super(IncidentCreate, self).dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.department = self.department
        if 'public' not in form.fields:  # ASK_PUBLICATION_CONSENT is off
            form.instance.public = True
        response = super(IncidentCreate, self).form_valid(form)
        self.request.session[NEW_CODE_SESSION_KEY] = self.object.comment_code
        return response


# never_cache on the pages of a report (code, report, address): on a shared PC the Back button
# or the history must not bring back what was shown once or after "End access".
@method_decorator(never_cache, name='dispatch')
class IncidentSuccess(TemplateView):
    """The page after reporting. The code comes from the session and is shown only once."""
    template_name = 'cirs/success.html'

    def dispatch(self, request, *args, **kwargs):
        get_active_department(kwargs['dept'])  # 404 for an unknown or inactive department
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['comment_code'] = self.request.session.pop(NEW_CODE_SESSION_KEY, None)
        # In groups of four, as in the mail: the template puts each group in a span of its own.
        context['comment_code_groups'] = group_code(context['comment_code'] or '').split()
        # Only where the form asks for an address, the page may promise a mail.
        context['reporter_email_offered'] = bool(settings.DEFAULT_FROM_EMAIL)
        return context


@method_decorator(never_cache, name='dispatch')
class IncidentSearch(ContextAndRedirectMixin, FormView):
    form_class = IncidentSearchForm
    template_name = 'cirs/incident_search_form.html'
    
    REDIRECT_MESSAGE = _('If you want comment on any incident, please click on the number of '
                         'comments in the last column for published incidents or use the '
                         '"View on site" link in the admin interface!')

    def dispatch(self, *args, **kwargs):
        self.department = get_active_department(kwargs['dept'])
        if hasattr(self.request.user, 'reviewer'):
            messages.warning(self.request, self.REDIRECT_MESSAGE)
            return redirect('incidents_for_department', dept=self.kwargs['dept'])
        else:
            return super(IncidentSearch, self).dispatch(*args, **kwargs)

    def form_valid(self, form):
        comment_code = form.cleaned_data.get('incident_code')
        incident = CriticalIncident.objects.get(comment_code=comment_code)
        # New session key: the code may have been entered on a shared computer.
        self.request.session.cycle_key()
        self.request.session['accessible_incident'] = incident.id
        return redirect(incident.get_absolute_url())


class IncidentAccessEnd(RedirectMixin, View):
    """Ends the access to a report, so the next person at a shared PC cannot use it."""
    http_method_names = ['post']

    def post(self, request, dept, pk):
        get_active_department(dept)  # 404 for an unknown or inactive department
        request.session.pop('accessible_incident', None)
        messages.success(request, _('Access to this report has ended.'))
        return redirect('incident_search', dept=dept)


class IncidentAccessMixin(RedirectMixin):
    """
    Views of one incident (URL kwargs dept and pk): only the reviewers of its department and the
    reporter who entered the code get in. The incident is self.incident.
    """

    def dispatch(self, request, *args, **kwargs):
        # Access is checked here, before GET and POST alike.
        self.incident = get_object_or_404(CriticalIncident, pk=kwargs['pk'],
                                          department__label=kwargs['dept'])
        user = request.user
        if hasattr(user, 'reviewer'):
            if not user.reviewer.departments.filter(pk=self.incident.department_id).exists():
                return redirect('labcirs_home')
        # Superusers pass on to RedirectMixin, which sends them to the admin.
        elif (not user.is_superuser
              and request.session.get('accessible_incident') != self.incident.pk):
            return redirect('incident_search', dept=self.incident.department.label)
        return super(IncidentAccessMixin, self).dispatch(request, *args, **kwargs)


# TODO: Rename to Comment view?
@method_decorator(never_cache, name='dispatch')
class IncidentDetailView(ContextAndRedirectMixin, IncidentAccessMixin, SuccessMessageMixin,
                         CreateView):
    """
    Delivers detail view of an incident for commenting. Simple form for comments
    is included and followed by a list of comments for this incident
    """
    model = Comment
    form_class = CommentForm
    template_name = 'cirs/criticalincident_detail.html'
    # The new reply stands at the end of the list, below the fold: the message says it arrived.
    success_message = _('Your reply has been saved and is listed under “Replies”.')

    def get_success_url(self):
        return self.incident.get_absolute_url()

    def get_form(self, form_class=None):
        form = super(IncidentDetailView, self).get_form(form_class)
        if hasattr(self.request.user, 'reviewer'):
            # The hint above the form already says it to the QM.
            form.fields['text'].help_text = ''
        return form

    def form_valid(self, form):
        user = self.request.user
        form.instance.author = (user if hasattr(user, 'reviewer')
                                else self.incident.department.reporter.user)
        form.instance.critical_incident = self.incident
        return super(IncidentDetailView, self).form_valid(form)

    def get_context_data(self, **kwargs):
        context = super(IncidentDetailView, self).get_context_data(**kwargs)
        context['incident'] = self.incident
        # Oldest first, as the page says; the date has no time, so the id breaks ties.
        context['comments'] = self.incident.comments.order_by('created', 'pk')
        # Only for the reporter: the hint for the QM must not tell whether there is an address.
        context['reporter_email_active'] = (
            not hasattr(self.request.user, 'reviewer')
            and ReporterContact.objects.filter(incident=self.incident).exists())
        return context


@method_decorator(never_cache, name='dispatch')
class ReporterContactDelete(IncidentAccessMixin, View):
    """Deletes the e-mail address of the reporter, if the reporter confirmed (WCAG 3.3.4)."""
    http_method_names = ['post']

    def dispatch(self, request, *args, **kwargs):
        # Only the holder of the code, not the QM or an administrator. Before the access
        # check, which lets reviewers and superusers in.
        if request.user.is_superuser or hasattr(request.user, 'reviewer'):
            raise PermissionDenied
        return super(ReporterContactDelete, self).dispatch(request, *args, **kwargs)

    def post(self, request, dept, pk):
        if request.POST.get('confirm'):
            ReporterContact.objects.filter(incident=self.incident).delete()
            messages.success(request, _('Your e-mail address has been deleted. You will receive '
                                        'no more e-mails about this report.'))
        else:
            messages.error(request, _('Please confirm that you want to delete your e-mail '
                                      'address permanently.'))
        return redirect(self.incident.get_absolute_url())


class PublishableIncidentList(ContextAndRedirectMixin, ListView):
    """
    Returns the list of publishable incidents where "publish" is set to true and the department
    matches the department in the URL, 25 per page. The GET parameter q searches the texts of
    all translations (incident, description, measures and consequences).
    """
    paginate_by = 25
    SEARCH_FIELDS = ('translations__incident', 'translations__description',
                     'translations__measures_and_consequences')

    def dispatch(self, *args, **kwargs):
        self.department = get_active_department(kwargs['dept'])
        return super(PublishableIncidentList, self).dispatch(*args, **kwargs)

    def get_search_term(self):
        return self.request.GET.get('q', '').strip()

    def get_queryset(self):
        # The related objects come along, so that a page costs the same queries for 1 and 25 cases.
        incidents = PublishableIncident.objects.filter(
            publish=True, critical_incident__department=self.department,
        ).select_related('critical_incident__department').prefetch_related('translations')
        # the comment column of the reviewers, counted in the query: distinct, because the search
        # joins the translations and would count each comment once per matching translation
        incidents = incidents.annotate(
            comment_count=Count('critical_incident__comments', distinct=True))
        # Newest first. A query with an aggregate ignores Meta.ordering (GROUP BY), so it is named.
        incidents = incidents.order_by('-id')
        term = self.get_search_term()
        if term:
            # One filter() call: one translation row must match, in any of the fields.
            match = Q()
            for field in self.SEARCH_FIELDS:
                match |= Q(**{field + '__icontains': term})
            # A case with several matching translations would appear once per translation.
            incidents = incidents.filter(match).distinct()
        return incidents

    def get_context_data(self, **kwargs):
        context = super(PublishableIncidentList, self).get_context_data(**kwargs)
        context['q'] = self.get_search_term()
        context['department_name'] = self.department.name
        return context


MISSING_ROLE_MSG = _('This is a valid account, but you are neither reporter, '
                     'nor reviewer. Please contact the administrator!')

MISSING_DEPARTMENT_MSG =_('Your account has no associated department! '
                          'Please contact the administrator!')

REPORTER_LOGIN_MSG = _('Reporting works without logging in. Please use “Report incident”.')

# Translators: {} is the address of the password reset; keep the a tag around the link text.
NOT_AUTHENTICATED_MSG = _('Your username or password is incorrect, you are not logged in. '
                          'If you have forgotten your password, you can '
                          '<a href="{}">reset your password</a>.')


def login_user(request, redirect_field_name=REDIRECT_FIELD_NAME):
    username = password = message = ''
    message_class = 'danger'
    redirect_url = request.GET.get(redirect_field_name, '')
    if not url_has_allowed_host_and_scheme(redirect_url, allowed_hosts={request.get_host()},
                                           require_https=request.is_secure()):
        redirect_url = reverse('labcirs_home')

    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(username=username, password=password)
        if user is not None and hasattr(user, 'reporter'):
            # The reporter account stays only as technical author of anonymous comments.
            message = REPORTER_LOGIN_MSG
            message_class = 'info'
        elif user is not None:
            if user.is_active:
                login(request, user)
                if user.is_superuser:
                    return redirect(redirect_url)#'admin:index')
                elif hasattr(user, 'reviewer'):
                    if user.reviewer.departments.count() > 0:
                        return redirect('admin:index')
                    else:
                        message = MISSING_DEPARTMENT_MSG
                        logout(request)
                else:
                    message = MISSING_ROLE_MSG
                    logout(request)
            else:
                message = _('Your account is not active, please contact the admin.')
                message_class = 'warning'
        else:
            message = format_html(NOT_AUTHENTICATED_MSG, reverse('password_reset'))

    context = {'message': message,
               'message_class': message_class,
               'username': username,
               redirect_field_name: redirect_url,
               }
    
    try:
        # Resolve seems not to work if django project is not run from web root.
        prefix = get_script_prefix()
        match = resolve(redirect_url.replace(prefix, '/'))
        context['department'] = match.kwargs['dept']
        context['labcirs_config'] = LabCIRSConfig.objects.get(
            department__label=match.kwargs['dept'])
    except Exception as e:
        pass
        #print e
        #traceback.print_exc()
    return render(request, 'cirs/login.html', context)


@require_POST  # logging out changes state; the header uses a form
def logout_user(request):
    logout(request)
    return redirect('labcirs_home')


# Error pages that must work while the database is down: rendered without the context processors
# that reach the request (no user, session or messages). What the footer and the head of the normal
# pages show (branding, legal links, source link, version) is read from settings only, by
# cirs_data. 403 and 404 use Django's handlers, which render 403.html and 404.html with the usual
# context.

def _bare_error_page(request, template_name, status):
    context = cirs_data(request)  # settings and the version only, never the request
    return HttpResponse(loader.render_to_string(template_name, context), status=status)


def server_error(request):
    return _bare_error_page(request, '500.html', 500)


def bad_request(request, exception):
    return _bare_error_page(request, '400.html', 400)


def csrf_failure(request, reason=''):
    return _bare_error_page(request, '403_csrf.html', 403)
