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

import json
import logging
import os
import runpy
import tempfile
from unittest import mock

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase
from django.utils.log import AdminEmailHandler, configure_logging
from parameterized import parameterized

from labcirs.settings import base
from labcirs.settings.base import (default_language_code, get_bool_setting,
                                   get_local_setting, get_positive_int_setting)


class LocalSettingTest(SimpleTestCase):
    """Settings come from LABCIRS_* variables, then the JSON file, then the default."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.file = os.path.join(tmp.name, 'local_config.json')
        with open(self.file, 'w', encoding='utf-8') as f:
            json.dump({'ORGANIZATION': 'Datei', 'TIME_ZONE': ''}, f)
        self.missing = os.path.join(tmp.name, 'missing.json')

    @mock.patch.dict(os.environ, {'LABCIRS_ALLOWED_HOSTS': '["a.example"]'}, clear=True)
    def test_env_json_is_decoded(self):
        self.assertEqual(get_local_setting('ALLOWED_HOSTS', config_file=self.missing), ['a.example'])

    @mock.patch.dict(os.environ, {'LABCIRS_ORGANIZATION': 'Klinik Test'}, clear=True)
    def test_env_plain_text(self):
        self.assertEqual(get_local_setting('ORGANIZATION', config_file=self.missing), 'Klinik Test')

    @mock.patch.dict(os.environ, {'LABCIRS_EMAIL_PORT': '587', 'LABCIRS_DEBUG': 'true'}, clear=True)
    def test_env_number_and_bool(self):
        self.assertEqual(get_local_setting('EMAIL_PORT', 25, config_file=self.missing), 587)
        self.assertIs(get_local_setting('DEBUG', False, config_file=self.missing), True)

    @mock.patch.dict(os.environ, {'LABCIRS_ORGANIZATION': 'Umgebung'}, clear=True)
    def test_env_wins_over_file(self):
        self.assertEqual(get_local_setting('ORGANIZATION', config_file=self.file), 'Umgebung')

    @mock.patch.dict(os.environ, {}, clear=True)
    def test_file_used_without_env(self):
        self.assertEqual(get_local_setting('ORGANIZATION', config_file=self.file), 'Datei')

    @mock.patch.dict(os.environ, {}, clear=True)
    def test_missing_file_returns_default(self):
        self.assertEqual(get_local_setting('ORGANIZATION', 'LabCIRS', config_file=self.missing), 'LabCIRS')

    def test_empty_value_falls_back_to_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):  # empty value in the file
            self.assertEqual(get_local_setting('TIME_ZONE', 'UTC', config_file=self.file), 'UTC')
        with mock.patch.dict(os.environ, {'LABCIRS_TIME_ZONE': ''}, clear=True):  # and in the environment
            self.assertEqual(get_local_setting('TIME_ZONE', 'UTC', config_file=self.file), 'UTC')

    @mock.patch.dict(os.environ, {}, clear=True)
    def test_missing_required_raises(self):
        with self.assertRaises(ImproperlyConfigured):
            get_local_setting('SECRET_KEY', config_file=self.missing)

    @mock.patch.dict(os.environ, {}, clear=True)
    def test_invalid_json_file_raises(self):
        with open(self.file, 'w', encoding='utf-8') as f:
            f.write('{"ORGANIZATION": ')
        with self.assertRaises(ImproperlyConfigured):
            get_local_setting('ORGANIZATION', config_file=self.file)


BOOLEAN_SETTINGS = ('DEBUG', 'SESSION_COOKIE_SECURE', 'BEHIND_PROXY', 'EMAIL_USE_TLS',
                    'EMAIL_USE_SSL', 'ALL_LANGUAGES_MANDATORY_DEFAULT', 'ASK_PUBLICATION_CONSENT')


def load_settings(**env):
    """Runs base.py again with the given LABCIRS_* variables and returns its names."""
    current = {k: v for k, v in os.environ.items() if k.removeprefix('LABCIRS_') not in env}
    with mock.patch.dict(os.environ, dict(current, **{'LABCIRS_' + k: v for k, v in env.items()}),
                         clear=True):
        return runpy.run_path(base.__file__)


class BooleanSettingTest(SimpleTestCase):
    """LABCIRS_DEBUG=False must not become the truthy text "False" (debug pages in production)."""

    @parameterized.expand([('False', False), ('false', False), ('0', False), ('off', False),
                           ('NO', False), (' No ', False), ('TRUE', True), ('True', True),
                           ('true', True), ('1', True), ('yes', True), ('On', True)])
    def test_words_in_any_case(self, value, expected):
        self.assertIs(load_settings(DEBUG=value)['DEBUG'], expected)

    @parameterized.expand([('maybe',), ('2',), ('[]',), ('null',)])
    def test_other_values_are_rejected(self, value):
        with self.assertRaisesRegex(ImproperlyConfigured, 'LABCIRS_DEBUG.*true.*false'):
            load_settings(DEBUG=value)

    def test_empty_value_means_the_default(self):
        self.assertIs(load_settings(DEBUG='')['DEBUG'], False)
        self.assertIs(load_settings(ASK_PUBLICATION_CONSENT='')['ASK_PUBLICATION_CONSENT'], True)

    def test_every_boolean_setting_is_strict(self):
        for name in BOOLEAN_SETTINGS:
            with self.subTest(name):
                self.assertIs(load_settings(**{name: 'False'})[name], False)
                self.assertIs(load_settings(**{name: 'TRUE'})[name], True)
                with self.assertRaises(ImproperlyConfigured):
                    load_settings(**{name: 'maybe'})

    def test_tls_and_ssl_together_are_rejected(self):
        self.assertIs(load_settings(EMAIL_USE_TLS='true', EMAIL_USE_SSL='false')['EMAIL_USE_TLS'], True)
        with self.assertRaisesRegex(ImproperlyConfigured, 'EMAIL_USE_TLS.*EMAIL_USE_SSL'):
            load_settings(EMAIL_USE_TLS='true', EMAIL_USE_SSL='yes')

    def test_config_file_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'local_config.json')
            with open(path, 'w', encoding='utf-8') as f:
                json.dump({'A': True, 'B': 'False', 'C': 'maybe', 'D': 1}, f)
            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertIs(get_bool_setting('A', False, config_file=path), True)
                self.assertIs(get_bool_setting('B', True, config_file=path), False)
                self.assertIs(get_bool_setting('D', False, config_file=path), True)
                self.assertIs(get_bool_setting('MISSING', True, config_file=path), True)
                with self.assertRaises(ImproperlyConfigured):
                    get_bool_setting('C', False, config_file=path)


class PositiveIntSettingTest(SimpleTestCase):
    """A day limit or a smallest cell must be a whole number of at least 1. JSON would let
    true, 14.0 and "14" pass as a value, and the number 0 would silently turn the limit off."""

    @parameterized.expand([('14', 14), ('1', 1), ('365', 365), (' 7 ', 7)])
    def test_whole_numbers_from_1_are_accepted(self, value, expected):
        self.assertEqual(load_settings(QM_OVERDUE_DAYS=value)['QM_OVERDUE_DAYS'], expected)

    @parameterized.expand([('0',), ('-3',), ('true',), ('false',), ('14.0',), ('2.5',),
                           ('abc',), ('"14"',), ('[14]',), ('null',), ('{}',)])
    def test_everything_else_is_rejected(self, value):
        with self.assertRaisesRegex(ImproperlyConfigured, 'LABCIRS_QM_OVERDUE_DAYS.*whole number'):
            load_settings(QM_OVERDUE_DAYS=value)

    def test_empty_value_means_the_default(self):
        self.assertEqual(load_settings(QM_OVERDUE_DAYS='')['QM_OVERDUE_DAYS'], 14)
        self.assertEqual(load_settings(REPORT_MIN_CELL='')['REPORT_MIN_CELL'], 3)

    def test_the_running_settings_have_the_defaults(self):
        self.assertEqual((settings.QM_OVERDUE_DAYS, settings.REPORT_MIN_CELL), (14, 3))

    def test_both_settings_are_strict(self):
        for name in ('QM_OVERDUE_DAYS', 'REPORT_MIN_CELL'):
            with self.subTest(name):
                self.assertEqual(load_settings(**{name: '5'})[name], 5)
                with self.assertRaises(ImproperlyConfigured):
                    load_settings(**{name: '0'})

    def test_config_file_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'local_config.json')
            with open(path, 'w', encoding='utf-8') as f:
                json.dump({'A': 5, 'B': '5', 'C': True, 'D': 2.5, 'E': 0, 'F': -1, 'G': ''}, f)
            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertEqual(get_positive_int_setting('A', 14, config_file=path), 5)
                self.assertEqual(get_positive_int_setting('G', 14, config_file=path), 14)
                self.assertEqual(get_positive_int_setting('MISSING', 14, config_file=path), 14)
                for name in 'BCDEF':
                    with self.subTest(name), self.assertRaises(ImproperlyConfigured):
                        get_positive_int_setting(name, 14, config_file=path)


class BackupStatusDirSettingTest(SimpleTestCase):
    """The folder is text, and empty means that no backup status is set up."""

    def test_default_is_empty(self):
        self.assertEqual(load_settings(BACKUP_STATUS_DIR='')['BACKUP_STATUS_DIR'], '')
        self.assertEqual(settings.BACKUP_STATUS_DIR, '')

    def test_path_is_kept_as_text(self):
        self.assertEqual(load_settings(BACKUP_STATUS_DIR='/backup-status')['BACKUP_STATUS_DIR'],
                         '/backup-status')

    @parameterized.expand([('12',), ('true',), ('[]',)])
    def test_a_value_that_is_not_text_is_rejected(self, value):
        with self.assertRaisesRegex(ImproperlyConfigured, 'LABCIRS_BACKUP_STATUS_DIR'):
            load_settings(BACKUP_STATUS_DIR=value)


class EmailTimeoutTest(SimpleTestCase):
    """A mail server that does not answer must not hold a reporter's request for minutes."""

    def test_default_is_ten_seconds(self):
        self.assertEqual(load_settings(EMAIL_TIMEOUT='')['EMAIL_TIMEOUT'], 10)

    def test_can_be_set(self):
        self.assertEqual(load_settings(EMAIL_TIMEOUT='3')['EMAIL_TIMEOUT'], 3)

    def test_the_running_settings_use_a_timeout(self):
        self.assertIsNotNone(settings.EMAIL_TIMEOUT)


class DefaultLanguageCodeTest(SimpleTestCase):

    def test_default_language_code(self):
        self.assertEqual(default_language_code({'de': 'Deutsch'}), 'de')
        self.assertEqual(default_language_code({}), 'en')


class ProductionLoggingTest(SimpleTestCase):
    """Errors are only logged to the console: error mails carry the request data (user agent, headers)."""

    def test_no_admin_email_handler(self):
        # The settings of the tests run with DEBUG, so load the file again as in production.
        with mock.patch.dict(os.environ, dict(os.environ, LABCIRS_DEBUG='false'), clear=True):
            production = runpy.run_path(base.__file__)
        self.addCleanup(configure_logging, settings.LOGGING_CONFIG, settings.LOGGING)
        configure_logging(settings.LOGGING_CONFIG, production['LOGGING'])
        logger = logging.getLogger('django.request')
        while logger:
            handlers = [h for h in logger.handlers if isinstance(h, AdminEmailHandler)]
            self.assertEqual(handlers, [], f'AdminEmailHandler on logger {logger.name!r}')
            logger = logger.parent

    def test_admins_is_not_a_setting(self):
        """Nothing reads ADMINS, so LabCIRS has no knob for it: a LABCIRS_ADMINS variable or an
        "ADMINS" entry of an old local_config.json is ignored, the settings load without it."""
        env = dict(os.environ, LABCIRS_ADMINS='{"admin@example.org": "Admin"}')
        with mock.patch.dict(os.environ, env, clear=True):
            loaded = runpy.run_path(base.__file__)
        self.assertNotIn('ADMINS', loaded)
        self.assertEqual(settings.ADMINS, [])
