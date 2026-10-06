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

Both are plain forms that know nothing of users or permissions; the view decides who may use
them. The rules of the model hold in them as they do in the admin.
"""

import copy
import re
from datetime import date
from unittest import mock

from django.db import IntegrityError, connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import translation
from model_bakery import baker

from cirs.models import CriticalIncident, OrgUnit, PublishableIncident
from cirs.qm.forms import PublicationForm, ReviewForm

from .helpers import make_incident

REVIEW = {'status': 'in process', 'org_unit': '', 'risk': 'high', 'frequency': 'seldom',
          'hazard': 'low', 'category': ['infrastructure', 'other'], 'responsibilty': 'Station',
          'action': 'Synthetic action', 'review_date': '2026-12-01'}
FIELDS = ('status', 'org_unit', 'risk', 'frequency', 'hazard', 'category', 'responsibilty',
          'action', 'review_date')


def flat_choices(field):
    """The (value, label) of a choice field, the members of a group of choices as single ones."""
    flat = []
    for value, label in field.choices:
        flat.extend(list(label) if isinstance(label, (list, tuple)) else [(value, label)])
    return flat


class FormCase(TestCase):

    def setUp(self):
        self.enterContext(translation.override('en'))
        self.dept = baker.make_recipe('cirs.department')
        self.incident = make_incident(self.dept, incident='Synthetic report', reason='Synthetic reason')


class ReviewFormTest(FormCase):

    def form(self, data=None, incident=None, **extra):
        return ReviewForm(data, instance=incident or copy.copy(self.incident), **extra)

    def test_it_has_the_fields_of_the_review_and_no_other(self):
        self.assertEqual(tuple(self.form().fields), FIELDS)

    def test_the_groups_cover_every_field_once(self):
        form = self.form()
        names = [field.name for group in form.field_groups for field in group.fields]
        self.assertCountEqual(names, FIELDS)
        self.assertEqual(len(names), len(set(names)))

    def test_the_form_shows_what_is_stored(self):
        unit = baker.make(OrgUnit, name='Synthetic ward')
        incident = make_incident(self.dept, status='under supervision', org_unit=unit,
                                 risk='low', category=['other'], action='Done',
                                 responsibilty='Role', review_date=date(2026, 12, 24))
        form = self.form(incident=CriticalIncident.objects.get(pk=incident.pk))
        self.assertEqual(form['status'].value(), 'under supervision')
        self.assertEqual(form['org_unit'].value(), unit.pk)
        self.assertEqual(form['risk'].value(), 'low')
        self.assertEqual(form['category'].value(), ['other'])
        self.assertEqual(form['review_date'].value(), date(2026, 12, 24))

    def test_a_valid_review_is_saved_and_nothing_else_of_the_incident(self):
        stored = CriticalIncident.objects.get(pk=self.incident.pk)
        data = dict(REVIEW, incident='forged', reason='forged', immediate_action='forged',
                    preventability='not avoidable', public='False', reported='2000-01-01',
                    date='2000-01-01', comment_code='forged', department=baker.make_recipe(
                        'cirs.department').pk)
        form = self.form(data, incident=copy.copy(stored))
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        saved = CriticalIncident.objects.get(pk=self.incident.pk)
        self.assertEqual((saved.status, saved.risk, saved.frequency, saved.hazard),
                         ('in process', 'high', 'seldom', 'low'))
        self.assertEqual(saved.category, ['infrastructure', 'other'])
        self.assertEqual((saved.responsibilty, saved.action, saved.review_date),
                         ('Station', 'Synthetic action', date(2026, 12, 1)))
        for name in ('incident', 'reason', 'immediate_action', 'preventability', 'public',
                     'reported', 'date', 'comment_code', 'department_id', 'photo'):
            self.assertEqual(getattr(saved, name), getattr(stored, name), name)

    def test_saving_writes_the_review_columns_and_no_other(self):
        # an update of the whole row would undo what somebody else wrote to the report meanwhile
        form = self.form(REVIEW)
        self.assertTrue(form.is_valid(), form.errors)
        with CaptureQueriesContext(connection) as queries:
            form.save()
        updates = [q['sql'] for q in queries if q['sql'].startswith('UPDATE "cirs_criticalincident"')]
        self.assertEqual(len(updates), 1)
        assigned = updates[0].split(' SET ')[1].split(' WHERE ')[0]
        columns = set(re.findall(r'"(\w+)" = ', assigned))
        self.assertEqual(columns, {'status', 'org_unit_id', 'risk', 'frequency', 'hazard',
                                   'category', 'responsibilty', 'action', 'review_date'})

    def test_a_change_of_the_status_is_logged_once(self):
        form = self.form(REVIEW)
        self.assertTrue(form.is_valid(), form.errors)
        with self.captureOnCommitCallbacks() as callbacks:
            form.save()
        self.assertEqual(list(self.incident.status_changes.values_list('status', flat=True)),
                         ['new', 'in process'])
        self.assertEqual(len(callbacks), 1)  # the mail to the reporter, after the commit

    def test_a_review_without_a_new_status_logs_nothing(self):
        stored = make_incident(self.dept, status='in process')
        form = self.form(dict(REVIEW, action='Another'), incident=copy.copy(stored))
        self.assertTrue(form.is_valid(), form.errors)
        with self.captureOnCommitCallbacks() as callbacks:
            form.save()
        self.assertEqual(stored.status_changes.count(), 1)
        self.assertEqual(callbacks, [])

    def test_the_rule_of_the_model_holds_a_review_needs_a_status_beyond_new(self):
        form = self.form(dict(REVIEW, status='new'))
        self.assertFalse(form.is_valid())
        self.assertEqual(list(form.errors), ['status'])
        self.assertIn('in process', form.errors['status'][0])
        # nothing was written
        self.assertEqual(CriticalIncident.objects.get(pk=self.incident.pk).risk, '')

    def test_an_empty_review_of_a_new_report_is_fine(self):
        form = self.form({'status': 'new'})
        self.assertTrue(form.is_valid(), form.errors)

    def test_values_that_are_no_choice_are_refused(self):
        for name, value in (('status', 'done'), ('risk', 'extreme'), ('frequency', 'never'),
                            ('hazard', 'none'), ('category', ['nonsense']),
                            ('org_unit', '999999'), ('review_date', 'tomorrow')):
            with self.subTest(name=name):
                form = self.form(dict(REVIEW, **{name: value}))
                self.assertFalse(form.is_valid())
                self.assertIn(name, form.errors)

    def test_no_category_chosen_clears_the_categories(self):
        incident = make_incident(self.dept, status='in process', category=['other'])
        data = {key: value for key, value in REVIEW.items() if key != 'category'}
        form = self.form(data, incident=copy.copy(incident))
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(CriticalIncident.objects.get(pk=incident.pk).category, [])

    def test_the_place_can_be_set_and_cleared(self):
        unit = baker.make(OrgUnit, name='Synthetic ward')
        form = self.form(dict(REVIEW, org_unit=str(unit.pk)))
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(CriticalIncident.objects.get(pk=self.incident.pk).org_unit, unit)
        form = self.form(dict(REVIEW, org_unit=''), incident=CriticalIncident.objects.get(
            pk=self.incident.pk))
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertIsNone(CriticalIncident.objects.get(pk=self.incident.pk).org_unit)

    def test_the_place_of_the_report_stays_a_choice_even_when_it_is_no_longer_offered(self):
        # switched off after the report: saving the review must not clear it
        unit = baker.make(OrgUnit, name='Old ward', active=False)
        incident = make_incident(self.dept, status='in process', org_unit=unit)
        stored = CriticalIncident.objects.get(pk=incident.pk)
        form = self.form(incident=stored)
        self.assertIn(unit.pk, [value for value, _label in flat_choices(form.fields['org_unit'])])
        form = self.form(dict(REVIEW, org_unit=str(unit.pk)), incident=copy.copy(stored))
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(CriticalIncident.objects.get(pk=incident.pk).org_unit, unit)

    def test_the_places_are_the_ones_the_report_form_offers(self):
        group = baker.make(OrgUnit, name='Group')
        child = baker.make(OrgUnit, name='Child', parent=group)
        off = baker.make(OrgUnit, name='Off', active=False)
        flat = flat_choices(self.form().fields['org_unit'])
        values = [value for value, _label in flat]
        self.assertIn(child.pk, values)
        self.assertNotIn(off.pk, values)
        self.assertEqual(flat[0], ('', 'No answer'))  # "not specified" first

    def test_the_blank_choices_say_not_specified(self):
        form = self.form()
        for name in ('risk', 'frequency', 'hazard'):
            self.assertEqual(form.fields[name].choices[0], ('', 'Not specified'), name)

    def test_the_status_and_the_categories_are_groups_of_choices(self):
        form = self.form()
        self.assertTrue(form['status'].use_fieldset)
        self.assertTrue(form['category'].use_fieldset)
        # the widget of the library puts a class on every box that no style knows
        self.assertNotIn('multiselectfield', str(form['category']))

    def test_the_date_is_an_input_of_its_own_kind_in_the_iso_format(self):
        tag = str(self.form(incident=make_incident(self.dept, status='in process',
                                                   review_date=date(2026, 12, 24))
                            )['review_date'])
        self.assertIn('type="date"', tag)
        self.assertIn('value="2026-12-24"', tag)

    def test_the_rendering_names_every_field_and_marks_an_invalid_one(self):
        form = self.form(dict(REVIEW, status='new'))
        html = form.render()
        self.assertEqual(html.count('class="ui-fieldset"'), 4)  # status, place, category, measure
        for name in FIELDS:
            self.assertIn('name="%s"' % name, html, name)
        self.assertIn('href="#id_status"', html)
        self.assertIn('role="alert"', html)

    def test_the_copy_of_a_failed_review_leaves_the_stored_incident_alone(self):
        stored = CriticalIncident.objects.get(pk=self.incident.pk)
        form = self.form(dict(REVIEW, status='new'), incident=copy.copy(stored))
        self.assertFalse(form.is_valid())
        self.assertEqual(stored.risk, '')
        self.assertEqual(stored.status, 'new')


def make_case(incident, texts, publish=False):
    """A published case with the texts {language: (title, description, measures)}."""
    case = PublishableIncident.objects.create(critical_incident=incident)
    for language, (title, description, measures) in texts.items():
        case.create_translation(language, incident=title, description=description,
                                measures_and_consequences=measures)
    if publish:
        case.publish = True
        case.save()
    return case


FULL = {'en-incident': 'Title', 'en-description': 'Description', 'en-measures_and_consequences': 'Measures',
        'de-incident': 'Titel', 'de-description': 'Beschreibung', 'de-measures_and_consequences': 'Massnahmen'}


class PublicationFormTest(FormCase):

    def form(self, data=None):
        return PublicationForm(self.incident, data)

    def stored(self):
        return PublishableIncident.objects.filter(critical_incident=self.incident).first()

    def texts(self):
        return {t.language_code: (t.incident, t.description, t.measures_and_consequences)
                for t in self.stored().translations.all()}

    def test_it_has_a_block_for_every_language_of_the_installation(self):
        form = self.form()
        self.assertEqual([language.code for language in form.languages], ['en', 'de'])
        self.assertEqual([language.mandatory for language in form.languages], [True, True])
        for code in ('en', 'de'):
            for name in ('incident', 'description', 'measures_and_consequences'):
                self.assertIn('%s-%s' % (code, name), form.fields)
        self.assertIn('publish', form.fields)

    def test_the_languages_that_are_not_mandatory_are_named_so(self):
        config = self.dept.labcirsconfig
        config.mandatory_languages = ['en']
        config.save()
        form = self.form()
        self.assertEqual([(language.code, language.mandatory) for language in form.languages],
                         [('en', True), ('de', False)])

    @override_settings(PARLER_LANGUAGES={None: ({'code': 'en'},),
                                         'default': {'fallbacks': ['en'], 'hide_untranslated': False}})
    def test_an_installation_with_one_language_has_one_block(self):
        form = self.form()
        self.assertEqual([language.code for language in form.languages], ['en'])
        self.assertNotIn('de-incident', form.fields)

    def test_the_labels_say_the_language_so_that_the_error_list_is_clear(self):
        form = self.form()
        self.assertEqual(form.fields['de-incident'].label, 'Title (Deutsch)')
        self.assertEqual(form.fields['en-description'].label, 'Description (English)')

    def test_the_blocks_are_groups_with_a_legend_and_the_switch_is_one_of_its_own(self):
        groups = self.form().field_groups
        self.assertEqual([group.legend for group in groups],
                         ['English (mandatory language)', 'Deutsch (mandatory language)', None])
        self.assertEqual([[field.name for field in group.fields] for group in groups][-1],
                         ['publish'])

    def test_a_new_case_starts_empty_nothing_is_copied_from_the_report(self):
        form = self.form()
        for name in form.fields:
            self.assertFalse(form[name].value(), name)
        self.assertNotIn('Synthetic', form.render())

    def test_both_languages_and_publish_make_a_published_case(self):
        form = self.form(dict(FULL, publish='on'))
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        case = self.stored()
        self.assertTrue(case.publish)
        self.assertEqual(self.texts(), {'en': ('Title', 'Description', 'Measures'),
                                        'de': ('Titel', 'Beschreibung', 'Massnahmen')})

    def test_a_draft_may_be_saved_with_one_language_and_not_published(self):
        form = self.form({'en-incident': 'Title', 'en-description': 'Description'})
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        case = self.stored()
        self.assertFalse(case.publish)
        self.assertEqual(self.texts(), {'en': ('Title', 'Description', '')})
        self.assertNotIn('de', self.texts())  # a language without text has no translation

    def test_publishing_needs_every_text_in_every_mandatory_language(self):
        data = dict(FULL, publish='on')
        data['de-description'] = ''
        form = self.form(data)
        self.assertFalse(form.is_valid())
        self.assertEqual(list(form.errors), ['publish'])
        self.assertIn('mandatory languages', form.errors['publish'][0])
        self.assertIn('Deutsch', form.errors['publish'][0])
        self.assertIsNone(self.stored())  # and nothing was saved

    def test_publishing_needs_a_language_that_is_missing_altogether(self):
        form = self.form({'en-incident': 'Title', 'en-description': 'D',
                          'en-measures_and_consequences': 'M', 'publish': 'on'})
        self.assertFalse(form.is_valid())
        self.assertEqual(list(form.errors), ['publish'])

    def test_a_language_that_is_not_mandatory_may_stay_empty(self):
        config = self.dept.labcirsconfig
        config.mandatory_languages = ['en']
        config.save()
        data = {key: value for key, value in FULL.items() if key.startswith('en-')}
        form = self.form(dict(data, publish='on'))
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertTrue(self.stored().publish)

    def test_a_language_with_a_text_needs_a_title(self):
        form = self.form({'en-incident': '', 'en-description': 'Only a text'})
        self.assertFalse(form.is_valid())
        self.assertEqual(list(form.errors), ['en-incident'])
        self.assertEqual(form.errors['en-incident'], ['This field is required.'])

    def test_a_language_that_has_a_translation_keeps_a_title(self):
        make_case(self.incident, {'en': ('Title', 'D', 'M')})
        form = self.form({'en-incident': '', 'en-description': '', 'en-measures_and_consequences': ''})
        self.assertFalse(form.is_valid())
        self.assertEqual(list(form.errors), ['en-incident'])

    def test_nothing_at_all_is_not_a_case(self):
        form = self.form({})
        self.assertFalse(form.is_valid())
        self.assertEqual(len(form.non_field_errors()), 1)
        self.assertIsNone(self.stored())

    def test_a_title_has_at_most_255_characters(self):
        form = self.form({'en-incident': 'x' * 256})
        self.assertFalse(form.is_valid())
        self.assertIn('en-incident', form.errors)
        self.assertTrue(self.form({'en-incident': 'x' * 255}).is_valid())

    def test_the_text_is_trimmed_and_only_whitespace_is_nothing(self):
        form = self.form({'en-incident': '  Title  ', 'en-description': '   ', 'de-incident': '   '})
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(self.texts(), {'en': ('Title', '', '')})

    def test_a_report_that_may_not_be_published_makes_no_case(self):
        incident = make_incident(self.dept, public=False)
        form = PublicationForm(incident, dict(FULL, publish='on'))
        self.assertFalse(form.is_valid())
        self.assertIn('did not agreed', ' '.join(form.non_field_errors()))
        self.assertFalse(PublishableIncident.objects.filter(critical_incident=incident).exists())
        draft = PublicationForm(incident, {'en-incident': 'Title'})  # not even a draft
        self.assertFalse(draft.is_valid())

    def test_an_existing_case_is_changed_in_place(self):
        case = make_case(self.incident, {'en': ('Title', 'D', 'M'), 'de': ('Titel', 'B', 'N')})
        form = self.form(dict(FULL, **{'en-incident': 'New title'}))
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(PublishableIncident.objects.filter(critical_incident=self.incident).count(), 1)
        self.assertEqual(self.stored().pk, case.pk)
        self.assertEqual(self.texts()['en'][0], 'New title')
        self.assertEqual(self.texts()['de'][0], 'Titel')

    def test_the_form_of_an_existing_case_shows_its_texts(self):
        make_case(self.incident, {'en': ('Title', 'D', 'M'), 'de': ('Titel', 'B', 'N')}, publish=True)
        form = self.form()
        self.assertEqual(form['en-incident'].value(), 'Title')
        self.assertEqual(form['de-measures_and_consequences'].value(), 'N')
        self.assertTrue(form['publish'].value())
        self.assertTrue(form.published)
        self.assertTrue(form.complete)

    def test_a_case_can_be_taken_back_and_keeps_its_texts(self):
        make_case(self.incident, {'en': ('Title', 'D', 'M'), 'de': ('Titel', 'B', 'N')}, publish=True)
        form = self.form({key: value for key, value in FULL.items()})  # publish left out
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertFalse(self.stored().publish)
        self.assertEqual(len(self.texts()), 2)

    def test_a_text_that_did_not_change_is_not_written_again(self):
        make_case(self.incident, {'en': ('Title', 'Description', 'Measures'),
                                  'de': ('Titel', 'Beschreibung', 'Massnahmen')})
        form = self.form(dict(FULL, **{'de-incident': 'Anderer Titel'}))
        self.assertTrue(form.is_valid(), form.errors)
        with CaptureQueriesContext(connection) as queries:
            form.save()
        updates = [q['sql'] for q in queries
                   if q['sql'].startswith('UPDATE "cirs_publishableincident_translation"')]
        self.assertEqual(len(updates), 1)

    def test_the_state_shown_is_the_stored_one_whatever_the_form_does(self):
        make_case(self.incident, {'en': ('Title', 'D', 'M')})
        form = self.form(dict(FULL, publish='on', **{'en-incident': 'Changed'}))
        form.is_valid()
        self.assertFalse(form.published)
        self.assertFalse(form.complete)  # the German texts are not stored yet
        self.assertEqual(form.case.pk, self.stored().pk)

    def test_the_languages_complete_status_ignores_the_language_of_the_request(self):
        # the model asks the translation of the language of the request for the field names: an
        # English page of a case that only exists in German would count as incomplete
        config = self.dept.labcirsconfig
        config.mandatory_languages = ['de']
        config.save()
        make_case(self.incident, {'de': ('Titel', 'Beschreibung', 'Massnahmen')})
        with translation.override('en'):
            self.assertTrue(self.form().complete)
        form = self.form({'de-incident': 'Titel', 'de-description': 'B',
                          'de-measures_and_consequences': 'M', 'publish': 'on'})
        self.assertTrue(form.is_valid(), form.errors)

    def test_nothing_is_stored_when_a_part_of_the_save_fails(self):
        form = self.form(dict(FULL, publish='on'))
        self.assertTrue(form.is_valid(), form.errors)
        with mock.patch('cirs.models.PublishableIncidentTranslation.save',
                        side_effect=IntegrityError('x')):
            with self.assertRaises(IntegrityError):
                form.save()
        self.assertIsNone(self.stored())

    def test_two_requests_that_create_the_case_at_once_end_in_one_case(self):
        # the second finds the case that the first made while it was working
        form = self.form(dict(FULL, publish='on'))
        self.assertTrue(form.is_valid(), form.errors)
        make_case(self.incident, {'en': ('First', 'D', 'M')})  # the other request was faster
        form.save()
        self.assertEqual(PublishableIncident.objects.filter(critical_incident=self.incident).count(), 1)
        self.assertEqual(self.texts()['en'][0], 'Title')
        self.assertTrue(self.stored().publish)

    def test_the_rendering_has_a_fieldset_for_every_language_and_a_switch(self):
        html = self.form().render()
        self.assertEqual(html.count('<fieldset'), 2)
        self.assertEqual(html.count('<legend>'), 2)
        self.assertIn('type="checkbox" name="publish"', html)
        self.assertIn('<textarea', html)
