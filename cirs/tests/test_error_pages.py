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

from unittest import mock

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.middleware import AuthenticationMiddleware
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.exceptions import PermissionDenied, SuspiciousOperation
from django.db import DEFAULT_DB_ALIAS, OperationalError, connections
from django.http import Http404
from django.middleware.csrf import _get_failure_view
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import get_resolver, reverse
from django.utils import translation
from django.views import defaults
from model_bakery import baker

import cirs
from cirs.models import Department
from cirs.tests.helpers import csp_violations

# Django's own English pages (no framework text reaches the user).
DJANGO_DEFAULT_TEXTS = ('Not Found', 'Server Error', 'Bad Request', 'Forbidden', 'CSRF')
BRANDING = {'SITE_NAME': 'Synthetic CIRS', 'ORGANIZATION': 'Synthetic Clinic',
            'LOGO_URL': '/branding/synthetic-logo.svg', 'THEME_CSS_URL': '/branding/synthetic.css',
            'IMPRINT_URL': '/branding/synthetic-imprint.html',
            'PRIVACY_URL': '/branding/synthetic-privacy.html',
            'SOURCE_URL': 'https://example.org/synthetic-source'}


@override_settings(DEBUG=False)
class ErrorPagesTest(TestCase):

    def assertErrorPage(self, response, status, heading, text, absent=()):
        self.assertEqual(response.status_code, status)
        html = response.content.decode()
        self.assertIn(heading, html)
        self.assertIn(text, html)
        self.assertIn('Fehler %d' % status, html)
        self.assertIn('ui-ente', html)
        self.assertEqual(html.count('<h1'), 1)
        self.assertEqual(html.count('ui-btn--primary'), 1)
        self.assertIn('Zur Startseite', html)
        self.assertIn('<html lang="de"', html)
        self.assertNotRegex(html, '[\u2013\u2014]')  # no en or em dash
        self.assertEqual(csp_violations(html), [])
        for unwanted in DJANGO_DEFAULT_TEXTS + tuple(absent):
            self.assertNotIn(unwanted, html)
        return html

    def get_with_failing_view(self, exception):
        """GET / while the view raises exception; the client returns the error response."""
        client = Client(raise_request_exception=False)
        with mock.patch('cirs.views.DepartmentList.get_queryset', side_effect=exception):
            return client.get(reverse('labcirs_home'), HTTP_ACCEPT_LANGUAGE='de')

    def test_404_page_is_german_and_styled(self):
        response = self.client.get('/gibt-es-nicht-4711/?code=GEHEIM4711',
                                   HTTP_ACCEPT_LANGUAGE='de')
        self.assertErrorPage(response, 404, 'Seite nicht gefunden',
                             'Diese Adresse gibt es nicht oder nicht mehr.',
                             absent=('gibt-es-nicht-4711', 'GEHEIM4711'))

    def test_error_pages_do_not_show_pending_messages(self):
        # IncidentCreate's success message is the plain reporter code; an error page that
        # follows must not print it (the empty messages block in errors/http.html).
        request = RequestFactory().get('/gibt-es-nicht/')
        SessionMiddleware(lambda r: None).process_request(request)
        request._messages = FallbackStorage(request)
        messages.add_message(request, messages.SUCCESS, 'CODE-4711-SYNTH')
        for response in (defaults.page_not_found(request, Http404()),
                         defaults.permission_denied(request, PermissionDenied())):
            with self.subTest(status=response.status_code):
                html = response.content.decode()
                self.assertIn('ui-ente', html)
                self.assertNotIn('CODE-4711-SYNTH', html)

    @override_settings(**BRANDING)
    def test_500_page_without_exception_text(self):
        response = self.get_with_failing_view(RuntimeError('geheim-123'))
        html = self.assertErrorPage(
            response, 500, 'Etwas ist schiefgelaufen',
            'Der Fehler wurde nicht durch Sie verursacht. '
            'Bitte versuchen Sie es später noch einmal.',
            absent=('geheim-123', 'Traceback', 'RuntimeError'))
        for value in BRANDING.values():
            self.assertIn(value, html)

    @override_settings(**BRANDING)
    def test_500_renders_when_database_is_unavailable(self):
        # A request as in production: session cookie, lazy session and user. A handler that
        # touches either reaches the patched cursor below and fails.
        request = RequestFactory().get('/')
        request.COOKIES[settings.SESSION_COOKIE_NAME] = 'x' * 32
        SessionMiddleware(lambda r: None).process_request(request)
        AuthenticationMiddleware(lambda r: None).process_request(request)
        handlers = (
            (500, lambda: get_resolver().resolve_error_handler(500)(request)),
            (400, lambda: get_resolver().resolve_error_handler(400)(
                request, SuspiciousOperation('geheim-400'))),
            (403, lambda: _get_failure_view()(request, reason='geheim-csrf')),
        )
        with mock.patch.object(connections[DEFAULT_DB_ALIAS], 'cursor',
                               side_effect=OperationalError('database unavailable')):
            with self.assertRaises(OperationalError):
                Department.objects.count()  # the database is really out of reach
            with self.assertRaises(OperationalError):
                request.user.is_authenticated  # and so are session and user
            with translation.override('de'):
                responses = [(status, render()) for status, render in handlers]
        for status, response in responses:
            with self.subTest(status=status):
                self.assertEqual(response.status_code, status)
                html = response.content.decode()
                self.assertIn('ui-ente', html)
                self.assertIn('<html lang="de"', html)
                self.assertIn('Fehler %d' % status, html)
                self.assertNotIn('geheim', html)
                for value in BRANDING.values():
                    self.assertIn(value, html)
                # the footer of the normal pages: legal links, the source link and the version
                self.assertIn('<a href="%s">Impressum</a>' % BRANDING['IMPRINT_URL'], html)
                self.assertIn('<a href="%s">Datenschutz</a>' % BRANDING['PRIVACY_URL'], html)
                self.assertIn('href="%s"' % BRANDING['SOURCE_URL'], html)
                self.assertIn('Version %s' % cirs.__version__, html)
        self.assertIn('Etwas ist schiefgelaufen', responses[0][1].content.decode())

    def test_403_and_400_pages(self):
        self.assertErrorPage(self.get_with_failing_view(PermissionDenied('geheim-403')),
                             403, 'Kein Zugriff', 'Für diese Seite fehlt Ihnen die Berechtigung.',
                             absent=('geheim-403',))
        self.assertErrorPage(
            self.get_with_failing_view(SuspiciousOperation('geheim-400')),
            400, 'Anfrage nicht verständlich',
            'Die Anfrage konnte nicht verarbeitet werden. Bitte versuchen Sie es noch einmal.',
            absent=('geheim-400',))

    def test_csrf_failure_page(self):
        dept = baker.make_recipe('cirs.department')
        client = Client(enforce_csrf_checks=True)
        response = client.post(reverse('create_incident', kwargs={'dept': dept.label}),
                               {'incident': 'Synthetic'}, HTTP_ACCEPT_LANGUAGE='de')
        self.assertErrorPage(
            response, 403, 'Sitzung abgelaufen',
            'Bitte laden Sie die Seite neu und senden Sie das Formular noch einmal.')

    def test_error_pages_show_duck_sentence_and_quack(self):
        html = self.client.get('/gibt-es-nicht/', HTTP_ACCEPT_LANGUAGE='de').content.decode()
        self.assertIn('Quak!', html)
        self.assertIn('Warum eine Ente?', html)
        self.assertRegex(html, r'<svg class="ui-ente__bild"[^>]*aria-hidden="true"')
