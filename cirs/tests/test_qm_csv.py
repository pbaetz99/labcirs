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

"""The CSV file of the evaluations: the numbers of the page without the small ones, in the long
form that a spreadsheet can filter. Test data is constructed, never taken from a real report; see
export_data for the numbers."""

import codecs
from datetime import date

from django.db import connection
from django.test import SimpleTestCase
from django.test.utils import CaptureQueriesContext, override_settings
from django.urls import reverse
from model_bakery import baker
from parameterized import parameterized

from cirs.models import CriticalIncident, OrgUnit, ReporterContact
from cirs.qm.metrics import SECONDARY_MARK
from cirs.qm.report import Period
from cirs.qm.report_csv import filename, neutralise

from .export_data import PERIOD, ExportCase, csv_rows, second_department
from .helpers import make_incident
from .test_qm_overview import at
from .test_qm_reports import DE, EN, publish, summary

LESS = '< 3'
HEAD = {'ORGANIZATION': 'Klinik Test', 'SITE_NAME': 'Test-CIRS'}


class FileNameAndCellTest(SimpleTestCase):

    def test_the_name_has_the_period_and_nothing_else(self):
        self.assertEqual(filename(Period(date(2026, 1, 1), date(2026, 10, 31))),
                         'auswertung-2026-01-bis-2026-10.csv')
        self.assertEqual(filename(Period(date(2026, 3, 1), date(2026, 3, 31))),
                         'auswertung-2026-03.csv')
        self.assertTrue(filename(Period(date(2025, 12, 1), date(2026, 2, 28))).isascii())

    @parameterized.expand([('=1+1',), ('+1',), ('-1',), ('@SUM(A1)',), ('\tx',), ('\rx',),
                           ("=HYPERLINK(\"http://example.org\")",)])
    def test_a_cell_that_a_spreadsheet_would_read_as_a_formula_is_made_text(self, cell):
        self.assertEqual(neutralise(cell), "'" + cell)

    @parameterized.expand([('Labor',), ('< 3',), ('3,5',), ('12',), ('*',), ('–',), (' =x',),
                           ('nicht erfasst',), ('Jan 2026',)])
    def test_every_other_cell_stays_as_it_is(self, cell):
        self.assertEqual(neutralise(cell), cell)

    def test_a_number_is_made_text_too(self):
        self.assertEqual(neutralise(12), '12')


class CsvContentTest(ExportCase):

    @override_settings(**HEAD)
    def test_the_head_names_the_organisation_the_system_the_period_the_department_and_the_day(self):
        rows = self.rows()
        self.assertEqual(rows[0], ['Bereich', 'Merkmal', 'Wert'])
        self.assertEqual(rows[1:6], [
            ['Bericht', 'Organisation', 'Klinik Test'], ['Bericht', 'System', 'Test-CIRS'],
            ['Bericht', 'Zeitraum', 'Januar 2026 bis Oktober 2026'],
            ['Bericht', 'Abteilung', 'Station Eins'], ['Bericht', 'Erstellt am', '2026-10-02']])

    def test_every_department_and_the_area_have_a_row_of_their_own(self):
        second_department(self.reviewer)
        rows = self.rows({**PERIOD, 'bereich': self.units.labor.pk})
        self.assertEqual([row[2] for row in rows if row[:2] == ['Bericht', 'Abteilung']],
                         ['Station Eins', 'Station Zwei'])
        self.assertIn(['Bericht', 'Bereich', 'Labor'], rows)

    def test_the_key_figures_hide_the_small_number_and_give_the_times_in_two_rows_each(self):
        figures = [row for row in self.rows() if row[0] == 'Kennzahlen']
        self.assertEqual(figures, [
            ['Kennzahlen', 'Eingang', '15'], ['Kennzahlen', 'Abgeschlossen', '8'],
            ['Kennzahlen', 'Offen (Stand heute)', '7'], ['Kennzahlen', 'Veröffentlicht', LESS],
            ['Kennzahlen', 'Noch im Stand „neu“', '3'],
            ['Kennzahlen', 'Reaktionszeit, Median in Tagen', '2'],
            ['Kennzahlen', 'Reaktionszeit, Anzahl', '12'],
            ['Kennzahlen', 'Bearbeitungsdauer, Median in Tagen', '12,5'],
            ['Kennzahlen', 'Bearbeitungsdauer, Anzahl', '8']])

    def test_a_median_has_the_number_format_of_the_language(self):
        english = [row for row in self.rows(**EN) if 'median' in row[1]]
        self.assertEqual([row[2] for row in english], ['2', '12.5'])
        self.assertEqual(self.rows(**EN)[0], ['Section', 'Characteristic', 'Value'])

    def test_the_months_are_texts_that_no_spreadsheet_turns_into_a_date(self):
        months = [row for row in self.rows() if row[0] == 'Monatsverlauf']
        self.assertEqual(len(months), 20)
        self.assertEqual(months[:4], [
            ['Monatsverlauf', 'Eingang, Januar 2026', '4'],
            ['Monatsverlauf', 'Abgeschlossen, Januar 2026', '4'],
            ['Monatsverlauf', 'Eingang, Februar 2026', LESS],
            ['Monatsverlauf', 'Abgeschlossen, Februar 2026', '0']])
        for row in months:
            self.assertRegex(row[1], r'^(Eingang|Abgeschlossen), [A-Za-zäöü]+ 2026$')
        self.assertEqual({row[1]: row[2] for row in self.rows(**EN)}['Incoming, January 2026'], '4')

    def test_the_distributions_hide_the_small_cells_and_keep_the_zeros(self):
        rows = self.rows()
        self.assertEqual([row[1:] for row in rows if row[0] == 'Verteilung: Bereich'], [
            ['Diagnostik', LESS], ['Labor', '6'], ['Pflege', '6'], ['Verwaltung', LESS]])
        self.assertEqual([row[1:] for row in rows if row[0] == 'Verteilung: Gefährdung'], [
            ['sehr niedrig', '0'], ['niedrig', '12'], ['moderat', LESS], ['hoch', LESS],
            ['sehr hoch', '0']])
        self.assertEqual(len([row for row in rows if row[0].startswith('Verteilung: ')]), 28)

    def test_a_month_that_is_not_recorded_says_so(self):
        rows = self.rows({'von_monat': 11, 'von_jahr': 2025, 'bis_monat': 1, 'bis_jahr': 2026})
        self.assertIn(['Monatsverlauf', 'Abgeschlossen, November 2025', 'nicht erfasst'], rows)
        self.assertIn(['Monatsverlauf', 'Abgeschlossen, Januar 2026', '4'], rows)
        # the log began on 1 January: the figures count from then on
        self.assertIn(['Kennzahlen', 'Abgeschlossen (ab 01.01.2026)', '4'], rows)
        self.assertIn(['Kennzahlen', 'Reaktionszeit, Anzahl (ab 01.01.2026)', '4'], rows)

    def test_a_period_before_the_log_has_nothing_to_give_for_the_log_figures(self):
        rows = self.rows({'von_monat': 1, 'von_jahr': 2025, 'bis_monat': 12, 'bis_jahr': 2025})
        figures = {row[1]: row[2] for row in rows if row[0] == 'Kennzahlen'}
        for name in ('Abgeschlossen', 'Reaktionszeit', 'Bearbeitungsdauer',
                     'Offen (Stand heute)'):
            self.assertEqual(figures[name], 'nicht erfasst', name)

    def test_the_notes_are_in_the_file_too(self):
        notes = [row for row in self.rows() if row[0] == 'Hinweise']
        self.assertEqual([row[1] for row in notes], ['Kleine Zahlen', 'Zeiten', 'Vergleiche'])
        self.assertIn('Zahlen über 0 und unter 3 stehen als „< 3“', notes[0][2])
        self.assertIn('frei gewählte Zeiträume', notes[2][2])

    def test_a_minimum_other_than_3_is_the_one_that_is_used(self):
        with override_settings(REPORT_MIN_CELL=5):
            rows = self.rows()
        self.assertIn(['Kennzahlen', 'Veröffentlicht', '< 5'], rows)
        self.assertNotIn(LESS, [row[2] for row in rows if row[0] != 'Hinweise'])

    def test_a_withheld_number_is_the_star_and_a_hidden_one_is_less_than(self):
        CriticalIncident.objects.filter(department=self.dept).delete()
        make_incident(self.dept, reported=date(2026, 5, 2), history=[('new', at(5, 2))],
                      risk='low', preventability='avoidable')
        make_incident(self.dept, reported=date(2026, 5, 3), history=[('new', at(5, 3))],
                      risk='high', preventability='avoidable')
        cells = {row[2] for row in self.rows() if row[0] != 'Hinweise'}
        self.assertIn(SECONDARY_MARK, cells)
        self.assertIn(LESS, cells)


class CsvSameInOutputTest(ExportCase):
    """Two data sets that differ only in cells that are hidden give the same file."""

    def test_the_two_variants_differ_on_the_page_and_not_in_the_file(self):
        screen_a, file_a = self.html(PERIOD), self.download().content
        self.build('b')
        screen_b, file_b = self.html(PERIOD), self.download().content
        self.assertNotEqual(screen_a, screen_b)
        self.assertEqual(file_a, file_b)

    def test_the_file_is_the_same_in_english_too(self):
        file_a = self.download(**EN).content
        self.build('b')
        self.assertEqual(file_a, self.download(**EN).content)


class CsvFileTest(ExportCase):

    def test_utf8_with_a_byte_order_mark_and_semicolons(self):
        response = self.download()
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        self.assertTrue(response.content.startswith(codecs.BOM_UTF8))
        self.assertEqual(response.content[3:].decode('utf-8').splitlines()[0], 'Bereich;Merkmal;Wert')
        self.assertEqual({len(row) for row in csv_rows(response.content)}, {3})

    def test_a_download_in_an_ascii_name_with_the_period_only(self):
        for params in (PERIOD, {**PERIOD, 'bereich': self.units.labor.pk}):
            disposition = self.download(params)['Content-Disposition']
            self.assertEqual(disposition, 'attachment; filename="auswertung-2026-01-bis-2026-10.csv"')
            self.assertTrue(disposition.isascii())
        one = self.download({'von_monat': 3, 'von_jahr': 2026, 'bis_monat': 3, 'bis_jahr': 2026})
        self.assertEqual(one['Content-Disposition'], 'attachment; filename="auswertung-2026-03.csv"')

    def test_nothing_of_the_file_is_kept(self):
        response = self.download()
        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn('private', response['Cache-Control'])

    def test_a_cell_that_starts_like_a_formula_is_neutralised(self):
        for name in ('=1+1', '+SUM(A1)', '-2', '@x', '\tx'):
            unit = OrgUnit.objects.create(name=name)
            make_incident(self.dept, reported=date(2026, 5, 4), history=[('new', at(5, 4))],
                          org_unit=unit, risk='low', preventability='avoidable')
        cells = [row[1] for row in self.rows() if row[0] == 'Verteilung: Bereich']
        for name in ('=1+1', '+SUM(A1)', '-2', '@x', '\tx'):
            self.assertIn("'" + name, cells)
            self.assertNotIn(name, cells)
        for row in self.rows():
            for cell in row:
                self.assertFalse(cell.startswith(('=', '+', '-', '@', '\t', '\r')), cell)

    def test_an_area_that_is_named_like_a_formula_is_neutralised_in_the_head_too(self):
        unit = OrgUnit.objects.create(name='=cmd')
        make_incident(self.dept, reported=date(2026, 5, 4), history=[('new', at(5, 4))],
                      org_unit=unit, risk='low', preventability='avoidable')
        rows = self.rows({**PERIOD, 'bereich': unit.pk})
        self.assertIn(['Bericht', 'Bereich', "'=cmd"], rows)

    def test_no_text_of_a_report_and_no_measure_is_in_the_file(self):
        ReporterContact.objects.create(incident=self.incidents[0], email='melder@example.org')
        publish(self.incidents[0], 'Titel A', 'Maßnahme A')
        publish(self.incidents[5], 'Titel B', 'Maßnahme B')
        text = self.download().content.decode('utf-8-sig')
        for incident in self.incidents:
            self.assertNotIn(incident.incident, text)
            self.assertNotIn(incident.comment_code, text)
        for absent in ('Titel', 'Maßnahme', 'melder@example.org', self.reviewer.user.username,
                       '/incidents/'):
            self.assertNotIn(absent, text)

    def test_nothing_of_the_foreign_department(self):
        for params in ({}, PERIOD, {**PERIOD, 'bereich': self.units.labor.pk}):
            self.assertNoCanary(self.download(params).content, params)
        self.assertEqual({row[1]: row[2] for row in self.rows()}['Eingang'], '15')

    def test_without_parameters_it_is_this_year_up_to_the_current_month(self):
        self.assertEqual(self.download({}).content, self.download(PERIOD).content)

    @parameterized.expand([
        ('swapped', {'von_monat': 5, 'von_jahr': 2026, 'bis_monat': 4, 'bis_jahr': 2026}),
        ('37 months', {'von_monat': 1, 'von_jahr': 2023, 'bis_monat': 1, 'bis_jahr': 2026}),
        ('month text', {**PERIOD, 'von_monat': 'abc'}),
        ('year too late', {**PERIOD, 'bis_jahr': 2100}),
        ('area text', {**PERIOD, 'bereich': 'abc'}),
        ('only one parameter', {'von_monat': 3}),
    ])
    def test_a_request_that_is_not_in_order_is_led_to_the_page_and_gets_no_file(self, _name, params):
        response = self.export('csv', params)
        self.assertEqual(response.status_code, 302)
        self.assertNotIn('Content-Disposition', response)
        self.assertEqual(response.content, b'')
        self.assertTrue(response.url.startswith('/qm/auswertungen/?'), response.url)
        self.assertIsNotNone(summary(self.client.get(response.url, **DE).content.decode()))

    def test_a_reviewer_without_a_department_is_led_to_the_page_that_says_why(self):
        self.client.force_login(baker.make_recipe('cirs.reviewer').user)
        response = self.export('csv')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse('qm_reports')), response.url)

    def test_head_and_the_other_methods(self):
        self.assertEqual(self.client.post(reverse('qm_reports_csv'), PERIOD).status_code, 405)
        self.assertEqual(self.client.head(reverse('qm_reports_csv')).status_code, 200)


class CsvQueriesTest(ExportCase):

    def queries(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.export('csv')
        self.assertEqual(response.status_code, 200)
        return len(queries)

    def test_the_same_number_of_queries_for_a_few_and_for_many_incidents(self):
        self.queries()
        few = self.queries()
        for number in range(40):
            make_incident(self.dept, reported=date(2026, 1, 1 + number % 28), history=[
                ('new', at(1, 1 + number % 28)), ('in process', at(2, 1 + number % 28))],
                risk=('low', 'high', '')[number % 3], preventability='avoidable')
        self.assertEqual(self.queries(), few)
