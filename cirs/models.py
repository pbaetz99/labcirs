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

from datetime import date

from django.conf import settings
from django.contrib.auth.models import Permission, User
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.urls import reverse
from django.utils.crypto import get_random_string
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from multiselectfield import MultiSelectField
from parler.models import (TranslatableModel, TranslatedFields,
                           TranslationDoesNotExist)
from parler.utils import get_language_title


class Role(models.Model):
    user = models.OneToOneField(User, verbose_name=_('User'), on_delete=models.PROTECT,
        help_text=_('User assigned to other roles and superusers are not listed here!'))

    def clean(self):
        if self.user.is_superuser:
            raise ValidationError(
                _('Superuser cannot become {}').format(type(self).__name__.lower()))

    class Meta:
        abstract = True

    def __str__(self):
        return self.user.username


class Reporter(Role):
    
    def clean(self):
        super(Reporter, self).clean()
        if hasattr(self.user, 'reviewer'):
            raise ValidationError(
                _('This user is already a reviewer and thus cannot become a reporter'))
    
    class Meta:
        verbose_name = _('Reporter')
        verbose_name_plural = _('Reporters')


class Reviewer(Role):
    REVIEWER_PERM_CODES = ('change_criticalincident', 'add_publishableincident', 
                           'change_publishableincident', 'change_labcirsconfig',
                           'change_user', 'add_orgunit', 'change_orgunit', 'view_orgunit',
                           'view_comment')
    
    def clean(self):
        super(Reviewer, self).clean()
        if hasattr(self.user, 'reporter'):
            raise ValidationError(
                _('This user is already a reporter and thus cannot become a reviewer'))
            
    def save(self, *args, **kwargs):
        super(Reviewer, self).save(*args, **kwargs)
        REVIEWER_PERMS = Permission.objects.filter(codename__in=self.REVIEWER_PERM_CODES)
        self.user.user_permissions.set(REVIEWER_PERMS)
        self.user.is_staff = True
        self.user.save()

    class Meta:
        verbose_name = _('Reviewer')
        verbose_name_plural = _('Reviewers')      


class Department(models.Model):
    label = models.SlugField(_('Label'), max_length=32, unique=True,
            help_text=_('Label can only consist of letters, numbers, underscores and hyphens.'))
    name = models.CharField(_('Name'), max_length=255, unique=True)
    reporter = models.OneToOneField(Reporter, verbose_name=_("Reporter"), on_delete=models.PROTECT,
            help_text=_('Reporters assigned to other departments are not listed here!'))
    reviewers = models.ManyToManyField(Reviewer, verbose_name=_("Reviewers"), related_name='departments')
    active = models.BooleanField(_("Active"), 
            help_text=_('Incidents can be created only if department is active.'))

    class Meta:
        verbose_name = _('Department')
        verbose_name_plural = _('Departments')

    def get_absolute_url(self):
        return reverse('incidents_for_department', kwargs={'dept': self.label})

    def __str__(self):
        return self.label


FREQUENCY_CHOICES = (('singular case', _('singular case (for the first time)')),
                     ('seldom', _('seldom (once per year)')),
                     ('occasional', _('occasional (once per month)')),
                     ('frequent', _('frequent (once per Week)')),
                     ('regular', _('regular (daily)')))
HAZARD_CHOICES = (('very low', _('very low')), ('low', _('low')),
                  ('moderate', _('moderate')), ('high', _('high')),
                  ('very high', _('very high')))
PREVENTABILITY_CHOICES = (('indistinct', _('appraisal not possible')),
                          ('avoidable', _('The incident was avoidable')),
                          ('not avoidable', _('The incident was not avoidable')))
PUBLIC_CHOICES = ((True, _('I agree that this report will be made public to people outside the quality management team after copyedit.')),
    (False, _('I DO NOT agree that this report will be made public to people outside the quality management team even after copyedit.')))
RISK_CHOICES = (('low', _('low')), ('middle', _('middle')), ('high', _('high')))
STATUS_CHOICES = (('new', _('new')), ('in process', _('in process')),
                  ('under supervision', _('under supervision')),
                  ('completed', _('completed')))
# Reporters see a plainer wording of the review status than the reviewers.
REPORTER_STATUS_LABELS = {
    'new': _('Received'),
    'in process': _('In progress'),
    'under supervision': _('Measures in place, under observation'),
    'completed': _('Closed'),
}
# No look-alikes (0/o, 1/l/i), because reporters write the code down by hand.
COMMENT_CODE_CHARS = 'abcdefghijkmnpqrstuvwxyz23456789'
COMMENT_CODE_LENGTH = 16


def group_code(code):
    """The code in groups of four, as the reporter sees it and notes it down: 'abcd efgh ...'.
    Only for display, the stored code has no spaces (normalize_code ignores them again)."""
    return ' '.join(code[i:i + 4] for i in range(0, len(code), 4))


CATEGORY_CHOICES = (('organisation/communication', _('organisation/communication')),
                    ('technique/methods', _('technique/methods')),
                    ('knowledge/training', _('knowledge/training')),
                    ('concentration/attention (mistake/slip)',
                     _('concentration/attention (mistake/slip)')),
                    ('infrastructure', _('infrastructure')),
                    ('other', _('other'))
                    )


class OrgUnit(models.Model):
    """Optional, self-chosen place of an incident, e.g. a ward. Only two levels are offered."""
    name = models.CharField(_('Name'), max_length=100)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT,
                               related_name='children', verbose_name=_('Parent unit'))
    active = models.BooleanField(_('Active'), default=True)
    position = models.PositiveSmallIntegerField(_('Position'), default=0)

    class Meta:
        ordering = ('position', 'name')
        verbose_name = _('Organisational unit')
        verbose_name_plural = _('Organisational units')

    def __str__(self):
        return '{} › {}'.format(self.parent.name, self.name) if self.parent else self.name


class CriticalIncident(models.Model):
    """
    Generate incident objects. The first part is generated by the reporter,
    the second, separated by #review in the source code
    may be generated by the reviewer.
    """
    today = date.today
    # general part, visible to all
    date = models.DateField(_("Date of incident"))
    incident = models.TextField(_("Mistake / problem / critical incident"))
    reason = models.TextField(_("Cause of failure"))
    immediate_action = models.TextField(
        _("Immediate action / suggestion"), help_text=_(
            "Immediate action or suggestion regarding prevention / troubleshooting"
            )
        )
    preventability = models.CharField(
        _("Preventability"), max_length=255, choices=PREVENTABILITY_CHOICES,
        help_text=_("In your opinion, was the incident avoidable or not?"))
    photo = models.ImageField(
        _("Photo"), upload_to="photos/%Y/%m/%d", null=True, blank=True)
    public = models.BooleanField(
        _("Publication"), choices=PUBLIC_CHOICES, default=None)
    comment_code = models.CharField(max_length=16, blank=True)
    org_unit = models.ForeignKey(OrgUnit, null=True, blank=True, on_delete=models.PROTECT,
                                 related_name='incidents', verbose_name=_('Where did it happen?'))
    # auto filled part, invisible for reporter
    # "auto_now_add" was changed to "default=today" to allow migration to new common DB
    # It should be switched back later to remove possibility of manipulation at python level 
    reported = models.DateField(_("Date of report"), default=today)#auto_now_add=True)
    department = models.ForeignKey(Department, verbose_name=_('Department'),
                                   on_delete=models.PROTECT)
    # review part, invisible for reporter
    action = models.TextField(_("Action"), blank=True)
    responsibilty = models.CharField(
        _("Responsibility"), max_length=255, blank=True)
    review_date = models.DateField(_("Review date"), null=True, blank=True)
    status = models.CharField(
        _("Status"), max_length=255, choices=STATUS_CHOICES, default="new")
    risk = models.CharField(
        _("Risk classification"), max_length=255, choices=RISK_CHOICES, blank=True)
    frequency = models.CharField(
        _("Frequency"), max_length=255, choices=FREQUENCY_CHOICES, blank=True)
    hazard = models.CharField(
        _("Hazard"), help_text=_("For emploees or science"), max_length=255,
        choices=HAZARD_CHOICES, blank=True)
    category = MultiSelectField(
        _("Category"), max_length=255, choices=CATEGORY_CHOICES, blank=True)

    class Meta:
        verbose_name = _("Critical incident")
        verbose_name_plural = _("Critical incidents")

    def photo_tag(self):
        photo_html_tag = ''
        if self.photo:
            image_url = str(settings.MEDIA_URL) + str(self.photo)
            photo_html_tag = format_html(
                '<a href="{}" target="_blank" rel="noopener"><img class="labcirs-thumb" src="{}" alt=""><br>{}</a>',
                image_url, image_url, _("Click to see full size in new window/tab"))
        return photo_html_tag
    photo_tag.short_description = _("Photo")
    photo_tag.help_text = _("Click to see full size in new window/tab")

    def clean(self):
        today = date.today()
        if self.date and self.date > today:
            raise ValidationError(_("Please report only incidents which already happened."))
        if self.id is not None:
            if self.status == 'new':
                for field in ('action', 'responsibilty', 'review_date', 'risk', 'frequency'):
                    if getattr(self, field) not in ('', None):
                        raise ValidationError({'status': _('If anything was changed in the Review block, please set status at least to "in process".')})
    
    def get_absolute_url(self):
        return reverse('incident_detail', kwargs={'dept': self.department.label, 'pk':self.id})
    
    def __str__(self):
        info = (self.incident[:25] + '..') if len(self.incident) > 25 else self.incident
        return info
    
    def get_reporter_status_display(self):
        return str(REPORTER_STATUS_LABELS[self.status])

    # The status as loaded or last saved, to see a change. None: new, or status not loaded.
    _saved_status = None

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._saved_status = instance.__dict__.get('status')  # a deferred field stays unread
        return instance

    def save(self, *agrs, **kwargs):
        while not self.comment_code:
            random_string = get_random_string(COMMENT_CODE_LENGTH, COMMENT_CODE_CHARS)
            if CriticalIncident.objects.filter(comment_code=random_string).count() == 0:
                self.comment_code = random_string
        adding = self._state.adding
        # A save that leaves the status out (update_fields without it, or a status that was not
        # loaded) stores no status change: nothing to log, nothing to tell the reporter.
        update_fields = kwargs.get('update_fields')
        stores_status = 'status' in self.__dict__ and (update_fields is None
                                                       or 'status' in update_fields)
        # The entry of the status log is stored in the same transaction as the incident.
        with transaction.atomic():
            super(CriticalIncident, self).save(*agrs, **kwargs)
            changed = stores_status and self._saved_status not in (None, self.status)
            if adding or changed:
                IncidentStatusChange.objects.create(incident=self, status=self.status)
        if stores_status:
            self._saved_status = self.status
        if changed:
            # After the commit: the admin saves in a transaction, and a rolled-back save must
            # not tell the reporter a status that was not stored.
            transaction.on_commit(self._notify_reporter_of_status)

    def _notify_reporter_of_status(self):
        from .reporter_mail import notify_reporter  # reporter_mail imports this module
        notify_reporter(self, 'status')
        if self.status == 'completed':
            # The address is kept only as long as the report is open.
            ReporterContact.objects.filter(incident=self).delete()


class IncidentStatusChange(models.Model):
    """
    One entry per status an incident took: the initial status when it was created, then every
    change. Written by CriticalIncident.save, shown read-only in the admin. Incidents from before
    the log was introduced have no entries.
    """
    incident = models.ForeignKey(CriticalIncident, on_delete=models.CASCADE,
                                 related_name='status_changes',
                                 verbose_name=_("Critical incident"))
    status = models.CharField(_("Status"), max_length=255, choices=STATUS_CHOICES)
    changed_at = models.DateTimeField(_("Changed at"), auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = _("Status change")
        verbose_name_plural = _("Status changes")
        ordering = ['changed_at', 'id']

    def __str__(self):
        return str(self.get_status_display())


class ReporterContact(models.Model):
    """
    Optional address for notifications to the reporter of an incident. It is the only data that
    can identify a reporter, so it is not registered in the admin, has no readable string form
    and is deleted when the incident is completed (see CriticalIncident.save).
    """
    incident = models.OneToOneField(CriticalIncident, on_delete=models.CASCADE,
                                    related_name='reporter_contact')
    email = models.EmailField(_('E-mail address'))
    created = models.DateTimeField(auto_now_add=True)


class TranslationStatusMixin(object):
    
    mandatory_fields = None
    
    def get_mandatory_fields(self):
        # if no mandatory_fields are definde, return all translated fields
        if self.mandatory_fields == None:
            return self._get_translated_model().get_translated_fields()
        else:
            return self.mandatory_fields
    
    def _translation_status(self):
        for code in self.mandatory_languages:
            try:
                translation = self.get_translation(code)
                for field_name in self.get_mandatory_fields():
                    field = getattr(translation, field_name)
                    if field == "":
                        return 'incomplete'
            except TranslationDoesNotExist:
                return 'incomplete'
        return 'complete'
    _translation_status.short_description = _('Translation status')
    
    translation_status = property(_translation_status)
    
    def _translation_info(self):
        # Colours come from static/css/admin-theme.css (no inline style, CSP).
        msg = '<span class="labcirs-status labcirs-status--{}">{}<br>{}!</span>'
        if self.translation_status == 'complete':
            return format_html(msg, 'complete', _('Translation'), _('complete'))
        else:
            return format_html(msg, 'incomplete', _('Translation'), _('incomplete'))
    _translation_info.short_description = _('Translation info')

    translation_info = property(_translation_info)

class PublishableIncident(TranslationStatusMixin, TranslatableModel):
    critical_incident = models.OneToOneField(CriticalIncident,
                                             verbose_name=_("Critical incident"),
                                             on_delete=models.CASCADE)
   
    translations = TranslatedFields(
        incident=models.CharField(_("Incident"), max_length=255),
        description=models.TextField(_("Description"), blank=True),
        measures_and_consequences=models.TextField(_("Measures and consequences"), blank=True)
    )
    publish = models.BooleanField(_("Publish"), default=False)

    def _mandatory_languages(self):
        return self.critical_incident.department.labcirsconfig.mandatory_languages
    
    _mandatory_languages.short_description = _('Mandatory languages')

    mandatory_languages = property(_mandatory_languages)

    class Meta:
        verbose_name = _("Publishable incident")
        verbose_name_plural = _("Publishable incidents")
        ordering = ['-id']

    def clean(self):
        if self.critical_incident.public is False:
            raise ValidationError(_("The reporter did not agreed to publish this incident!"))
        if self.publish is True:
            languages =  ", ".join(
                [str(get_language_title(lang)) for lang in self.mandatory_languages])
            missing_message = _(
                "All text fields in mandatory languages (%s) has to be filled before 'publish' can be checked!") % languages
            if self.translation_status == 'incomplete':
                raise ValidationError({'publish': missing_message})

    def __str__(self):
        return self.incident


class LabCIRSConfig(TranslationStatusMixin, TranslatableModel):
    EMAIL_HOST_ERROR = _(
        'If you want to send notifications, an existing email server distinct '
        'from localhost has to be set by the server administrator in the local '
        'server configuration.')
    # why _languages does not work here?
    LANGUAGES_HELP = _("If you choose mandatory languages besides the default one, "
            "don't forget to translate fields below. Further fields in publishable "
            "incidents have to be translated before publishing!")
    LANGUAGE_CHOICES = tuple(
        x for x in settings.LANGUAGES if x[0] in [y['code'] for y in settings.PARLER_LANGUAGES[None]])
    mandatory_languages = MultiSelectField(
        _("Mandatory languages"), max_length=255, choices=LANGUAGE_CHOICES,
        default=settings.DEFAULT_MANDATORY_LANGUAGES, help_text=LANGUAGES_HELP)

    # Login data
    login_info_url = models.URLField(_('URL for login info'), blank=True)
    
    translations = TranslatedFields(
        login_info = models.TextField(_('Login info'),
            help_text=_('Translate if there are multiple mandatory languages')),
        login_info_link_text = models.CharField(_('Link text'), max_length=255, blank=True,
            help_text=_('Translate if there are multiple mandatory languages and you store infos at given URL'))
    )
    # Notification settings
    send_notification = models.BooleanField(
        _('Send notification'), default=False,
        help_text=_(
            """Check if you wish to be informed about new incidents per email.<br>
            IMPORTANT: Sender email has to exist and at least one recipient is necessary.<br>
            BUG: If you had no recipients selected before changingn this setting,
            please choose at least one and save.<br>
            Not till then you can activate sending of notifications.""")
        )
    notification_sender_email = models.EmailField(
        _('Notification sender email address'), blank=True,
        help_text=_('Enter a valid email address you want to use as a sender'))
    # TODO: Check if user has email!
    notification_recipients = models.ManyToManyField(
        User, verbose_name=_('Notification recipients'), blank=True,
        help_text=_('Choose recipients of the notification email'))
    notification_text = models.TextField(
        _('Notification text'), blank=True,
        help_text=_('Enter the message which will be send to the reviewer(s) '
                    'when a new incident is reported')
        )

    # auto filled part, invisible for reviewer
    department = models.OneToOneField(Department, verbose_name=_('Department'),
                                      on_delete=models.PROTECT)

    class Meta:
        verbose_name = _('LabCIRS configuration')
        verbose_name_plural = _('LabCIRS configuration')

    def clean(self):
        # TODO: show all error messages if multiple validation errors occur at the same time?
        if self.send_notification is True:
            if (settings.EMAIL_HOST == '' or settings.EMAIL_HOST == 'localhost'):
                raise ValidationError(self.EMAIL_HOST_ERROR)
            if self.notification_recipients.all().count() == 0:
                raise ValidationError(_('You have to choose at least one notification recipient.'))
            if self.notification_sender_email == '':
                raise ValidationError(_('You have to enter valid sender email.'))
        # default language
        default_lang = settings.PARLER_DEFAULT_LANGUAGE_CODE
        verbose_lang = str(get_language_title(default_lang))
        if default_lang not in self.mandatory_languages:
            raise ValidationError(
                _('You have to reactivate %s as it is default language.') % verbose_lang)
            
    def get_mandatory_fields(self):
        if self.login_info_url in (None, ''):
            return ['login_info', ]
        else:
            return super(LabCIRSConfig, self).get_mandatory_fields()
            
    def __str__(self):
        return 'LabCIRS configuration for {}'.format(self.department.label)

@receiver(post_save, sender=Department)
def create_config_for_department(sender, instance, created, **kwargs):
    if created:
        LabCIRSConfig.objects.create(department=instance) # really good idea? might be a security issue. On the other hand, if one department is deleted, pk will not be reused!


COMMENT_STATUS_CHOICES = (('open', _('open')), ('in process', _('in process')),
                  ('closed', _('closed')))

class Comment(models.Model):
    critical_incident = models.ForeignKey(CriticalIncident, 
                                          verbose_name=_("Critical incident"),
                                          related_name='comments',
                                          on_delete=models.PROTECT)
    author = models.ForeignKey(User, verbose_name=_("Author"), 
                               on_delete=models.PROTECT)
    created = models.DateField(_("Created at"), default=date.today)#auto_now_add=True)
    text =  models.TextField(_("Text"))
    status = models.CharField(
        _("Status"), help_text=_("Status of the comment"), max_length=255,
        choices=COMMENT_STATUS_CHOICES, default=COMMENT_STATUS_CHOICES[0][0])
    
    def __str__(self):
        return self.text[:64]
