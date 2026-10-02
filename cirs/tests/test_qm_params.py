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

"""The parameters of the work list and the one place that builds and reads its addresses."""

from datetime import date, datetime

from django.http import QueryDict
from django.test import SimpleTestCase
from django.urls import reverse

from cirs.models import CATEGORY_CHOICES, RISK_CHOICES, STATUS_CHOICES
from cirs.qm.params import FILTERS, NO_PLACE, PARAMS, SORTS, parse_params, worklist_url


class NoPlaceTest(SimpleTestCase):
    """The incidents without an organisational unit have a value of wo of their own."""

    def test_the_address_builder_writes_it_and_the_reader_gives_it_back(self):
        url = worklist_url(wo=NO_PLACE)
        self.assertEqual(url, reverse('qm_incidents') + '?wo=' + NO_PLACE)
        self.assertEqual(parse_params(QueryDict(url.split('?', 1)[1])), ({'wo': NO_PLACE}, []))

    def test_nothing_else_than_that_word_is_a_place_besides_an_id(self):
        for text in ('Keine', 'KEINE', 'none', 'keine,3', '0'):
            self.assertEqual(parse_params(QueryDict('wo=' + text)), ({}, ['wo']), text)
            with self.assertRaises(ValueError, msg=text):
                worklist_url(wo=text)


class WorklistUrlTest(SimpleTestCase):

    def test_without_parameters_it_is_the_plain_list(self):
        self.assertEqual(worklist_url(), reverse('qm_incidents'))
        self.assertEqual(worklist_url(stand=None, wartet=False, q=''), reverse('qm_incidents'))

    def test_every_parameter_is_written_by_its_own_name(self):
        url = worklist_url(stand='completed', wo=7, kategorie='infrastructure', risiko='low',
                           von=date(2026, 1, 31), bis='2026-02-01', wartet=True,
                           ohne_bearbeitung=True, q='Etikett', sort='-gemeldet', page=2)
        self.assertEqual(url, reverse('qm_incidents') + '?stand=completed&wo=7'
                         '&kategorie=infrastructure&risiko=low&von=2026-01-31&bis=2026-02-01'
                         '&wartet=1&ohne_bearbeitung=1&q=Etikett&sort=-gemeldet&page=2')

    def test_the_order_does_not_depend_on_the_call(self):
        self.assertEqual(worklist_url(wartet=True, stand='new'), worklist_url(stand='new', wartet=True))

    def test_values_with_a_space_are_encoded(self):
        self.assertEqual(worklist_url(stand='in process'), reverse('qm_incidents') + '?stand=in+process')
        self.assertEqual(worklist_url(stand='under supervision'),
                         reverse('qm_incidents') + '?stand=under+supervision')
        self.assertEqual(worklist_url(q='a&b=c d'), reverse('qm_incidents') + '?q=a%26b%3Dc+d')

    def test_a_flag_is_one_or_absent(self):
        self.assertEqual(worklist_url(wartet=True), reverse('qm_incidents') + '?wartet=1')
        self.assertEqual(worklist_url(wartet=1), reverse('qm_incidents') + '?wartet=1')
        self.assertEqual(worklist_url(wartet=None, ohne_bearbeitung=False), reverse('qm_incidents'))

    def test_a_search_term_is_trimmed(self):
        self.assertEqual(worklist_url(q='  Etikett '), reverse('qm_incidents') + '?q=Etikett')
        self.assertEqual(worklist_url(q='   '), reverse('qm_incidents'))

    def test_an_unknown_parameter_or_a_value_out_of_range_is_a_programming_error(self):
        for params in ({'colour': 'red'}, {'stand': 'done'}, {'stand': 'In Process'},
                       {'wo': 0}, {'wo': -3}, {'wo': 'x'}, {'wo': 2**31}, {'page': 10**30},
                       {'kategorie': 'nonsense'},
                       {'risiko': 'extreme'}, {'von': '2026-13-01'}, {'von': '20260101'},
                       {'von': datetime(2026, 1, 1, 12, 0)}, {'wartet': 2}, {'wartet': 'yes'},
                       {'sort': 'size'}, {'sort': '--nr'}, {'sort': '+nr'}, {'page': 0},
                       {'q': 'x' * 201}):
            with self.assertRaises(ValueError, msg=params):
                worklist_url(**params)

    def test_every_status_category_and_risk_of_the_model_is_in_range(self):
        for name, choices in (('stand', STATUS_CHOICES), ('kategorie', CATEGORY_CHOICES),
                              ('risiko', RISK_CHOICES)):
            for key, _ in choices:
                worklist_url(**{name: key})

    def test_every_sort_goes_in_both_directions(self):
        self.assertEqual(SORTS, ('nr', 'gemeldet', 'aktivitaet'))
        for sort in SORTS:
            worklist_url(sort=sort)
            worklist_url(sort='-' + sort)


class ParseParamsTest(SimpleTestCase):

    def parse(self, query):
        return parse_params(QueryDict(query))

    def test_the_names_are_the_filters_then_sort_and_page(self):
        self.assertEqual(FILTERS, ('stand', 'wo', 'kategorie', 'risiko', 'von', 'bis', 'wartet',
                                   'ohne_bearbeitung', 'q'))
        self.assertEqual(PARAMS, FILTERS + ('sort', 'page'))

    def test_valid_values_come_back_typed(self):
        values, ignored = self.parse('stand=in+process&wo=7&von=2026-01-31&wartet=1&q=+Etikett+'
                                     '&sort=-aktivitaet&page=3')
        self.assertEqual(values, {'stand': 'in process', 'wo': 7, 'von': date(2026, 1, 31),
                                  'wartet': True, 'q': 'Etikett', 'sort': '-aktivitaet', 'page': 3})
        self.assertEqual(ignored, [])

    def test_invalid_values_are_left_out_and_named(self):
        values, ignored = self.parse('stand=done&wo=0&von=31.01.2026&bis=2026-02-30&wartet=2'
                                     '&kategorie=x&risiko=y&sort=size&page=abc&q=' + 'x' * 201)
        self.assertEqual(values, {})
        self.assertEqual(ignored, ['stand', 'wo', 'kategorie', 'risiko', 'von', 'bis', 'wartet',
                                   'q', 'sort', 'page'])

    def test_an_empty_value_is_no_filter_and_not_an_error(self):
        self.assertEqual(self.parse('stand=&von=&q=+&wartet='), ({}, []))

    def test_unknown_parameters_are_no_business_of_the_list(self):
        self.assertEqual(self.parse('utm_source=x&stand=new'), ({'stand': 'new'}, []))

    def test_the_last_value_of_a_repeated_parameter_counts(self):
        self.assertEqual(self.parse('stand=new&stand=completed'), ({'stand': 'completed'}, []))

    def test_what_the_address_builder_writes_is_read_back(self):
        params = {'stand': 'under supervision', 'wo': 12, 'kategorie': 'other', 'risiko': 'high',
                  'von': date(2026, 1, 1), 'bis': date(2026, 12, 31), 'wartet': True,
                  'ohne_bearbeitung': True, 'q': 'Ä & Ö = ?', 'sort': '-nr', 'page': 4}
        query = worklist_url(**params).split('?', 1)[1]
        self.assertEqual(self.parse(query), (params, []))
