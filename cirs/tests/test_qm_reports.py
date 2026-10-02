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

"""The evaluations of the QM: the page for a period and an area, the report behind it, the form
and what it does with requests that are not in order. A foreign department never shows, and the
page costs the same queries for one incident as for thirty. Test data is constructed, never taken
from a real report."""

import html as html_lib
import re
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from unittest import mock

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone, translation
from model_bakery import baker
from parameterized import parameterized

from cirs.models import OrgUnit, PublishableIncident
from cirs.qm.access import scoped_incidents
from cirs.qm.metrics import Durations
from cirs.qm.report import MAX_MONTHS, Distribution, build_report
from cirs.qm.views_reports import report_url

from .canary import CanaryMixin
from .helpers import csp_violations, make_incident
from .test_pages_report import css_text
from .test_qm_overview import TODAY, at, hrefs, tables, tile, words

DE = {'HTTP_ACCEPT_LANGUAGE': 'de'}
EN = {'HTTP_ACCEPT_LANGUAGE': 'en'}
THIS_YEAR = {'von_monat': 1, 'von_jahr': 2026, 'bis_monat': 10, 'bis_jahr': 2026}
SINCE_2023 = {'von_monat': 1, 'von_jahr': 2023, 'bis_monat': 12, 'bis_jahr': 2023}
NOT_RECORDED = 'nicht erfasst'
CHOICE_ERROR = 'Bitte wählen Sie einen Eintrag aus der Liste.'
MONTHS_DE = ['Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli', 'August', 'September',
             'Oktober', 'November', 'Dezember']


def figures(html):
    """The key figures of the page as {label: value}."""
    section = tile(html, 'auswertung-kennzahlen')
    return {words(label): words(value) for label, value in
            re.findall(r'<div><dt>(.*?)</dt><dd[^>]*>(.*?)</dd></div>', section, re.S)}


def select_of(html, name):
    return re.search(r'<select name="%s".*?</select>' % name, html, re.S).group(0)


def selected(html, name):
    """The values of the options that are selected in the select with this name."""
    return re.findall(r'<option value="([^"]*)" selected>', select_of(html, name))


def options(html, name):
    """(value, text) of each option of the select with this name."""
    return [(value, words(label)) for value, label in
            re.findall(r'<option value="([^"]*)"[^>]*>(.*?)</option>', select_of(html, name), re.S)]


def summary(html):
    """The items of the summary of errors as (target, name, message), None if there is none."""
    box = re.search(r'<div class="ui-alert ui-alert--danger[^>]*role="alert".*?</ul>', html, re.S)
    if not box:
        return None
    return [(target, words(label), words(message)) for target, label, message in
            re.findall(r'<li><a href="#([^"]+)">(.*?)</a>: (.*?)</li>', box.group(0), re.S)]


def publish(incident, title, measures, languages=('de', 'en')):
    """A published case of the incident with the texts "title (language)" and "measures
    (language)"; they are in each language the same except for the name of the language."""
    case = PublishableIncident.objects.create(critical_incident=incident)
    for language in languages:
        case.create_translation(language, incident='%s (%s)' % (title, language),
                                description='Beschreibung',
                                measures_and_consequences='%s (%s)' % (measures, language))
    case.publish = True
    case.save()
    return case


class ReportBase(TestCase):
    """A department with a reviewer. The page takes "today" from the clock of the time zone, so
    that is fixed, and the data is built around that day."""

    @classmethod
    def setUpTestData(cls):
        cls.dept = baker.make_recipe('cirs.department', name='Station Eins')
        cls.reviewer = baker.make_recipe('cirs.reviewer')
        cls.dept.reviewers.add(cls.reviewer)

    def setUp(self):
        self.enterContext(mock.patch.object(timezone, 'localdate', return_value=TODAY))
        self.client.force_login(self.reviewer.user)

    def get(self, params=None, **extra):
        return self.client.get(reverse('qm_reports'), params or {}, **{**DE, **extra})

    def html(self, params=None, **extra):
        response = self.get(params, **extra)
        self.assertEqual(response.status_code, 200, params)
        return response.content.decode()


class ReportData(CanaryMixin, ReportBase):
    """The department has seven incidents of every kind, an old one and a published case. A
    foreign department has some too, reported today, and nothing below is right if one of them
    is counted or shown."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.make_canary(reported=TODAY)
        cls.labor = OrgUnit.objects.create(name='Labor')
        cls.station = OrgUnit.objects.create(name='Station A', parent=cls.labor)
        cls.pflege = OrgUnit.objects.create(name='Pflege')
        cls.empty = OrgUnit.objects.create(name='Leer')  # a group without incidents

        def make(reported, history, **fields):
            return make_incident(cls.dept, reported=reported, history=history, **fields)

        # in the log from the start to the completion: 2 days until the reaction, 22 to the end
        cls.a = make(date(2026, 1, 10), [('new', at(1, 10)), ('in process', at(1, 12)),
                                         ('completed', at(2, 1))],
                     org_unit=cls.station, category=['knowledge/training'], risk='high',
                     preventability='avoidable', frequency='seldom', hazard='low')
        # 0 days until the reaction, 10 to the end, two categories, a published case
        cls.b = make(date(2026, 3, 5), [('new', at(3, 5)), ('in process', at(3, 5, hour=15)),
                                        ('completed', at(3, 15))],
                     org_unit=cls.labor, category=['technique/methods', 'infrastructure'],
                     risk='middle', preventability='not avoidable', frequency='occasional',
                     hazard='moderate')
        cls.case = publish(cls.b, 'Titel B', 'Maßnahmen B\nzweite Zeile')
        cls.c = make(date(2026, 3, 20), [('new', at(3, 20)), ('in process', at(3, 25))],
                     org_unit=cls.pflege, category=['knowledge/training'], risk='low',
                     preventability='avoidable', frequency='seldom', hazard='low')
        cls.d = make(date(2026, 6, 2), [('new', at(6, 2))], preventability='indistinct')
        cls.e = make(date(2026, 9, 30), [('new', at(9, 30))], org_unit=cls.pflege,
                     category=['other'], preventability='avoidable')
        # from before the log: it has no entry
        cls.f = make_incident(cls.dept, reported=date(2026, 2, 2), status='in process',
                              legacy=True, risk='high', preventability='not avoidable',
                              hazard='high')
        # reported the year before, completed in this one: it is in no count of the incoming
        cls.g = make(date(2025, 12, 20), [('new', at(12, 20, 2025)), ('completed', at(1, 5))],
                     org_unit=cls.labor, preventability='avoidable')
        # long before the log, completed: no entry, so no time, and it is not open
        make_incident(cls.dept, reported=date(2023, 6, 1), status='completed', legacy=True,
                      preventability='avoidable')


class KeyFiguresTest(ReportData):

    def test_the_key_figures_of_this_year_up_to_the_current_month(self):
        self.assertEqual(figures(self.html(THIS_YEAR)), {
            'Eingang': '6', 'Abgeschlossen': '3', 'Offen am Ende des Zeitraums': '4',
            'Veröffentlicht': '1', 'Reaktionszeit': 'Median 3,5 Tage, Anzahl 4',
            'Bearbeitungsdauer': 'Median 16 Tage, Anzahl 3'})

    def test_the_figures_are_those_of_the_report(self):
        report = build_report(scoped_incidents(self.reviewer.user), date(2026, 1, 1),
                              date(2026, 10, 1), today=TODAY)
        self.assertEqual((report.incoming, report.completed, report.open_end, report.published),
                         (6, 3, 4, 1))
        self.assertEqual((report.reaction, report.processing),
                         (Durations(3.5, 4), Durations(16.0, 3)))
        self.assertEqual(list(figures(self.html(THIS_YEAR)).values())[:4],
                         [str(report.incoming), str(report.completed), str(report.open_end),
                          str(report.published)])

    def test_without_parameters_it_is_this_year_up_to_the_current_month(self):
        html = self.html()
        self.assertEqual(figures(html), figures(self.html(THIS_YEAR)))
        self.assertEqual(tables(tile(html, 'auswertung-verlauf')),
                         tables(tile(self.html(THIS_YEAR), 'auswertung-verlauf')))
        self.assertIn('Zeitraum: Januar 2026 bis Oktober 2026', words(html))

    def test_a_parameter_that_is_not_the_business_of_the_page_changes_nothing(self):
        self.assertEqual(figures(self.html({'q': 'x', 'page': 'y'})), figures(self.html()))

    def test_a_period_that_ended_does_not_know_what_was_open_at_its_end(self):
        # the state of the day is not recorded: only the state of now is known
        html = self.html({'von_monat': 1, 'von_jahr': 2026, 'bis_monat': 9, 'bis_jahr': 2026})
        self.assertEqual(figures(html)['Offen am Ende des Zeitraums'], NOT_RECORDED)
        self.assertIn('<dd class="ui-kennzahlen__text">nicht erfasst</dd>', html)
        self.assertEqual(figures(html)['Eingang'], '6')

    def test_a_period_that_reaches_today_has_the_open_incidents_of_now(self):
        html = self.html({'von_monat': 10, 'von_jahr': 2026, 'bis_monat': 10, 'bis_jahr': 2026})
        self.assertEqual(figures(html)['Offen am Ende des Zeitraums'], '4')
        self.assertEqual(figures(html)['Eingang'], '0')

    def test_one_day_is_singular_and_the_dash_stands_for_no_incident(self):
        html = self.html({'von_monat': 1, 'von_jahr': 2023, 'bis_monat': 1, 'bis_jahr': 2023})
        self.assertEqual(figures(html)['Reaktionszeit'], '–')
        self.assertEqual(figures(html)['Bearbeitungsdauer'], '–')
        feb = self.html({'von_monat': 2, 'von_jahr': 2026, 'bis_monat': 2, 'bis_jahr': 2026})
        self.assertEqual(figures(feb)['Bearbeitungsdauer'], 'Median 22 Tage, Anzahl 1')
        make_incident(self.dept, reported=date(2026, 5, 1), preventability='avoidable',
                      history=[('new', at(5, 1)), ('in process', at(5, 2))])
        may = self.html({'von_monat': 5, 'von_jahr': 2026, 'bis_monat': 5, 'bis_jahr': 2026})
        self.assertEqual(figures(may)['Reaktionszeit'], 'Median 1 Tag, Anzahl 1')
        self.assertEqual(figures(self.html({'von_monat': 5, 'von_jahr': 2026, 'bis_monat': 5,
                                            'bis_jahr': 2026}, **EN))['Reaction time'],
                         'Median 1 day, count 1')
        make_incident(self.dept, reported=date(2026, 5, 3), preventability='avoidable',
                      history=[('new', at(5, 3)), ('in process', at(5, 3, hour=13))])
        may = self.html({'von_monat': 5, 'von_jahr': 2026, 'bis_monat': 5, 'bis_jahr': 2026})
        self.assertEqual(figures(may)['Reaktionszeit'], 'Median 0,5 Tage, Anzahl 2')

    def test_the_log_covers_the_period_so_nothing_is_missing(self):
        html = self.html(THIS_YEAR)
        self.assertNotIn('Abschlüsse und Zeiten werden', html)
        self.assertNotIn(NOT_RECORDED, tile(html, 'auswertung-verlauf'))


class DevelopmentTest(ReportData):

    MONTHS = ['Jan', 'Feb', 'Mär', 'Apr', 'Mai', 'Jun', 'Jul', 'Aug', 'Sep', 'Okt']

    def test_a_row_for_each_month_with_the_incoming_and_the_completed(self):
        [chart] = tables(tile(self.html(THIS_YEAR), 'auswertung-verlauf'))
        self.assertEqual(chart[0], ['Monat', 'Eingang', 'Abgeschlossen'])
        incoming = [1, 1, 2, 0, 0, 1, 0, 0, 1, 0]
        completed = [1, 1, 1, 0, 0, 0, 0, 0, 0, 0]
        self.assertEqual(chart[1:], [['%s 2026' % month, str(i), str(c)] for month, i, c
                                     in zip(self.MONTHS, incoming, completed)])

    def test_the_months_add_up_to_the_figures(self):
        html = self.html(THIS_YEAR)
        [chart] = tables(tile(html, 'auswertung-verlauf'))
        self.assertEqual(sum(int(row[1]) for row in chart[1:]), int(figures(html)['Eingang']))
        self.assertEqual(sum(int(row[2]) for row in chart[1:]),
                         int(figures(html)['Abgeschlossen']))

    def test_the_chart_is_named_and_described(self):
        verlauf = tile(self.html(THIS_YEAR), 'auswertung-verlauf')
        self.assertIn('<title id="chart-verlauf-title">Eingang und Abgeschlossen pro Monat, '
                      'Januar 2026 bis Oktober 2026</title>', verlauf)
        self.assertIn('<desc id="chart-verlauf-desc">Meldungen im Zeitraum Januar 2026 bis '
                      'Oktober 2026: 6 eingegangen, 3 abgeschlossen.</desc>', verlauf)

    def test_the_months_before_the_log_are_not_recorded(self):
        # the log begins on 20 December 2025: eleven months of 2025 are before it
        html = self.html({'von_monat': 1, 'von_jahr': 2025, 'bis_monat': 10, 'bis_jahr': 2026})
        [chart] = tables(tile(html, 'auswertung-verlauf'))
        self.assertEqual([row[2] for row in chart[1:12]], [NOT_RECORDED] * 11)
        self.assertEqual(chart[12][0], 'Dez 2025')
        self.assertEqual(chart[12][1:], ['1', '0'])
        self.assertEqual(html.count('ui-diagramm__fehlt-2'), 11)
        text = re.sub(r'<(title|desc)\b.*?</\1>', '', html, flags=re.S)  # not in the image text
        self.assertEqual(words(text).count('Abschlüsse und Zeiten werden seit 20.12.2025 erfasst.'),
                         1)
        self.assertIn('seit 20.12.2025 erfasst', tile(html, 'auswertung-verlauf'))

    def test_the_table_opens_by_itself_for_more_than_12_months(self):
        opened = '<details class="ui-diagramm__tabelle" open>'
        year = {'von_monat': 1, 'von_jahr': 2026, 'bis_monat': 12, 'bis_jahr': 2026}
        self.assertEqual(self.html(year).count(opened), 0)
        longer = {'von_monat': 12, 'von_jahr': 2025, 'bis_monat': 12, 'bis_jahr': 2026}
        html = self.html(longer)
        self.assertEqual(html.count(opened), 1)
        self.assertIn(opened, tile(html, 'auswertung-verlauf'))
        [chart] = tables(tile(html, 'auswertung-verlauf'))
        self.assertEqual(len(chart), 14)

    def test_a_period_without_reports_and_completions_has_no_chart(self):
        html = self.html({'von_monat': 4, 'von_jahr': 2024, 'bis_monat': 4, 'bis_jahr': 2024})
        # the years that can be chosen start with the one of the oldest incident, 2023
        self.assertIn('Keine Meldungen im Zeitraum.', tile(html, 'auswertung-verlauf'))
        self.assertNotIn('<table', tile(html, 'auswertung-verlauf'))


class DistributionTest(ReportData):

    EXPECTED = [
        [['Bereich', 'Anzahl'], ['Labor', '2'], ['Pflege', '2'], ['Keine Angabe', '2']],
        [['Kategorie', 'Anzahl'], ['Organisation/Kommunikation', '0'], ['Technik/Methoden', '1'],
         ['Wissen/Training', '2'],
         ['Konzentration/Aufmerksamkeit (Versehen/Ausrutscher)', '0'], ['Infrastruktur', '1'],
         ['Sonstige', '1'], ['Keine Angabe', '2']],
        [['Vermeidbarkeit', 'Anzahl'], ['Beurteilung nicht möglich', '1'],
         ['Das Ereignis war vermeidbar', '3'], ['Das Ereignis war nicht vermeidbar', '2']],
        [['Risiko', 'Anzahl'], ['niedrig', '1'], ['mittel', '1'], ['hoch', '2'],
         ['Keine Angabe', '2']],
        [['Häufigkeit', 'Anzahl'], ['einzellfall (erstmalig)', '0'], ['selten (1 pro Jahr)', '2'],
         ['gelegentlich (1 pro Monat)', '1'], ['häufig (1 pro Woche)', '0'],
         ['ständig (täglich)', '0'], ['Keine Angabe', '3']],
        [['Gefährdung', 'Anzahl'], ['sehr niedrig', '0'], ['niedrig', '2'], ['moderat', '1'],
         ['hoch', '1'], ['sehr hoch', '0'], ['Keine Angabe', '2']],
    ]

    def test_six_distributions_of_the_incidents_of_the_period(self):
        self.assertEqual(tables(tile(self.html(THIS_YEAR), 'auswertung-verteilungen')),
                         self.EXPECTED)

    def test_every_distribution_but_the_categories_adds_up_to_the_incoming(self):
        for number, table in enumerate(tables(tile(self.html(THIS_YEAR),
                                                   'auswertung-verteilungen'))):
            total = sum(int(row[1]) for row in table[1:])
            self.assertEqual(total, 7 if number == 1 else 6, table[0][0])

    def test_the_categories_say_that_a_report_can_have_several(self):
        html = self.html(THIS_YEAR)
        note = 'Mehrfachnennung möglich: Eine Meldung zählt in jeder ihrer Kategorien.'
        self.assertEqual(words(html).count(note), 1)
        self.assertIn(note, words(re.search(r'<h3[^>]*>Kategorie</h3>.*?<div class="ui-diagramm">',
                                            html, re.S).group(0)))

    def test_each_chart_is_named_and_described_with_its_key_message(self):
        html = self.html(THIS_YEAR)
        period = 'Januar 2026 bis Oktober 2026'
        for chart_id, name, message in (
                ('chart-org-unit-group', 'Bereich', 'Am meisten: Labor mit 2 von 6.'),
                ('chart-category', 'Kategorie', 'Am meisten: Wissen/Training mit 2.'),
                ('chart-preventability', 'Vermeidbarkeit',
                 'Am meisten: Das Ereignis war vermeidbar mit 3 von 6.'),
                ('chart-risk', 'Risiko', 'Am meisten: hoch mit 2 von 6.')):
            self.assertIn('<span class="ui-visually-hidden" id="%s-title">%s, %s</span>'
                          % (chart_id, name, period), html)
            self.assertIn('<span class="ui-visually-hidden" id="%s-desc">%s</span>'
                          % (chart_id, message), html)

    def test_a_period_without_reports_has_no_charts(self):
        html = self.html({'von_monat': 4, 'von_jahr': 2024, 'bis_monat': 4, 'bis_jahr': 2024})
        verteilungen = tile(html, 'auswertung-verteilungen')
        self.assertIn('Keine Meldungen im Zeitraum.', verteilungen)
        self.assertNotIn('<table', verteilungen)


class MeasuresTest(ReportData):

    def measures(self, params=THIS_YEAR, **extra):
        return tile(self.html(params, **extra), 'auswertung-massnahmen')

    def test_the_published_case_with_its_number_day_title_and_measures(self):
        section = self.measures()
        self.assertEqual(hrefs(section), [self.b.get_absolute_url()])
        self.assertIn('Nr. %d' % self.b.pk, words(section))
        self.assertIn('<time datetime="2026-03-05">05.03.2026</time>', section)
        self.assertIn('Titel B (de)', words(section))
        self.assertIn('Maßnahmen B<br>zweite Zeile (de)', section)

    def test_the_texts_are_in_the_active_language(self):
        section = self.measures(**EN)
        self.assertIn('Titel B (en)', words(section))
        self.assertNotIn('(de)', section)
        self.assertIn('No. %d' % self.b.pk, words(section))

    def test_a_case_without_the_active_language_shows_the_language_that_it_has(self):
        incident = make_incident(self.dept, reported=date(2026, 4, 1), preventability='avoidable')
        publish(incident, 'Nur Englisch', 'Nur Maßnahmen', languages=('en',))
        self.assertIn('Nur Englisch (en)', words(self.measures()))

    def test_the_cases_of_the_reports_of_the_period_only_the_oldest_report_first(self):
        early = make_incident(self.dept, reported=date(2026, 2, 3), preventability='avoidable')
        publish(early, 'Frueh', 'Massnahme')
        outside = make_incident(self.dept, reported=date(2025, 11, 3), preventability='avoidable')
        publish(outside, 'Draussen', 'Massnahme')
        section = self.measures()
        self.assertEqual(hrefs(section), [early.get_absolute_url(), self.b.get_absolute_url()])
        self.assertNotIn('Draussen', section)

    def test_a_case_that_is_not_published_is_not_listed(self):
        incident = make_incident(self.dept, reported=date(2026, 4, 1), preventability='avoidable')
        case = publish(incident, 'Entwurf', 'Massnahme')
        case.publish = False
        case.save()
        self.assertNotIn('Entwurf', self.measures())

    def test_the_figure_and_the_list_say_the_same(self):
        for params, count in ((THIS_YEAR, 1), (SINCE_2023, 0)):
            html = self.html(params)
            self.assertEqual(figures(html)['Veröffentlicht'], str(count))
            self.assertEqual(len(re.findall(r'<li>\s*<p class="ui-massnahmen__kopf">',
                                            tile(html, 'auswertung-massnahmen'))), count)

    def test_without_a_case_there_is_an_empty_state(self):
        section = self.measures(SINCE_2023)
        self.assertIn('Keine veröffentlichten Fälle für diesen Zeitraum.', words(section))
        self.assertNotIn('<li>', section)


class AreaTest(ReportData):

    def of(self, group, **extra):
        return self.html({**THIS_YEAR, 'bereich': group.pk}, **extra)

    def test_an_area_narrows_every_number(self):
        html = self.of(self.labor)
        # a (in the group through its unit), b and, for the times of completion, g
        self.assertEqual(figures(html), {
            'Eingang': '2', 'Abgeschlossen': '3', 'Offen am Ende des Zeitraums': '0',
            'Veröffentlicht': '1', 'Reaktionszeit': 'Median 2 Tage, Anzahl 3',
            'Bearbeitungsdauer': 'Median 16 Tage, Anzahl 3'})
        self.assertEqual(tables(tile(html, 'auswertung-verteilungen'))[0],
                         [['Bereich', 'Anzahl'], ['Labor', '2']])
        [chart] = tables(tile(html, 'auswertung-verlauf'))
        self.assertEqual([row[1] for row in chart[1:]], ['1', '0', '1', '0', '0', '0', '0', '0',
                                                         '0', '0'])
        self.assertEqual(hrefs(tile(html, 'auswertung-massnahmen')), [self.b.get_absolute_url()])

    def test_another_area_has_its_own_numbers(self):
        html = self.of(self.pflege)
        self.assertEqual(figures(html), {
            'Eingang': '2', 'Abgeschlossen': '0', 'Offen am Ende des Zeitraums': '2',
            'Veröffentlicht': '0', 'Reaktionszeit': 'Median 5 Tage, Anzahl 1',
            'Bearbeitungsdauer': '–'})
        self.assertIn('Keine veröffentlichten Fälle für diesen Zeitraum.',
                      words(tile(html, 'auswertung-massnahmen')))
        self.assertNotIn('Titel B', html)

    def test_the_area_is_named_in_the_head_of_the_results_and_kept_in_the_form(self):
        html = self.of(self.labor)
        self.assertIn('Zeitraum: Januar 2026 bis Oktober 2026 · Bereich: Labor', words(html))
        self.assertEqual(selected(html, 'bereich'), [str(self.labor.pk)])
        self.assertNotIn('Bereich: ', words(self.html(THIS_YEAR)))

    def test_the_quick_choices_keep_the_area(self):
        html = self.of(self.labor)
        links = [url for url in hrefs(html) if url.startswith('/qm/auswertungen/?')]
        self.assertEqual(len(links), 3)
        for url in links:
            self.assertTrue(url.endswith('&bereich=%d' % self.labor.pk), url)

    def test_all_areas_is_no_area_and_the_same_as_none(self):
        self.assertEqual(figures(self.html({**THIS_YEAR, 'bereich': ''})),
                         figures(self.html(THIS_YEAR)))

    def test_only_the_groups_of_the_incidents_of_the_reviewer_are_offered(self):
        # not the unit below a group, not a group without incidents, not that of a foreign
        # department
        self.assertEqual(options(self.html(), 'bereich'),
                         [('', 'Alle'), (str(self.labor.pk), 'Labor'),
                          (str(self.pflege.pk), 'Pflege')])

    def test_the_area_matches_the_report_of_the_same_area(self):
        incidents = scoped_incidents(self.reviewer.user)
        for group in (self.labor, self.pflege):
            report = build_report(incidents, date(2026, 1, 1), date(2026, 10, 1), group.pk,
                                  TODAY)
            shown = figures(self.of(group))
            self.assertEqual(shown['Eingang'], str(report.incoming))
            self.assertEqual(shown['Abgeschlossen'], str(report.completed))
            self.assertEqual(shown['Offen am Ende des Zeitraums'], str(report.open_end))


class FormTest(ReportData):

    def test_the_form_holds_this_year_up_to_the_current_month(self):
        html = self.html()
        self.assertEqual([selected(html, name) for name in ('von_monat', 'von_jahr', 'bis_monat',
                                                            'bis_jahr', 'bereich')],
                         [['1'], ['2026'], ['10'], ['2026'], ['']])

    def test_the_form_holds_what_was_asked_for(self):
        html = self.html({'von_monat': 3, 'von_jahr': 2025, 'bis_monat': 6, 'bis_jahr': 2026})
        self.assertEqual([selected(html, name) for name in ('von_monat', 'von_jahr', 'bis_monat',
                                                            'bis_jahr')],
                         [['3'], ['2025'], ['6'], ['2026']])

    def test_the_months_are_named_and_the_years_run_from_the_oldest_incident(self):
        html = self.html()
        for name in ('von_monat', 'bis_monat'):
            self.assertEqual(options(html, name),
                             [(str(number), month) for number, month in enumerate(MONTHS_DE, 1)])
        for name in ('von_jahr', 'bis_jahr'):
            self.assertEqual(options(html, name), [(str(year), str(year)) for year in
                                                   range(2023, 2027)])

    def test_the_fields_have_labels_and_the_groups_a_legend(self):
        html = self.html()
        self.assertEqual(re.findall(r'<legend>(.*?)</legend>', html), ['Von', 'Bis'])
        labels = re.findall(r'<label class="ui-field__label" for="([^"]+)">(.*?)</label>', html)
        self.assertEqual(labels, [('id_von_monat', 'Monat'), ('id_von_jahr', 'Jahr'),
                                  ('id_bis_monat', 'Monat'), ('id_bis_jahr', 'Jahr'),
                                  ('id_bereich', 'Bereich')])
        self.assertIn('>Auswerten</button>', html)
        self.assertIn('Eine Gruppe umfasst ihre Einheiten.', html)

    def test_the_form_is_a_get_form_without_script_and_names_itself(self):
        html = self.html()
        self.assertIn('<form method="get" class="ui-card ui-mt-4" aria-label="Zeitraum wählen">',
                      html)
        self.assertNotIn('csrfmiddlewaretoken', re.search(r'<form method="get".*?</form>', html,
                                                         re.S).group(0))

    def test_three_quick_choices_for_the_last_quarter_the_year_and_the_year_before(self):
        html = self.html()
        group = re.search(r'role="group" aria-labelledby="auswertung-schnellwahl">(.*?)</div>',
                          html, re.S).group(1)
        self.assertEqual([(words(text), html_lib.unescape(href)) for href, text in
                          re.findall(r'<a [^>]*href="([^"]*)"[^>]*>(.*?)</a>', group, re.S)],
                         [('Letztes Quartal', report_url(date(2026, 7, 1), date(2026, 9, 1))),
                          ('Laufendes Jahr', report_url(date(2026, 1, 1), date(2026, 10, 1))),
                          ('Letztes Jahr', report_url(date(2025, 1, 1), date(2025, 12, 1)))])
        self.assertIn('Schnellwahl', html)

    def test_the_quick_choices_lead_to_periods_that_are_answered(self):
        self.assertEqual(report_url(date(2026, 7, 1), date(2026, 9, 1)),
                         '/qm/auswertungen/?von_monat=7&von_jahr=2026&bis_monat=9&bis_jahr=2026')
        for url in hrefs(self.html()):
            if url.startswith('/qm/auswertungen/?'):
                response = self.client.get(url, **DE)
                self.assertEqual(response.status_code, 200, url)
                self.assertNotIn('ui-alert--danger', response.content.decode(), url)

    def test_last_year_is_not_recorded_at_its_end_and_has_the_note_on_the_log(self):
        html = self.html({'von_monat': 1, 'von_jahr': 2025, 'bis_monat': 12, 'bis_jahr': 2025})
        self.assertEqual(figures(html)['Offen am Ende des Zeitraums'], NOT_RECORDED)
        self.assertIn('Abschlüsse und Zeiten werden seit 20.12.2025 erfasst.', words(html))
        self.assertEqual(figures(html)['Eingang'], '1')


class InvalidTest(ReportData):

    @parameterized.expand([
        ('swapped', {'von_monat': 5, 'von_jahr': 2026, 'bis_monat': 4, 'bis_jahr': 2026},
         [('id_bis_monat', 'Bis: Monat', 'Der Zeitraum endet vor seinem Beginn.')]),
        ('swapped years', {'von_monat': 1, 'von_jahr': 2026, 'bis_monat': 12, 'bis_jahr': 2025},
         [('id_bis_monat', 'Bis: Monat', 'Der Zeitraum endet vor seinem Beginn.')]),
        ('37 months', {'von_monat': 1, 'von_jahr': 2023, 'bis_monat': 1, 'bis_jahr': 2026},
         [('id_bis_monat', 'Bis: Monat', 'Der Zeitraum darf höchstens 36 Monate umfassen.')]),
        # the summary lists the errors in the order of the fields on the page
        ('swapped and area', {'von_monat': 5, 'von_jahr': 2026, 'bis_monat': 4, 'bis_jahr': 2026,
                              'bereich': 'x'},
         [('id_bis_monat', 'Bis: Monat', 'Der Zeitraum endet vor seinem Beginn.'),
          ('id_bereich', 'Bereich', CHOICE_ERROR)]),
        ('month text', {**THIS_YEAR, 'von_monat': 'abc'},
         [('id_von_monat', 'Von: Monat', CHOICE_ERROR)]),
        ('month 0', {**THIS_YEAR, 'von_monat': 0}, [('id_von_monat', 'Von: Monat', CHOICE_ERROR)]),
        ('month 13', {**THIS_YEAR, 'bis_monat': 13},
         [('id_bis_monat', 'Bis: Monat', CHOICE_ERROR)]),
        ('month negative', {**THIS_YEAR, 'bis_monat': -1},
         [('id_bis_monat', 'Bis: Monat', CHOICE_ERROR)]),
        ('month leading zero', {**THIS_YEAR, 'von_monat': '01'},
         [('id_von_monat', 'Von: Monat', CHOICE_ERROR)]),
        ('month empty', {**THIS_YEAR, 'von_monat': ''},
         [('id_von_monat', 'Von: Monat', CHOICE_ERROR)]),
        ('year too early', {**THIS_YEAR, 'von_jahr': 1900},
         [('id_von_jahr', 'Von: Jahr', CHOICE_ERROR)]),
        ('year too late', {**THIS_YEAR, 'bis_jahr': 2100},
         [('id_bis_jahr', 'Bis: Jahr', CHOICE_ERROR)]),
        ('year decimal', {**THIS_YEAR, 'von_jahr': '2026.5'},
         [('id_von_jahr', 'Von: Jahr', CHOICE_ERROR)]),
        ('year huge', {**THIS_YEAR, 'von_jahr': '9' * 40},
         [('id_von_jahr', 'Von: Jahr', CHOICE_ERROR)]),
        ('other digits', {**THIS_YEAR, 'bis_monat': '١٢'},
         [('id_bis_monat', 'Bis: Monat', CHOICE_ERROR)]),
        ('area text', {**THIS_YEAR, 'bereich': 'abc'},
         [('id_bereich', 'Bereich', CHOICE_ERROR)]),
        ('area negative', {**THIS_YEAR, 'bereich': -1}, [('id_bereich', 'Bereich', CHOICE_ERROR)]),
        ('area huge', {**THIS_YEAR, 'bereich': '9' * 40},
         [('id_bereich', 'Bereich', CHOICE_ERROR)]),
        ('only one parameter', {'von_monat': 3},
         [('id_von_jahr', 'Von: Jahr', CHOICE_ERROR), ('id_bis_monat', 'Bis: Monat', CHOICE_ERROR),
          ('id_bis_jahr', 'Bis: Jahr', CHOICE_ERROR)]),
        ('only the area', {'bereich': 1},
         [('id_von_monat', 'Von: Monat', CHOICE_ERROR), ('id_von_jahr', 'Von: Jahr', CHOICE_ERROR),
          ('id_bis_monat', 'Bis: Monat', CHOICE_ERROR), ('id_bis_jahr', 'Bis: Jahr', CHOICE_ERROR),
          ('id_bereich', 'Bereich', CHOICE_ERROR)]),
        ('script', {**THIS_YEAR, 'von_monat': '<script>alert(1)</script>'},
         [('id_von_monat', 'Von: Monat', CHOICE_ERROR)]),
    ])
    def test_a_request_that_is_not_in_order_names_the_error_and_shows_no_numbers(
            self, _name, params, expected):
        html = self.html(params)  # status 200: never a server error
        self.assertEqual(summary(html), expected)
        self.assertNotIn('auswertung-kennzahlen', html)
        self.assertNotIn('Kennzahlen', html)
        self.assertNotIn('<table', html)
        self.assertNotIn('alert(1)', html)
        self.assertEqual(csp_violations(html), [])

    def test_the_summary_is_focusable_and_each_item_leads_to_its_field(self):
        html = self.html({'von_monat': 5, 'von_jahr': 2026, 'bis_monat': 4, 'bis_jahr': 2026})
        self.assertIn('<div class="ui-alert ui-alert--danger ui-mt-4" role="alert" tabindex="-1" '
                      'autofocus aria-labelledby="auswertung-fehler-titel">', html)
        self.assertIn('Bitte prüfen Sie Ihre Eingaben.', html)
        ids = re.findall(r'\sid="([^"]+)"', html)
        for target, _label, _message in summary(html):
            self.assertIn(target, ids)

    def test_the_field_names_the_error_and_the_select_points_to_it(self):
        html = self.html({'von_monat': 5, 'von_jahr': 2026, 'bis_monat': 4, 'bis_jahr': 2026})
        self.assertIn('<div class="ui-field__error" id="id_bis_monat_error">Der Zeitraum endet '
                      'vor seinem Beginn.</div>', html)
        tag = re.search(r'<select name="bis_monat"[^>]*>', html).group(0)
        self.assertIn('aria-invalid="true"', tag)
        self.assertIn('aria-describedby="id_bis_monat_error"', tag)
        valid = re.search(r'<select name="von_monat"[^>]*>', html).group(0)
        self.assertNotIn('aria-invalid', valid)
        area = self.html({**THIS_YEAR, 'bereich': 'abc'})
        tag = re.search(r'<select name="bereich"[^>]*>', area).group(0)
        self.assertIn('id_bereich_error', tag)
        self.assertIn('id_bereich_helptext', tag)

    def test_the_form_keeps_what_was_chosen_and_the_quick_choices_stay(self):
        html = self.html({'von_monat': 5, 'von_jahr': 2026, 'bis_monat': 4, 'bis_jahr': 2026})
        self.assertEqual([selected(html, name) for name in ('von_monat', 'von_jahr', 'bis_monat',
                                                            'bis_jahr')],
                         [['5'], ['2026'], ['4'], ['2026']])
        self.assertEqual(len([url for url in hrefs(html) if url.startswith('/qm/auswertungen/?')]),
                         3)

    def test_a_nul_byte_is_a_bad_request_and_no_server_error(self):
        response = self.client.get(reverse('qm_reports'), {**THIS_YEAR, 'bis_jahr': '\x00'}, **DE)
        self.assertEqual(response.status_code, 400)

    def test_36_months_are_the_most(self):
        self.assertEqual(MAX_MONTHS, 36)
        longest = self.html({'von_monat': 1, 'von_jahr': 2023, 'bis_monat': 12, 'bis_jahr': 2025})
        self.assertIsNone(summary(longest))
        self.assertEqual(figures(longest)['Eingang'], '2')  # one of 2023, one of December 2025

    def test_one_month_is_the_least_and_the_end_may_be_the_start(self):
        html = self.html({'von_monat': 3, 'von_jahr': 2026, 'bis_monat': 3, 'bis_jahr': 2026})
        self.assertIsNone(summary(html))
        self.assertIn('Zeitraum: März 2026', words(html))
        self.assertEqual(figures(html)['Eingang'], '2')

    def test_an_area_of_a_foreign_department_is_not_a_choice(self):
        group = OrgUnit.objects.get(name='Kanarienvogel-Gruppe')
        unit = OrgUnit.objects.get(name='Kanarienvogel-Station')
        for foreign in (group, unit, self.station):  # a unit below a group is no area either
            html = self.html({**THIS_YEAR, 'bereich': foreign.pk})
            self.assertEqual(summary(html), [('id_bereich', 'Bereich', CHOICE_ERROR)])
            self.assertNoCanary(html)

    def test_head_and_other_methods_do_not_reach_the_numbers(self):
        self.assertEqual(self.client.head(reverse('qm_reports') + '?von_monat=x').status_code, 200)
        self.assertEqual(self.client.post(reverse('qm_reports'), THIS_YEAR).status_code, 405)


class RecordedTest(ReportBase):

    def test_an_empty_department_has_zeros_and_empty_states_and_says_nothing_is_recorded(self):
        html = self.html()
        self.assertEqual(figures(html), {
            'Eingang': '0', 'Abgeschlossen': '0', 'Offen am Ende des Zeitraums': '0',
            'Veröffentlicht': '0', 'Reaktionszeit': '–', 'Bearbeitungsdauer': '–'})
        self.assertIn('Abschlüsse und Zeiten werden noch nicht erfasst.', words(html))
        for name in ('auswertung-verlauf', 'auswertung-verteilungen'):
            self.assertIn('Keine Meldungen im Zeitraum.', words(tile(html, name)))
            self.assertNotIn('<table', tile(html, name))
        self.assertIn('Keine veröffentlichten Fälle für diesen Zeitraum.',
                      words(tile(html, 'auswertung-massnahmen')))
        self.assertEqual(csp_violations(html), [])

    def test_an_incident_from_before_the_log_leaves_everything_unrecorded(self):
        make_incident(self.dept, reported=date(2026, 1, 1), legacy=True, status='completed',
                      preventability='avoidable')
        html = self.html()
        self.assertIn('Abschlüsse und Zeiten werden noch nicht erfasst.', words(html))
        self.assertEqual(figures(html)['Eingang'], '1')
        self.assertEqual(figures(html)['Abgeschlossen'], '0')
        self.assertEqual(figures(html)['Offen am Ende des Zeitraums'], '0')
        self.assertEqual(figures(html)['Reaktionszeit'], '–')
        [chart] = tables(tile(html, 'auswertung-verlauf'))
        self.assertEqual({row[2] for row in chart[1:]}, {NOT_RECORDED})
        self.assertNotIn('Abschlüsse und Zeiten werden seit', html)

    def test_the_year_before_can_always_be_chosen(self):
        # the quick choice of last year has to be a period that the form takes, also for a QM
        # whose incidents are all of this year
        make_incident(self.dept, reported=date(2026, 5, 1), preventability='avoidable')
        for name in ('von_jahr', 'bis_jahr'):
            self.assertEqual(options(self.html(), name), [('2025', '2025'), ('2026', '2026')])
        last_year = report_url(date(2025, 1, 1), date(2025, 12, 1))
        self.assertIsNone(summary(self.client.get(last_year, **DE).content.decode()))

    def test_a_reviewer_without_a_department_gets_the_empty_state_and_no_form(self):
        reviewer = baker.make_recipe('cirs.reviewer')
        self.client.force_login(reviewer.user)
        for params in ({}, THIS_YEAR, {'von_monat': 'abc'}):
            html = self.html(params)
            self.assertIn('Ihr Zugang gehört keiner Abteilung an.', html)
            self.assertIn('Hier gibt es noch nichts anzuzeigen.', html)
            for absent in ('id_von_monat', 'auswertung-kennzahlen', 'Auswerten',
                           'ui-alert--danger'):
                self.assertNotIn(absent, html, params)

    def test_a_qm_of_two_departments_has_the_numbers_of_both(self):
        second = baker.make_recipe('cirs.department', name='Station Zwei')
        second.reviewers.add(self.reviewer)
        make_incident(self.dept, reported=date(2026, 5, 1), preventability='avoidable')
        make_incident(second, reported=date(2026, 5, 2), preventability='avoidable')
        publish(make_incident(second, reported=date(2026, 5, 3), preventability='avoidable'),
                'Zweite', 'Massnahme')
        html = self.html()
        self.assertEqual(figures(html)['Eingang'], '3')
        self.assertEqual(figures(html)['Veröffentlicht'], '1')
        self.assertIn('Abteilungen: Station Eins, Station Zwei', words(html))


class ReportTest(ReportData):

    def build(self, *args, **kwargs):
        return build_report(scoped_incidents(self.reviewer.user), *args, today=TODAY, **kwargs)

    def test_the_report_holds_every_number_of_the_page(self):
        with translation.override('de'):
            report = self.build(date(2026, 1, 15), date(2026, 10, 20))
        self.assertEqual(report.period, (date(2026, 1, 1), date(2026, 10, 31)))
        self.assertEqual((report.incoming, report.completed, report.open_end, report.published),
                         (6, 3, 4, 1))
        self.assertEqual(report.protocol_start, date(2025, 12, 20))
        self.assertEqual([(row.month.month, row.incoming, row.completed) for row in report.monthly],
                         [(1, 1, 1), (2, 1, 1), (3, 2, 1), (4, 0, 0), (5, 0, 0), (6, 1, 0),
                          (7, 0, 0), (8, 0, 0), (9, 1, 0), (10, 0, 0)])
        self.assertEqual([(d.field, d.title, d.multiple_answers) for d in report.distributions],
                         [('org_unit_group', 'Bereich', False), ('category', 'Kategorie', True),
                          ('preventability', 'Vermeidbarkeit', False),
                          ('risk', 'Risiko', False), ('frequency', 'Häufigkeit', False),
                          ('hazard', 'Gefährdung', False)])
        [measure] = report.measures
        self.assertEqual((measure.number, measure.reported, measure.title, measure.measures),
                         (self.b.pk, date(2026, 3, 5), 'Titel B (de)',
                          'Maßnahmen B\nzweite Zeile (de)'))
        self.assertEqual(measure.url, self.b.get_absolute_url())

    def test_the_report_cannot_be_changed_but_can_be_replaced(self):
        report = self.build(date(2026, 1, 1), date(2026, 10, 1))
        with self.assertRaises(FrozenInstanceError):
            report.incoming = 0
        self.assertEqual(replace(report, incoming=0).incoming, 0)
        self.assertEqual(report.incoming, 6)

    def test_a_period_is_whole_months_in_any_day_of_the_month(self):
        self.assertEqual(self.build(date(2026, 2, 28), date(2026, 3, 1)).period,
                         (date(2026, 2, 1), date(2026, 3, 31)))
        self.assertEqual(self.build(date(2028, 2, 10), date(2028, 2, 10)).period,
                         (date(2028, 2, 1), date(2028, 2, 29)))

    def test_a_period_that_is_no_evaluation_is_refused(self):
        with self.assertRaises(ValueError):
            self.build(date(2026, 5, 1), date(2026, 4, 1))
        with self.assertRaises(ValueError):
            self.build(date(2023, 1, 1), date(2026, 1, 1))
        self.build(date(2023, 1, 1), date(2025, 12, 1))  # 36 months

    def test_no_area_is_not_the_cell_not_specified(self):
        everything = self.build(date(2026, 1, 1), date(2026, 10, 1))
        none = self.build(date(2026, 1, 1), date(2026, 10, 1), group_id=None)
        self.assertEqual(everything, none)
        self.assertEqual(everything.incoming, 6)

    def test_an_area_narrows_the_report_but_not_the_log_that_it_reads(self):
        pflege = self.build(date(2026, 1, 1), date(2026, 10, 1), self.pflege.pk)
        self.assertEqual((pflege.incoming, pflege.completed, pflege.open_end, pflege.published),
                         (2, 0, 2, 0))
        # the log began on 20 December for all, though not for the incidents of this area
        self.assertEqual(pflege.protocol_start, date(2025, 12, 20))
        self.assertEqual([row.completed for row in pflege.monthly], [0] * 10)

    def test_the_title_and_the_measures_follow_the_active_language(self):
        with translation.override('en'):
            [measure] = self.build(date(2026, 1, 1), date(2026, 10, 1)).measures
        self.assertEqual((measure.title, measure.measures),
                         ('Titel B (en)', 'Maßnahmen B\nzweite Zeile (en)'))

    def test_the_distributions_are_made_of_buckets(self):
        with translation.override('en'):
            report = self.build(date(2026, 1, 1), date(2026, 10, 1))
        group = report.distributions[0]
        self.assertIsInstance(group, Distribution)
        self.assertEqual([(b.label, b.count) for b in group.buckets],
                         [('Labor', 2), ('Pflege', 2), ('Not specified', 2)])


class CanaryTest(ReportData):

    def test_nothing_of_the_foreign_department_shows_anywhere(self):
        for params in ({}, THIS_YEAR, {'von_monat': 1, 'von_jahr': 2026, 'bis_monat': 12,
                                       'bis_jahr': 2026}):
            html = self.html(params)
            self.assertNoCanary(html, params)

    def test_its_incidents_are_in_no_number(self):
        html = self.html(THIS_YEAR)
        self.assertEqual(figures(html)['Eingang'], '6')  # nine with the three of the foreign one
        self.assertEqual(figures(html)['Offen am Ende des Zeitraums'], '4')
        self.assertEqual(figures(html)['Veröffentlicht'], '1')
        for incident in self.canary_incidents:
            self.assertNotIn(incident.get_absolute_url(), html)
        self.assertNotIn('Kanarienvogel', ' '.join(label for _t, label in options(html, 'bereich')))

    def test_nor_in_the_measures_the_charts_or_the_tables(self):
        html = self.html(THIS_YEAR)
        for name in ('auswertung-massnahmen', 'auswertung-verteilungen', 'auswertung-verlauf'):
            self.assertNoCanary(tile(html, name), name)
        # the canary incidents are reported today, in this period: with them the group chart
        # would have a row for their group
        self.assertEqual([row[0] for row in tables(tile(html, 'auswertung-verteilungen'))[0][1:]],
                         ['Labor', 'Pflege', 'Keine Angabe'])


class PageTest(ReportData):

    def test_title_and_one_heading(self):
        html = self.html(THIS_YEAR)
        self.assertRegex(html, r'<title>Auswertungen · [^<]+</title>')
        self.assertEqual(re.findall(r'<h1[^>]*>(.*?)</h1>', html, re.S), ['Auswertungen'])

    def test_the_sections_have_their_headings_in_order(self):
        html = self.html(THIS_YEAR)
        self.assertEqual([words(h) for h in re.findall(r'<h2[^>]*>(.*?)</h2>', html, re.S)],
                         ['Kennzahlen', 'Monatsverlauf', 'Verteilungen', 'Maßnahmen', 'Begriffe'])
        self.assertEqual([words(h) for h in re.findall(r'<h3[^>]*>(.*?)</h3>', html, re.S)],
                         ['Bereich', 'Kategorie', 'Vermeidbarkeit', 'Risiko', 'Häufigkeit',
                          'Gefährdung'])

    def test_each_key_figure_is_defined_in_one_sentence(self):
        begriffe = tile(self.html(THIS_YEAR), 'auswertung-begriffe')
        terms = [words(t) for t in re.findall(r'<dt>(.*?)</dt>', begriffe, re.S)]
        self.assertEqual(terms, ['Eingang', 'Abgeschlossen', 'Offen am Ende des Zeitraums',
                                 'Veröffentlicht', 'Reaktionszeit', 'Bearbeitungsdauer'])
        sentences = [words(d) for d in re.findall(r'<dd>(.*?)</dd>', begriffe, re.S)]
        self.assertEqual(len(sentences), 6)
        for sentence in sentences:
            self.assertTrue(sentence.endswith('.'), sentence)
            self.assertEqual(sentence.count('. '), 0, sentence)

    def test_no_inline_code(self):
        self.assertEqual(csp_violations(self.html(THIS_YEAR)), [])
        self.assertEqual(csp_violations(self.html()), [])

    def test_all_ids_are_unique_and_every_reference_resolves_with_seven_charts(self):
        html = self.html(THIS_YEAR)
        self.assertEqual(html.count('role="img"'), 7)
        ids = re.findall(r'\sid="([^"]+)"', html)
        self.assertEqual(len(ids), len(set(ids)), sorted(ids))
        for attribute in ('aria-labelledby', 'aria-describedby'):
            for target in re.findall(r'%s="([^"]+)"' % attribute, html):
                for name in target.split():
                    self.assertIn(name, ids, '%s %s' % (attribute, target))
        for name in re.findall(r'<label[^>]*for="([^"]+)"', html):
            self.assertIn(name, ids)
        for chart in ('chart-verlauf', 'chart-org-unit-group', 'chart-category',
                      'chart-preventability', 'chart-risk', 'chart-frequency', 'chart-hazard'):
            self.assertIn(chart + '-title', ids)
            self.assertIn(chart + '-desc', ids)

    def test_every_class_has_a_rule(self):
        css = css_text()
        main = re.search(r'<main.*?</main>', self.html(THIS_YEAR), re.S).group(0)
        for token in set(' '.join(re.findall(r'\sclass="([^"]*)"', main)).split()):
            self.assertRegex(css, r'\.%s(?![\w-])' % re.escape(token), token)

    def test_the_new_rules_use_tokens_and_no_colour_values(self):
        css = css_text()
        section = css[css.index('QM evaluations and print view'):]
        self.assertNotRegex(section, r'#[0-9a-fA-F]{3,8}\b')
        self.assertNotRegex(section, r'rgba?\(')
        self.assertIn('var(--ui-', section)

    def test_the_texts_are_translatable(self):
        html = self.html(THIS_YEAR, **EN)
        for text in ('Key figures', 'Monthly trend', 'Distributions', 'Measures', 'Definitions',
                     'Evaluate', 'Quick selection', 'Last quarter', 'Current year', 'Last year',
                     'Open at the end of the period', 'Reaction time', 'Processing time',
                     'Median 3.5 days, count 4', 'Median 16 days, count 3',
                     'Several answers are possible', 'Period: January 2026 to October 2026'):
            self.assertIn(text, words(html))
        self.assertIn('aria-label="Choose the period"', html)
        self.assertEqual(re.findall(r'<legend>(.*?)</legend>', html), ['From', 'Until'])
        error = self.html({'von_monat': 5, 'von_jahr': 2026, 'bis_monat': 4, 'bis_jahr': 2026},
                          **EN)
        self.assertIn('The period ends before it begins.', error)
        self.assertIn('Please check your entries.', error)


class QueriesTest(ReportBase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.units = [OrgUnit.objects.create(name='Labor'), None,
                     OrgUnit.objects.create(name='Pflege')]

    def add(self, number):
        """An incident of its own kind. The first has a place, a log and a published case, so
        that the request of one incident goes the same ways as the request of thirty."""
        reported = date(2026, 1, 1) + timedelta(days=9 * number)
        start = datetime.combine(reported, time(12), tzinfo=dt_timezone.utc)
        status = ('new', 'in process', 'under supervision', 'completed')[number % 4]
        history = [('new', start)] + ([(status, start + timedelta(days=3))] if number % 4 else [])
        incident = make_incident(self.dept, reported=reported, history=history,
                                 org_unit=self.units[number % 3], preventability='avoidable',
                                 category=['other', 'infrastructure'][:number % 3],
                                 risk=('low', 'high', '')[number % 3])
        if number % 5 == 0:
            publish(incident, 'Titel %d' % number, 'Massnahme')

    def queries(self, params=None):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse('qm_reports'), params or {}, **DE)
        self.assertEqual(response.status_code, 200)
        return len(queries)

    def test_the_same_number_of_queries_for_one_and_for_thirty_incidents(self):
        self.add(0)
        self.queries()  # warms the caches that the first request fills
        one = self.queries()
        for number in range(1, 30):
            self.add(number)
        self.assertEqual(self.queries(), one)
        html = self.html()
        self.assertEqual(figures(html)['Eingang'], '30')
        self.assertEqual(figures(html)['Veröffentlicht'], '6')
        self.assertEqual(len(re.findall(r'<li>\s*<p class="ui-massnahmen__kopf">',
                                        tile(html, 'auswertung-massnahmen'))), 6)

    def test_the_same_for_an_area_and_a_longer_period(self):
        self.add(0)
        params = {'von_monat': 1, 'von_jahr': 2025, 'bis_monat': 12, 'bis_jahr': 2026,
                  'bereich': self.units[0].pk}
        self.queries(params)
        one = self.queries(params)
        for number in range(1, 30):
            self.add(number)
        self.assertEqual(self.queries(params), one)

    def test_the_report_asks_the_same_number_of_times_for_one_and_for_thirty_incidents(self):
        def asked():
            incidents = scoped_incidents(self.reviewer.user)
            with CaptureQueriesContext(connection) as queries:
                build_report(incidents, date(2026, 1, 1), date(2026, 10, 1), today=TODAY)
            return len(queries)
        self.add(0)
        asked()
        one = asked()
        for number in range(1, 30):
            self.add(number)
        self.assertEqual(asked(), one)
