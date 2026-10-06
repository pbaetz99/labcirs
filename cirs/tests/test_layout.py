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

import re
from pathlib import Path

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from model_bakery import baker

from cirs.models import CriticalIncident, TranslationStatusMixin

from .test_pages_report import css_text


class TopBarTest(TestCase):

    def test_the_bar_sticks_only_where_one_row_fits_the_navigation_of_the_qm(self):
        # Wrapped to two rows a sticky bar would cover the element that has the focus. The
        # navigation of the QM is about 260 px longer than the one of a visitor.
        self.assertIn('@media (max-width: 80rem) { .ui-topbar { position: static; } }', css_text())

    def test_the_text_of_a_chart_does_not_grow_with_the_font_size_of_the_browser(self):
        rule = re.search(r'\.ui-diagramm__beschriftung, [^{]*\{([^}]*)\}', css_text()).group(1)
        self.assertRegex(rule, r'font-size: \d+px')


class LayoutTest(TestCase):

    def setUp(self):
        self.dept = baker.make_recipe('cirs.department')
        self.url = self.dept.get_absolute_url()

    def html(self, url=None, **extra):
        return self.client.get(url or self.url, **extra).content.decode()

    def logo(self, html):
        return re.search(r'class="ui-logo".*?</a>', html, re.S).group(0)

    def footer(self, html):
        return html.split('<footer', 1)[1]

    def make_reviewer(self):
        reviewer = baker.make_recipe('cirs.reviewer')
        self.dept.reviewers.add(reviewer)
        return reviewer

    @override_settings(LOGO_URL='/branding/logo.svg')
    def test_logo_image_when_configured(self):
        self.assertIn('<img src="/branding/logo.svg" alt="', self.html())

    @override_settings(ORGANIZATION='Synthetic Clinic')
    def test_wordmark_without_logo(self):
        logo = self.logo(self.html())
        self.assertIn('Synthetic Clinic', logo)
        self.assertNotIn('<img', logo)

    def test_footer_links_only_when_set(self):
        footer = self.footer(self.html())
        self.assertNotIn('href="/imprint/"', footer)
        self.assertNotIn('href="/privacy/"', footer)
        # also without a source link: each link depends only on its own setting
        with self.settings(IMPRINT_URL='/imprint/', PRIVACY_URL='/privacy/', SOURCE_URL=''):
            footer = self.footer(self.html())
        self.assertIn('href="/imprint/"', footer)
        self.assertIn('href="/privacy/"', footer)
        self.assertNotIn('href=""', footer)

    def test_source_link_present(self):
        self.assertIn('href="%s"' % settings.SOURCE_URL, self.footer(self.html()))

    @override_settings(SITE_NAME='Example-CIRS')
    def test_site_name_in_title_brand_and_footer(self):
        html = self.html()
        self.assertRegex(html, r'<title>[^<]*Example-CIRS[^<]*</title>')
        self.assertIn('class="ui-logo__produkt">Example-CIRS</span>', html)
        self.assertIn('Example-CIRS', self.footer(html))
        self.assertIn('based on LabCIRS', self.footer(html))
        self.assertIn('basiert auf LabCIRS', self.footer(self.html(HTTP_ACCEPT_LANGUAGE='de')))

    @override_settings(SITE_NAME='Example-CIRS')
    def test_admin_header_uses_site_name(self):
        self.client.force_login(self.make_reviewer().user)
        html = self.html(reverse('admin:index'))
        self.assertRegex(html, r'id="site-name"><a [^>]*>Example-CIRS</a>')
        self.assertIn('Example-CIRS administration', html)
        de = self.html(reverse('admin:index'), HTTP_ACCEPT_LANGUAGE='de')
        self.assertIn('Example-CIRS-Verwaltung', de)

    def test_skip_link_and_main(self):
        html = self.html()
        self.assertIn('href="#inhalt"', html)
        self.assertIn('id="inhalt"', html)

    def test_no_template_comment_in_output(self):
        # {# #} spans one line only; a longer one would be printed on the page
        self.client.force_login(self.make_reviewer().user)
        for url in (self.url, reverse('admin:index')):
            self.assertNotIn('#}', self.html(url), url)

    def test_page_stays_light(self):
        # core.css follows prefers-color-scheme unless data-theme="hell" is set
        self.assertRegex(self.html(), r'<html [^>]*data-theme="hell"')

    def test_language_switch_is_form_with_buttons(self):
        html = self.html()
        self.assertRegex(html, r'<button [^>]*name="language"')
        self.assertNotIn('<select', html)

    @override_settings(LANGUAGES=[('de', 'Deutsch')])
    def test_language_switch_hidden_with_one_language(self):
        self.assertNotIn('name="language"', self.html())

    def test_navigation_for_anonymous_marks_current_page(self):
        html = self.html(HTTP_ACCEPT_LANGUAGE='de')
        nav = re.search(r'<nav class="ui-nav".*?</nav>', html, re.S).group(0)
        for name, text in (('create_incident', 'Ereignis melden'),
                           ('incident_search', 'Meine Meldung'),
                           ('incidents_for_department', 'Veröffentlichte Fälle')):
            self.assertIn(reverse(name, kwargs={'dept': self.dept.label}), nav)
            self.assertIn(text, nav)
        self.assertRegex(nav, r'href="%s"\s+aria-current="page"' % re.escape(self.url))
        self.assertIn(reverse('login'), nav)
        self.assertNotIn(reverse('admin:index'), nav)

    def test_staff_sees_admin_link_and_logout_form(self):
        self.client.force_login(self.make_reviewer().user)
        html = self.html()
        self.assertIn('href="%s"' % reverse('admin:index'), html)
        self.assertRegex(html, r'<form [^>]*method="post" action="%s"' % reverse('logout'))

    def test_messages_are_alerts_with_role(self):
        self.client.force_login(self.make_reviewer().user)
        # a reviewer is sent from the code search to the list with a warning
        response = self.client.get(reverse('incident_search', kwargs={'dept': self.dept.label}),
                                   follow=True)
        self.assertContains(response, 'class="ui-alert ui-alert--warning" role="status"')

    def test_logout_requires_post(self):
        self.client.force_login(self.make_reviewer().user)
        self.assertEqual(self.client.get(reverse('logout')).status_code, 405)
        self.assertIn('_auth_user_id', self.client.session)
        response = self.client.post(reverse('logout'))
        self.assertRedirects(response, reverse('labcirs_home'), fetch_redirect_response=False)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_admin_uses_theme(self):
        self.client.force_login(self.make_reviewer().user)
        self.assertIn('admin-theme.css', self.html(reverse('admin:index')))


class AdminMarkupTest(TestCase):

    def test_translation_info_uses_classes_not_inline_style(self):
        for status in ('complete', 'incomplete'):
            stub = type('Stub', (TranslationStatusMixin,), {'translation_status': status})()
            html = stub.translation_info
            self.assertNotIn('style=', html)
            self.assertIn('labcirs-status--%s' % status, html)

    def test_inline_fields_shrink_to_their_cell(self):
        """
        The inline of a publishable incident has three wide fields in a row (size 62 and two
        textareas of 60 columns). Next to the navigation of the admin they did not fit into 1440 px
        and the whole page scrolled sideways (acceptance run). In a table cell the fields take
        the width of the cell instead of their attributes.
        """
        css = Path(settings.BASE_DIR, 'static/css/admin-theme.css').read_text(encoding='utf-8')
        rule = re.search(r'([^{}]*\.inline-group[^{}]*)\{([^{}]*)\}', css)
        self.assertIsNotNone(rule, 'no rule for the fields of an inline')
        for field in ('input[type=text]', 'textarea'):
            self.assertIn('.inline-group .tabular td ' + field, rule.group(1))
        self.assertRegex(rule.group(2), r'width:\s*100%')
        self.assertRegex(rule.group(2), r'box-sizing:\s*border-box')

    def test_photo_link_has_accessible_name(self):
        html = CriticalIncident(photo='photos/2026/01/01/x.jpg').photo_tag()
        link_text = re.sub(r'<[^>]+>', '', re.search(r'<a .*?</a>', html, re.S).group(0))
        self.assertTrue(link_text.strip(), html)

    def test_index_names_are_hyphenated_not_cut(self):
        """
        "Organisationseinheiten" in the admin index is wider than its cell at 320 px and was cut
        in the middle of the word. The page carries lang, so the browser can hyphenate.
        """
        css = Path(settings.BASE_DIR, 'static/css/admin-theme.css').read_text(encoding='utf-8')
        rule = re.search(r'\.module th\s*\{([^{}]*)\}', css)
        self.assertIsNotNone(rule, 'no rule for the names in the tables of the admin index')
        self.assertRegex(rule.group(1), r'hyphens:\s*auto')


class AdminIncidentPageTest(TestCase):
    """The incident page and the index of the admin, in both languages."""

    def setUp(self):
        dept = baker.make_recipe('cirs.department')
        reviewer = baker.make_recipe('cirs.reviewer')
        dept.reviewers.add(reviewer)
        self.client.force_login(reviewer.user)
        self.incident = baker.make_recipe('cirs.public_ci', department=dept,
                                          photo='photos/2026/01/01/x.jpg')

    def get(self, name, language, *args):
        return self.client.get(reverse(name, args=args), HTTP_ACCEPT_LANGUAGE=language).content.decode()

    def incident_page(self, language):
        return self.get('admin:cirs_criticalincident_change', language, self.incident.pk)

    def test_comment_and_review_blocks_are_translated(self):
        de = self.incident_page('de')
        self.assertRegex(de, r'<h2[^>]*>\s*Kommentare\s*</h2>')
        self.assertRegex(de, r'<h2[^>]*>\s*Bewertung\s*</h2>')
        self.assertNotRegex(de, r'<h2[^>]*>\s*(Comments|Review)\s*</h2>')
        en = self.incident_page('en')
        self.assertRegex(en, r'<h2[^>]*>\s*Comments\s*</h2>')
        self.assertRegex(en, r'<h2[^>]*>\s*Review\s*</h2>')

    def test_application_name_is_not_the_label(self):
        for language in ('de', 'en'):
            for html in (self.incident_page(language), self.get('admin:index', language)):
                self.assertNotIn('>Cirs<', html)
                self.assertIn('>CIRS<', html)

    def test_photo_is_shown_once(self):
        html = self.incident_page('en')
        self.assertEqual(html.count('<label>Photo:</label>'), 1)
        self.assertIn('labcirs-thumb', html)
