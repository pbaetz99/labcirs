# Copyright (C) 2018-2025 Sebastian Major
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


import logging
import smtplib
from datetime import date

from django.conf import settings
from django.core import mail
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from django.db.models import Prefetch
from django.forms import (CharField, ClearableFileInput, DateInput,
                          EmailField, EmailInput, Form, ModelForm,
                          RadioSelect, Textarea, TextInput, ValidationError)
from django.utils import translation
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from .models import (COMMENT_CODE_LENGTH, Comment, CriticalIncident, OrgUnit,
                     ReporterContact)
from .photos import FORMAT_ERROR, PhotoError, random_photo_name, sanitize_image
from .reporter_mail import notify_reporter

logger = logging.getLogger('cirs')


def notify_on_creation(form, department, subject='', excluded_user_id=None):
    config = department.labcirsconfig
    if config.send_notification:
        # send only if incident was saved
        if form.instance.pk is not None:
            # The QM reads the mail: site language, not the language of the reporter's request.
            with translation.override(settings.LANGUAGE_CODE):
                subject = str(subject)
                try:
                    # TODO: add comment notification
                    mail_body = config.notification_text
                except:
                    mail_body = ""
            to_list = []
            for user in config.notification_recipients.all().exclude(id=excluded_user_id):
                to_list.append(user.email)
            try:
                mail.send_mail(subject, mail_body,
                               config.notification_sender_email,
                               to_list, fail_silently=False)
            except (smtplib.SMTPException, OSError):
                # The report is saved and the reporter needs the code, so the request goes on.
                logger.exception('Notification mail for department %s failed', department.label)


def org_unit_choices():
    # Two levels only: units nested deeper than one child stay unlisted.
    active_children = Prefetch('children', queryset=OrgUnit.objects.filter(active=True))
    choices = [('', _('No answer'))]
    for unit in OrgUnit.objects.filter(active=True, parent=None).prefetch_related(active_children):
        children = [(child.pk, child.name) for child in unit.children.all()]
        choices.append((unit.name, children) if children else (unit.pk, unit.name))
    return choices


class UiFormMixin:
    """
    Renders a form with the markup of the design system (core.css), see ui_form.html.
    Django sets aria-invalid and aria-describedby (hint and error ids) itself.
    """
    template_name = 'cirs/forms/ui_form.html'
    template_name_label = 'cirs/forms/ui_label.html'

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('label_suffix', '')  # "Datum", not "Datum:"
        super().__init__(*args, **kwargs)


class IncidentCreateForm(UiFormMixin, ModelForm):
    # Not a field of the incident: the address goes to ReporterContact, which the QM never sees.
    reporter_email = EmailField(
        required=False, label=_('E-mail for notifications'),
        # off: the browser must not offer the work address
        widget=EmailInput(attrs={'autocomplete': 'off'}))

    class Meta:
        model = CriticalIncident
        fields = ['date', 'incident', 'reason', 'immediate_action',
                  'preventability', 'photo', 'public', 'org_unit']
        widgets = {"date": DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
                   "incident": Textarea(attrs={'rows': 5}),
                   "reason": Textarea(attrs={'rows': 5}),
                   "immediate_action": Textarea(attrs={'rows': 5}),
                   # HEIC is not in the list: phones then hand over a JPEG
                   "photo": ClearableFileInput(attrs={
                       'class': 'ui-w-full ui-dateiwahl',
                       'accept': 'image/jpeg,image/png,image/gif,image/webp'}),
                   "public": RadioSelect,
                   }
        help_texts = {'org_unit': _('Optional. In small units this information may allow '
                                    'conclusions about you.'),
                      'photo': _('Optional. JPEG, PNG, GIF or WebP up to 10 MB are allowed. '
                                 'Metadata such as location and device are removed on upload.')}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Queryset first: assigning it resets the choices.
        self.fields['org_unit'].queryset = OrgUnit.objects.filter(active=True)
        self.fields['org_unit'].choices = org_unit_choices()
        self.fields['photo'].error_messages['invalid_image'] = FORMAT_ERROR
        # No future date: the browser stops it before the model does.
        self.fields['date'].widget.attrs['max'] = date.today().isoformat()
        # Not the framework's "---------".
        self.fields['preventability'].choices = (
            [('', _('Please choose'))] + [c for c in self.fields['preventability'].choices if c[0]])
        if not settings.ASK_PUBLICATION_CONSENT:
            del self.fields['public']  # IncidentCreate.form_valid sets public = True
        if not settings.DEFAULT_FROM_EMAIL:
            del self.fields['reporter_email']  # no mail can be sent without a sender
        else:
            # built per request, so it follows the active language; the warning must catch the eye
            self.fields['reporter_email'].help_text = format_html(
                '{} <strong class="labcirs-warnung">{}</strong>', _('Optional.'),
                _('With an address your report is no longer completely anonymous.'))

    def clean_photo(self):
        photo = self.cleaned_data.get('photo')
        if not isinstance(photo, UploadedFile):
            return photo
        try:
            data, ext = sanitize_image(photo)
        except PhotoError as error:
            raise ValidationError(str(error), code='invalid_photo')
        # Random name: the original file name may identify the reporter.
        return ContentFile(data, name=random_photo_name(ext))

    def save(self):
        result = super(IncidentCreateForm, self).save()
        department = self.instance.department
        notify_on_creation(self, department, _('New critical incident'))
        email = self.cleaned_data.get('reporter_email')
        if email:
            ReporterContact.objects.create(incident=self.instance, email=email)
            notify_reporter(self.instance, 'received')
        return result


def normalize_code(value):
    # Codes are copied from paper or read aloud: ignore spaces and case.
    return ''.join(value.split()).lower()


class IncidentSearchForm(UiFormMixin, Form):
    incident_code = CharField(
        label=_('Incident code'),
        help_text=_('The code has 16 characters, older codes have 8. Spaces and upper or lower '
                    'case do not matter.'),
        # off: shared PCs must not remember codes; no auto-capitalisation or spell check on a code
        widget=TextInput(attrs={'autocomplete': 'off', 'autocapitalize': 'none',
                                'spellcheck': 'false'}))

    def clean_incident_code(self):
        comment_code = normalize_code(self.cleaned_data.get('incident_code'))
        try:
            CriticalIncident.objects.get(comment_code=comment_code)
            return comment_code
        except CriticalIncident.DoesNotExist:
            count = len(comment_code)
            if count not in (8, COMMENT_CODE_LENGTH):
                # A part is missing or too much was typed: say it, "not found" would only
                # make the reporter doubt the code. Only after the lookup, so that a code
                # of any length that exists is found.
                raise ValidationError(
                    ngettext('You entered %(count)d character. A code has 16 characters, older '
                             'codes 8. Part of it may be missing.',
                             'You entered %(count)d characters. A code has 16 characters, older '
                             'codes 8. Part of it may be missing.', count),
                    code='invalid_length', params={'count': count})
            raise ValidationError(_('No matching critical incident found!'), code='invalid_id')


class CommentForm(UiFormMixin, ModelForm):

    class Meta:
        model = Comment
        fields = ['text']
        labels = {'text': _('Your reply')}
        help_texts = {'text': _('Do not write names or patient data in your reply. Describe '
                                'people by their role, e.g. “nurse on night shift”.')}
        widgets = {"text": Textarea(attrs={'rows': 5})}

    def save(self):
        result = super(CommentForm, self).save()
        department = self.instance.critical_incident.department
        notify_on_creation(self, department, _('New LabCIRS comment'), self.instance.author.id)
        if self.instance.author_id != department.reporter.user_id:
            notify_reporter(self.instance.critical_incident, 'reply', self.instance)
        return result
