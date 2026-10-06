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
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from django.db import models
from django.forms import Textarea, TextInput
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext_lazy as _
from parler.admin import TranslatableAdmin, TranslatableTabularInline

from cirs.models import (Comment, CriticalIncident, Department, IncidentStatusChange,
                         LabCIRSConfig, OrgUnit, PublishableIncident, Reporter, Reviewer)
from cirs.status_log import shows_time, with_first_entry
from cirs.system_status import system_status


class LabCIRSAdminSite(admin.AdminSite):
    # Properties, so that SITE_NAME is read per request and not frozen at import.

    @property
    def site_header(self):
        return settings.SITE_NAME

    site_title = site_header

    @property
    def index_title(self):
        # Translators: %(site)s is the display name (setting SITE_NAME)
        return _('%(site)s administration') % {'site': settings.SITE_NAME}

    def index(self, request, extra_context=None):
        # The system status is for those who run the system. A QM gets the way to the QM area from
        # the template instead; neither of them gets a number of any incident.
        context = dict(extra_context or {})
        if request.user.is_superuser:
            context['system_status'] = system_status()
        return super().index(request, context)


admin_site = LabCIRSAdminSite()


class LabCIRSUserAdmin(UserAdmin):
    '''Local UserAdmin class to allow reviewers changing of reporter user data'''
    
    def get_queryset(self, request):
        qs = super(LabCIRSUserAdmin, self).get_queryset(request)
        try: 
            return qs.filter(
                reporter__in=Reporter.objects.filter(
                    department__in=request.user.reviewer.departments.all()))
        except Reviewer.DoesNotExist:
            if request.user.is_superuser is True:
                return qs

    def get_fieldsets(self, request, obj=None):
        # Reviewer can modify only names and change the password
        if hasattr(request.user, 'reviewer'):
            return ((None, {'fields': ('username', 'password')}),
                    (_('Personal info'), {'fields': ('first_name', 'last_name')}))
        else:
            return super(LabCIRSUserAdmin, self).get_fieldsets(request, obj=obj)

class HasPublishableIncidentListFilter(admin.SimpleListFilter):
    title = _('has publishable incident')
    parameter_name = 'has_publishable_incident'

    def lookups(self, request, model_admin):
        return (
            ('1', _('Yes')),
            ('0', _('No')),
        )

    def queryset(self, request, queryset):
        if self.value() == '1':
            return queryset.exclude(publishableincident=None)
        if self.value() == '0':
            return queryset.filter(publishableincident=None)

common_pi_fields = (
    ('incident', 'description', 'measures_and_consequences')
    )

pi_form_overrides = {
    models.CharField: {'widget': TextInput(attrs={'size': '62'})},
    models.TextField: {'widget': Textarea(attrs={'rows': 6, 'cols': 60})},
    }


class PublishableIncidentInline(TranslatableTabularInline):
    model = PublishableIncident
    fields = common_pi_fields + ('publish', 'translation_info')
    formfield_overrides = pi_form_overrides
    readonly_fields = ('translation_info', )


class CommentInline(admin.TabularInline):
    model = Comment
    verbose_name = _('Comment')
    verbose_name_plural = _('Comments')
    extra = 0
    readonly_fields = ('author', 'text',)
    
    def has_add_permission(self, request, *args, **kwargs):
        # TODO: write tests. Reviewer should not add comments in the admin inline view
        return False

class StatusChangeInline(admin.TabularInline):
    """The status log of an incident: shown, never edited. The entry of the report has its day
    only, see cirs.status_log."""
    model = IncidentStatusChange
    fields = readonly_fields = ('status', 'moment')
    extra = 0
    can_delete = False

    def get_queryset(self, request):
        return with_first_entry(super().get_queryset(request))

    @admin.display(description=IncidentStatusChange._meta.get_field('changed_at').verbose_name)
    def moment(self, obj):
        # as the admin writes a date and time: the time zone of the installation, the format of
        # the language
        moment = timezone.localtime(obj.changed_at)
        with_time = shows_time(obj.status, obj.pk, obj.first_entry_id)
        return date_format(moment, 'DATETIME_FORMAT' if with_time else 'DATE_FORMAT')

    def has_view_permission(self, request, obj=None):
        # Whoever may edit the incident may read its log, no permission of its own to hand out.
        return request.user.has_perm('cirs.change_criticalincident')

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class CriticalIncidentAdmin(admin.ModelAdmin):
    readonly_fields = ('date', 'incident', 'reason', 'immediate_action',
                       'public', 'reported', 'preventability', 'photo_tag')
    list_filter = ('department', 'status', 'date', 'reported', 'public', 'risk',
                   'org_unit', HasPublishableIncidentListFilter)
    list_display = ('incident', 'date', 'reported', 'status', 'risk', 'org_unit')
    list_display_links = ('incident', 'status', 'risk')
    fieldsets = (
        (_('Reported incident'), {
            'fields': (('date', 'reported'), 'public', 'incident', 'reason',
                       'immediate_action', 'preventability', 'photo_tag')
            
        }),
        (_('Review'), {
            'fields': (('review_date', 'status'), 'org_unit',
                       ('risk', 'frequency', 'hazard'),
                       'responsibilty', 'action', 'category'),
            'classes': ['collapse',]
        })
    )
    inlines = [PublishableIncidentInline, CommentInline, StatusChangeInline]

    def get_queryset(self, request):
        qs = super(CriticalIncidentAdmin, self).get_queryset(request)
        try:
            return qs.filter(department__in=request.user.reviewer.departments.all())
        except Reviewer.DoesNotExist:
            return qs.none()

class PublishableIncidentAdmin(TranslatableAdmin):

    fields = (('critical_incident', 'publish', 'translation_info'),) + common_pi_fields
    list_filter = ('publish', )
    list_display = ('incident', 'critical_incident', 'translation_status')
    list_display_links = ('incident', )
    readonly_fields = ('translation_info', )

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "critical_incident":
            incidents = (CriticalIncident.objects.filter(public=True)
                         .filter(publishableincident=None).exclude(status='new'))
            # Only the incidents of the own departments: the list shows the beginning of every title,
            # and a posted number would otherwise attach a publication to a foreign incident.
            try:
                incidents = incidents.filter(department__in=request.user.reviewer.departments.all())
            except Reviewer.DoesNotExist:
                incidents = incidents.none()
            kwargs["queryset"] = incidents
        return super(PublishableIncidentAdmin, self).formfield_for_foreignkey(db_field, request, **kwargs)

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(self.readonly_fields)
        if obj:
            readonly_fields.extend(['critical_incident'])
        return readonly_fields

    formfield_overrides = pi_form_overrides
    
    def get_queryset(self, request):
        qs = super(PublishableIncidentAdmin, self).get_queryset(request)
        try:
            return qs.filter(
                critical_incident__department__in=request.user.reviewer.departments.all())
        except Reviewer.DoesNotExist:
            return qs.none()


class OrgUnitAdmin(admin.ModelAdmin):
    list_display = ('name', 'parent', 'active', 'position')
    list_editable = ('active', 'position')
    list_filter = ('active', 'parent')
    search_fields = ('name',)


class AdminObjectMixin(object):
    
    def get_form(self, request, obj=None, **kwargs):
        self.model_instance = None
        if obj:
            self.model_instance = obj
           
        return super(AdminObjectMixin, self).get_form(request, obj, **kwargs)


class ConfigurationAdmin(AdminObjectMixin, TranslatableAdmin):
    
    list_display = ('__str__', 'translation_status')
    filter_horizontal = ('notification_recipients',)
    formfield_overrides = {models.URLField: {'assume_scheme': 'https'}}
    fieldsets = (
        (_('Languages'), {
            'fields': ('mandatory_languages', 'translation_info')
        }),
        (_('Login infos - translate "login info" and "link text" if using multiple languages!'), {
            'fields': ('login_info', 'login_info_url', 'login_info_link_text')
        }),
        (_('Notification settings'), {
            'fields': (('send_notification', 'notification_sender_email'),
                       'notification_text', 'notification_recipients')
        })
    )
    readonly_fields = ('translation_info', )

    def has_add_permission(self, request):
        # The config of a department is created with the department (post_save signal).
        return False

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if db_field.name == "notification_recipients":
            kwargs["queryset"] = User.objects.filter(reviewer__in=self.model_instance.department.reviewers.all())
        return super(ConfigurationAdmin, self).formfield_for_manytomany(db_field, request, **kwargs)

    def get_queryset(self, request):
        qs = super(ConfigurationAdmin, self).get_queryset(request)
        try:
            return qs.filter(
                department__in=request.user.reviewer.departments.all())
        except Reviewer.DoesNotExist:
            if request.user.is_superuser is True:
                return qs


class DepartmentAdmin(AdminObjectMixin, admin.ModelAdmin):
    filter_horizontal = ('reviewers',)
       
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "reporter":
            kwargs["queryset"] = (Reporter.objects.filter(department=None) 
                                  | Reporter.objects.filter(department=self.model_instance))
        return super(DepartmentAdmin, self).formfield_for_foreignkey(db_field, request, **kwargs)


class RoleAdmin(AdminObjectMixin, admin.ModelAdmin):

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "user":
            queryset = User.objects.filter(is_superuser=False, reporter=None, reviewer=None)
            assigned_user = User.objects.none()
            # add assigned user if existing object is provided
            if self.model_instance:
                assigned_user =  User.objects.filter(**{self.model._meta.model_name: self.model_instance})
            kwargs["queryset"] = queryset | assigned_user
            
        return super(RoleAdmin, self).formfield_for_foreignkey(db_field, request, **kwargs)


admin_site.register(User, LabCIRSUserAdmin)
admin_site.register(CriticalIncident, CriticalIncidentAdmin)
admin_site.register(PublishableIncident, PublishableIncidentAdmin)
admin_site.register(LabCIRSConfig, ConfigurationAdmin)
admin_site.register(Department, DepartmentAdmin)
admin_site.register(OrgUnit, OrgUnitAdmin)
admin_site.register(Reporter, RoleAdmin)
admin_site.register(Reviewer, RoleAdmin)
