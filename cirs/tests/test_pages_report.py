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

"""The reporter's pages in the design-system layout: report form, success page, code search
and incident detail.

The pages are checked for the usual rules of the layout (labels, hints, one h1, no inline code, no
Bootstrap classes, every class has a rule) and for the wording the reporter reads.
"""

import re
from datetime import date
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

from django.conf import settings
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from model_bakery import baker
from parameterized import parameterized

from cirs import forms as cirs_forms
from cirs.forms import CommentForm, IncidentCreateForm, IncidentSearchForm
from cirs.models import Comment, CriticalIncident, ReporterContact, group_code
from cirs.views import NEW_CODE_SESSION_KEY

from .helpers import code_markup, create_user, csp_violations
from .test_anonymous import VALID

DE = {'HTTP_ACCEPT_LANGUAGE': 'de'}
EN = {'HTTP_ACCEPT_LANGUAGE': 'en'}
ADDRESS = 'melder@example.org'

# Du-forms of address: every text for reporters is in Sie-form
DU_FORM = re.compile(r'\b(du|dich|dir|dein|deine|deiner|deinen|deinem|deines|euch|euer|eure)\b',
                     re.I)

BOOTSTRAP_PREFIXES = ('btn', 'form-', 'col-', 'text-', 'table', 'container', 'alert', 'float-',
                      'border', 'align-', 'jumbotron', 'clearfix', 'row', 'card', 'badge',
                      'navbar', 'nav-', 'list-group', 'comment')


class Html:
    """What the tests ask about a page: tags with attributes, classes, visible text."""

    class Parser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.tags = []
            self.text = []
            self.skip = 0

        def handle_starttag(self, tag, attrs):
            self.tags.append((tag, dict(attrs)))
            if tag in ('script', 'style'):
                self.skip += 1

        def handle_endtag(self, tag):
            if tag in ('script', 'style'):
                self.skip -= 1

        def handle_data(self, data):
            if not self.skip:
                self.text.append(data)

    def __init__(self, source):
        if hasattr(source, 'content'):
            source = source.content.decode()
        self.source = source
        parser = self.Parser()
        parser.feed(source)
        self.tags = parser.tags
        self.text = ' '.join(' '.join(parser.text).split())

    def find(self, tag, **attrs):
        return [a for t, a in self.tags
                if t == tag and all(a.get(k.replace('_', '-')) == v for k, v in attrs.items())]

    @property
    def classes(self):
        return {token for _, a in self.tags for token in a.get('class', '').split()}

    @property
    def title(self):
        return re.search(r'<title>(.*?)</title>', self.source, re.S).group(1).strip()

    def controls(self):
        """The fields a person fills in (not hidden inputs and buttons)."""
        return [a for t, a in self.tags
                if t in ('input', 'select', 'textarea')
                and a.get('type') not in ('hidden', 'submit', 'button')]

    def tag_of(self, name):
        """The opening tag of the control called name, as written in the page."""
        return re.search(r'<(?:input|select|textarea)[^>]*\sname="%s"[^>]*>' % name,
                         self.source).group(0)

    def section(self, label_id):
        return re.search(r'<section[^>]*aria-labelledby="%s".*?</section>' % label_id,
                         self.source, re.S).group(0)


def css_text():
    return ''.join(Path(settings.BASE_DIR, 'static/css', name).read_text(encoding='utf-8')
                   for name in ('core.css', 'labcirs.css'))


def grant_access(client, incident):
    session = client.session
    session['accessible_incident'] = incident.pk
    session.save()


class PageBase(TestCase):

    def setUp(self):
        self.dept = baker.make_recipe('cirs.department')
        self.kwargs = {'dept': self.dept.label}
        self.create_url = reverse('create_incident', kwargs=self.kwargs)
        self.search_url = reverse('incident_search', kwargs=self.kwargs)
        self.success_url = reverse('success', kwargs=self.kwargs)

    def report(self, **extra):
        """Sends a report as a reporter; returns the success page."""
        return self.client.post(self.create_url, dict(VALID, **extra), follow=True, **DE)

    def incident(self, **fields):
        return baker.make_recipe('cirs.public_ci', department=self.dept, **fields)

    def reviewer(self):
        reviewer = baker.make_recipe('cirs.reviewer')
        self.dept.reviewers.add(reviewer)
        return reviewer

    def detail(self, ci, lang=DE):
        grant_access(self.client, ci)
        return self.client.get(ci.get_absolute_url(), **lang)

    def pages(self):
        """Every state of the four pages, as (name, response)."""
        ci = self.incident(status='in process', photo='photos/2026/01/01/x.jpg')
        ReporterContact.objects.create(incident=ci, email=ADDRESS)
        baker.make(Comment, critical_incident=ci, author=self.dept.reporter.user,
                   text='Synthetic reporter comment')
        baker.make(Comment, critical_incident=ci, author=create_user('zz_qm'),
                   text='Synthetic QM comment')
        yield 'form', self.client.get(self.create_url, **DE)
        yield 'form with errors', self.client.post(self.create_url, {}, **DE)
        yield 'success', self.report()
        yield 'success again', self.client.get(self.success_url, **DE)
        yield 'search', self.client.get(self.search_url, **DE)
        yield 'search with error', self.client.post(self.search_url,
                                                    {'incident_code': 'nosuchcodeatall2'}, **DE)
        yield 'detail', self.detail(ci)
        yield 'detail with error', self.client.post(ci.get_absolute_url(), {'text': ''}, **DE)
        self.client.logout()
        self.client.force_login(self.reviewer().user)
        yield 'detail for the QM', self.client.get(ci.get_absolute_url(), **DE)


class ReportFormPageTest(PageBase):

    def get(self, lang=EN):
        return self.client.get(self.create_url, **lang)

    def test_every_visible_field_has_label(self):
        response = self.get()
        page = Html(response)
        for bound in response.context['form'].visible_fields():
            if bound.id_for_label:
                self.assertIn('for="%s"' % bound.id_for_label, page.source, bound.name)
            else:  # a group of radio buttons is named by its legend
                self.assertIn('<legend>%s' % bound.label, page.source, bound.name)
        labelled = {a.get('for') for a in page.find('label')}
        for control in page.controls():
            self.assertIn(control.get('id'), labelled, control)

    def test_every_field_is_a_ui_field_with_hint_linked_to_its_input(self):
        page = Html(self.get())
        self.assertGreaterEqual(page.source.count('class="ui-field"'), 6)
        self.assertRegex(page.source, r'<label class="ui-field__label" for="id_date">')
        self.assertIn('<p class="ui-field__hint" id="id_immediate_action_helptext">', page.source)
        self.assertRegex(page.tag_of('immediate_action'),
                         r'aria-describedby="id_immediate_action_helptext"')
        self.assertRegex(page.tag_of('preventability'),
                         r'aria-describedby="id_preventability_helptext"')

    def test_required_fields_are_marked_and_explained(self):
        page = Html(self.get(DE))
        self.assertRegex(page.tag_of('date'), r'\srequired')
        self.assertIn('Datum des Ereignisses *', page.text)
        self.assertIn('Felder mit * sind Pflichtangaben.', page.text)
        # the star of the note is read out ("Felder mit * ..."), the star of a label is not
        self.assertIn('<p class="ui-secondary">Felder mit <span class="ui-required">*</span> '
                      'sind Pflichtangaben.</p>', page.source)
        # the star is decoration: the input says it is required
        self.assertIn('<span class="ui-required" aria-hidden="true">*</span>', page.source)
        self.assertNotRegex(page.source, r'for="id_photo">[^<]*<span class="ui-required"')

    def test_date_input_is_type_date(self):
        self.assertIn('type="date"', Html(self.get()).tag_of('date'))

    def test_date_input_keeps_its_value_in_iso_format_and_refuses_the_future(self):
        response = self.client.post(self.create_url, {'date': '2026-09-01'}, **DE)
        tag = Html(response).tag_of('date')
        self.assertIn('value="2026-09-01"', tag)
        self.assertIn('max="%s"' % date.today().isoformat(), tag)

    def test_form_widgets_carry_no_bootstrap(self):
        form = IncidentCreateForm()
        self.assertEqual(form.fields['date'].widget.input_type, 'date')
        self.assertEqual(form.fields['date'].widget.format, '%Y-%m-%d')
        for form_class in (IncidentCreateForm, IncidentSearchForm, CommentForm):
            self.assertEqual(form_class.template_name, 'cirs/forms/ui_form.html')
            for field in form_class().fields.values():
                self.assertNotIn('form-', field.widget.attrs.get('class', ''))
        self.assertFalse(hasattr(cirs_forms, 'BootstrapRadioSelect'))
        self.assertFalse(Path(settings.BASE_DIR,
                              'cirs/templates/cirs/radio_option_bootstrap_4.html').exists())

    def test_hint_box_german(self):
        page = Html(self.get(DE))
        self.assertIn('Bevor Sie melden', page.text)
        for sentence in ('Beschreiben Sie Rollen statt Namen, z. B. „Pflegekraft im Nachtdienst“.',
                         'Geben Sie keine Patientendaten ein, also keine Namen, Geburtsdaten oder '
                         'Zimmernummern.',
                         'Besteht akute Gefahr, handeln Sie sofort und informieren Sie Ihre '
                         'Vorgesetzten. Diese Meldung ersetzt das nicht.',
                         'Meldungen sind freiwillig und sanktionsfrei.'):
            self.assertIn(sentence, page.text)
        box = re.search(r'<div class="ui-alert ui-alert--info">(.*?)</ul>', page.source, re.S)
        self.assertIsNotNone(box)
        self.assertEqual(box.group(1).count('<li>'), 4)

    def test_hint_box_english(self):
        text = Html(self.get()).text
        for sentence in ('Before you report',
                         'Describe roles, not names, e.g. “nurse on night shift”.',
                         'Do not enter patient data such as names, dates of birth or room '
                         'numbers.',
                         'If there is acute danger, act immediately and inform your supervisor. '
                         'This report does not replace that.',
                         'Reporting is voluntary and free of sanctions.'):
            self.assertIn(sentence, text)

    def test_submit_button(self):
        page = Html(self.get())
        self.assertIn('Submit report', page.text)
        form = re.search(r'<form[^>]*enctype="multipart/form-data"[^>]*>', page.source).group(0)
        self.assertIn('data-einmal-absenden', form)
        self.assertIn('method="post"', form)
        button = re.search(r'<button[^>]*data-busy-text[^>]*>', page.source).group(0)
        self.assertIn('class="ui-btn ui-btn--primary"', button)
        self.assertIn('data-busy-text="Sending report …"', button)
        self.assertIn('csrfmiddlewaretoken', re.search(r'<form[^>]*enctype.*?</form>', page.source,
                                                        re.S).group(0))
        de = Html(self.get(DE))
        self.assertIn('Meldung absenden', de.text)
        self.assertIn('data-busy-text="Meldung wird gesendet …"', de.source)

    @override_settings(DEFAULT_FROM_EMAIL='cirs@example.org')
    def test_email_field_is_the_last_field_before_the_button(self):
        source = self.get(DE).content.decode()
        positions = [source.index('name="%s"' % name)
                     for name in ('date', 'incident', 'reason', 'immediate_action',
                                  'preventability', 'photo', 'org_unit', 'reporter_email')]
        self.assertEqual(positions, sorted(positions))
        self.assertLess(positions[-1], source.index('Meldung absenden'))

    def test_photo_input_offers_photo_formats_and_says_what_is_allowed(self):
        page = Html(self.get(DE))
        tag = page.tag_of('photo')
        self.assertIn('accept="image/jpeg,image/png,image/gif,image/webp"', tag)
        self.assertIn('aria-describedby="id_photo_helptext"', tag)
        self.assertIn('Freiwillig. Erlaubt sind JPEG, PNG, GIF oder WebP bis 10 MB. Metadaten wie '
                      'Ort und Gerät werden beim Hochladen entfernt.', page.text)

    def test_select_has_no_framework_placeholder(self):
        page = Html(self.get(DE))
        self.assertNotIn('---------', page.source)
        self.assertIn('<option value="" selected>Bitte wählen</option>', page.source)

    @override_settings(ASK_PUBLICATION_CONSENT=True)
    def test_consent_is_a_fieldset_with_legend_and_check_labels(self):
        page = Html(self.get(DE))
        group = re.search(r'<fieldset[^>]*>\s*<legend>(.*?)</legend>(.*?)</fieldset>',
                          page.source, re.S)
        self.assertIsNotNone(group, page.source)
        self.assertIn('Veröffentlichung', group.group(1))
        self.assertEqual(group.group(2).count('class="ui-check"'), 2)
        self.assertIn('for="id_public_0"', group.group(2))
        self.assertIn('for="id_public_1"', group.group(2))
        # the group is one required choice; the fieldset is the way to it from the summary
        self.assertIn('id="id_public"', group.group(0))

    def test_errors_are_a_summary_and_at_the_field(self):
        response = self.client.post(self.create_url, {}, **DE)
        page = Html(response)
        summary = re.search(r'<div class="ui-alert ui-alert--danger"[^>]*>', page.source).group(0)
        for attribute in ('role="alert"', 'tabindex="-1"', 'autofocus'):
            self.assertIn(attribute, summary)
        self.assertIn('Bitte prüfen Sie Ihre Eingaben.', page.text)
        self.assertIn('<a href="#id_date">Datum des Ereignisses</a>', page.source)
        # at the field: text, aria-invalid and the link from the input to the text
        self.assertIn('id="id_date_error"', page.source)
        tag = page.tag_of('date')
        self.assertIn('aria-invalid="true"', tag)
        self.assertIn('aria-describedby="id_date_error"', tag)
        self.assertIn('aria-describedby="id_immediate_action_helptext id_immediate_action_error"',
                      page.tag_of('immediate_action'))
        self.assertEqual(CriticalIncident.objects.count(), 0)

    def test_valid_page_has_no_error_summary(self):
        self.assertNotIn('role="alert"', self.get().content.decode())

    def test_error_of_the_whole_form_is_listed(self):
        response = self.client.post(self.create_url, dict(VALID, date='2999-01-01'), **EN)
        self.assertContains(response, 'Please report only incidents which already happened.')
        self.assertContains(response, 'role="alert"')

    def test_dead_javascript_block_is_gone(self):
        source = Path(settings.BASE_DIR,
                      'cirs/templates/cirs/criticalincident_form.html').read_text(encoding='utf-8')
        self.assertNotIn('block javascript', source)
        self.assertNotIn('jquery', source.lower())
        self.assertNotIn('<script', source)

    def test_the_rest_of_the_old_form_is_gone(self):
        # the old note said "anonymous", which the optional address contradicts
        self.assertNotIn('Anonymous report will be send', self.get().content.decode())


class SuccessPageTest(PageBase):

    def test_success_shows_code_in_code_element(self):
        response = self.report()
        code = CriticalIncident.objects.get().comment_code
        html = response.content.decode()
        self.assertIn(code_markup(code), html)
        self.assertEqual(html.count('<code'), 1)
        # grouped for the eye, but not once as a whole string: the code is shown in its groups
        self.assertNotIn(code, html)
        self.assertIn(group_code(code), Html(html).text)

    @parameterized.expand([('ab#d$f-9', 'ab#d', '$f-9'),
                           ('#%&*+-=@', '#%&amp;*', '+-=@')])  # the & is escaped like any text
    def test_success_shows_an_old_code_in_two_groups(self, code, first, second):
        session = self.client.session
        session[NEW_CODE_SESSION_KEY] = code
        session.save()
        html = self.client.get(self.success_url).content.decode()
        self.assertIn('<code class="ui-meldecode"><span class="ui-meldecode__gruppe">%s</span>'
                      ' <span class="ui-meldecode__gruppe">%s</span></code>' % (first, second),
                      html)

    def test_a_group_of_the_code_never_breaks(self):
        # the old rule broke the code after any character (break-all): a reporter noted 11 of 16
        css = css_text()
        rule = re.search(r'\.ui-meldecode__gruppe\s*\{([^}]*)\}', css).group(1)
        self.assertRegex(rule, r'white-space:\s*nowrap')
        code_rule = re.search(r'\.ui-meldecode\s*\{([^}]*)\}', css).group(1)
        self.assertNotIn('break-all', code_rule)
        self.assertNotIn('break-word', code_rule)
        self.assertNotIn('anywhere', code_rule)

    def test_success_text_and_hint_german(self):
        page = Html(self.report())
        self.assertIn('Vielen Dank', page.text)
        self.assertIn('Ihre Meldung ist eingegangen.', page.text)
        self.assertIn('Ihr Code', page.text)
        # the hint on the success page, word for word
        self.assertIn('Bitte notieren Sie diesen Code. Sie brauchen ihn, um den Stand Ihrer '
                      'Meldung zu verfolgen und Rückfragen zu beantworten.', page.text)
        self.assertIn('Der Code lässt sich nicht wiederherstellen.', page.text)

    def test_success_message_is_announced_as_status(self):
        page = Html(self.report())
        self.assertEqual(page.source.count('role="status"'), 1)
        self.assertRegex(page.source,
                         r'<div class="ui-alert ui-alert--success[^"]*" role="status">')

    def test_success_links(self):
        response = self.report()
        page = Html(response)
        links = {a['href']: a['class'] for a in page.find('a') if 'ui-btn' in a.get('class', '')}
        self.assertEqual(set(links), {self.search_url,
                                      reverse('incidents_for_department', kwargs=self.kwargs),
                                      self.create_url})
        self.assertEqual(links[self.search_url], 'ui-btn ui-btn--primary')
        self.assertEqual(page.source.count('ui-btn--primary'), 1)  # one main action
        for text in ('Meine Meldung ansehen', 'Veröffentlichte Fälle ansehen',
                     'Weiteres Ereignis melden'):
            self.assertIn(text, page.text)

    @override_settings(DEFAULT_FROM_EMAIL='cirs@example.org')
    def test_mail_sentence_only_when_an_address_can_be_given(self):
        sentence = 'Haben Sie eine E-Mail-Adresse angegeben, erhalten Sie den Code zusätzlich per '
        self.assertIn(sentence, Html(self.report()).text)
        with override_settings(DEFAULT_FROM_EMAIL=''):
            self.assertNotIn(sentence, Html(self.report()).text)

    def test_code_is_shown_once(self):
        self.report()
        response = self.client.get(self.success_url, **DE)
        page = Html(response)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('<code', page.source)
        self.assertIn('Diese Seite zeigt den Code nur einmal, direkt nach dem Absenden.', page.text)
        self.assertEqual(page.source.count('<h1'), 1)

    def test_success_is_a_form_width_page(self):
        self.assertIn('ui-main ui-main--form', self.report().content.decode())


class CodeSearchPageTest(PageBase):

    def test_search_form(self):
        page = Html(self.client.get(self.search_url, **EN))
        self.assertIn('for="id_incident_code"', page.source)
        tag = page.tag_of('incident_code')
        for attribute in ('autocomplete="off"', 'autocapitalize="none"', 'spellcheck="false"',
                          'aria-describedby="id_incident_code_helptext"'):
            self.assertIn(attribute, tag)
        self.assertIn('Check code', page.text)
        form = re.search(r'<form[^>]*data-einmal-absenden[^>]*>', page.source).group(0)
        self.assertIn('method="post"', form)
        self.assertNotIn('enctype', form)
        self.assertIn('class="ui-btn ui-btn--primary"', page.source)
        self.assertNotIn('Search', page.text)

    def test_search_page_german(self):
        page = Html(self.client.get(self.search_url, **DE))
        self.assertIn('<p class="ui-secondary">Felder mit <span class="ui-required">*</span> '
                      'sind Pflichtangaben.</p>', page.source)
        self.assertIn('Code prüfen', page.text)
        self.assertIn('data-busy-text="Wird geprüft …"', page.source)
        self.assertIn('Code *', page.text)
        self.assertIn('Der Code hat 16 Zeichen, ältere Codes haben 8. Leerzeichen sowie Groß- '
                      'und Kleinschreibung spielen keine Rolle.', page.text)
        self.assertIn('Behalten Sie den Code für sich.', page.text)

    def test_unknown_code_is_a_summary_and_a_field_error(self):
        response = self.client.post(self.search_url, {'incident_code': 'nosuchcodeatall2'}, **DE)
        page = Html(response)
        self.assertIn('role="alert"', page.source)
        self.assertIn('Zu diesem Code wurde keine Meldung gefunden.', page.text)
        tag = page.tag_of('incident_code')
        self.assertIn('aria-invalid="true"', tag)
        self.assertIn('aria-describedby="id_incident_code_helptext id_incident_code_error"', tag)
        self.assertIn('value="nosuchcodeatall2"', tag)

    def test_wrong_length_is_a_summary_and_a_field_error_too(self):
        response = self.client.post(self.search_url, {'incident_code': 'nosuchcode'}, **DE)
        page = Html(response)
        self.assertIn('role="alert"', page.source)
        self.assertIn('Sie haben 10 Zeichen eingegeben. Ein Code hat 16 Zeichen, ältere Codes 8. '
                      'Vielleicht fehlt ein Teil.', page.text)
        self.assertNotIn('Zu diesem Code wurde keine Meldung gefunden.', page.text)
        tag = page.tag_of('incident_code')
        self.assertIn('aria-invalid="true"', tag)
        self.assertIn('value="nosuchcode"', tag)

    def test_message_after_ending_access_is_shown_once(self):
        ci = self.incident()
        grant_access(self.client, ci)
        url = reverse('end_incident_access', kwargs={'dept': self.dept.label, 'pk': ci.pk})
        response = self.client.post(url, follow=True, **DE)
        self.assertEqual(response.content.decode().count('Der Zugang zu dieser Meldung ist '
                                                         'beendet.'), 1)


class DetailPageTest(PageBase):

    def setUp(self):
        super().setUp()
        self.ci = self.incident(status='in process', date=date(2026, 8, 7),
                                incident='Synthetic incident line one\nline two',
                                preventability='avoidable')

    def comment(self, author, text, created):
        return baker.make(Comment, critical_incident=self.ci, author=author, text=text,
                          created=created)

    def test_detail_lists_comments_and_reply_form(self):
        self.comment(create_user('zz_qm'), 'Synthetic question', date(2026, 8, 11))
        self.comment(self.dept.reporter.user, 'Synthetic answer', date(2026, 8, 12))
        page = Html(self.detail(self.ci))
        comments = page.section('rueckmeldungen-titel')
        self.assertLess(comments.index('Synthetic question'), comments.index('Synthetic answer'))
        self.assertIn('2 Rückmeldungen, die älteste zuerst.', page.text)
        self.assertIn('11.08.2026', comments)
        self.assertIn('<time datetime="2026-08-11">', comments)
        # the reply form: a labelled field, one primary button, the guard against double sending
        self.assertIn('for="id_text"', page.source)
        self.assertIn('Ihre Antwort', page.text)
        form = re.search(r'<form[^>]*method="post"[^>]*>(?:(?!</form>).)*id_text.*?</form>',
                         page.source, re.S).group(0)
        self.assertIn('data-einmal-absenden', form)
        self.assertIn('Antwort senden', form)
        self.assertIn('data-busy-text="Wird gesendet …"', form)
        self.assertIn('ui-btn ui-btn--primary', form)

    def test_a_reply_is_confirmed_at_the_top_of_the_page(self):
        # the new reply is at the end of the list; without a message the reload looks like nothing
        grant_access(self.client, self.ci)
        response = self.client.post(self.ci.get_absolute_url(), {'text': 'Synthetic reply'},
                                    follow=True, **DE)
        page = Html(response)
        self.assertRegex(page.source, r'<div class="ui-alert ui-alert--success" role="status">')
        self.assertIn('Ihre Antwort wurde gespeichert. Sie steht jetzt unter „Rückmeldungen“.',
                      page.text)
        self.assertIn('Synthetic reply', page.section('rueckmeldungen-titel'))
        # a failed reply is not confirmed
        response = self.client.post(self.ci.get_absolute_url(), {'text': ''}, **DE)
        self.assertNotIn('ui-alert--success', response.content.decode())

    def test_replies_are_oldest_first_also_when_saved_out_of_order(self):
        self.comment(self.dept.reporter.user, 'Synthetic second', date(2026, 8, 12))
        self.comment(create_user('zz_qm'), 'Synthetic first', date(2026, 8, 11))
        comments = Html(self.detail(self.ci)).section('rueckmeldungen-titel')
        self.assertLess(comments.index('Synthetic first'), comments.index('Synthetic second'))

    def test_no_replies_yet_says_so(self):
        page = Html(self.detail(self.ci))
        self.assertIn('Es gibt noch keine Rückmeldungen.', page.text)
        self.assertEqual(page.find('article'), [])

    def test_authors_are_roles_never_user_names(self):
        # the reporter account is the technical author of the reporter's replies
        dept = baker.make_recipe('cirs.department', reporter__user__username='zz_reporter_account')
        ci = baker.make_recipe('cirs.public_ci', department=dept)
        baker.make(Comment, critical_incident=ci, author=dept.reporter.user, text='From reporter')
        qm = baker.make_recipe('cirs.reviewer', user__username='zz_qm_person')
        dept.reviewers.add(qm)
        baker.make(Comment, critical_incident=ci, author=qm.user, text='From QM')
        baker.make(Comment, critical_incident=ci, author=create_user('zz_other'), text='From admin')
        grant_access(self.client, ci)
        comments = Html(self.client.get(ci.get_absolute_url(), **DE)).section('rueckmeldungen-titel')
        for name in ('zz_reporter_account', 'zz_qm_person', 'zz_other'):
            self.assertNotIn(name, comments)
        titles = re.findall(r'<h3[^>]*>(.*?)</h3>', comments, re.S)
        roles = [' '.join(unescape(re.sub(r'<[^>]+>', '', t)).split()).split(' · ')[0]
                 for t in titles]
        self.assertEqual(roles, ['Meldende Person', 'Qualitätsmanagement', 'Qualitätsmanagement'])
        en = Html(self.client.get(ci.get_absolute_url(), **EN)).section('rueckmeldungen-titel')
        self.assertIn('Reporting person', en)
        self.assertIn('Quality management', en)

    def test_the_qm_sees_roles_too(self):
        qm = self.reviewer()
        self.comment(qm.user, 'Synthetic QM reply', date(2026, 8, 11))
        self.client.force_login(qm.user)
        comments = Html(self.client.get(self.ci.get_absolute_url(), **DE)).section(
            'rueckmeldungen-titel')
        self.assertIn('Qualitätsmanagement', comments)
        self.assertNotIn(qm.user.username, comments)

    @parameterized.expand([('new', 'Eingegangen', 'info'), ('in process', 'In Bearbeitung', 'info'),
                           ('under supervision', 'Maßnahmen umgesetzt, in Beobachtung', 'info'),
                           ('completed', 'Abgeschlossen', 'success')])
    def test_status_is_a_badge_with_the_word(self, status, label, tone):
        self.ci.status = status
        self.ci.save()
        page = Html(self.detail(self.ci))
        badge = re.search(r'Stand:\s*<span class="ui-badge ui-badge--(\w+)">([^<]*)</span>',
                          page.source)
        self.assertEqual((badge.group(1), badge.group(2).strip()), (tone, label))

    def test_status_steps_mark_the_current_one(self):
        page = Html(self.detail(self.ci))
        steps = re.findall(r'<li[^>]*>.*?</li>', page.section('stand-titel'), re.S)
        self.assertEqual(len(steps), 4)
        self.assertIn('Eine Meldung durchläuft diese vier Stufen:', page.text)
        current = [s for s in steps if 'aria-current="step"' in s]
        self.assertEqual(len(current), 1)
        self.assertIn('In Bearbeitung', current[0])
        self.assertIn('aktueller Stand', current[0])

    def test_the_report_is_a_definition_list_with_dates_in_the_house_format(self):
        page = Html(self.detail(self.ci))
        data = page.section('ereignis-titel')
        self.assertIn('<dl class="ui-recht__daten">', data)
        self.assertIn('<time datetime="2026-08-07">07.08.2026</time>', data)
        self.assertIn('Synthetic incident line one<br>line two', data)  # line breaks stay
        self.assertIn('Das Ereignis war vermeidbar', data)
        self.assertNotIn('Foto', data)  # no photo, no row

    def test_photo_is_a_link_without_modal_or_script(self):
        self.ci.photo = 'photos/2026/01/01/x.jpg'
        self.ci.save()
        page = Html(self.detail(self.ci))
        data = page.section('ereignis-titel')
        self.assertIn('href="%sphotos/2026/01/01/x.jpg"' % settings.MEDIA_URL, data)
        self.assertIn('target="_blank" rel="noopener"', data)
        self.assertIn('Foto', data)
        self.assertNotIn('<dialog', page.source)
        self.assertNotIn('modal', page.source.lower())
        self.assertEqual([a for a in page.find('script') if not a.get('src')], [])

    def test_email_notice_and_remove_form(self):
        ReporterContact.objects.create(incident=self.ci, email=ADDRESS)
        page = Html(self.detail(self.ci))
        email = page.section('email-titel')
        self.assertIn('E-Mail-Benachrichtigung ist aktiv.', email)
        self.assertIn('nie Ihre Meldung selbst', ' '.join(unescape(email).split()))
        self.assertIn('action="%s"' % reverse('remove_reporter_email',
                                              kwargs={'dept': self.dept.label, 'pk': self.ci.pk}),
                      email)
        self.assertIn('data-einmal-absenden', email)
        self.assertRegex(email, r'<button[^>]*class="ui-btn ui-btn--secondary"[^>]*>\s*E-Mail-'
                                r'Adresse löschen')
        # the confirmation is a required checkbox with its own label
        self.assertRegex(email, r'<label class="ui-check" for="id_confirm">\s*<input[^>]*'
                                r'name="confirm"[^>]*\srequired[^>]*>\s*<span>Ich möchte meine '
                                r'E-Mail-Adresse endgültig löschen\.</span>')
        self.assertNotIn(ADDRESS, page.source)

    def test_no_email_section_without_contact(self):
        page = Html(self.detail(self.ci))
        self.assertNotIn('E-Mail-Benachrichtigung ist aktiv.', page.text)
        self.assertNotIn('id="email-titel"', page.source)

    def test_reporter_ends_access_with_a_post_form(self):
        page = Html(self.detail(self.ci))
        form = re.search(r'<form[^>]*action="%s"[^>]*>.*?</form>' % reverse(
            'end_incident_access', kwargs={'dept': self.dept.label, 'pk': self.ci.pk}),
            page.source, re.S).group(0)
        self.assertIn('method="post"', form)
        self.assertIn('data-einmal-absenden', form)
        self.assertIn('ui-btn ui-btn--secondary', form)
        self.assertIn('Zugang beenden', form)

    def test_qm_gets_neither_reporter_texts_nor_the_end_access_form(self):
        qm = self.reviewer()
        self.client.force_login(qm.user)
        page = Html(self.client.get(self.ci.get_absolute_url(), **DE))
        self.assertNotIn('Zugang beenden', page.text)
        self.assertNotIn('Sie sehen diese Meldung, weil Sie', page.text)
        self.assertNotIn('Ihre Meldung', page.text)  # the report is not "theirs" for the QM
        status = page.section('stand-titel')
        self.assertIn('ui-badge', status)
        self.assertNotIn('<li', status)  # the four steps explain the process to the reporter

    def test_reply_hint_for_the_reporter_and_the_qm(self):
        page = Html(self.detail(self.ci))
        hint = ('Schreiben Sie keine Namen und keine Patientendaten in Ihre Antwort. Beschreiben '
                'Sie Personen über ihre Rolle, z. B. „Pflegekraft im Nachtdienst“.')
        self.assertIn(hint, page.text)
        self.assertIn('aria-describedby="id_text_helptext"', page.tag_of('text'))
        # the QM has the hint above the form, not a second one at the field
        self.client.logout()
        self.client.force_login(self.reviewer().user)
        page = Html(self.client.get(self.ci.get_absolute_url(), **DE))
        self.assertNotIn(hint, page.text)
        self.assertEqual(page.text.count('Schreiben Sie keine Namen oder Patientendaten.'), 1)
        self.assertIn('Ihre Antwort geht zusätzlich per E-Mail an die meldende Person, wenn sie '
                      'Benachrichtigungen eingerichtet hat.', page.text)


class AllFourPagesTest(PageBase):
    """What holds for every state of every page."""

    def test_no_inline_code(self):
        for name, response in self.pages():
            self.assertEqual(csp_violations(response.content.decode()), [], name)

    def test_no_template_source_in_output(self):
        # {# #} spans one line only; a longer one, or a tag in a comment, would be printed
        for name, response in self.pages():
            html = response.content.decode()
            for source in ('{#', '#}', '{%', '%}', '{{', '}}'):
                self.assertNotIn(source, html, name)

    def test_no_bootstrap_classes_left(self):
        for name, response in self.pages():
            html = response.content.decode()
            for token in ('form-control', 'btn-primary', 'btn-danger', 'btn-info', 'jumbotron',
                          'table-responsive', 'alert-success', 'alert-info', 'form-check'):
                self.assertNotIn(token, html, name)
            for token in Html(html).classes:
                if not token.startswith(('ui-', 'labcirs-')):
                    self.fail('%s: class "%s" is not from the design system' % (name, token))

    def test_every_class_has_a_rule(self):
        # a class without a rule is a class from an old stylesheet
        css = css_text()
        for name, response in self.pages():
            for token in Html(response).classes:
                self.assertRegex(css, r'\.%s(?![\w-])' % re.escape(token),
                                 '%s: no rule for "%s"' % (name, token))

    def test_one_h1_and_a_title_of_the_page(self):
        titles = {}
        for name, response in self.pages():
            page = Html(response)
            self.assertEqual(page.source.count('<h1'), 1, name)
            self.assertNotEqual(page.title.split(' · ')[0], settings.ORGANIZATION, name)
            titles[name] = page.title
        self.assertRegex(titles['form'], r'^Ereignis melden · ')
        self.assertRegex(titles['success'], r'^Vielen Dank · ')
        self.assertRegex(titles['search'], r'^Meine Meldung · ')
        self.assertRegex(titles['detail'], r'^Ihre Meldung · ')
        self.assertRegex(titles['detail for the QM'], r'^Meldung · ')
        # the tab shows the start of the title, so two pages must differ there
        self.assertEqual(len({titles[n] for n in ('form', 'success', 'search', 'detail')}), 4)

    def test_no_du_form_and_no_english_left_over_in_german(self):
        for name, response in self.pages():
            text = Html(response).text
            self.assertNotRegex(text, DU_FORM, name)
        text = Html(self.client.get(self.create_url, **DE)).text
        for english in ('Submit', 'Send', 'Search', 'Save', 'Preventability', 'Publication'):
            self.assertNotIn(english, text)

    def test_the_old_du_form_hints_are_sie_form(self):
        page = Html(self.client.get(self.create_url, **DE))
        self.assertIn('War das Ereignis Ihrer Meinung nach vermeidbar oder nicht vermeidbar?',
                      page.text)
        self.assertIn('Nennen Sie, was sofort unternommen wurde und was Sie zur Vermeidung oder '
                      'Behebung vorschlagen.', page.text)
        self.assertIn('Datum des Ereignisses', page.text)  # was "Ereignises"
        # the error of the model for a date in the future
        page = Html(self.client.post(self.create_url, dict(VALID, date='2999-01-01'), **DE))
        self.assertIn('Bitte melden Sie nur Ereignisse, die bereits geschehen sind.', page.text)

    def test_pages_are_usable_without_javascript(self):
        # the only script is the shared one from base.html; forms carry no handlers
        for name, response in self.pages():
            scripts = Html(response).find('script')
            self.assertTrue(all('formular' in s.get('src', '') for s in scripts), name)


class CachingAndCodeTest(PageBase):
    """Shared ward PCs: the Back button must not bring back a code or a report."""

    def test_pages_with_a_code_or_a_report_are_never_stored(self):
        ci = self.incident()
        ReporterContact.objects.create(incident=ci, email=ADDRESS)
        remove_url = reverse('remove_reporter_email', kwargs={'dept': self.dept.label, 'pk': ci.pk})
        responses = {'success (with the code)': self.report(),
                     'success (reloaded)': self.client.get(self.success_url),
                     'search': self.client.get(self.search_url),
                     'search (unknown code)': self.client.post(self.search_url,
                                                               {'incident_code': 'nosuchcodeatall2'}),
                     'detail': self.detail(ci),
                     'detail (reply that fails)': self.client.post(ci.get_absolute_url(),
                                                                   {'text': ''}),
                     'remove address (GET)': self.client.get(remove_url),
                     'remove address (POST)': self.client.post(remove_url, {'confirm': 'on'}),
                     'detail without access': Client().get(ci.get_absolute_url())}
        for name, response in responses.items():
            self.assertIn('no-store', response['Cache-Control'], name)

    def test_the_qm_page_is_not_stored_either(self):
        ci = self.incident()
        self.client.force_login(self.reviewer().user)
        self.assertIn('no-store', self.client.get(ci.get_absolute_url())['Cache-Control'])

    def test_detail_pages_never_show_the_code(self):
        # the code is what gives access; it is shown once, after sending, and nowhere else
        ci = self.incident()
        baker.make(Comment, critical_incident=ci, author=self.dept.reporter.user, text='Synthetic')
        self.assertNotContains(self.detail(ci), ci.comment_code)
        self.assertNotContains(self.detail(ci, EN), ci.comment_code)
        self.client.logout()
        self.client.force_login(self.reviewer().user)
        self.assertNotContains(self.client.get(ci.get_absolute_url()), ci.comment_code)

    def test_a_stray_message_is_never_shown_as_the_code(self):
        ci = self.incident()
        grant_access(self.client, ci)
        url = reverse('end_incident_access', kwargs={'dept': self.dept.label, 'pk': ci.pk})
        self.client.post(url, **DE)  # leaves a message ("Access ... has ended") that nobody has read
        response = self.client.get(self.success_url, **DE)
        self.assertNotContains(response, '<code')
        self.assertContains(response, 'Diese Seite zeigt den Code nur einmal')
        # the message is a message: an alert of the frame, read by nobody as a code
        self.assertContains(response, 'Der Zugang zu dieser Meldung ist beendet.')
        # and the real code still is the code, with the stray message next to it
        self.client.post(url, **DE)
        response = self.report()
        code = CriticalIncident.objects.latest('pk').comment_code
        self.assertIn(code_markup(code), response.content.decode())
        self.assertEqual(response.content.decode().count('<code'), 1)

    def test_the_code_is_gone_after_one_view(self):
        self.report()
        code = CriticalIncident.objects.get().comment_code
        self.assertNotIn(code, str(dict(self.client.session)))  # popped, not kept
        self.assertNotContains(self.client.get(self.success_url), '<code')
        self.assertNotIn(code, str(dict(self.client.session)))

    @parameterized.expand([('unknown', False), ('inactive', True)])
    def test_success_page_of_an_unknown_or_inactive_department_is_404(self, name, inactive):
        label = baker.make_recipe('cirs.department', active=False).label if inactive else 'unknown'
        self.assertEqual(self.client.get(reverse('success', kwargs={'dept': label})).status_code,
                         404)


class MessageWordingTest(PageBase):

    def test_the_redirect_message_for_the_qm_is_spelled_right(self):
        self.client.force_login(self.reviewer().user)
        response = self.client.get(self.search_url, follow=True, **DE)
        text = Html(response).text
        self.assertIn('in der letzten Spalte (gilt für veröffentlichte Ereignisse)', text)
        for typo in ('letzen', 'Ereignise'):
            self.assertNotIn(typo, text)

    def test_no_typo_of_the_old_catalog_in_the_german_texts(self):
        for name in ('cirs/locale/de/LC_MESSAGES/django.po', 'locale/de/LC_MESSAGES/django.po'):
            text = Path(settings.BASE_DIR, name).read_text(encoding='utf-8')
            live = '\n'.join(l for l in text.splitlines() if not l.startswith('#~'))
            for typo in ('Ereignise ', 'Ereignise"', 'Ereignise)', 'letzen'):
                self.assertNotIn(typo, live, (name, typo))


class FrameCarryTest(PageBase):
    """Checks of the page frame in base.html."""

    def test_logout_form_guards_against_double_submit(self):
        self.client.force_login(self.reviewer().user)
        html = self.client.get(self.dept.get_absolute_url()).content.decode()
        form = re.search(r'<form[^>]*action="%s"[^>]*>' % reverse('logout'), html).group(0)
        self.assertIn('data-einmal-absenden', form)

    def test_source_link_is_in_the_footer_navigation(self):
        source_url = 'https://example.org/source'
        with override_settings(SOURCE_URL=source_url):
            html = self.client.get(self.dept.get_absolute_url()).content.decode()
        self.assertTrue(re.search(r'<nav class="ui-fuss__wege".*?href="%s".*?</nav>'
                                  % re.escape(source_url), html, re.S), html)
