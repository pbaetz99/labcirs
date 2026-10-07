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

"""The two forms of the QM on the page of an incident: the review and the publication.

They know nothing of users and permissions, the view decides who may use them. The rules of the
model hold in them as they do in the admin. Both are drawn in groups (grouped.html), which long
forms need: the groups are what a person sees before the fields.
"""

from typing import NamedTuple

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.forms import (BooleanField, CharField, CheckboxSelectMultiple, DateInput, Form,
                          ModelForm, RadioSelect, Textarea)
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy
from parler.utils import get_language_title

from cirs.forms import UiFormMixin, org_unit_choices
from cirs.models import CriticalIncident, OrgUnit, PublishableIncident


class Group(NamedTuple):
    """Fields that belong together: a legend (or None), the names of the fields, and whether they
    stand side by side where there is room. A form's field_groups has the bound fields instead."""
    legend: object
    fields: tuple
    columns: bool = False


class GroupedFormMixin(UiFormMixin):
    template_name = 'cirs/qm/forms/grouped.html'
    groups = ()

    def get_groups(self):
        return self.groups

    @property
    def field_groups(self):
        return [Group(group.legend, tuple(self[name] for name in group.fields), group.columns)
                for group in self.get_groups()]


class ReviewForm(GroupedFormMixin, ModelForm):
    """The assessment of an incident: the fields of the "Review" block of the admin."""
    groups = (
        Group(None, ('status',)),
        Group(_('Classification'), ('org_unit', 'risk', 'frequency', 'hazard'), columns=True),
        Group(None, ('category',)),
        Group(_('Measure'), ('action', 'responsibilty', 'review_date')),
    )

    class Meta:
        model = CriticalIncident
        fields = ['status', 'org_unit', 'risk', 'frequency', 'hazard', 'category',
                  'responsibilty', 'action', 'review_date']
        labels = {'status': pgettext_lazy('incident list', 'Status'), 'org_unit': _('Where'),
                  'risk': _('Risk')}
        widgets = {'status': RadioSelect,
                   # the widget of the library puts a class on every box that no style knows
                   'category': CheckboxSelectMultiple,
                   'action': Textarea(attrs={'rows': 4}),
                   'review_date': DateInput(attrs={'type': 'date'}, format='%Y-%m-%d')}
        help_texts = {
            'status': _('A new status goes by e-mail to the reporting person if they have set up '
                        'notifications. The e-mail names the status and nothing else. With '
                        '“Completed” the notifications end: later replies and statuses are no '
                        'longer sent by e-mail, so send a reply first.'),
            'category': _('You may choose several.'),
            'review_date': _('The day on which the measure is to be checked.')}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        place = self.instance.org_unit_id
        # Queryset first: assigning it resets the choices. The place that the report names stays a
        # choice even if it was switched off since, or the review would clear it unasked.
        self.fields['org_unit'].queryset = OrgUnit.objects.filter(Q(active=True) | Q(pk=place))
        choices = org_unit_choices()
        if place and place not in [value for value, _label in self._flat(choices)]:
            choices.append((place, str(self.instance.org_unit)))
        self.fields['org_unit'].choices = choices
        for name in ('risk', 'frequency', 'hazard'):
            field = self.fields[name]
            field.choices = [('', _('Not specified'))] + [c for c in field.choices if c[0]]

    @staticmethod
    def _flat(choices):
        flat = []
        for value, label in choices:
            flat.extend(list(label) if isinstance(label, (list, tuple)) else [(value, label)])
        return flat

    def save(self):
        # Only these columns: the rest of the report is not the QM's to change, and a save of the
        # whole row would undo what somebody else wrote to it in the meantime.
        self.instance.save(update_fields=list(self.fields))
        return self.instance


class Language(NamedTuple):
    code: str
    title: str
    mandatory: bool


# The translated fields of a case, in the order of the page, with their labels.
TRANSLATED = (('incident', _('Title')), ('description', _('Description')),
              ('measures_and_consequences', _('Measures and consequences')))


class PublicationForm(GroupedFormMixin, Form):
    """
    The published case of an incident: the texts in every language of the installation and the
    switch that publishes it. The case is created with the first save. The rules are the ones of
    the model (PublishableIncident.clean): a report that was not approved for publication gets no
    case, and "publish" needs all texts in all mandatory languages.

    The case as it is stored is self.case (None before the first save), with published and
    complete; they are read before the form is validated and stay what is stored.
    """
    publish = BooleanField(required=False, label=_('Publish'), help_text=_(
        'Published cases are visible to every visitor of the site, also without logging in. '
        'Make sure that the texts hold no names and no patient data.'))

    def __init__(self, incident, data=None, **kwargs):
        super().__init__(data, **kwargs)
        self.incident = incident
        self.case = self._stored_case()
        mandatory = incident.department.labcirsconfig.mandatory_languages
        codes = [entry['code'] for entry in settings.PARLER_LANGUAGES[None]]
        self.languages = [Language(code, str(get_language_title(code)), code in mandatory)
                          for code in codes]
        stored = {t.language_code: t for t in self.case.translations.all()} if self.case else {}
        self.stored_languages = set(stored)
        for language in self.languages:
            for name, label in TRANSLATED:
                key = '%s-%s' % (language.code, name)
                self.fields[key] = CharField(
                    required=False, label='%s (%s)' % (label, language.title),
                    max_length=255 if name == 'incident' else None,
                    widget=None if name == 'incident' else Textarea(attrs={'rows': 4}))
                if language.code in stored:
                    self.initial[key] = getattr(stored[language.code], name)
        self.initial['publish'] = bool(self.case and self.case.publish)
        self.published = self.initial['publish']
        self.complete = self._complete(self.case)

    def get_groups(self):
        groups = []
        for language in self.languages:
            legend = (_('%(language)s (mandatory language)') if language.mandatory
                      else _('%(language)s (optional language)'))
            groups.append(Group(legend % {'language': language.title},
                                tuple('%s-%s' % (language.code, name) for name, _l in TRANSLATED)))
        return groups + [Group(None, ('publish',))]

    def _stored_case(self):
        case = (PublishableIncident.objects.filter(critical_incident=self.incident)
                .prefetch_related('translations').first())
        if case is not None:
            case.critical_incident = self.incident  # the incident that is loaded, with its department
        return case

    def _ready(self, case):
        # The model asks the translation of the current language for the names of the fields, so
        # the current language has to be one that has a translation: else a case that exists in
        # German only would be incomplete on an English page.
        for language in self.languages:
            if case.has_translation(language.code):
                case.set_current_language(language.code)
                return

    def _complete(self, case):
        if case is None:
            return False
        self._ready(case)
        return case.translation_status == 'complete'

    def clean(self):
        cleaned = super().clean()
        if self.errors:  # a text that is too long: nothing to compare yet
            return cleaned
        self.texts = {
            language.code: {name: cleaned.get('%s-%s' % (language.code, name), '')
                            for name, _label in TRANSLATED}
            for language in self.languages}
        for code, texts in self.texts.items():
            # a language that has text, or has a translation already, has a title
            if (any(texts.values()) or code in self.stored_languages) and not texts['incident']:
                self.add_error('%s-incident' % code,
                               self.fields['%s-incident' % code].error_messages['required'])
        if self.errors:
            return cleaned
        if not any(any(texts.values()) for texts in self.texts.values()) and self.case is None:
            raise ValidationError(_('Please fill in the texts of at least one language.'))
        # The rules of the model, on the case as it would be after the save.
        self._build(cleaned['publish']).clean()
        return cleaned

    def _build(self, publish):
        """The case with the texts of the form applied, not saved."""
        case = self._stored_case() or PublishableIncident(critical_incident=self.incident)
        for language in self.languages:
            texts = self.texts[language.code]
            if any(texts.values()) or case.has_translation(language.code):
                case.set_current_language(language.code)
                for name, value in texts.items():
                    setattr(case, name, value)
        case.publish = publish
        self._ready(case)
        return case

    def save(self):
        publish = self.cleaned_data['publish']
        try:
            with transaction.atomic():
                case = self._build(publish)
                case.save()
        except IntegrityError:
            # A second request made the case while this one worked (a double click without
            # JavaScript): the case is there now, so the texts go into it.
            with transaction.atomic():
                case = self._build(publish)
                case.save()
        return case
