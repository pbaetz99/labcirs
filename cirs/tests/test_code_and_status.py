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

from django.test import TestCase
from django.urls import reverse
from django.utils import translation
from model_bakery import baker
from parameterized import parameterized

from cirs.forms import normalize_code
from cirs.models import (COMMENT_CODE_CHARS, COMMENT_CODE_LENGTH,
                         REPORTER_STATUS_LABELS, STATUS_CHOICES, group_code)

DE = {'HTTP_ACCEPT_LANGUAGE': 'de'}
EN = {'HTTP_ACCEPT_LANGUAGE': 'en'}


class CommentCodeTest(TestCase):

    def setUp(self):
        self.dept = baker.make_recipe('cirs.department')
        self.search_url = reverse('incident_search', kwargs={'dept': self.dept.label})

    def test_new_code_uses_16_chars_from_alphabet(self):
        ci = baker.make_recipe('cirs.public_ci')
        self.assertEqual(COMMENT_CODE_LENGTH, 16)
        self.assertEqual(len(ci.comment_code), 16)
        self.assertLessEqual(set(ci.comment_code), set(COMMENT_CODE_CHARS))

    def test_existing_code_is_kept_on_save(self):
        ci = baker.make_recipe('cirs.public_ci', comment_code='ab#d$f-9')
        ci.save()
        ci.refresh_from_db()
        self.assertEqual(ci.comment_code, 'ab#d$f-9')

    @parameterized.expand([
        ('16 characters', 'abcdefghjkmnpqrs', 'abcd efgh jkmn pqrs'),
        ('8 characters', 'abcdefgh', 'abcd efgh'),
        ('old code with special characters', 'ab#d$f-9', 'ab#d $f-9'),
        ('old code, all special', '#%&*+-=@', '#%&* +-=@'),
        ('old code with underscore', '_a=b-c@d', '_a=b -c@d'),
    ])
    def test_group_code(self, name, code, grouped):
        self.assertEqual(group_code(code), grouped)
        self.assertEqual(normalize_code(grouped), code)  # nothing but spaces was added

    def test_normalize_code(self):
        self.assertEqual(normalize_code(' AB#D $F-9 \n'), 'ab#d$f-9')

    def test_old_code_found_with_spaces_and_uppercase(self):
        ci = baker.make_recipe('cirs.public_ci', department=self.dept, comment_code='ab#d$f-9')
        self.client.post(self.search_url, {'incident_code': ' AB#D $F-9 \n'})
        self.assertEqual(self.client.session['accessible_incident'], ci.pk)

    def test_new_code_found_with_spaces_in_groups(self):
        ci = baker.make_recipe('cirs.public_ci', department=self.dept)
        code = ci.comment_code
        grouped = ' '.join(code[i:i + 4] for i in range(0, 16, 4)).upper()
        self.client.post(self.search_url, {'incident_code': grouped})
        self.assertEqual(self.client.session['accessible_incident'], ci.pk)

    @parameterized.expand([('16 characters', 'nosuchcodeatall2'), ('8 characters', 'nosuchco')])
    def test_unknown_code_is_rejected(self, name, code):
        response = self.client.post(self.search_url, {'incident_code': code})
        self.assertContains(response, 'No matching critical incident found!')
        self.assertNotContains(response, 'You entered')
        self.assertNotIn('accessible_incident', self.client.session)

    @parameterized.expand([('16 characters', 'nosuchcodeatall2'), ('8 characters', 'nosuchco')])
    def test_unknown_code_of_a_right_length_keeps_its_message_german(self, name, code):
        response = self.client.post(self.search_url, {'incident_code': code}, **DE)
        self.assertContains(response, 'Zu diesem Code wurde keine Meldung gefunden.')
        self.assertNotContains(response, 'Sie haben')

    @parameterized.expand([('8', 8), ('11', 11), ('15', 15)])
    def test_part_of_a_real_code_never_opens_the_report(self, name, length):
        # exact match only: a truncated code must not lead to anyone's report
        ci = baker.make_recipe('cirs.public_ci', department=self.dept)
        response = self.client.post(self.search_url,
                                    {'incident_code': ci.comment_code[:length]}, **DE)
        self.assertNotIn('accessible_incident', self.client.session)
        self.assertNotContains(response, ci.incident)
        if length == 8:
            self.assertContains(response, 'Zu diesem Code wurde keine Meldung gefunden.')
        else:
            self.assertContains(response, 'Sie haben %d Zeichen eingegeben' % length)

    def test_grouped_code_is_found(self):
        # what the success page shows, typed back as it stands
        ci = baker.make_recipe('cirs.public_ci', department=self.dept)
        grouped = group_code(ci.comment_code)
        self.assertEqual(grouped.count(' '), 3)
        self.client.post(self.search_url, {'incident_code': grouped})
        self.assertEqual(self.client.session['accessible_incident'], ci.pk)

    def test_grouped_old_code_with_special_characters_is_found(self):
        ci = baker.make_recipe('cirs.public_ci', department=self.dept, comment_code='ab#d$f-9')
        self.client.post(self.search_url, {'incident_code': group_code('ab#d$f-9')})
        self.assertEqual(self.client.session['accessible_incident'], ci.pk)

    def test_wrong_length_says_how_many_characters_were_entered_german(self):
        response = self.client.post(self.search_url, {'incident_code': 'abcd efgh jkm'}, **DE)
        self.assertContains(response, 'Sie haben 11 Zeichen eingegeben. Ein Code hat 16 Zeichen, '
                                      'ältere Codes 8. Vielleicht fehlt ein Teil.')
        self.assertNotContains(response, 'keine Meldung gefunden')
        self.assertNotIn('accessible_incident', self.client.session)

    @parameterized.expand([(1, 'You entered 1 character. A code has 16 characters, older codes 8. '
                               'Part of it may be missing.'),
                           (11, 'You entered 11 characters. A code has 16 characters, older codes 8. '
                                'Part of it may be missing.')])
    def test_wrong_length_message_in_english(self, count, message):
        response = self.client.post(self.search_url, {'incident_code': 'a' * count}, **EN)
        self.assertContains(response, message)

    def test_wrong_length_message_for_one_character_german(self):
        response = self.client.post(self.search_url, {'incident_code': 'a'}, **DE)
        self.assertContains(response, 'Sie haben 1 Zeichen eingegeben.')

    def test_wrong_length_counts_without_spaces(self):
        response = self.client.post(self.search_url, {'incident_code': ' ab cd\n ef '}, **EN)
        self.assertContains(response, 'You entered 6 characters.')

    def test_existing_code_of_another_length_is_still_found(self):
        # the length message is for codes that match nothing, never for one that exists
        ci = baker.make_recipe('cirs.public_ci', department=self.dept, comment_code='abc123')
        self.client.post(self.search_url, {'incident_code': 'ABC 123'})
        self.assertEqual(self.client.session['accessible_incident'], ci.pk)

    def test_search_field_has_autocomplete_off(self):
        response = self.client.get(self.search_url)
        self.assertContains(response, 'autocomplete="off"')


class ReporterStatusTest(TestCase):

    def test_labels_cover_all_statuses(self):
        self.assertEqual(set(REPORTER_STATUS_LABELS), {value for value, _ in STATUS_CHOICES})

    @parameterized.expand([('new', 'Received'), ('in process', 'In progress'),
                           ('under supervision', 'Measures in place, under observation'),
                           ('completed', 'Closed')])
    def test_reporter_status_label(self, status, label):
        ci = baker.make_recipe('cirs.public_ci', status=status)
        # A request in an earlier test may have left another language active.
        with translation.override('en'):
            self.assertEqual(ci.get_reporter_status_display(), label)

    @parameterized.expand([('new', 'Eingegangen'), ('in process', 'In Bearbeitung'),
                           ('under supervision', 'Maßnahmen umgesetzt, in Beobachtung'),
                           ('completed', 'Abgeschlossen')])
    def test_reporter_status_label_german(self, status, label):
        ci = baker.make_recipe('cirs.public_ci', status=status)
        with translation.override('de'):
            self.assertEqual(ci.get_reporter_status_display(), label)

    def grant_access(self, ci):
        session = self.client.session
        session['accessible_incident'] = ci.pk
        session.save()

    def test_detail_shows_reporter_status(self):
        ci = baker.make_recipe('cirs.public_ci')
        self.grant_access(ci)
        response = self.client.get(ci.get_absolute_url(), HTTP_ACCEPT_LANGUAGE='en')
        self.assertContains(response, 'Received')

    def test_detail_shows_reporter_status_german(self):
        ci = baker.make_recipe('cirs.public_ci', status='in process')
        self.grant_access(ci)
        response = self.client.get(ci.get_absolute_url(), HTTP_ACCEPT_LANGUAGE='de')
        # the status is a badge
        self.assertContains(
            response, 'Stand: <span class="ui-badge ui-badge--info">In Bearbeitung</span>')
