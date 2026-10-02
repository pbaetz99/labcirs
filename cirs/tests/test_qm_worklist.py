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

"""The work list of the QM: its filters, sorting and paging, the notes of a row, what a QM of
several departments sees, and the numbers behind the overview. A foreign department never shows,
and the page costs the same queries for one incident as for thirty. Test data is constructed,
never taken from a real report."""

import html as html_lib
import re
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from unittest import mock

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from model_bakery import baker

from cirs.models import OrgUnit
from cirs.qm import metrics
from cirs.qm.metrics import annotate_workflow, awaiting_qm, monthly, overdue, protocol_start
from cirs.qm.params import FILTERS, NO_PLACE, SORTS, worklist_url
from cirs.qm.views_worklist import NARROWINGS, PAGE_SIZE

from .canary import CANARY, CanaryMixin
from .helpers import create_user, csp_violations, make_incident
from .test_pages_report import css_text
from .test_qm_metrics_log import LogTestCase, utc, vienna
from .test_qm_overview import TODAY, at, tables, tile, words

DE = {'HTTP_ACCEPT_LANGUAGE': 'de'}
EN = {'HTTP_ACCEPT_LANGUAGE': 'en'}
LIST_COLUMNS = ['Nr.', 'Gemeldet', 'Stand', 'Wo', 'Kategorie', 'Risiko', 'Letzte Aktivität',
                'Hinweise', 'Bearbeiten']


def listed(html):
    """The numbers of the incidents the page lists, in the order of the list."""
    return [int(number) for number in
            re.findall(r'<td class="ui-strong"><a href="[^"]*">(\d+)</a>', html)]


def rows_of(html):
    """The table of the list as the text of its cells: the head, then a row for each incident."""
    [rows] = tables(html)  # the page has this one table
    return rows


def headings(html):
    """The head of the table: (name, aria-sort, address of the link) of each column. The name is
    the text of the link if the heading is one, the address is None otherwise."""
    head = re.search(r'<thead>.*?</thead>', html, re.S).group(0)
    found = []
    for attributes, inner in re.findall(r'<th scope="col"([^>]*)>(.*?)</th>', head, re.S):
        sort = re.search(r'aria-sort="(\w+)"', attributes)
        link = re.search(r'<a href="([^"]*)">([^<]*)<span', inner)
        found.append((link.group(2) if link else words(inner), sort.group(1) if sort else None,
                      html_lib.unescape(link.group(1)) if link else None))
    return found


def filter_state(html):
    """What the box of the filter state says, or '' if the page has none."""
    box = re.search(r'<div class="ui-filterstand.*?</div>', html, re.S)
    return words(box.group(0)) if box else ''


def pks(*incidents):
    return [incident.pk for incident in incidents]


class WorklistBase(TestCase):
    """A department with a reviewer. The list takes "today" from the clock of the time zone, so
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
        return self.client.get(reverse('qm_incidents'), params or {}, **{**DE, **extra})

    def html(self, params=None, **extra):
        response = self.get(params, **extra)
        self.assertEqual(response.status_code, 200, params)
        return response.content.decode()

    def ids(self, **params):
        return listed(self.html(params))


class WorklistData(CanaryMixin, WorklistBase):
    """The department has five incidents of every kind. A foreign department has some too, and
    nothing below is right if one of them is counted or shown."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.make_canary(reported=TODAY)
        cls.reporter, cls.qm = cls.dept.reporter.user, cls.reviewer.user
        cls.labor = OrgUnit.objects.create(name='Labor')
        cls.station = OrgUnit.objects.create(name='Station A', parent=cls.labor)
        cls.pflege = OrgUnit.objects.create(name='Pflege')

        def make(reported, history, **fields):
            return make_incident(cls.dept, reported=reported, history=history, **fields)

        cls.fresh = make(date(2026, 10, 1), [('new', at(10, 1))], org_unit=cls.labor,
                         incident='Etikett fehlt')
        # the reporting person has the last word
        cls.waiting = make(date(2026, 9, 30), [('new', at(9, 30)), ('in process', at(10, 1))],
                           org_unit=cls.station, risk='high', category=['infrastructure'],
                           incident='Kühlschrank defekt',
                           comments=[(cls.reporter, date(2026, 10, 1))])
        cls.late = make(date(2026, 9, 10), [('new', at(9, 10))], org_unit=cls.pflege, risk='low',
                        category=['knowledge/training', 'other'], incident='Probe vertauscht')
        cls.done = make(date(2026, 8, 1), [('new', at(8, 1)), ('completed', at(8, 20))],
                        risk='middle', category=['other'], incident='Alles geklärt')
        # the QM has answered
        cls.watched = make(date(2026, 9, 26),
                           [('new', at(9, 26)), ('under supervision', at(9, 28))],
                           org_unit=cls.pflege, risk='high', category=['technique/methods'],
                           incident='Gerät gestört',
                           comments=[(cls.reporter, date(2026, 9, 27)),
                                     (cls.qm, date(2026, 9, 28))])
        cls.everyone = [cls.fresh, cls.waiting, cls.late, cls.done, cls.watched]


class FilterTest(WorklistData):

    def check(self, expected, **params):
        self.assertCountEqual(self.ids(**params), pks(*expected), params)

    def test_without_a_filter_every_incident_of_the_department_is_listed(self):
        self.check(self.everyone)

    def test_every_filter_of_the_parameters_narrows_the_list(self):
        self.assertEqual(set(NARROWINGS), set(FILTERS))

    def test_the_status(self):
        self.check([self.fresh, self.late], stand='new')
        self.check([self.waiting], stand='in process')
        self.check([self.watched], stand='under supervision')
        self.check([self.done], stand='completed')

    def test_a_group_includes_its_units(self):
        self.check([self.fresh, self.waiting], wo=self.labor.pk)
        self.check([self.late, self.watched], wo=self.pflege.pk)

    def test_no_place(self):
        self.check([self.done], wo=NO_PLACE)

    def test_the_category(self):
        self.check([self.late, self.done], kategorie='other')
        self.check([self.late], kategorie='knowledge/training')
        self.check([self.waiting], kategorie='infrastructure')
        self.check([self.watched], kategorie='technique/methods')
        self.check([], kategorie='organisation/communication')

    def test_a_category_is_found_wherever_it_stands_in_the_list_of_categories(self):
        three = make_incident(self.dept, reported=TODAY,
                              category=['infrastructure', 'other', 'technique/methods'])
        for key in ('infrastructure', 'other', 'technique/methods'):
            self.assertIn(three.pk, self.ids(kategorie=key), key)
        self.assertNotIn(three.pk, self.ids(kategorie='knowledge/training'))

    def test_the_risk(self):
        self.check([self.waiting, self.watched], risiko='high')
        self.check([self.late], risiko='low')
        self.check([self.done], risiko='middle')

    def test_the_days_of_the_period_are_both_included(self):
        self.check([self.fresh, self.waiting], von='2026-09-30')
        self.check([self.late, self.done, self.watched], bis='2026-09-26')
        self.check([self.waiting, self.watched], von='2026-09-26', bis='2026-09-30')
        self.check([self.waiting], von='2026-09-30', bis='2026-09-30')
        self.check([], von='2026-10-01', bis='2026-09-01')

    def test_waiting_for_the_qm(self):
        self.check([self.waiting], wartet='1')

    def test_without_processing(self):
        self.check([self.late], ohne_bearbeitung='1')

    def test_both_notes_together_leave_nothing_here(self):
        self.check([], wartet='1', ohne_bearbeitung='1')

    def test_the_search_term_is_looked_for_in_the_text_of_the_incident(self):
        self.check([self.fresh], q='Etikett')
        self.check([self.fresh], q='etikett')
        self.check([self.fresh], q='  Etikett ')
        self.check([self.waiting], q='Kühl')
        self.check([self.late], q='Probe')

    def test_the_signs_of_a_pattern_are_no_pattern(self):
        self.check([], q='%')
        self.check([], q='_')

    def test_filters_combined(self):
        self.check([self.late], stand='new', wo=self.pflege.pk)
        self.check([self.late], stand='new', ohne_bearbeitung='1')
        self.check([self.waiting], stand='in process', wartet='1', risiko='high',
                   kategorie='infrastructure', wo=self.labor.pk, von='2026-09-30',
                   bis='2026-10-01', q='Kühl')
        self.check([self.watched], risiko='high', wo=self.pflege.pk)
        self.check([], stand='new', risiko='high')

    def test_an_unknown_parameter_is_no_business_of_the_list(self):
        html = self.html({'utm_source': 'x', 'stand': 'new'})
        self.assertCountEqual(listed(html), pks(self.fresh, self.late))
        self.assertNotIn('ignoriert', html)

    def test_the_form_shows_what_is_chosen(self):
        html = self.html({'stand': 'new', 'wo': self.pflege.pk, 'kategorie': 'other',
                          'risiko': 'low', 'von': '2026-09-01', 'bis': '2026-09-30',
                          'wartet': '1', 'ohne_bearbeitung': '1', 'q': 'Probe'})
        for name, value in (('stand', 'new'), ('wo', str(self.pflege.pk)), ('kategorie', 'other'),
                            ('risiko', 'low')):
            self.assertRegex(html, r'<option value="%s" selected>' % re.escape(value), name)
        for name, value in (('von', '2026-09-01'), ('bis', '2026-09-30'), ('q', 'Probe')):
            self.assertIn('name="%s" value="%s"' % (name, value), html)
        for name in ('wartet', 'ohne_bearbeitung'):
            self.assertRegex(html, r'name="%s" value="1" checked' % name)
        self.assertNotIn('ignoriert', html)

    def test_the_filter_state_counts_what_matches(self):
        self.assertIn('2 Meldungen entsprechen Ihren Filtern.',
                      filter_state(self.html({'stand': 'new'})))
        self.assertIn('1 Meldung entspricht Ihren Filtern.',
                      filter_state(self.html({'stand': 'completed'})))
        self.assertEqual(filter_state(self.html()), '')  # no filter, no state

    def test_the_filter_state_leads_back_to_the_plain_list(self):
        html = self.html({'stand': 'new', 'sort': 'nr'})
        self.assertIn('<a class="ui-btn ui-btn--quiet" href="%s">Filter aufheben</a>'
                      % worklist_url(sort='nr'), html)

    def test_an_empty_result_has_its_state_and_a_way_out(self):
        html = self.html({'stand': 'new', 'risiko': 'high'})
        self.assertEqual(listed(html), [])
        self.assertNotIn('<table', html)
        self.assertIn('Keine Meldung entspricht Ihren Filtern.', words(html))
        self.assertIn('href="%s">Filter aufheben</a>' % worklist_url(), html)


class TilesMatchTheListTest(WorklistData):
    """What the overview counts and what the list behind each count shows are the same incidents."""

    def overview(self):
        return self.client.get(reverse('qm_overview'), **DE).content.decode()

    def behind(self, url):
        response = self.client.get(html_lib.unescape(url), **DE)
        self.assertEqual(response.status_code, 200, url)
        return response.context['paginator'].count

    def test_the_count_of_each_open_state_is_the_number_of_rows_behind_its_link(self):
        for number in range(1, 4):  # more of every state
            for status in ('new', 'in process', 'under supervision'):
                changes = [(status, at(8, number))] if status != 'new' else []
                make_incident(self.dept, reported=date(2026, 7, number),
                              history=[('new', at(7, number))] + changes)
        found = re.findall(r'<a href="([^"]*)">[^<]*</a></dt><dd>(\d+)</dd>',
                           tile(self.overview(), 'kachel-offen'))
        self.assertEqual([int(count) for _url, count in found], [2 + 3, 1 + 3, 1 + 3])
        for url, count in found:
            self.assertEqual(self.behind(url), int(count), url)

    def test_the_lists_of_what_waits_are_the_rows_behind_their_links(self):
        waiting = tile(self.overview(), 'kachel-wartet')
        links = re.findall(r'<a href="([^"]*)">Alle anzeigen', waiting)
        late, awaiting = tables(waiting)
        self.assertEqual([self.behind(url) for url in links], [len(late) - 1, len(awaiting) - 1])
        self.assertEqual([len(late) - 1, len(awaiting) - 1], [1, 1])

    def test_the_list_has_them_all_where_the_tile_shows_ten(self):
        for day in range(1, 13):
            make_incident(self.dept, reported=date(2026, 8, day), history=[('new', at(8, day))])
        waiting = tile(self.overview(), 'kachel-wartet')
        late = tables(waiting)[0]
        self.assertEqual(len(late) - 1, 10)
        url = re.findall(r'<a href="([^"]*)">Alle anzeigen', waiting)[0]
        self.assertEqual(self.behind(url), 13)
        scope = self.dept.criticalincident_set.all()
        self.assertEqual(self.behind(url), overdue(scope, TODAY, 14).count())

    def test_the_list_and_the_metrics_agree_on_the_notes(self):
        scope = self.dept.criticalincident_set.all()
        self.assertEqual(self.behind(worklist_url(wartet=1)), awaiting_qm(scope).count())
        self.assertEqual(self.behind(worklist_url(ohne_bearbeitung=1)),
                         overdue(scope, TODAY, 14).count())


class SortingTest(WorklistBase):
    """Many incidents of one day: the order must still give every one of them once."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.same_day = [make_incident(cls.dept, reported=date(2026, 9, 1),
                                      history=[('new', at(9, 1))])
                        for _ in range(PAGE_SIZE + 1)]

    def test_every_sort_gives_every_incident_once_on_the_two_pages(self):
        everyone = sorted(pks(*self.same_day))
        for key in SORTS:
            for sort, expected in ((key, everyone), ('-' + key, everyone[::-1])):
                with self.subTest(sort=sort):
                    first = listed(self.html({'sort': sort}))
                    second = listed(self.html({'sort': sort, 'page': 2}))
                    self.assertEqual((len(first), len(second)), (PAGE_SIZE, 1))
                    self.assertEqual(len(set(first + second)), PAGE_SIZE + 1)
                    self.assertEqual(first + second, expected)  # the id breaks the tie

    def test_the_headings_that_sort_keep_the_filter_and_start_again_at_the_first_page(self):
        found = headings(self.html({'stand': 'new', 'page': 2}))
        self.assertEqual([(name, sort) for name, sort, url in found if url is not None],
                         [('Nr.', None), ('Gemeldet', 'descending'), ('Letzte Aktivität', None)])
        self.assertEqual([url for _name, _sort, url in found if url],
                         [worklist_url(stand='new', sort='nr'),
                          worklist_url(stand='new', sort='gemeldet'),
                          worklist_url(stand='new', sort='aktivitaet')])

    def test_the_heading_that_is_sorted_by_turns_the_order_round(self):
        for sort, state, link in (('nr', 'ascending', '-nr'), ('-nr', 'descending', 'nr'),
                                  ('aktivitaet', 'ascending', '-aktivitaet'),
                                  ('-aktivitaet', 'descending', 'aktivitaet')):
            current = [heading for heading in headings(self.html({'sort': sort}))
                       if heading[1] is not None]
            self.assertEqual(len(current), 1, sort)  # only one column is the sorted one
            self.assertEqual(current[0][1:], (state, worklist_url(sort=link)), sort)


class SortOrderTest(WorklistBase):

    def setUp(self):
        super().setUp()
        qm = self.reviewer.user
        self.one = make_incident(self.dept, reported=date(2026, 9, 1), history=[('new', at(9, 1))],
                                 comments=[(qm, date(2026, 9, 20))])
        self.two = make_incident(self.dept, reported=date(2026, 9, 10),
                                 history=[('new', at(9, 10))])
        self.three = make_incident(self.dept, reported=date(2026, 9, 5),
                                   history=[('new', at(9, 5)), ('in process', at(9, 25))])

    def test_by_the_number(self):
        self.assertEqual(self.ids(sort='nr'), pks(self.one, self.two, self.three))
        self.assertEqual(self.ids(sort='-nr'), pks(self.three, self.two, self.one))

    def test_by_the_day_of_the_report(self):
        self.assertEqual(self.ids(sort='gemeldet'), pks(self.one, self.three, self.two))
        self.assertEqual(self.ids(sort='-gemeldet'), pks(self.two, self.three, self.one))

    def test_by_the_last_activity(self):
        # a comment of the 20th, an entry of the log on the 10th, a change of the status on the 25th
        self.assertEqual(self.ids(sort='aktivitaet'), pks(self.two, self.one, self.three))
        self.assertEqual(self.ids(sort='-aktivitaet'), pks(self.three, self.one, self.two))

    def test_the_newest_report_comes_first_without_a_sort(self):
        self.assertEqual(self.ids(), pks(self.two, self.three, self.one))
        self.assertEqual(headings(self.html())[1][1], 'descending')

    def test_the_last_activity_is_shown(self):
        rows = {row[0]: row[6] for row in rows_of(self.html())[1:]}
        self.assertEqual(rows, {str(self.one.pk): '20.09.2026', str(self.two.pk): '10.09.2026',
                                str(self.three.pk): '25.09.2026'})


class PagingTest(WorklistBase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.many = [make_incident(cls.dept, reported=date(2026, 9, 1), history=[('new', at(9, 1))])
                    for _ in range(PAGE_SIZE + 1)]

    def test_26_incidents_are_two_pages(self):
        first, second = self.html(), self.html({'page': 2})
        self.assertEqual((len(listed(first)), len(listed(second))), (25, 1))
        self.assertEqual(len(set(listed(first) + listed(second))), 26)

    def test_the_pager_says_where_it_is_and_leads_on(self):
        first = self.html({'stand': 'new'})
        self.assertIn('Seite 1 von 2', words(first))
        self.assertIn('href="%s" rel="next">Nächste Seite</a>'
                      % html_lib.escape(worklist_url(stand='new', page=2)), first)
        self.assertNotIn('Vorherige Seite', first)
        second = self.html({'stand': 'new', 'page': 2})
        self.assertIn('Seite 2 von 2', words(second))
        self.assertIn('href="%s" rel="prev">Vorherige Seite</a>'
                      % html_lib.escape(worklist_url(stand='new', page=1)), second)
        self.assertNotIn('Nächste Seite', second)

    def test_a_list_of_one_page_has_no_pager(self):
        html = self.html({'stand': 'completed'})
        self.assertNotIn('Seite 1 von', html)
        self.assertNotIn('rel="next"', html)

    def test_a_page_that_does_not_exist_is_a_404_never_a_500(self):
        for page in ('0', '-1', 'abc', '999', '3', '1.5', 'last', '٢', '1e3',
                     '99999999999999999999'):
            with self.subTest(page=page):
                self.assertEqual(self.get({'page': page}).status_code, 404)

    def test_the_pages_that_exist_are_200(self):
        for page in ('', '1', '2'):
            self.assertEqual(self.get({'page': page}).status_code, 200, page)

    def test_without_incidents_there_is_a_first_page_and_no_second(self):
        for incident in self.many:
            incident.delete()
        self.assertEqual(self.get({'page': 1}).status_code, 200)
        self.assertEqual(self.get({'page': 2}).status_code, 404)
        self.assertIn('Es gibt noch keine Meldungen.', words(self.html()))


class IgnoredParametersTest(WorklistData):

    def check(self, note, **params):
        """The page answers 200, names the parameter and lists everything, as without it."""
        html = self.html(params)
        self.assertIn(note, filter_state(html), params)
        self.assertCountEqual(listed(html), pks(*self.everyone), params)

    def test_a_day_that_is_none_is_named(self):
        for name in ('von', 'bis'):
            for text in ('31.01.2026', '2026-02-30', '20260131', 'x', '2026-1-5'):
                self.check('Der Filter „%s“ wurde ignoriert: ungültiges Datum.' % name,
                           **{name: text})

    def test_a_value_out_of_range_is_named(self):
        for name, text in (('stand', 'done'), ('stand', 'In Process'), ('kategorie', 'x'),
                           ('risiko', 'extreme'), ('wartet', '2'), ('wartet', 'yes'),
                           ('ohne_bearbeitung', 'true')):
            self.check('Der Filter „%s“ wurde ignoriert: ungültiger Wert.' % name,
                       **{name: text})

    def test_a_search_term_that_is_too_long_is_named(self):
        self.check('Der Filter „q“ wurde ignoriert: zu lang.', q='x' * 201)

    def test_a_sort_that_is_none_is_named(self):
        for text in ('size', '--nr', '+nr', 'NR'):
            self.check('Die Sortierung wurde ignoriert: ungültiger Wert.', sort=text)

    def test_a_place_that_is_no_group_of_the_incidents_is_named(self):
        canary_group = OrgUnit.objects.get(name=CANARY + '-Gruppe')
        for text in ('abc', '0', '-1', str(self.station.pk), '999999', str(canary_group.pk)):
            self.check('Der Filter „wo“ wurde ignoriert: unbekannter Bereich.', wo=text)

    def test_several_are_all_named_the_valid_ones_still_narrow_the_list(self):
        html = self.html({'stand': 'new', 'von': 'x', 'bis': 'y', 'sort': 'size', 'q': 'Probe'})
        state = filter_state(html)
        for note in ('Der Filter „von“ wurde ignoriert: ungültiges Datum.',
                     'Der Filter „bis“ wurde ignoriert: ungültiges Datum.',
                     'Die Sortierung wurde ignoriert: ungültiger Wert.'):
            self.assertEqual(state.count(note), 1, note)
        self.assertEqual(listed(html), pks(self.late))

    def test_an_empty_value_is_no_filter_and_is_not_named(self):
        html = self.html({'stand': '', 'von': '', 'q': ' ', 'wartet': '', 'sort': ''})
        self.assertNotIn('ignoriert', html)
        self.assertEqual(filter_state(html), '')

    def test_what_was_left_out_is_not_in_the_links_of_the_page(self):
        html = self.html({'stand': 'new', 'von': 'x', 'sort': 'size'})
        self.assertEqual([url for _name, _sort, url in headings(html) if url],
                         [worklist_url(stand='new', sort=key) for key in SORTS])
        # not the whole page: the language switch of the top bar carries the address as asked
        page = re.search(r'<main.*?</main>', html, re.S).group(0)
        self.assertNotIn('von=x', page)
        self.assertNotIn('sort=size', page)
        self.assertIn('href="%s">Filter aufheben</a>' % worklist_url(), html)


class SearchTest(WorklistData):

    def test_the_search_term_is_echoed_escaped(self):
        for term in ('<script>alert(1)</script>', '"><img src=x onerror=alert(1)>',
                     "'; DROP TABLE cirs_criticalincident; --"):
            with self.subTest(term=term):
                html = self.html({'q': term})
                escaped = html_lib.escape(term)
                self.assertNotIn('<script>alert', html)
                self.assertNotRegex(html, r'<img[^>]*onerror')
                self.assertIn('name="q" value="%s"' % escaped, html)  # in the field
                self.assertIn('Suche nach „%s“ im Text der Meldungen.' % escaped, html)  # in the state
                self.assertEqual(listed(html), [])

    def test_the_echo_of_a_script_is_no_inline_code(self):
        self.assertEqual(csp_violations(self.html({'q': '<script>alert(1)</script>'})), [])

    def test_a_search_for_the_foreign_department_finds_nothing_of_it(self):
        html = self.html({'q': CANARY})
        self.assertEqual(listed(html), [])
        self.assertIn('Keine Meldung entspricht Ihren Filtern.', words(html))


class PlaceFilterTest(WorklistData):

    def options(self, html):
        select = re.search(r'<select[^>]*name="wo".*?</select>', html, re.S).group(0)
        return re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)</option>', select)

    def test_the_choice_is_the_groups_of_the_incidents_and_no_place(self):
        self.assertEqual(self.options(self.html()),
                         [('', 'Alle'), (str(self.labor.pk), 'Labor'),
                          (str(self.pflege.pk), 'Pflege'), (NO_PLACE, 'Keine Angabe')])

    def test_a_unit_below_a_group_is_no_choice_and_no_value(self):
        self.assertNotIn(('%d' % self.station.pk, 'Station A'), self.options(self.html()))
        html = self.html({'wo': self.station.pk})
        self.assertCountEqual(listed(html), pks(*self.everyone))  # not filtered, and it says so
        self.assertIn('Der Filter „wo“ wurde ignoriert', filter_state(html))

    def test_a_group_without_incidents_is_no_choice(self):
        empty = OrgUnit.objects.create(name='Leer')
        self.assertNotIn(str(empty.pk), [value for value, _ in self.options(self.html())])
        state = filter_state(self.html({'wo': empty.pk}))
        self.assertIn('Der Filter „wo“ wurde ignoriert', state)

    def test_the_groups_of_others_are_never_offered(self):
        html = self.html()
        self.assertNotIn(CANARY, html)
        canary_group = OrgUnit.objects.get(name=CANARY + '-Gruppe')
        self.assertNotIn(str(canary_group.pk), [value for value, _ in self.options(html)])

    def test_the_chosen_place_is_selected(self):
        self.assertIn('<option value="%s" selected>' % NO_PLACE, self.html({'wo': NO_PLACE}))
        self.assertIn('<option value="%d" selected>' % self.labor.pk,
                      self.html({'wo': self.labor.pk}))


class DepartmentColumnTest(CanaryMixin, WorklistBase):
    """Only a QM of more than one department sees whose incident a row is."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.make_canary(reported=TODAY)
        cls.second = baker.make_recipe('cirs.department', name='Station Zwei')
        cls.second.reviewers.add(cls.reviewer)
        # both are late and the reporting persons have the last word, so they are in every list
        cls.in_first = make_incident(cls.dept, reported=date(2026, 9, 1),
                                     history=[('new', at(9, 1))],
                                     comments=[(cls.dept.reporter.user, date(2026, 9, 2))])
        cls.in_second = make_incident(cls.second, reported=date(2026, 9, 2),
                                      history=[('new', at(9, 2))],
                                      comments=[(cls.second.reporter.user, date(2026, 9, 3))])

    def overview(self):
        return self.client.get(reverse('qm_overview'), **DE).content.decode()

    def test_the_list_names_the_department_of_each_row(self):
        html = self.html()
        self.assertEqual([name for name, _sort, _url in headings(html)][:5],
                         ['Nr.', 'Gemeldet', 'Stand', 'Abteilung', 'Wo'])
        self.assertEqual({row[0]: row[3] for row in rows_of(html)[1:]},
                         {str(self.in_first.pk): 'Station Eins',
                          str(self.in_second.pk): 'Station Zwei'})

    def test_the_lists_of_the_overview_name_it_too(self):
        late, awaiting = tables(tile(self.overview(), 'kachel-wartet'))
        for rows in (late, awaiting):
            self.assertEqual(rows[0], ['Nr.', 'Gemeldet', 'Abteilung', 'Wo'])
            self.assertEqual({row[0]: row[2] for row in rows[1:]},
                             {str(self.in_first.pk): 'Station Eins',
                              str(self.in_second.pk): 'Station Zwei'})

    def test_the_department_of_somebody_else_never_shows(self):
        for html in (self.html(), self.overview(), self.html({'q': 'Meldung'})):
            self.assertNoCanary(html)
        html = self.html()
        self.assertCountEqual(listed(html), pks(self.in_first, self.in_second))
        self.assertTrue(set(pks(*self.canary_incidents)).isdisjoint(listed(html)))

    def test_a_qm_of_one_department_sees_no_such_column(self):
        self.second.reviewers.remove(self.reviewer)
        self.assertEqual(listed(self.html()), pks(self.in_first))
        self.assertEqual([name for name, _sort, _url in headings(self.html())], LIST_COLUMNS)
        for rows in tables(tile(self.overview(), 'kachel-wartet')):
            self.assertNotIn('Abteilung', rows[0])
            self.assertEqual(rows[0], ['Nr.', 'Gemeldet', 'Wo'])


class ForeignDepartmentTest(WorklistData):

    def test_nothing_of_the_foreign_department_shows_on_any_page_of_the_list(self):
        queries = ({}, {'stand': 'in process'}, {'kategorie': 'knowledge/training'},
                   {'risiko': 'high'}, {'von': '2026-10-02'}, {'wartet': '1'},
                   {'ohne_bearbeitung': '1'}, {'sort': 'aktivitaet'}, {'sort': '-nr'},
                   {'wo': OrgUnit.objects.get(name=CANARY + '-Gruppe').pk}, {'q': 'Meldung'},
                   {'page': 1})
        for query in queries:
            self.assertNoCanary(self.html(query), query)

    def test_its_incidents_are_in_no_count_and_no_row(self):
        # they are in process and high, reported today, with a category of the incidents below
        response = self.get({'von': TODAY.isoformat()})
        self.assertEqual(response.context['paginator'].count, 0)
        self.assertEqual(self.ids(stand='in process'), pks(self.waiting))
        self.assertCountEqual(self.ids(risiko='high'), pks(self.waiting, self.watched))
        self.assertEqual(self.ids(kategorie='knowledge/training'), pks(self.late))
        everyone = self.get().context['paginator'].count
        self.assertEqual(everyone, len(self.everyone))
        for incident in self.canary_incidents:
            self.assertNotIn(incident.get_absolute_url(), self.html())


class RowTest(WorklistData):

    def row(self, incident):
        return next(row for row in rows_of(self.html())[1:] if row[0] == str(incident.pk))

    def admin_cell(self, incident):
        return 'Im Admin bearbeiten : Meldung %d' % incident.pk

    def test_a_row_with_everything(self):
        self.assertEqual(self.row(self.waiting), [
            str(self.waiting.pk), '30.09.2026', 'In Bearbeitung', 'Labor › Station A',
            'Infrastruktur', 'Hoch', '01.10.2026', 'Wartet auf QM', self.admin_cell(self.waiting)])

    def test_a_row_without_a_place_a_category_or_a_risk_says_so(self):
        self.assertEqual(self.row(self.fresh)[3:6], ['Labor', 'Keine Angabe', 'Keine Angabe'])
        self.assertEqual(self.row(self.done)[3:6], ['Keine Angabe', 'Sonstige', 'Mittel'])

    def test_an_incident_can_carry_both_notes(self):
        both = make_incident(self.dept, reported=date(2026, 9, 1), history=[('new', at(9, 1))],
                             comments=[(self.reporter, date(2026, 9, 2))])
        self.assertEqual(self.row(both)[7], 'Wartet auf QM Ohne Bearbeitung')

    def test_the_notes_are_text_in_a_badge_and_none_where_nothing_is_to_note(self):
        html = self.html()
        self.assertIn('<span class="ui-badge ui-badge--warning">Wartet auf QM</span>', html)
        self.assertIn('<span class="ui-badge ui-badge--warning">Ohne Bearbeitung</span>', html)
        self.assertEqual(self.row(self.watched)[7], '')
        self.assertEqual(self.row(self.late)[7], 'Ohne Bearbeitung')

    def test_the_status_is_a_badge_that_is_done_for_a_completed_incident(self):
        html = self.html()
        self.assertIn('<span class="ui-badge ui-badge--success">Erledigt</span>', html)
        self.assertIn('<span class="ui-badge ui-badge--info">Neu</span>', html)
        self.assertIn('<span class="ui-badge ui-badge--info">Unter Beobachtung</span>', html)

    def test_the_number_links_to_the_incident_and_the_admin_is_one_link_further(self):
        html = self.html()
        for incident in self.everyone:
            self.assertIn('<td class="ui-strong"><a href="%s">%d</a></td>'
                          % (incident.get_absolute_url(), incident.pk), html)
            self.assertIn('<a href="%s">Im Admin bearbeiten' % reverse(
                'admin:cirs_criticalincident_change', args=[incident.pk]), html)

    def test_the_day_is_a_time_element(self):
        self.assertIn('<time datetime="2026-09-30">30.09.2026</time>', self.html())

    def test_the_head_names_the_columns(self):
        self.assertEqual([name for name, _sort, _url in headings(self.html())], LIST_COLUMNS)


class PageTest(WorklistData):

    def test_title_and_one_heading(self):
        html = self.html()
        self.assertRegex(html, r'<title>Meldungen · [^<]+</title>')
        self.assertEqual(re.findall(r'<h1[^>]*>(.*?)</h1>', html, re.S), ['Meldungen'])

    def test_no_inline_code_with_or_without_filters(self):
        for query in ({}, {'stand': 'new', 'q': '<b>'}, {'wo': 'x', 'von': 'y'},
                      {'q': 'Nichts da'}):
            self.assertEqual(csp_violations(self.html(query)), [], query)

    def test_nothing_is_kept_in_the_cache_of_the_browser(self):
        self.assertIn('no-store', self.get()['Cache-Control'])

    def test_the_table_has_a_caption_and_every_head_cell_a_scope(self):
        html = self.html()
        self.assertRegex(html, r'<caption class="ui-visually-hidden">Meldungen</caption>')
        self.assertEqual(len(re.findall(r'<th[\s>]', html)), len(LIST_COLUMNS))
        self.assertEqual(html.count('<th scope="col"'), len(LIST_COLUMNS))

    def test_every_field_has_its_label_and_its_hint(self):
        html = self.html()
        form = re.search(r'<form method="get".*?</form>', html, re.S).group(0)
        ids = re.findall(r'\sid="([^"]+)"', html)
        self.assertEqual(len(ids), len(set(ids)), sorted(ids))
        fields = [attributes for attributes in re.findall(r'<(?:input|select)\b([^>]*)>', form)
                  if 'type="hidden"' not in attributes]
        self.assertEqual(len(fields), 9)
        for attributes in fields:
            field_id = re.search(r'\sid="([^"]+)"', attributes).group(1)
            self.assertRegex(form, r'<label[^>]*for="%s"' % field_id)
            hint = re.search(r'aria-describedby="([^"]+)"', attributes).group(1)
            self.assertIn(hint, ids)
        for target in re.findall(r'aria-labelledby="([^"]+)"', html):
            self.assertIn(target, ids)

    def test_the_form_is_a_get_form_that_keeps_the_sort(self):
        html = self.html({'sort': '-nr'})
        self.assertRegex(html, r'<form method="get"[^>]*role="search"')
        self.assertIn('<input type="hidden" name="sort" value="-nr">', html)
        self.assertNotIn('name="sort"', self.html())

    def test_every_class_has_a_rule(self):
        css = css_text()
        main = re.search(r'<main.*?</main>', self.html({'stand': 'new'}), re.S).group(0)
        for token in set(' '.join(re.findall(r'\sclass="([^"]*)"', main)).split()):
            self.assertRegex(css, r'\.%s(?![\w-])' % re.escape(token), token)

    def test_the_texts_are_translatable(self):
        html = self.html({'stand': 'new', 'von': 'x'}, **EN)
        for text in ('Filter the incidents', 'Reported from', 'Waiting for the QM', 'Last activity',
                     'Edit in the admin', 'Clear filter', 'incidents match your filters',
                     'The filter “von” was ignored: invalid date.', 'Show only'):
            self.assertIn(text, html)
        empty = self.html({'stand': 'new', 'risiko': 'high'}, **EN)
        self.assertIn('No incident matches your filters.', empty)

    def test_a_qm_without_incidents_gets_the_empty_state_and_no_form(self):
        self.client.force_login(baker.make_recipe('cirs.reviewer').user)
        html = self.html()
        self.assertIn('Es gibt noch keine Meldungen.', words(html))
        self.assertNotIn('<form method="get"', html)
        self.assertNotIn('<table', html)

    def test_only_get_and_head_are_allowed(self):
        self.assertEqual(self.client.post(reverse('qm_incidents')).status_code, 405)
        self.assertEqual(self.client.head(reverse('qm_incidents')).status_code, 200)


class QueriesTest(WorklistBase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.second = baker.make_recipe('cirs.department', name='Station Zwei')
        cls.second.reviewers.add(cls.reviewer)
        cls.departments = [cls.dept, cls.second]
        cls.units = [OrgUnit.objects.create(name='Labor'), None,
                     OrgUnit.objects.create(name='Pflege')]
        cls.categories = [['other'], [], ['infrastructure', 'other']]

    def add(self, number):
        """An incident of its own kind: the first is late, waits for the QM and has a place."""
        reported = TODAY - timedelta(days=30 + 15 * number)
        start = datetime.combine(reported, time(12), tzinfo=dt_timezone.utc)
        status = ('new', 'in process', 'under supervision', 'completed')[number % 4]
        history = [('new', start)] + ([(status, start + timedelta(days=3))] if number % 4 else [])
        department = self.departments[number % 2]
        author = department.reporter.user if number % 3 == 0 else self.reviewer.user
        make_incident(department, reported=reported, history=history,
                      org_unit=self.units[number % 3], category=self.categories[number % 3],
                      risk=('', 'low', 'high')[number % 3], comments=[(author, reported)],
                      incident='Meldung %d' % number)

    def queries(self, params):
        with CaptureQueriesContext(connection) as queries:
            response = self.get(params)
        self.assertEqual(response.status_code, 200)
        return len(queries)

    def test_the_same_number_of_queries_for_one_and_for_thirty_incidents(self):
        urls = ({}, {'wartet': '1'}, {'ohne_bearbeitung': '1'}, {'sort': 'aktivitaet'},
                {'sort': '-nr'}, {'q': 'Meldung'}, {'stand': 'new', 'kategorie': 'other'})
        self.add(0)
        for params in urls:
            self.queries(params)  # warms the caches that the first request fills
        one = [self.queries(params) for params in urls]
        for number in range(1, 30):
            self.add(number)
        for params, expected in zip(urls, one):
            with self.subTest(params=params), self.assertNumQueries(expected):
                self.assertEqual(self.get(params).status_code, 200)
        self.assertEqual(len(listed(self.html())), PAGE_SIZE)  # a page of many rows, not of one


class ReviewFixesTest(WorklistData):

    def test_the_tiles_are_one_column_that_can_shrink_below_48rem_and_two_from_there(self):
        css = css_text()
        rule = re.search(r'\.ui-kacheln\s*\{([^}]*)\}', css).group(1)
        self.assertIn('display: grid', rule)
        self.assertIn('grid-template-columns: minmax(0, 1fr)', rule)
        self.assertIn('gap: var(--ui-s4)', rule)
        self.assertRegex(css, r'@media \(min-width: 48rem\) \{ \.ui-kacheln \{ '
                              r'grid-template-columns: repeat\(2, minmax\(0, 1fr\)\);')

    def test_the_overview_asks_for_the_start_of_the_log_once_with_a_log_and_without(self):
        bare = baker.make_recipe('cirs.department')
        reviewer = baker.make_recipe('cirs.reviewer')
        bare.reviewers.add(reviewer)
        make_incident(bare, reported=date(2026, 9, 1), legacy=True)  # no entry in the log at all
        for user in (self.reviewer.user, reviewer.user):
            self.client.force_login(user)
            with mock.patch.object(metrics, 'protocol_start', wraps=protocol_start) as asked:
                self.assertEqual(self.client.get(reverse('qm_overview'), **DE).status_code, 200)
            self.assertEqual(asked.call_count, 1, user)

    def test_the_months_take_the_start_of_the_log_instead_of_asking_for_it(self):
        scope = self.dept.criticalincident_set.all()
        first = date(2026, 7, 1)
        started = protocol_start(scope)
        with self.assertNumQueries(3):
            asked = monthly(scope, first, 4)
        with self.assertNumQueries(2):
            given = monthly(scope, first, 4, started=started)
        self.assertEqual(given, asked)
        with self.assertNumQueries(1):  # no log, as the caller says: no completions to ask for
            self.assertEqual({row.completed for row in monthly(scope, first, 4, started=None)},
                             {None})


class AnnotateWorkflowTest(LogTestCase):
    TODAY = date(2026, 10, 15)

    def setUp(self):
        super().setUp()
        self.reporter = self.dept.reporter.user
        self.qm = create_user('qm-user')

    def rows(self, days=14):
        return {row.pk: row for row in annotate_workflow(self.scope, self.TODAY, days)}

    def test_overdue_is_a_new_incident_of_more_than_the_days_not_one_of_exactly_the_days(self):
        young = self.incident(date(2026, 10, 1))  # 14 days
        late = self.incident(date(2026, 9, 30))  # 15 days
        rows = self.rows()
        self.assertFalse(rows[young.pk].is_overdue)
        self.assertTrue(rows[late.pk].is_overdue)
        self.assertTrue(self.rows(days=7)[young.pk].is_overdue)

    def test_only_a_new_incident_can_be_overdue(self):
        found = [self.incident(date(2026, 1, 1), status=status)
                 for status in ('in process', 'under supervision', 'completed')]
        self.assertFalse(any(self.rows()[incident.pk].is_overdue for incident in found))

    def test_awaiting_the_qm_is_the_reporting_person_with_the_last_comment(self):
        waiting = self.incident(date(2026, 2, 1), comments=[(self.qm, date(2026, 2, 3)),
                                                            (self.reporter, date(2026, 2, 4))])
        answered = self.incident(date(2026, 2, 1), comments=[(self.reporter, date(2026, 2, 3)),
                                                             (self.qm, date(2026, 2, 4))])
        silent = self.incident(date(2026, 2, 1))
        same_day = self.incident(date(2026, 2, 1), comments=[(self.qm, date(2026, 2, 4)),
                                                             (self.reporter, date(2026, 2, 4))])
        rows = self.rows()
        self.assertEqual([rows[i.pk].is_awaiting_qm for i in (waiting, answered, silent, same_day)],
                         [True, False, False, True])

    def test_a_completed_incident_waits_for_nobody(self):
        done = self.incident(date(2026, 2, 1), status='completed',
                             comments=[(self.reporter, date(2026, 2, 4))])
        self.assertFalse(self.rows()[done.pk].is_awaiting_qm)

    def test_the_flags_and_the_functions_say_the_same(self):
        for day in range(1, 29):
            comments = [(self.reporter, date(2026, 9, day))] if day % 3 == 0 else []
            self.incident(date(2026, 9, day), comments=comments)
        rows = self.rows().values()
        annotated = annotate_workflow(self.scope, self.TODAY, 14)
        late = overdue(self.scope, self.TODAY, 14)
        waiting = awaiting_qm(self.scope)
        self.assertCountEqual(late, [row for row in rows if row.is_overdue])
        self.assertCountEqual(waiting, [row for row in rows if row.is_awaiting_qm])
        self.assertCountEqual(late, annotated.filter(is_overdue=True))
        self.assertCountEqual(waiting, annotated.filter(is_awaiting_qm=True))
        self.assertTrue(late and waiting)  # the data has both, so the checks above mean something

    def test_the_last_activity_is_the_day_of_the_report_if_nothing_else_happened(self):
        legacy = self.incident(date(2026, 2, 1), legacy=True)
        logged = self.incident(date(2026, 2, 2), history=[('new', utc(2026, 2, 2, 9))])
        self.assertEqual(self.rows()[legacy.pk].last_activity, date(2026, 2, 1))
        self.assertEqual(self.rows()[logged.pk].last_activity, date(2026, 2, 2))

    def test_the_last_activity_is_the_newest_of_report_comment_and_change_of_status(self):
        comment = self.incident(date(2026, 2, 1), history=[('new', utc(2026, 2, 1, 9))],
                                comments=[(self.qm, date(2026, 2, 5)),
                                          (self.reporter, date(2026, 2, 9))])
        change = self.incident(date(2026, 2, 1), history=[('new', utc(2026, 2, 1, 9)),
                                                          ('in process', utc(2026, 2, 12, 9))],
                               comments=[(self.qm, date(2026, 2, 5))])
        older = self.incident(date(2026, 2, 20), history=[('new', utc(2026, 2, 1, 9))],
                              comments=[(self.qm, date(2026, 2, 5))])  # reported after both
        rows = self.rows()
        self.assertEqual([rows[i.pk].last_activity for i in (comment, change, older)],
                         [date(2026, 2, 9), date(2026, 2, 12), date(2026, 2, 20)])

    def test_a_change_counts_on_the_day_of_the_wall_clock_of_the_installation(self):
        # half past midnight in Vienna is the evening before in UTC
        incident = self.incident(
            date(2026, 2, 1),
            history=[('new', utc(2026, 2, 1, 9)), ('in process', vienna(2026, 2, 4, 0, 30))])
        self.assertEqual(self.rows()[incident.pk].last_activity, date(2026, 2, 3))
        with override_settings(TIME_ZONE='Europe/Vienna'):
            self.assertEqual(self.rows()[incident.pk].last_activity, date(2026, 2, 4))

    def test_no_query_for_a_row(self):
        self.incident(date(2026, 2, 1), comments=[(self.reporter, date(2026, 2, 4))])
        with self.assertNumQueries(1):
            list(annotate_workflow(self.scope, self.TODAY, 14))
        for day in range(1, 29):
            self.incident(date(2026, 2, 1), comments=[(self.qm, date(2026, 2, 1)),
                                                      (self.reporter, date(2026, 2, day))],
                          history=[('new', utc(2026, 2, 1, 9)),
                                   ('in process', utc(2026, 2, day, 9))])
        with self.assertNumQueries(1):
            rows = [(row.is_overdue, row.is_awaiting_qm, row.last_activity)
                    for row in annotate_workflow(self.scope, self.TODAY, 14)]
            self.assertEqual(len(rows), 29)

    def test_the_incidents_of_another_department_stay_out(self):
        self.foreign(date(2026, 1, 1))
        self.assertEqual(self.rows(), {})
