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

"""The list of published cases (search, paging), the department list and the login page."""

import re

from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from model_bakery import baker

from cirs.models import Comment, PublishableIncident

from .helpers import create_user


def make_case(dept, title, description='Synthetic description', measures='Synthetic measures',
              publish=True, photo=None):
    """A case of dept with English and German texts, published unless publish is False."""
    incident = baker.make_recipe('cirs.public_ci', department=dept, photo=photo)
    case = PublishableIncident.objects.create(critical_incident=incident, publish=publish)
    for language in ('en', 'de'):
        case.create_translation(language, incident=title, description=description,
                                measures_and_consequences=measures)
    return case


def comment_on(case, reviewer, number):
    for _ in range(number):
        baker.make(Comment, critical_incident=case.critical_incident, author=reviewer.user)


def rows(html):
    """The table rows of the body; every row has one <time> element for month and year."""
    return html.count('<time ')


class PublishedListTest(TestCase):

    def setUp(self):
        self.dept = baker.make_recipe('cirs.department')
        self.url = self.dept.get_absolute_url()

    def get(self, **params):
        return self.client.get(self.url, params, HTTP_ACCEPT_LANGUAGE='de')

    def html(self, **params):
        return self.get(**params).content.decode()

    def test_search_filters_by_title(self):
        make_case(self.dept, 'Synthetic infusion pump alarm')
        make_case(self.dept, 'Synthetic door handle')
        response = self.get(q='pump')
        self.assertEqual([case.incident for case in response.context['object_list']],
                         ['Synthetic infusion pump alarm'])
        html = response.content.decode()
        self.assertIn('Synthetic infusion pump alarm', html)
        self.assertNotIn('Synthetic door handle', html)

    def test_search_covers_description_and_measures_ignoring_case(self):
        make_case(self.dept, 'Case A', description='Wrong Needle size')
        make_case(self.dept, 'Case B', measures='Second Check of labels')
        make_case(self.dept, 'Case C')
        for term, expected in (('needle SIZE', 'Case A'), ('second check', 'Case B')):
            with self.subTest(term=term):
                self.assertEqual([c.incident for c in self.get(q=term).context['object_list']],
                                 [expected])

    def test_search_lists_a_case_once_although_both_translations_match(self):
        make_case(self.dept, 'Synthetic pump alarm')  # English and German text both match
        response = self.get(q='pump')
        self.assertEqual(len(response.context['object_list']), 1)
        self.assertEqual(response.context['paginator'].count, 1)

    def test_search_stays_inside_department_and_published_cases(self):
        make_case(self.dept, 'Synthetic pump here')
        make_case(baker.make_recipe('cirs.department'), 'Synthetic pump elsewhere')
        make_case(self.dept, 'Synthetic pump unpublished', publish=False)
        self.assertEqual([c.incident for c in self.get(q='pump').context['object_list']],
                         ['Synthetic pump here'])

    def test_newest_case_comes_first_with_and_without_search(self):
        # the comment count is an aggregate, and an aggregate drops Meta.ordering
        for title in ('Synthetic pump first', 'Synthetic pump second', 'Synthetic pump third'):
            make_case(self.dept, title)
        expected = ['Synthetic pump third', 'Synthetic pump second', 'Synthetic pump first']
        for params in ({}, {'q': 'pump'}):
            with self.subTest(params=params):
                self.assertEqual([c.incident for c in self.get(**params).context['object_list']],
                                 expected)

    def test_blank_search_lists_everything(self):
        make_case(self.dept, 'Case A')
        make_case(self.dept, 'Case B')
        self.assertEqual(rows(self.html(q='   ')), 2)
        self.assertNotIn('ui-filterstand', self.html(q='   '))

    def test_pagination_25(self):
        for number in range(26):
            make_case(self.dept, 'Synthetic case %d' % number)
        first = self.html()
        self.assertEqual(rows(first), 25)
        self.assertIn('Seite 1 von 2', first)
        self.assertEqual(rows(self.html(page=2)), 1)
        self.assertIn('Seite 2 von 2', self.html(page=2))
        self.assertEqual(self.get(page=3).status_code, 404)

    def test_pager_links_keep_the_search_term(self):
        for number in range(26):
            make_case(self.dept, 'Synthetic case %d' % number)
        first = self.html(q='Synthetic case')
        self.assertRegex(first, r'href="\?q=Synthetic%20case&amp;page=2"')
        self.assertNotIn('page=0', first)
        second = self.html(q='Synthetic case', page=2)
        self.assertRegex(second, r'href="\?q=Synthetic%20case&amp;page=1"')

    def test_no_pager_for_a_single_page(self):
        make_case(self.dept, 'Case A')
        html = self.html()
        self.assertNotIn('Seite 1 von', html)
        self.assertNotIn('aria-label="Seiten der Fallliste"', html)

    def test_filter_status_names_the_search_and_offers_a_way_out(self):
        make_case(self.dept, 'Synthetic pump alarm')
        html = self.html(q='pump')
        self.assertRegex(html, r'role="status"[^>]*>\s*<span>1 veröffentlichter Fall passt')
        self.assertIn('„pump“', html)
        self.assertRegex(html, r'<a [^>]*href="%s"[^>]*>Filter aufheben</a>' % re.escape(self.url))

    def test_filter_status_counts_several_cases_in_the_plural(self):
        make_case(self.dept, 'Synthetic pump alarm')
        make_case(self.dept, 'Synthetic pump leak')
        make_case(self.dept, 'Synthetic door handle')
        self.assertRegex(self.html(q='pump'),
                         r'role="status"[^>]*>\s*<span>2 veröffentlichte Fälle passen zu Ihrer '
                         r'Suche nach „pump“\.</span>')
        self.assertIn('2 published cases match your search for “pump”.',
                      self.client.get(self.url, {'q': 'pump'}).content.decode())

    def test_empty_state_with_reset_link(self):
        make_case(self.dept, 'Synthetic door handle')
        html = self.html(q='pump')
        self.assertIn('ui-state', html)
        self.assertIn('Kein veröffentlichter Fall passt zu Ihrer Suche nach „pump“.', html)
        self.assertIn('Bitte prüfen Sie die Schreibweise oder versuchen Sie einen kürzeren Suchbegriff.', html)
        self.assertRegex(html, r'<a href="%s">Filter aufheben</a>' % re.escape(self.url))
        self.assertNotIn('<table', html)
        self.assertEqual(html.count('<h1'), 1)

    def test_empty_state_without_search_has_no_reset_link(self):
        html = self.html()
        self.assertIn('ui-state', html)
        self.assertIn('Es gibt noch keine veröffentlichten Fälle.', html)
        self.assertNotIn('Filter aufheben', html)
        self.assertNotIn('<table', html)

    def test_table_has_caption_and_scope(self):
        make_case(self.dept, 'Case A')
        html = self.html()
        self.assertRegex(html, r'<div class="ui-card ui-card--flush">\s*'
                               r'<div class="ui-tablewrap ui-tablewrap--eingebettet">\s*'
                               r'<table class="ui-table">\s*<caption class="ui-visually-hidden">')
        self.assertIn('Veröffentlichte Fälle, neueste zuerst', html)
        headers = re.findall(r'<th\b[^>]*>', html)
        self.assertEqual(len(headers), 5)
        self.assertEqual(headers, ['<th scope="col">'] * 5)

    def test_caption_names_the_page_when_there_are_several(self):
        for number in range(26):
            make_case(self.dept, 'Synthetic case %d' % number)
        self.assertIn('neueste zuerst &middot; Seite 2 von 2', self.html(page=2))

    def test_photo_is_a_link_with_the_case_in_its_name(self):
        case = make_case(self.dept, 'Synthetic pump alarm', photo='photos/2026/09/30/synthetic.jpg')
        make_case(self.dept, 'Synthetic door handle')
        html = self.html()
        link = re.search(r'<a href="([^"]*synthetic\.jpg)"[^>]*>(.*?)</a>', html, re.S)
        self.assertEqual(link.group(1), case.critical_incident.photo.url)
        self.assertIn('Foto ansehen', link.group(2))
        self.assertIn('zum Fall „Synthetic pump alarm“', link.group(2))
        self.assertEqual(html.count('Foto ansehen'), 1)
        self.assertIn('kein Foto', html)
        for old in ('data-toggle', 'data-target', 'modal', '<img'):
            self.assertNotIn(old, html)

    def test_comment_column_only_for_reviewers(self):
        case = make_case(self.dept, 'Case A')
        self.assertNotIn('Zahl der Kommentare', self.html())
        reviewer = baker.make_recipe('cirs.reviewer')
        self.dept.reviewers.add(reviewer)
        self.client.force_login(reviewer.user)
        html = self.html()
        self.assertIn('<th scope="col">Zahl der Kommentare</th>', html)
        self.assertIn('href="%s"' % case.critical_incident.get_absolute_url(), html)

    def test_add_incident_button_only_for_anonymous_and_the_only_primary_action(self):
        make_case(self.dept, 'Case A')
        html = self.html()
        self.assertIn(reverse('create_incident', kwargs={'dept': self.dept.label}), html)
        self.assertEqual(html.count('ui-btn--primary'), 1)

    def test_title_and_one_h1(self):
        html = self.html()
        self.assertRegex(html, r'<title>Veröffentlichte Fälle · [^<]+</title>')
        self.assertEqual(html.count('<h1'), 1)
        self.assertRegex(html, r'<h1>Veröffentlichte Fälle</h1>')
        self.assertRegex(html, r'<p class="ui-lede[^>]*>[^<]*%s' % re.escape(self.dept.name))

    def test_search_form_is_a_get_form_with_label_and_hint(self):
        html = self.html(q='pump')
        form = re.search(r'<form[^>]*role="search"[^>]*>.*?</form>', html, re.S).group(0)
        self.assertRegex(form, r'<form method="get"')
        self.assertNotIn('csrfmiddlewaretoken', form)
        self.assertRegex(form, r'<label [^>]*for="suche-q"[^>]*>Suchbegriff</label>')
        self.assertRegex(form, r'<input [^>]*type="search"[^>]*id="suche-q"[^>]*name="q"'
                               r'[^>]*value="pump"[^>]*aria-describedby="suche-q-hinweis"')
        self.assertIn('id="suche-q-hinweis"', form)

    def test_search_term_is_escaped(self):
        html = self.html(q='"><script>x</script>')
        self.assertNotIn('<script>x', html)
        self.assertIn('&quot;&gt;&lt;script&gt;x&lt;/script&gt;', html)

    def test_no_old_frontend_markup_left(self):
        make_case(self.dept, 'Case A')
        html = self.html()
        for old in ('class="container"', 'table-responsive', 'table-striped', 'btn-info',
                    'img-fluid', 'DataTable', 'tableIncidents'):
            self.assertNotIn(old, html)

    def count_queries(self, expected_rows):
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(rows(self.html()), expected_rows)
        return len(queries)

    def test_queries_do_not_grow_with_the_number_of_cases(self):
        # anonymous visitor, then the reviewer with the comment column
        reviewer = baker.make_recipe('cirs.reviewer')
        self.dept.reviewers.add(reviewer)
        make_case(self.dept, 'Case A', photo='photos/2026/09/30/a.jpg')
        one_anonymous = self.count_queries(1)
        self.client.force_login(reviewer.user)
        one_reviewer = self.count_queries(1)
        for number in range(10):
            case = make_case(self.dept, 'Case %d' % number,
                             photo='photos/2026/09/30/%d.jpg' % number)
            comment_on(case, reviewer, number % 3)
        self.assertEqual(self.count_queries(11), one_reviewer)
        self.client.logout()
        self.assertEqual(self.count_queries(11), one_anonymous)

    def test_comment_column_counts_the_comments_of_each_case(self):
        reviewer = baker.make_recipe('cirs.reviewer')
        self.dept.reviewers.add(reviewer)
        for title, comments in (('Case none', 0), ('Case two', 2), ('Case three', 3)):
            case = make_case(self.dept, title)
            comment_on(case, reviewer, comments)
        self.client.force_login(reviewer.user)
        counts = {case.incident: case.comment_count
                  for case in self.get().context['object_list']}
        self.assertEqual(counts, {'Case none': 0, 'Case two': 2, 'Case three': 3})
        # a search that joins the translations does not multiply the count
        counts = {case.incident: case.comment_count
                  for case in self.get(q='Case').context['object_list']}
        self.assertEqual(counts, {'Case none': 0, 'Case two': 2, 'Case three': 3})

    def test_bad_page_numbers_are_404_never_500(self):
        make_case(self.dept, 'Case A')
        client = Client(raise_request_exception=False)
        for page in ('0', '-1', 'abc', '1.5', '99999999999999999999', '3'):
            with self.subTest(page=page):
                response = client.get(self.url, {'page': page}, HTTP_ACCEPT_LANGUAGE='de')
                self.assertEqual(response.status_code, 404)
                self.assertIn('Seite nicht gefunden', response.content.decode())


class DepartmentListTest(TestCase):

    def setUp(self):
        self.depts = baker.make_recipe('cirs.department', _quantity=2)

    def html(self):
        return self.client.get(reverse('departments_list'), HTTP_ACCEPT_LANGUAGE='de').content.decode()

    def test_table_with_caption_scope_and_links(self):
        html = self.html()
        self.assertRegex(html, r'<div class="ui-card ui-card--flush">\s*'
                               r'<div class="ui-tablewrap ui-tablewrap--eingebettet">\s*'
                               r'<table class="ui-table">\s*<caption class="ui-visually-hidden">')
        self.assertEqual(re.findall(r'<th scope="col">([^<]*)</th>', html),
                         ['Name', 'Kurzbezeichnung'])
        for dept in self.depts:
            self.assertRegex(html, r'<a href="%s">%s</a>' % (re.escape(dept.get_absolute_url()),
                                                            dept.name))
            self.assertIn(dept.label, html)

    def test_title_and_one_h1(self):
        html = self.html()
        self.assertRegex(html, r'<title>Bitte eigene Abteilung auswählen · [^<]+</title>')
        self.assertEqual(html.count('<h1'), 1)

    def test_empty_state_when_a_reviewer_has_no_active_department(self):
        # two active departments, so that the list does not redirect; the reviewer has none
        reviewer = baker.make_recipe('cirs.reviewer')
        for dept in self.depts:
            dept.reviewers.add(reviewer)
            dept.active = False
            dept.save()
        self.client.force_login(reviewer.user)
        html = self.html()
        self.assertIn('ui-state', html)
        self.assertNotIn('<table', html)
        self.assertEqual(html.count('<h1'), 1)

    def test_no_old_frontend_markup_left(self):
        html = self.html()
        for old in ('class="container"', 'table-responsive', 'table-striped', 'DataTable',
                    'table_departments'):
            self.assertNotIn(old, html)


class LoginPageTest(TestCase):

    def setUp(self):
        self.dept = baker.make_recipe('cirs.department')
        self.reviewer = baker.make_recipe('cirs.reviewer')
        self.dept.reviewers.add(self.reviewer)
        self.reviewer.user.set_password('synthetic-pw')
        self.reviewer.user.save()

    def html(self, data=None, url=None):
        url = url or reverse('login')
        if data is None:
            response = self.client.get(url, HTTP_ACCEPT_LANGUAGE='de')
        else:
            response = self.client.post(url, data, HTTP_ACCEPT_LANGUAGE='de')
        return response.content.decode()

    def test_login_fields_have_labels(self):
        html = self.html()
        for name, label in (('username', 'Benutzername'), ('password', 'Passwort')):
            self.assertRegex(html, r'<label [^>]*for="%s"[^>]*>%s</label>' % (name, label))
            self.assertRegex(html, r'<input [^>]*id="%s"[^>]*name="%s"' % (name, name))
        self.assertIn('autocomplete="username"', html)
        self.assertIn('autocomplete="current-password"', html)
        self.assertNotIn('placeholder=', html)  # a placeholder is no label

    def test_login_is_a_card_in_the_auth_layout_with_one_h1(self):
        html = self.html()
        self.assertRegex(html, r'<main class="ui-main ui-main--auth"')
        self.assertRegex(html, r'<div class="ui-card">\s*<h1>Anmelden</h1>')
        self.assertEqual(html.count('<h1'), 1)
        self.assertRegex(html, r'<title>Anmelden · [^<]+</title>')

    def test_login_has_one_primary_button_and_no_old_markup(self):
        html = self.html()
        self.assertEqual(html.count('ui-btn--primary'), 1)
        self.assertRegex(html, r'<button class="ui-btn ui-btn--primary ui-w-full" type="submit"'
                               r'[^>]*data-busy-text="Wird angemeldet')
        for old in ('form-signin', 'form-control', 'btn-danger', 'alert-success', 'class="container"'):
            self.assertNotIn(old, html)

    def test_login_points_reporters_to_reporting_and_offers_password_reset(self):
        html = self.html(url=reverse('login') + '?next=' + self.dept.get_absolute_url())
        self.assertIn('Für eine Meldung brauchen Sie keine Anmeldung.', html)
        self.assertIn(reverse('create_incident', kwargs={'dept': self.dept.label}), html)
        self.assertRegex(html, r'<a href="%s">Passwort vergessen\?</a>'
                         % re.escape(reverse('password_reset')))

    def test_missing_fields_are_not_printed_as_none(self):
        # request.POST.get() gives None for a field that is not sent at all
        for data in ({'password': 'x'}, {'username': 'someone'}, {}):
            with self.subTest(data=data):
                html = self.html(data)
                self.assertNotIn('None', html)
                self.assertRegex(html, r'<input [^>]*id="username"[^>]*value="%s"'
                                 % data.get('username', ''))

    def test_next_is_kept_in_the_form(self):
        html = self.html(url=reverse('login') + '?next=' + self.dept.get_absolute_url())
        self.assertIn('<input type="hidden" name="next" value="%s">' % self.dept.get_absolute_url(),
                      html)

    def test_failed_login_is_an_alert_tied_to_both_fields(self):
        html = self.html({'username': self.reviewer.user.username, 'password': 'wrong'})
        self.assertRegex(html, r'<div class="ui-alert ui-alert--danger" id="anmeldemeldung" '
                               r'role="alert">')
        self.assertIn('Benutzername oder Passwort stimmen nicht, Sie sind nicht angemeldet.', html)
        self.assertIn('<a href="%s">Ihr Passwort zurücksetzen</a>' % reverse('password_reset'), html)
        self.assertEqual(len(re.findall(r'aria-describedby="anmeldemeldung"', html)), 2)
        self.assertIn('value="%s"' % self.reviewer.user.username, html)  # kept for the next try
        self.assertNotIn('Für eine Meldung brauchen Sie keine Anmeldung.', html)  # message wins

    def test_login_info_of_the_department_is_shown(self):
        config = self.dept.labcirsconfig
        config.login_info = 'Synthetic login hint'
        config.login_info_url = 'https://example.org/hint'
        config.login_info_link_text = 'Synthetic link'
        config.save()
        html = self.html(url=reverse('login') + '?next=' + self.dept.get_absolute_url())
        self.assertIn('Synthetic login hint', html)
        self.assertRegex(html, r'<a href="https://example.org/hint"[^>]*>Synthetic link</a>')

    def test_department_without_login_info_names_the_gap(self):
        config = self.dept.labcirsconfig
        config.login_info = ''
        config.save()
        html = self.html(url=reverse('login') + '?next=' + self.dept.get_absolute_url())
        self.assertIn('ui-alert--warning', html)
        self.assertIn(self.dept.label, html)


class PageStructureTest(TestCase):
    """Every page sets its title and has exactly one h1."""

    def test_title_and_one_h1_on_every_page_of_this_task(self):
        dept, other = baker.make_recipe('cirs.department', _quantity=2)
        urls = [reverse('departments_list'), dept.get_absolute_url(), reverse('login'),
                reverse('password_reset'), reverse('password_reset_done'),
                reverse('password_reset_confirm', kwargs={'uidb64': 'MQ', 'token': 'x-y'}),
                reverse('password_reset_complete')]
        for url in urls:
            with self.subTest(url=url):
                html = self.client.get(url, follow=True, HTTP_ACCEPT_LANGUAGE='de').content.decode()
                title = re.search(r'<title>([^<]*)</title>', html).group(1).strip()
                self.assertGreater(len(title), 3)
                self.assertNotRegex(title, r'^(·|\|)')  # a concrete part comes first
                self.assertEqual(html.count('<h1'), 1)
