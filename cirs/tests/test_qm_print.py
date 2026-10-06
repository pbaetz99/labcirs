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

"""The print view of the evaluations: a sheet of paper that says what the page says, but never a
small number, with the head of a report and without the frame of the application. Test data is
constructed, never taken from a real report; see export_data for the numbers."""

import html as html_lib
import re
from datetime import date

from django.db import connection
from django.test.utils import CaptureQueriesContext, override_settings
from django.urls import reverse
from model_bakery import baker
from parameterized import parameterized

from cirs.models import CriticalIncident, OrgUnit, ReporterContact
from cirs.qm.views_reports import report_url

from .export_data import PERIOD, ExportCase, second_department
from .helpers import csp_violations, make_incident
from .test_pages_report import css_text
from .test_qm_overview import at, hrefs, tables, tile, words
from .test_qm_reports import DE, EN, figures, publish, summary

LESS = '< 3'
STAR = '*'
HEAD = {'ORGANIZATION': 'Klinik Test', 'SITE_NAME': 'Test-CIRS'}


class PrintContentTest(ExportCase):

    def test_the_key_figures_hide_the_small_number_and_show_the_others(self):
        self.assertEqual(figures(self.print_html()), {
            'Eingang': '15', 'Abgeschlossen': '8', 'Offen (Stand heute)': '7',
            'Veröffentlicht': LESS, 'Reaktionszeit': 'Median 2 Tage, Anzahl 12',
            'Bearbeitungsdauer': 'Median 12,5 Tage, Anzahl 8', 'Noch ohne Bearbeitung': '3'})

    def test_the_months_hide_the_small_ones(self):
        [chart] = tables(tile(self.print_html(), 'auswertung-verlauf'))
        self.assertEqual(chart[0], ['Monat', 'Eingang', 'Abgeschlossen'])
        self.assertEqual(chart[1:], [
            ['Jan 2026', '4', '4'], ['Feb 2026', LESS, '0'], ['Mär 2026', '4', '4'],
            ['Apr 2026', '0', '0'], ['Mai 2026', '0', '0'], ['Jun 2026', '4', '0'],
            ['Jul 2026', '0', '0'], ['Aug 2026', '0', '0'], ['Sep 2026', LESS, '0'],
            ['Okt 2026', '0', '0']])

    def test_the_distributions_hide_the_small_ones_and_not_the_zeros(self):
        found = tables(tile(self.print_html(), 'auswertung-verteilungen'))
        self.assertEqual(found, [
            [['Bereich', 'Anzahl'], ['Diagnostik', LESS], ['Labor', '6'], ['Pflege', '6'],
             ['Verwaltung', LESS]],
            [['Kategorie', 'Anzahl'], ['Organisation/Kommunikation', '0'],
             ['Technik/Methoden', '6'], ['Wissen/Training', '6'],
             ['Konzentration/Aufmerksamkeit (Versehen/Ausrutscher)', '0'],
             ['Infrastruktur', LESS], ['Sonstige', LESS]],
            [['Vermeidbarkeit', 'Anzahl'], ['Beurteilung nicht möglich', LESS],
             ['Das Ereignis war vermeidbar', '12'], ['Das Ereignis war nicht vermeidbar', LESS]],
            [['Risiko', 'Anzahl'], ['niedrig', '6'], ['mittel', LESS], ['hoch', '6'],
             ['Keine Angabe', LESS]],
            [['Häufigkeit', 'Anzahl'], ['Einzelfall (erstmalig)', '0'],
             ['selten (1 pro Jahr)', '6'], ['gelegentlich (1 pro Monat)', '6'],
             ['häufig (1 pro Woche)', LESS], ['ständig (täglich)', '0'],
             ['Keine Angabe', LESS]],
            [['Gefährdung', 'Anzahl'], ['sehr niedrig', '0'], ['niedrig', '12'],
             ['moderat', LESS], ['hoch', LESS], ['sehr hoch', '0']]])

    def test_the_bars_say_less_than_and_have_no_length_for_a_hidden_number(self):
        area = re.search(r'<h3[^>]*>Bereich</h3>.*?<details', self.print_html(), re.S).group(0)
        bars = re.findall(r'<div class="ui-diagramm__zeilenname">(.*?)</div>\s*<svg[^>]*>(.*?)</svg>'
                          r'\s*<div class="ui-diagramm__zahl">(.*?)</div>', area, re.S)
        self.assertEqual([(words(name), '<rect' in svg, words(number)) for name, svg, number in bars],
                         [('Diagnostik', False, LESS), ('Labor', True, '6'), ('Pflege', True, '6'),
                          ('Verwaltung', False, LESS)])

    def test_the_key_message_of_a_chart_says_only_what_is_shown(self):
        html = self.print_html()
        self.assertIn('<span class="ui-visually-hidden" id="chart-org-unit-group-desc">'
                      'Am meisten: Labor mit 6 von 15.</span>', html)
        self.assertIn('Meldungen im Zeitraum Januar 2026 bis Oktober 2026: 15 eingegangen, '
                      '8 abgeschlossen.', words(html))

    def test_the_small_numbers_are_nowhere_in_the_markup(self):
        # the cells of 1 and 2 are the only ones with these values: no length, no text, no title
        html = self.print_html()
        for name in ('Diagnostik', 'Verwaltung'):
            bar = re.search(r'%s</div>\s*<svg[^>]*>(.*?)</svg>' % name, html, re.S).group(1)
            self.assertNotIn('<rect', bar)
        self.assertNotRegex(html, r'(?:mit|with) [12]\b')

    def test_a_published_number_below_the_minimum_is_hidden_and_so_is_the_list(self):
        html = self.print_html()
        massnahmen = tile(html, 'auswertung-massnahmen')
        self.assertIn('Weniger als 3 veröffentlichte Fälle in diesem Zeitraum; sie werden hier '
                      'nicht einzeln aufgeführt.', words(massnahmen))
        self.assertNotIn('<li>', massnahmen)
        for title in ('Titel Eins', 'Maßnahme Eins'):
            self.assertNotIn(title, html)

    def test_with_enough_published_cases_they_are_listed_by_month_and_year_only(self):
        publish(self.incidents[0], 'Titel A', 'Maßnahme A\nzweite Zeile')
        publish(self.incidents[5], 'Titel B', 'Maßnahme B')
        massnahmen = tile(self.print_html(), 'auswertung-massnahmen')
        self.assertEqual(figures(self.print_html())['Veröffentlicht'], '3')
        items = re.findall(r'<li>(.*?)</li>', massnahmen, re.S)
        # the oldest report first: the first incident (January), the one that swings (February)
        # and the sixth of the twelve (March)
        self.assertEqual([words(item) for item in items], [
            'Januar 2026 Titel A (de) Maßnahme A zweite Zeile (de)',
            'Februar 2026 Titel Eins (de) Maßnahme Eins (de)',
            'März 2026 Titel B (de) Maßnahme B (de)'])
        self.assertIn('<time datetime="2026-01">Januar 2026</time>', massnahmen)
        self.assertIn('Maßnahme A<br>zweite Zeile (de)', massnahmen)

    def test_a_list_has_no_number_no_day_and_no_link(self):
        publish(self.incidents[0], 'Titel A', 'Maßnahme A')
        publish(self.incidents[5], 'Titel B', 'Maßnahme B')
        massnahmen = tile(self.print_html(), 'auswertung-massnahmen')
        self.assertNotIn('Nr.', massnahmen)
        self.assertNotIn('<a ', massnahmen)
        self.assertNotRegex(massnahmen, r'\d{1,2}\.\d{1,2}\.\d{4}')
        self.assertNotRegex(massnahmen, r'20\d\d-\d\d-\d\d')
        for incident in self.incidents:
            self.assertNotIn(incident.get_absolute_url(), massnahmen)

    def test_the_cases_that_are_listed_are_those_of_the_period_and_of_the_active_language(self):
        publish(self.incidents[0], 'Titel A', 'Maßnahme A')
        publish(self.incidents[5], 'Titel B', 'Maßnahme B')
        listed = tile(self.print_html(**EN), 'auswertung-massnahmen')
        self.assertIn('Titel A (en)', listed)
        self.assertIn('January 2026', listed)
        self.assertNotIn('(de)', listed)

    def test_an_area_narrows_the_print_like_the_page(self):
        html = self.print_html({**PERIOD, 'bereich': self.units.labor.pk})
        self.assertEqual(figures(html)['Eingang'], '6')
        self.assertIn('Bereich: Labor', words(html))

    def test_a_period_before_the_log_says_not_recorded_and_a_period_in_part_says_from_when(self):
        before = figures(self.print_html({'von_monat': 1, 'von_jahr': 2025, 'bis_monat': 12,
                                          'bis_jahr': 2025}))
        for label in ('Abgeschlossen', 'Reaktionszeit', 'Bearbeitungsdauer'):
            self.assertEqual(before[label], 'nicht erfasst', label)
        self.assertEqual(before['Offen (Stand heute)'], 'nicht erfasst')
        # the log began on 1 January: a period from the 1st of the month before has it in part
        part = figures(self.print_html({'von_monat': 12, 'von_jahr': 2025, 'bis_monat': 3,
                                        'bis_jahr': 2026}))
        self.assertEqual(part['Abgeschlossen'], '8 (ab 01.01.2026)')
        self.assertTrue(part['Reaktionszeit'].endswith(' (ab 01.01.2026)'), part)

    def test_the_times_of_few_incidents_give_neither_median_nor_count(self):
        april = {'von_monat': 4, 'von_jahr': 2026, 'bis_monat': 4, 'bis_jahr': 2026}
        for number, expected in ((1, 'Median nicht angegeben, Anzahl < 3'),
                                 (2, 'Median nicht angegeben, Anzahl < 3'),
                                 (3, 'Median 2 Tage, Anzahl 3')):
            make_incident(self.dept, reported=date(2026, 4, number), history=[
                ('new', at(4, number)), ('in process', at(4, number + 2))])
            self.assertEqual(figures(self.print_html(april))['Reaktionszeit'], expected, number)

    def test_the_whole_report_is_made_of_small_numbers_if_the_department_is_small(self):
        CriticalIncident.objects.filter(department=self.dept).delete()
        make_incident(self.dept, reported=date(2026, 5, 2), history=[('new', at(5, 2))],
                      risk='low', preventability='avoidable')
        make_incident(self.dept, reported=date(2026, 5, 3), history=[('new', at(5, 3))],
                      risk='high', preventability='avoidable')
        html = self.print_html()
        shown = figures(html)
        # two incoming, one low and one high: the 1 and the 1 would follow from the 2, so the
        # total is withheld, and with it in every place
        self.assertEqual(shown['Eingang'], STAR)
        self.assertEqual(shown['Noch ohne Bearbeitung'], LESS)
        self.assertTrue(shown['Abgeschlossen'].startswith('0'))
        for table in tables(tile(html, 'auswertung-verteilungen')):
            for row in table[1:]:
                self.assertIn(row[1], ('0', LESS, STAR), row)
        self.assertNotIn('Am meisten', html)


class PrintDefinitionsTest(ExportCase):

    def test_the_terms_are_those_of_the_page_and_the_notes_come_after_them(self):
        html = self.print_html()
        begriffe = tile(html, 'auswertung-begriffe')
        self.assertEqual([words(term) for term in re.findall(r'<dt>(.*?)</dt>', begriffe, re.S)], [
            'Eingang', 'Abgeschlossen', 'Offen (Stand heute)', 'Veröffentlicht', 'Reaktionszeit',
            'Bearbeitungsdauer', 'Noch ohne Bearbeitung', 'Vermeidbarkeit', 'Risiko',
            'Häufigkeit', 'Gefährdung', 'Keine Angabe'])
        self.assertEqual([words(h) for h in re.findall(r'<h2[^>]*>(.*?)</h2>', html, re.S)], [
            'Kennzahlen', 'Monatsverlauf', 'Verteilungen', 'Maßnahmen', 'Begriffe', 'Hinweise'])

    def test_the_notes_name_the_rule_the_times_the_log_and_the_comparison(self):
        notes = tile(self.print_html(), 'auswertung-hinweise')
        items = [words(item) for item in re.findall(r'<li>(.*?)</li>', notes, re.S)]
        self.assertEqual(len(items), 3)  # the log covers the period: no note on it
        small, times, comparison = items
        self.assertIn('Zahlen über 0 und unter 3 stehen als „< 3“', small)
        self.assertIn('Stern', small)
        self.assertIn('weniger als 3 Meldungen', times)
        self.assertIn('weder der Median noch die Anzahl', times)
        # a period of any months, or an area against "All", lets a small number be worked out
        self.assertIn('frei gewählte Zeiträume', comparison)
        self.assertIn('für einen Bereich und für „Alle“', comparison)
        self.assertIn('nicht gemeinsam weitergegeben', comparison)

    def test_the_note_on_the_log_comes_where_the_log_does_not_cover_the_period(self):
        notes = tile(self.print_html({'von_monat': 12, 'von_jahr': 2025, 'bis_monat': 3,
                                      'bis_jahr': 2026}), 'auswertung-hinweise')
        self.assertIn('Abschlüsse und Zeiten werden seit 01.01.2026 erfasst.', words(notes))
        self.assertIn('Reaktionszeiten gibt es nur für Meldungen seit dem Beginn des Protokolls.',
                      words(notes))
        self.assertEqual(len(re.findall(r'<li>', notes)), 4)

    def test_a_minimum_other_than_3_is_the_one_that_is_named(self):
        with override_settings(REPORT_MIN_CELL=5):
            html = self.print_html()
        self.assertIn('Zahlen über 0 und unter 5 stehen als „< 5“', words(tile(
            html, 'auswertung-hinweise')))
        self.assertEqual(figures(html)['Veröffentlicht'], '< 5')
        self.assertNotIn(LESS, figures(html).values())

    def test_the_categories_say_that_a_report_can_have_several(self):
        note = 'Mehrfachnennung möglich: Eine Meldung zählt in jeder ihrer Kategorien.'
        self.assertEqual(words(self.print_html()).count(note), 1)


class PrintFrameTest(ExportCase):

    @override_settings(**HEAD)
    def test_title_and_one_heading(self):
        html = self.print_html()
        self.assertRegex(html, r'<title>Auswertung · Januar 2026 bis Oktober 2026 · Test-CIRS</title>')
        self.assertEqual(re.findall(r'<h1[^>]*>(.*?)</h1>', html, re.S), ['Auswertung der Meldungen'])
        self.assertEqual(html.count('<main'), 1)

    @override_settings(**HEAD)
    def test_the_head_names_the_organisation_the_system_the_period_the_departments_and_the_day(self):
        html = self.print_html()
        head = re.search(r'<header.*?</header>', html, re.S).group(0)
        text = words(head)
        for part in ('Klinik Test', 'Test-CIRS', 'Auswertung der Meldungen',
                     'Zeitraum: Januar 2026 bis Oktober 2026', 'Abteilung: Station Eins',
                     'Erstellt am 02.10.2026'):
            self.assertIn(part, text)
        self.assertNotIn('Bereich:', text)

    def test_the_name_is_said_once_where_the_organisation_and_the_system_are_called_the_same(self):
        with override_settings(ORGANIZATION='LabCIRS', SITE_NAME='LabCIRS'):
            head = words(re.search(r'<header.*?</header>', self.print_html(), re.S).group(0))
        self.assertEqual(head.count('LabCIRS'), 1)

    def test_the_head_names_every_department_of_the_reviewer(self):
        second = second_department(self.reviewer)
        make_incident(second, reported=date(2026, 5, 1), history=[('new', at(5, 1))])
        html = self.print_html()
        self.assertIn('Abteilungen: Station Eins, Station Zwei', words(html))
        self.assertEqual(figures(html)['Eingang'], '16')

    def test_the_head_names_the_period_in_months_and_the_area(self):
        html = self.print_html({'von_monat': 3, 'von_jahr': 2026, 'bis_monat': 3, 'bis_jahr': 2026,
                                'bereich': self.units.pflege.pk})
        head = words(re.search(r'<header.*?</header>', html, re.S).group(0))
        self.assertIn('Zeitraum: März 2026', head)
        self.assertIn('Bereich: Pflege', head)
        self.assertNotRegex(head, r'\d{2}\.\d{2}\.\d{4}\D*\d{2}\.\d{2}\.\d{4}')  # no days

    def test_the_name_of_the_person_is_nowhere(self):
        user = self.reviewer.user
        user.first_name, user.last_name, user.email = 'Maria', 'Mustermann', 'qm@example.org'
        user.save()
        html = self.print_html()
        for text in (user.username, 'Maria', 'Mustermann', 'qm@example.org'):
            self.assertNotIn(text, html)

    def test_no_navigation_no_top_bar_and_no_footer_of_the_application(self):
        html = self.print_html()
        for absent in ('ui-topbar', '<nav', 'ui-nav', 'ui-fuss', 'ui-logo', 'Abmelden',
                       'ui-skip'):
            self.assertNotIn(absent, html)
        self.assertNotIn('<form', html)
        self.assertNotIn('csrf', html)

    def test_a_page_of_its_own_in_the_language_of_the_reader(self):
        html = self.print_html()
        self.assertRegex(html, r'<html lang="de" data-theme="hell">')
        self.assertIn('<meta charset="utf-8">', html)
        self.assertIn('width=device-width', html)
        self.assertEqual(re.findall(r'<link rel="stylesheet" href="([^"]+)"', html)[:2],
                         ['/static/css/core.css', '/static/css/labcirs.css'])
        self.assertEqual(csp_violations(html), [])
        self.assertRegex(self.print_html(**EN), r'<html lang="en"')

    def test_a_button_for_the_script_and_a_hint_for_those_without(self):
        html = self.print_html()
        self.assertIn('<button type="button" class="ui-btn ui-btn--primary" data-drucken hidden>'
                      'Drucken / als PDF speichern</button>', html)
        hint = re.search(r'<p[^>]*data-drucken-hinweis[^>]*>(.*?)</p>', html, re.S).group(1)
        self.assertIn('Strg+P', hint)
        self.assertIn('Cmd+P', hint)
        self.assertRegex(html, r'<script src="/static/js/formular.js" defer></script>')
        self.assertEqual(csp_violations(html), [])  # no handler, no inline script

    def test_the_button_is_served_hidden_so_that_it_is_never_dead_without_the_script(self):
        # the markup of the button: hidden, no handler of any kind
        button = re.search(r'<button[^>]*data-drucken[^>]*>', self.print_html()).group(0)
        self.assertIn(' hidden', button)
        self.assertNotRegex(button, r'\son[a-z]+=')

    def test_the_script_shows_the_button_and_prints_and_takes_the_hint_away(self):
        script = open('static/js/formular.js', encoding='utf-8').read()
        self.assertIn('[data-drucken]', script)
        self.assertIn('window.print()', script)
        self.assertIn('[data-drucken-hinweis]', script)
        self.assertIn('.hidden = false', script)

    def test_a_way_back_to_the_evaluations_with_the_same_period_and_area(self):
        params = {**PERIOD, 'bereich': self.units.labor.pk}
        back = [href for href in hrefs(self.print_html(params)) if href.startswith('/qm/auswertungen/?')]
        self.assertEqual(back, [report_url(date(2026, 1, 1), date(2026, 10, 1), self.units.labor.pk)])
        self.assertIn('Zurück zur Auswertung', words(self.print_html(params)))

    def test_the_page_has_no_other_link_than_the_way_back(self):
        links = [html_lib.unescape(href) for href in
                 re.findall(r'<a [^>]*href="([^"]*)"', self.print_html())]
        self.assertEqual(links, [report_url(date(2026, 1, 1), date(2026, 10, 1))])

    def test_the_tables_have_a_caption_and_scoped_headings(self):
        html = self.print_html()
        for table in re.findall(r'<table.*?</table>', html, re.S):
            self.assertRegex(table, r'<caption[^>]*>[^<]+</caption>')
            self.assertNotRegex(table, r'<th(?![a-z])(?![^>]*scope=)')
        self.assertEqual(html.count('<table'), 7)

    def test_the_monthly_table_is_open_for_the_paper_and_the_bars_have_their_numbers_in_sight(self):
        html = self.print_html()
        opened = '<details class="ui-diagramm__tabelle" open>'
        self.assertIn(opened, tile(html, 'auswertung-verlauf'))
        self.assertEqual(tile(html, 'auswertung-verteilungen').count(opened), 0)
        rows = sum(len(table) - 1 for table in tables(tile(html, 'auswertung-verteilungen')))
        self.assertEqual(tile(html, 'auswertung-verteilungen').count('ui-diagramm__zahl'), rows)

    def test_all_ids_are_unique_and_every_reference_resolves_with_seven_charts(self):
        html = self.print_html()
        self.assertEqual(html.count('role="img"'), 7)
        ids = re.findall(r'\sid="([^"]+)"', html)
        self.assertEqual(len(ids), len(set(ids)), sorted(ids))
        for attribute in ('aria-labelledby', 'aria-describedby'):
            for target in re.findall(r'%s="([^"]+)"' % attribute, html):
                for name in target.split():
                    self.assertIn(name, ids, '%s %s' % (attribute, target))

    def test_every_class_has_a_rule(self):
        css = css_text()
        main = re.search(r'<body.*</body>', self.print_html(), re.S).group(0)
        for token in set(' '.join(re.findall(r'\sclass="([^"]*)"', main)).split()):
            self.assertRegex(css, r'\.%s(?![\w-])' % re.escape(token), token)

    def test_the_rules_for_the_paper_are_there(self):
        css = css_text()
        self.assertRegex(css, r'@page\s*\{[^}]*size:\s*A4')
        section = css[css.index('Print view of the evaluations'):]
        self.assertRegex(section, r'@media print\s*\{')
        self.assertNotRegex(section, r'#[0-9a-fA-F]{3,8}\b')
        self.assertNotRegex(section, r'rgba?\(')
        self.assertIn('var(--ui-', section)

    def test_the_texts_are_translatable(self):
        html = self.print_html(**EN)
        for text in ('Key figures', 'Monthly trend', 'Distributions', 'Measures', 'Definitions', 'Notes',
                     'Evaluation of reports', 'Period: January 2026 to October 2026',
                     'Created on 10/02/2026', 'Print / save as PDF', 'Back to the evaluations',
                     'Median 2 days, count 12', 'Median 12.5 days, count 8', 'Still without processing'):
            self.assertIn(text, words(html))
        self.assertIn('Fewer than 3 published cases in this period; they are not listed one by one.',
                      words(html))
        self.assertRegex(html, r'<title>Evaluation · January 2026 to October 2026 · ')


class PrintSameInOutputTest(ExportCase):
    """Two data sets that differ only in cells that are hidden give the same sheet."""

    def test_the_two_variants_differ_on_the_page_and_not_in_the_print(self):
        screen_a = self.html(PERIOD)
        print_a = self.print_html()
        self.build('b')
        screen_b = self.html(PERIOD)
        print_b = self.print_html()
        # the page says every number: the cells that swing are in it
        self.assertNotEqual(figures(screen_a)['Veröffentlicht'], figures(screen_b)['Veröffentlicht'])
        self.assertNotEqual(tables(tile(screen_a, 'auswertung-verteilungen')),
                            tables(tile(screen_b, 'auswertung-verteilungen')))
        # the sheet does not
        self.assertEqual(print_a, print_b)

    def test_the_two_variants_say_the_same_in_every_attribute_and_every_length(self):
        # the bars and columns are drawn from what is shown: no length, no height, no axis mark
        # tells 1 from 2
        self.build('a')
        a = self.print_html()
        self.build('b')
        b = self.print_html()
        for pattern in (r'<rect[^>]*>', r'<path[^>]*>', r'<text[^>]*>[^<]*</text>',
                        r'<line[^>]*>', r'<svg[^>]*>', r'<title[^>]*>[^<]*</title>',
                        r'<desc[^>]*>[^<]*</desc>', r'<span class="ui-visually-hidden"[^>]*>[^<]*</span>',
                        r'aria-[a-z]+="[^"]*"'):
            self.assertEqual(re.findall(pattern, a), re.findall(pattern, b), pattern)

    def test_the_part_that_is_shown_still_follows_the_data(self):
        a = self.print_html()
        make_incident(self.dept, reported=date(2026, 1, 20), history=[('new', at(1, 20))],
                      risk='low', preventability='avoidable', hazard='low', frequency='seldom')
        self.assertNotEqual(a, self.print_html())


class PrintAccessTest(ExportCase):

    def test_nothing_is_kept_in_the_cache_of_the_browser(self):
        response = self.export('print')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn('private', response['Cache-Control'])

    def test_the_content_type_is_html_in_utf8(self):
        self.assertEqual(self.export('print')['Content-Type'], 'text/html; charset=utf-8')

    def test_nothing_of_the_foreign_department_shows(self):
        for params in ({}, PERIOD, {**PERIOD, 'bereich': self.units.labor.pk}):
            html = self.print_html(params)
            self.assertNoCanary(html, params)
        # the foreign incidents are reported today, inside the period
        self.assertEqual(figures(self.print_html())['Eingang'], '15')
        for incident in self.canary_incidents:
            self.assertNotIn(incident.get_absolute_url(), self.print_html())

    def test_neither_the_code_nor_the_address_of_a_reporting_person(self):
        ReporterContact.objects.create(incident=self.incidents[0], email='melder@example.org')
        html = self.print_html()
        self.assertNotIn('melder@example.org', html)
        for incident in self.incidents:
            self.assertNotIn(incident.comment_code, html)

    def test_no_incident_text_is_printed(self):
        for incident in self.incidents:
            self.assertNotIn(incident.incident, self.print_html())

    def test_without_parameters_it_is_this_year_up_to_the_current_month(self):
        self.assertEqual(self.print_html({}), self.print_html(PERIOD))

    def test_parameters_that_are_not_the_business_of_the_page_change_nothing(self):
        self.assertEqual(self.print_html({**PERIOD, 'q': 'x', 'page': 'y'}), self.print_html(PERIOD))

    def test_a_reviewer_without_a_department_is_led_to_the_page_that_says_why(self):
        reviewer = baker.make_recipe('cirs.reviewer')
        self.client.force_login(reviewer.user)
        response = self.export('print')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse('qm_reports')), response.url)
        self.assertIn('Ihr Zugang gehört keiner Abteilung an.',
                      self.client.get(response.url, **DE).content.decode())

    @parameterized.expand([
        ('swapped', {'von_monat': 5, 'von_jahr': 2026, 'bis_monat': 4, 'bis_jahr': 2026}),
        ('37 months', {'von_monat': 1, 'von_jahr': 2023, 'bis_monat': 1, 'bis_jahr': 2026}),
        ('month text', {**PERIOD, 'von_monat': 'abc'}),
        ('month 13', {**PERIOD, 'bis_monat': 13}),
        ('year too early', {**PERIOD, 'von_jahr': 1900}),
        ('year too late', {**PERIOD, 'bis_jahr': 2100}),
        ('area text', {**PERIOD, 'bereich': 'abc'}),
        ('only one parameter', {'von_monat': 3}),
        ('script', {**PERIOD, 'von_monat': '<script>alert(1)</script>'}),
    ])
    def test_a_request_that_is_not_in_order_is_led_to_the_page_with_the_summary(self, _name, params):
        response = self.export('print', params)
        self.assertEqual(response.status_code, 302)
        self.assertNotIn('Content-Disposition', response)
        self.assertTrue(response.url.startswith('/qm/auswertungen/?'), response.url)
        self.assertNotIn('alert(1)', response.url)
        self.assertNotIn('<', response.url)
        page = self.client.get(response.url, **DE).content.decode()
        self.assertIsNotNone(summary(page))
        self.assertNotIn('auswertung-kennzahlen', page)

    def test_the_page_that_the_request_is_led_to_keeps_the_parameters_that_it_was_asked_with(self):
        response = self.export('print', {'von_monat': 5, 'von_jahr': 2026, 'bis_monat': 4,
                                         'bis_jahr': 2026, 'q': 'x'})
        self.assertEqual(response.url, '/qm/auswertungen/?von_monat=5&von_jahr=2026&bis_monat=4&bis_jahr=2026')

    def test_an_area_of_another_department_is_not_in_order(self):
        foreign = OrgUnit.objects.get(name='Kanarienvogel-Gruppe')
        response = self.export('print', {**PERIOD, 'bereich': foreign.pk})
        self.assertEqual(response.status_code, 302)
        self.assertNoCanary(self.client.get(response.url, **DE).content)

    def test_head_and_the_other_methods(self):
        self.assertEqual(self.client.head(reverse('qm_reports_print') + '?von_monat=x').status_code, 302)
        self.assertEqual(self.client.post(reverse('qm_reports_print'), PERIOD).status_code, 405)

    def test_a_nul_byte_is_a_bad_request(self):
        response = self.client.get(reverse('qm_reports_print'), {**PERIOD, 'bis_jahr': '\x00'}, **DE)
        self.assertEqual(response.status_code, 400)


class PrintQueriesTest(ExportCase):

    def queries(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.export('print')
        self.assertEqual(response.status_code, 200)
        return len(queries)

    def test_the_same_number_of_queries_for_a_few_and_for_many_incidents(self):
        self.queries()  # warms the caches that the first request fills
        few = self.queries()
        for number in range(40):
            make_incident(self.dept, reported=date(2026, 1, 1 + number % 28), history=[
                ('new', at(1, 1 + number % 28)), ('in process', at(2, 1 + number % 28))],
                risk=('low', 'high', '')[number % 3], preventability='avoidable')
        publish(self.incidents[2], 'Titel', 'Maßnahme')
        self.assertEqual(self.queries(), few)


class PageExportTest(ExportCase):
    """The page leads to the print view and the CSV file with its period and area, and its own
    print stays free of numbers."""

    def links(self, params=PERIOD):
        html = self.html(params)
        group = re.search(r'role="group" aria-label="Exportieren">(.*?)</div>', html, re.S).group(1)
        return [(words(text), html_lib.unescape(href)) for href, text in
                re.findall(r'<a [^>]*href="([^"]*)"[^>]*>(.*?)</a>', group, re.S)]

    def test_two_links_with_their_own_names_for_the_print_view_and_the_file(self):
        first, last = date(2026, 1, 1), date(2026, 10, 1)
        self.assertEqual(self.links(), [
            ('Drucken / als PDF speichern', report_url(first, last, None, 'qm_reports_print')),
            ('Als CSV herunterladen', report_url(first, last, None, 'qm_reports_csv'))])

    def test_the_links_keep_the_period_and_the_area_of_the_page(self):
        params = {'von_monat': 3, 'von_jahr': 2026, 'bis_monat': 6, 'bis_jahr': 2026,
                  'bereich': self.units.labor.pk}
        for _name, href in self.links(params):
            self.assertIn('von_monat=3&von_jahr=2026&bis_monat=6&bis_jahr=2026&bereich=%d'
                          % self.units.labor.pk, href)
        self.assertEqual(len(self.links({})), 2)  # also for the period of no parameters

    def test_the_links_lead_to_pages_that_answer(self):
        for _name, href in self.links():
            self.assertEqual(self.client.get(href, **DE).status_code, 200, href)

    def test_the_hint_says_what_the_exports_leave_out(self):
        self.assertIn('Druckansicht und CSV-Datei geben kleine Zahlen (über 0 und unter 3) nicht '
                      'an; am Bildschirm sehen Sie jede Zahl.', words(self.html(PERIOD)))

    def test_no_link_is_a_download_attribute_and_none_depends_on_script(self):
        html = self.html(PERIOD)
        self.assertEqual(csp_violations(html), [])
        self.assertNotIn('data-drucken', html)

    def test_the_paper_of_the_page_has_no_results_and_a_note_instead(self):
        html = self.html(PERIOD)
        self.assertIn('<div class="ui-stack ui-mt-4 ui-ergebnis">', html)
        self.assertIn('<p class="ui-druckhinweis">Diese Seite zeigt jede Zahl, auch kleine, und '
                      'wird nicht gedruckt.', html)
        css = css_text()
        section = css[css.index('The evaluations page on paper'):]
        self.assertRegex(section, r'\.ui-druckhinweis \{ display: none; \}')
        paper = re.search(r'@media print \{(.*?)\n\}', section, re.S).group(1)
        self.assertRegex(paper, r'\.ui-ergebnis \{ display: none; \}')
        self.assertRegex(paper, r'\.ui-druckhinweis \{ display: block;')

    def test_only_the_results_are_left_out_of_the_paper_not_the_heading(self):
        html = self.html(PERIOD)
        self.assertLess(html.index('<h1'), html.index('ui-ergebnis'))
        self.assertGreater(html.index('auswertung-kennzahlen'), html.index('ui-ergebnis'))
        self.assertEqual(re.findall(r'<h1[^>]*>(.*?)</h1>', html, re.S), ['Auswertungen'])

    def test_the_other_qm_pages_are_not_touched(self):
        for name in ('qm_overview', 'qm_incidents'):
            self.assertNotIn('ui-ergebnis', self.client.get(reverse(name), **DE).content.decode())
