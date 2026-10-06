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

"""The overview of the QM: five tiles that show the numbers of the data, link into the work list
with the parameters of worklist_url and never show a foreign department."""

import html as html_lib
import re
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from unittest import mock

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from model_bakery import baker

from cirs.models import OrgUnit
from cirs.qm.metrics import OPEN_STATUSES
from cirs.qm.params import incident_query, worklist_url

from .canary import CanaryMixin
from .helpers import csp_violations, make_incident
from .test_pages_report import css_text

TODAY = date(2026, 10, 2)
DE = {'HTTP_ACCEPT_LANGUAGE': 'de'}
EN = {'HTTP_ACCEPT_LANGUAGE': 'en'}
NOT_RECORDED = 'nicht erfasst'
MONTHS = ['Nov', 'Dez', 'Jan', 'Feb', 'Mär', 'Apr', 'Mai', 'Jun', 'Jul', 'Aug', 'Sep', 'Okt']


def at(month, day, year=2026, hour=12):
    return datetime(year, month, day, hour, tzinfo=dt_timezone.utc)


def words(fragment):
    """The text of a piece of HTML: no tags, one space between the words."""
    return ' '.join(html_lib.unescape(re.sub(r'<[^>]+>', ' ', fragment)).split())


def tile(html, name):
    return re.search(r'<section[^>]*aria-labelledby="%s".*?</section>' % name, html, re.S).group(0)


def tables(fragment):
    """The rows of each table in the fragment, as the text of their cells."""
    return [[[words(cell) for cell in re.findall(r'<t[hd][^>]*>(.*?)</t[hd]>', row, re.S)]
             for row in re.findall(r'<tr>(.*?)</tr>', table, re.S)]
            for table in re.findall(r'<table.*?</table>', fragment, re.S)]


def hrefs(fragment):
    return [html_lib.unescape(href) for href in re.findall(r'href="([^"]*)"', fragment)]


class OverviewBase(TestCase):
    """A department with a reviewer. The overview takes "today" from the clock of the time zone, so
    that is fixed, and the data is built around that day."""

    @classmethod
    def setUpTestData(cls):
        cls.dept = baker.make_recipe('cirs.department', name='Station Eins')
        cls.reviewer = baker.make_recipe('cirs.reviewer')
        cls.dept.reviewers.add(cls.reviewer)

    def setUp(self):
        self.enterContext(mock.patch.object(timezone, 'localdate', return_value=TODAY))
        self.client.force_login(self.reviewer.user)

    def html(self, **extra):
        return self.client.get(reverse('qm_overview'), **{**DE, **extra}).content.decode()


class OverviewTestCase(CanaryMixin, OverviewBase):
    """The department has incidents of every kind. A foreign department has some too, and the
    numbers below are right only if it is not counted anywhere."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.make_canary(reported=TODAY)
        reporter, qm = cls.dept.reporter.user, cls.reviewer.user
        cls.labor = OrgUnit.objects.create(name='Labor')
        station = OrgUnit.objects.create(name='Station A', parent=cls.labor)
        pflege = OrgUnit.objects.create(name='Pflege')

        def make(reported, history, **fields):
            return make_incident(cls.dept, reported=reported, history=history, **fields)

        make(TODAY, [('new', at(10, 2, hour=8))], org_unit=cls.labor)
        make(date(2026, 10, 1), [('new', at(10, 1))], org_unit=station)
        # the reporting person has the last word
        cls.waiting = make(date(2026, 9, 30), [('new', at(9, 30)), ('in process', at(10, 1))],
                           org_unit=pflege, comments=[(reporter, date(2026, 10, 1))])
        # the QM has answered
        make(date(2026, 9, 26), [('new', at(9, 26)), ('in process', at(9, 27))], org_unit=pflege,
             comments=[(reporter, date(2026, 9, 27)), (qm, date(2026, 9, 28))])
        make(date(2026, 9, 25), [('new', at(9, 25)), ('under supervision', at(9, 28))])
        make(date(2026, 9, 18), [('new', at(9, 18))])  # 14 days: not late yet
        cls.late = make(date(2026, 9, 17), [('new', at(9, 17))], org_unit=cls.labor)  # 15 days
        make(date(2026, 8, 15), [('new', at(8, 15)), ('completed', at(9, 2))])
        make(date(2026, 6, 10), [('new', at(6, 10)), ('completed', at(9, 15))],
             comments=[(reporter, date(2026, 9, 16))])  # completed: nobody waits for an answer


class TilesTest(OverviewTestCase):

    def test_what_is_new_counts_the_last_7_days_and_the_month(self):
        # 26 September to 2 October, and 1 and 2 October
        self.assertEqual(words(tile(self.html(), 'kachel-neu')),
                         'Was ist neu? Eingegangene Meldungen Letzte 7 Tage 4 Laufender Monat 2')

    def test_what_is_open_counts_each_open_state(self):
        offen = tile(self.html(), 'kachel-offen')
        self.assertIn('Neu 4 In Bearbeitung 2 Unter Beobachtung 1', words(offen))
        self.assertEqual(len(re.findall(r'<dt>', offen)), 3)  # completed is not open

    def test_the_states_link_into_the_work_list_with_the_parameters_of_worklist_url(self):
        self.assertEqual(hrefs(tile(self.html(), 'kachel-offen')),
                         [worklist_url(stand=status) for status in OPEN_STATUSES])

    def test_what_is_waiting_lists_the_late_incident_and_the_one_with_the_last_word(self):
        late, waiting = tables(tile(self.html(), 'kachel-wartet'))
        self.assertEqual(late, [['Nr.', 'Gemeldet', 'Wo'],
                                [str(self.late.pk), '17.09.2026', 'Labor']])
        self.assertEqual(waiting, [['Nr.', 'Gemeldet', 'Wo'],
                                   [str(self.waiting.pk), '30.09.2026', 'Pflege']])

    def test_each_row_links_to_its_incident_and_each_list_to_the_work_list(self):
        waiting = tile(self.html(), 'kachel-wartet')
        # an incident remembers the list it was opened from, so the way back keeps the filter
        self.assertEqual(hrefs(waiting),
                         [self.late.get_absolute_url() + incident_query(ohne_bearbeitung=1),
                          worklist_url(ohne_bearbeitung=1),
                          self.waiting.get_absolute_url() + incident_query(wartet=1),
                          worklist_url(wartet=1)])
        # a screen reader tells the two links apart by the list they lead to
        for name in ('Ohne Bearbeitung', 'Wartet auf QM'):
            self.assertIn('Alle anzeigen<span class="ui-visually-hidden">: %s</span></a>' % name,
                          waiting)

    def test_the_lists_say_what_they_hold(self):
        text = words(tile(self.html(), 'kachel-wartet'))
        self.assertIn('Ohne Bearbeitung Neu und älter als 14 Tage.', text)
        self.assertIn('Wartet auf QM Die letzte Rückmeldung stammt von der meldenden Person.',
                      text)

    def test_the_limit_of_days_is_the_one_of_the_setting(self):
        with self.settings(QM_OVERDUE_DAYS=1):
            self.assertIn('Neu und älter als 1 Tag.', words(tile(self.html(), 'kachel-wartet')))

    def test_a_list_shows_at_most_ten_rows_the_oldest_first(self):
        for day in range(1, 13):
            make_incident(self.dept, reported=date(2026, 8, day), history=[('new', at(8, day))])
        late, waiting = tables(tile(self.html(), 'kachel-wartet'))
        self.assertEqual([row[1] for row in late[1:]],
                         ['%02d.08.2026' % day for day in range(1, 11)])
        self.assertEqual(len(waiting), 2)

    def test_an_incident_without_a_place_says_so(self):
        make_incident(self.dept, reported=date(2026, 1, 5), history=[('new', at(1, 5))])
        late, _ = tables(tile(self.html(), 'kachel-wartet'))
        self.assertEqual(late[1][1:], ['05.01.2026', 'Keine Angabe'])

    def test_a_place_below_a_group_is_named_with_its_group(self):
        make_incident(self.dept, reported=date(2026, 1, 5), history=[('new', at(1, 5))],
                      org_unit=OrgUnit.objects.get(name='Station A'))
        late, _ = tables(tile(self.html(), 'kachel-wartet'))
        self.assertEqual(late[1][1:], ['05.01.2026', 'Labor › Station A'])

    def test_how_is_it_developing_has_a_row_for_each_of_the_last_12_months(self):
        # the log begins on 10 June: the months before it are not recorded, which is not 0
        [chart] = tables(tile(self.html(), 'kachel-verlauf'))
        self.assertEqual(chart[0], ['Monat', 'Eingang', 'Abgeschlossen'])
        self.assertEqual(
            chart[1:],
            [[month, '0', NOT_RECORDED] for month in MONTHS[:7]]
            + [['Jun', '1', '0'], ['Jul', '0', '0'], ['Aug', '1', '0'], ['Sep', '5', '2'],
               ['Okt', '2', '0']])

    def test_the_chart_is_named_and_described(self):
        verlauf = tile(self.html(), 'kachel-verlauf')
        self.assertIn('<title id="chart-verlauf-title">Eingang und Abgeschlossen pro Monat, letzte '
                      '12 Monate</title>', verlauf)
        self.assertIn('Meldungen in den letzten 12 Monaten: 9 eingegangen, 2 abgeschlossen. '
                      'Abschlüsse seit 10.06.2026 erfasst.', words(verlauf))

    def test_the_page_says_once_since_when_completions_are_recorded(self):
        html = self.html()
        shown = re.sub(r'<(title|desc)\b.*?</\1>', '', html, flags=re.S)  # not in the image text
        self.assertEqual(words(shown).count('Abschlüsse seit 10.06.2026 erfasst.'), 1)
        self.assertIn('Abschlüsse seit 10.06.2026 erfasst.', words(tile(html, 'kachel-verlauf')))

    def test_the_last_month_is_not_over(self):
        self.assertIn('Der letzte Monat läuft noch.', words(tile(self.html(), 'kachel-verlauf')))

    def test_months_that_are_not_recorded_are_marked_in_the_chart(self):
        # a dashed line on the zero line instead of a column, for the 7 months before the log
        self.assertEqual(self.html().count('ui-diagramm__fehlt-2'), 7)

    def test_where_does_it_happen_counts_each_group_and_those_without_a_place(self):
        [bars] = tables(tile(self.html(), 'kachel-wo'))
        self.assertEqual(bars, [['Wo', 'Anzahl'], ['Labor', '3'], ['Pflege', '2'],
                                ['Keine Angabe', '4']])

    def test_the_bars_are_named_and_described(self):
        wo = tile(self.html(), 'kachel-wo')
        self.assertIn('<span class="ui-visually-hidden" id="chart-wo-title">Meldungen je Bereich, '
                      'letzte 12 Monate</span>', wo)
        self.assertIn('am meisten: Keine Angabe mit 4 von 9.', words(wo))

    def test_the_numbers_are_the_real_ones(self):
        html = self.html()
        self.assertNotIn('< 3', html)
        self.assertNotIn('&lt; 3', html)


class ForeignDepartmentTest(OverviewTestCase):

    def test_nothing_of_the_foreign_department_shows(self):
        # its incidents are reported today, in process, with its place, and one waits for an answer
        self.assertNoCanary(self.html())

    def test_its_incidents_are_in_no_count(self):
        html = self.html()
        self.assertIn('Letzte 7 Tage 4 Laufender Monat 2', words(tile(html, 'kachel-neu')))
        self.assertIn('In Bearbeitung 2', words(tile(html, 'kachel-offen')))
        for incident in self.canary_incidents:
            self.assertNotIn(incident.get_absolute_url(), html)


class PageTest(OverviewTestCase):

    def test_title_and_one_heading(self):
        html = self.html()
        self.assertRegex(html, r'<title>Überblick · [^<]+</title>')
        self.assertEqual(re.findall(r'<h1[^>]*>(.*?)</h1>', html, re.S), ['Überblick'])

    def test_each_tile_asks_its_question(self):
        html = self.html()
        self.assertEqual([words(h2) for h2 in re.findall(r'<h2[^>]*>(.*?)</h2>', html, re.S)],
                         ['Was ist neu?', 'Was ist offen?', 'Was wartet auf uns?',
                          'Wie entwickelt es sich?', 'Wo passiert es?'])

    def test_no_inline_code(self):
        self.assertEqual(csp_violations(self.html()), [])

    def test_the_ids_of_the_two_charts_and_the_tiles_are_unique_and_resolve(self):
        html = self.html()
        ids = re.findall(r'\sid="([^"]+)"', html)
        self.assertEqual(len(ids), len(set(ids)), sorted(ids))
        for target in re.findall(r'aria-labelledby="([^"]+)"', html):
            for name in target.split():
                self.assertIn(name, ids, target)
        self.assertIn('chart-verlauf-title', ids)
        self.assertIn('chart-wo-title', ids)

    def test_every_class_has_a_rule(self):
        css = css_text()
        main = re.search(r'<main.*?</main>', self.html(), re.S).group(0)
        for token in set(' '.join(re.findall(r'\sclass="([^"]*)"', main)).split()):
            self.assertRegex(css, r'\.%s(?![\w-])' % re.escape(token), token)

    def test_the_texts_are_translatable(self):
        html = self.html(**EN)
        for text in ('What is new?', 'What is open?', 'What is waiting for us?',
                     'How is it developing?', 'Where does it happen?', 'Completions recorded since',
                     'Without processing', 'Show all'):
            self.assertIn(text, html)


class RecordedMonthsTest(OverviewBase):

    def test_when_the_log_is_old_enough_nothing_is_missing(self):
        make_incident(self.dept, reported=date(2025, 1, 5),
                      history=[('new', at(1, 5, 2025)), ('completed', at(2, 1, 2025))])
        make_incident(self.dept, reported=date(2026, 10, 1), history=[('new', at(10, 1))])
        html = self.html()
        [chart] = tables(tile(html, 'kachel-verlauf'))
        self.assertEqual([row[2] for row in chart[1:]], ['0'] * 12)
        self.assertNotIn(NOT_RECORDED, html)
        self.assertNotIn('Abschlüsse seit', html)

    def test_without_any_log_no_month_is_recorded_and_no_day_can_be_named(self):
        make_incident(self.dept, reported=date(2026, 9, 1), legacy=True)
        html = self.html()
        [chart] = tables(tile(html, 'kachel-verlauf'))
        self.assertEqual([row[1:] for row in chart[1:]],
                         [['1' if month == 'Sep' else '0', NOT_RECORDED] for month in MONTHS])
        self.assertIn('Das Protokoll läuft noch nicht.', words(html))
        self.assertNotIn('Abschlüsse seit', html)


class EmptyOverviewTest(OverviewBase):

    def test_every_tile_has_its_empty_state(self):
        html = self.html()
        self.assertEqual(words(tile(html, 'kachel-neu')),
                         'Was ist neu? Eingegangene Meldungen Letzte 7 Tage 0 Laufender Monat 0')
        self.assertIn('Neu 0 In Bearbeitung 0 Unter Beobachtung 0',
                      words(tile(html, 'kachel-offen')))
        waiting = tile(html, 'kachel-wartet')
        self.assertIn('Ohne Bearbeitung', words(waiting))
        self.assertIn('Nichts offen.', words(waiting))
        self.assertIn('Keine Rückmeldungen offen.', words(waiting))
        self.assertNotIn('<table', waiting)
        self.assertNotIn('Alle anzeigen', waiting)
        for name in ('kachel-verlauf', 'kachel-wo'):
            self.assertIn('Keine Meldungen in den letzten 12 Monaten.', words(tile(html, name)))
            self.assertNotIn('<table', tile(html, name))
        self.assertEqual(csp_violations(html), [])

    def test_a_year_without_incidents_is_empty_too(self):
        make_incident(self.dept, reported=date(2024, 1, 5), history=[('new', at(1, 5, 2024))])
        html = self.html()
        for name in ('kachel-verlauf', 'kachel-wo'):
            self.assertIn('Keine Meldungen in den letzten 12 Monaten.', words(tile(html, name)))


class QueriesTest(OverviewBase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.units = [OrgUnit.objects.create(name='Labor'), None,
                     OrgUnit.objects.create(name='Pflege')]
        cls.authors = [cls.dept.reporter.user, cls.reviewer.user]

    def add(self, number):
        """An incident of its own kind: the first is late, waits for the QM and has a place."""
        reported = TODAY - timedelta(days=30 + 15 * number)
        start = datetime.combine(reported, time(12), tzinfo=dt_timezone.utc)
        status = ('new', 'in process', 'under supervision', 'completed')[number % 4]
        history = [('new', start)] + ([(status, start + timedelta(days=3))] if number % 4 else [])
        make_incident(self.dept, reported=reported, history=history,
                      org_unit=self.units[number % 3],
                      comments=[(self.authors[number % 2], reported)])

    def queries(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse('qm_overview'), **DE)
        self.assertEqual(response.status_code, 200)
        return len(queries)

    def test_the_same_number_of_queries_for_one_and_for_thirty_incidents(self):
        self.add(0)
        self.queries()  # warms the caches that the first request fills
        html = self.html()
        self.assertEqual([len(table) for table in tables(tile(html, 'kachel-wartet'))], [2, 2])
        one = self.queries()
        for number in range(1, 30):
            self.add(number)
        self.assertEqual(self.queries(), one)
        self.assertGreater(len(tables(tile(self.html(), 'kachel-wartet'))[0]), 2)
