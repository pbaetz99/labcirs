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

"""What the system status of the admin start page counts: accounts by role, accounts that have not
logged in for long, departments without a QM and with the notification of the QM on or off, and
whether mail can be sent. Everything comes from a few queries, whatever the number of accounts, and
none of them reads an incident."""

import os
import tempfile
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path

from django.contrib.auth.models import User
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import translation
from model_bakery import baker

import cirs
from cirs.backup_status import BackupState
from cirs.models import LabCIRSConfig, Reporter, Reviewer
from cirs.system_status import (STALE_ACCOUNT_DAYS, STALE_ACCOUNT_LIST_LENGTH, Role,
                                account_numbers, departments, mail_configured, stale_accounts,
                                system_status)

from .helpers import create_role, create_user, make_incident

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=dt_timezone.utc)
# Creating an account hashes its password, which takes long enough to be felt with dozens of them.
FAST_HASHERS = override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
DAY = timedelta(days=1)
# tables of incidents and everything that belongs to them: no query of the status may read them
INCIDENT_TABLES = ('cirs_criticalincident', 'cirs_comment', 'cirs_incidentstatuschange',
                   'cirs_publishableincident', 'cirs_reportercontact')


def age(user, last_login=None, joined=None):
    """Sets the times of an account, which the database fills with now when it is created."""
    changes = {'last_login': last_login}
    if joined is not None:
        changes['date_joined'] = joined
    User.objects.filter(pk=user.pk).update(**changes)
    return user


def counts(numbers):
    return {number.role: (number.accounts, number.inactive) for number in numbers}


@FAST_HASHERS
class AccountNumbersTest(TestCase):

    def setUp(self):
        self.enterContext(translation.override('en'))

    def test_the_database_of_a_test_starts_without_accounts(self):
        self.assertEqual(User.objects.count(), 0)

    def test_every_account_counts_once_in_its_role(self):
        for name in ('boss', 'admin'):
            create_user(name, superuser=True)
        for name in ('qm1', 'qm2', 'qm3'):
            create_role(Reviewer, name)
        baker.make_recipe('cirs.department', _quantity=4)  # a technical account each
        create_role(Reporter, 'reporter-without-department')
        for name in ('norole1', 'norole2'):
            create_user(name)
        self.assertEqual(counts(account_numbers()),
                         {Role.SUPERUSER: (2, 0), Role.QM: (3, 0), Role.REPORTER: (5, 0),
                          Role.NONE: (2, 0)})
        self.assertEqual(sum(number.accounts for number in account_numbers()), User.objects.count())

    def test_the_four_roles_are_always_there_in_the_same_order(self):
        create_user('boss', superuser=True)
        numbers = account_numbers()
        self.assertEqual([number.role for number in numbers],
                         [Role.SUPERUSER, Role.QM, Role.REPORTER, Role.NONE])
        self.assertEqual(counts(numbers)[Role.QM], (0, 0))

    def test_a_superuser_with_a_role_added_behind_the_scenes_counts_as_superuser(self):
        # the same rule as for the access to the QM pages: a superuser is no QM
        boss = create_user('boss', superuser=True)
        Reviewer.objects.create(user=boss)
        Reporter.objects.create(user=create_user('boss2', superuser=True))
        self.assertEqual(counts(account_numbers()),
                         {Role.SUPERUSER: (2, 0), Role.QM: (0, 0), Role.REPORTER: (0, 0),
                          Role.NONE: (0, 0)})

    def test_an_account_with_both_roles_counts_as_qm(self):
        user = create_user('both')
        Reporter.objects.create(user=user)
        Reviewer.objects.create(user=user)
        numbers = counts(account_numbers())
        self.assertEqual((numbers[Role.QM], numbers[Role.REPORTER]), ((1, 0), (0, 0)))

    def test_inactive_accounts_are_counted_in_their_role_and_apart(self):
        create_user('boss', superuser=True)
        create_user('dormant', superuser=True)
        create_role(Reviewer, 'away')
        create_role(Reviewer, 'present')
        create_user('stranger')
        User.objects.filter(username__in=['dormant', 'away', 'stranger']).update(is_active=False)
        self.assertEqual(counts(account_numbers()),
                         {Role.SUPERUSER: (2, 1), Role.QM: (2, 1), Role.REPORTER: (0, 0),
                          Role.NONE: (1, 1)})

    def test_the_roles_have_names_for_the_page(self):
        names = {number.role: str(number.label) for number in account_numbers()}
        self.assertEqual(names, {Role.SUPERUSER: 'Superuser', Role.QM: 'QM',
                                 Role.REPORTER: 'Reporter account', Role.NONE: 'No role'})
        with translation.override('de'):
            self.assertEqual([str(number.label) for number in account_numbers()],
                             ['Superuser', 'QM', 'Reporter-Konto', 'Ohne Rolle'])

    def test_the_number_of_queries_does_not_grow_with_the_number_of_accounts(self):
        create_user('boss', superuser=True)
        with self.assertNumQueries(1):
            account_numbers()
        for number in range(40):
            create_role(Reviewer, 'qm%d' % number)
            create_user('norole%d' % number)
        with self.assertNumQueries(1):
            self.assertEqual(counts(account_numbers())[Role.QM], (40, 0))


@FAST_HASHERS
class StaleAccountsTest(TestCase):

    def test_the_limit_is_over_180_days(self):
        self.assertEqual(STALE_ACCOUNT_DAYS, 180)
        for days, seconds, stale in ((179, 0, False), (180, 0, False), (180, 1, True),
                                     (181, 0, True), (400, 0, True)):
            with self.subTest(days=days, seconds=seconds):
                last_login = NOW - days * DAY - timedelta(seconds=seconds)
                user = age(create_user('qm'), last_login=last_login, joined=NOW - 500 * DAY)
                self.assertEqual(stale_accounts(NOW).total, int(stale))
                user.delete()

    def test_the_last_login_counts_when_there_is_one_else_the_day_the_account_was_made(self):
        age(create_user('login-recent'), last_login=NOW - 10 * DAY, joined=NOW - 400 * DAY)
        age(create_user('login-old'), last_login=NOW - 300 * DAY, joined=NOW - 400 * DAY)
        age(create_user('never-new'), last_login=None, joined=NOW - 100 * DAY)
        age(create_user('never-old'), last_login=None, joined=NOW - 200 * DAY)
        result = stale_accounts(NOW)
        self.assertEqual([row.username for row in result.rows], ['login-old', 'never-old'])
        login_old, never_old = result.rows
        self.assertEqual((login_old.since, login_old.never_logged_in), (NOW - 300 * DAY, False))
        self.assertEqual((never_old.since, never_old.never_logged_in), (NOW - 200 * DAY, True))

    def test_the_oldest_come_first_and_the_name_decides_between_equal_days(self):
        day = NOW - 300 * DAY
        age(create_user('b-same'), last_login=day)
        age(create_user('a-same'), last_login=day)
        age(create_user('oldest'), last_login=NOW - 900 * DAY)
        age(create_user('newest'), last_login=NOW - 200 * DAY)
        self.assertEqual([row.username for row in stale_accounts(NOW).rows],
                         ['oldest', 'a-same', 'b-same', 'newest'])

    def test_reporter_accounts_never_log_in_and_are_not_listed(self):
        for department in baker.make_recipe('cirs.department', _quantity=2):
            age(department.reporter.user, last_login=None, joined=NOW - 900 * DAY)
        age(create_user('forgotten'), last_login=None, joined=NOW - 900 * DAY)
        self.assertEqual([row.username for row in stale_accounts(NOW).rows], ['forgotten'])

    def test_inactive_accounts_are_not_listed(self):
        age(create_user('gone'), last_login=NOW - 900 * DAY)
        User.objects.filter(username='gone').update(is_active=False)
        age(create_user('here'), last_login=NOW - 900 * DAY)
        self.assertEqual([row.username for row in stale_accounts(NOW).rows], ['here'])

    def test_superusers_qm_and_accounts_without_a_role_are_listed_with_their_role(self):
        age(create_user('boss', superuser=True), last_login=NOW - 300 * DAY)
        age(create_role(Reviewer, 'qm').user, last_login=NOW - 300 * DAY)
        age(create_user('stranger'), last_login=NOW - 300 * DAY)
        # a superuser with a reviewer role is a superuser
        both = create_user('both', superuser=True)
        Reviewer.objects.create(user=both)
        age(both, last_login=NOW - 300 * DAY)
        roles = {row.username: row.role for row in stale_accounts(NOW).rows}
        self.assertEqual(roles, {'boss': Role.SUPERUSER, 'qm': Role.QM, 'stranger': Role.NONE,
                                 'both': Role.SUPERUSER})

    def test_each_row_knows_its_account_for_the_link(self):
        user = age(create_user('forgotten'), last_login=NOW - 300 * DAY)
        self.assertEqual(stale_accounts(NOW).rows[0].pk, user.pk)

    def test_a_long_list_is_cut_and_still_counts_everyone(self):
        self.assertEqual(STALE_ACCOUNT_LIST_LENGTH, 20)
        users = [create_user('user%02d' % number)
                 for number in range(STALE_ACCOUNT_LIST_LENGTH + 5)]
        User.objects.filter(pk__in=[user.pk for user in users]).update(last_login=NOW - 300 * DAY)
        result = stale_accounts(NOW)
        self.assertEqual((len(result.rows), result.total, result.hidden), (20, 25, 5))
        self.assertEqual([row.username for row in result.rows][:2], ['user00', 'user01'])

    def test_a_short_list_hides_nothing(self):
        age(create_user('forgotten'), last_login=NOW - 300 * DAY)
        result = stale_accounts(NOW)
        self.assertEqual((len(result.rows), result.total, result.hidden), (1, 1, 0))

    def test_nobody_is_stale_in_an_empty_system(self):
        result = stale_accounts(NOW)
        self.assertEqual((result.rows, result.total, result.hidden), ((), 0, 0))

    def test_the_clock_is_the_default_for_now(self):
        age(create_user('forgotten'), last_login=datetime.now(dt_timezone.utc) - 181 * DAY)
        age(create_user('active'), last_login=datetime.now(dt_timezone.utc) - 179 * DAY)
        self.assertEqual([row.username for row in stale_accounts().rows], ['forgotten'])

    def test_two_queries_whatever_the_number_of_accounts(self):
        age(create_user('forgotten'), last_login=NOW - 300 * DAY)
        with self.assertNumQueries(2):
            stale_accounts(NOW)
        for number in range(30):
            age(create_user('user%d' % number), last_login=NOW - 300 * DAY)
            create_role(Reviewer, 'qm%d' % number)
        with self.assertNumQueries(2):
            self.assertEqual(stale_accounts(NOW).total, 31)


@FAST_HASHERS
class DepartmentsTest(TestCase):

    def test_every_department_with_its_qm_and_its_notification(self):
        covered, quiet = (baker.make_recipe('cirs.department', name=name)
                          for name in ('Covered', 'Quiet'))
        baker.make_recipe('cirs.department', name='Uncovered')
        covered.reviewers.add(create_role(Reviewer, 'qm-a'), create_role(Reviewer, 'qm-b'))
        quiet.reviewers.add(create_role(Reviewer, 'qm-c'))
        LabCIRSConfig.objects.filter(department=covered).update(send_notification=True)
        rows = departments()
        self.assertEqual([(row.name, row.has_qm, row.notifies) for row in rows],
                         [('Covered', True, True), ('Quiet', True, False),
                          ('Uncovered', False, False)])

    def test_a_department_with_two_qm_is_listed_once(self):
        department = baker.make_recipe('cirs.department')
        department.reviewers.add(create_role(Reviewer, 'qm-a'), create_role(Reviewer, 'qm-b'))
        self.assertEqual(len(departments()), 1)

    def test_a_department_without_its_configuration_has_no_notification(self):
        department = baker.make_recipe('cirs.department')
        LabCIRSConfig.objects.filter(department=department).delete()
        self.assertEqual([(row.pk, row.notifies) for row in departments()],
                         [(department.pk, False)])

    def test_the_departments_know_whether_they_are_active(self):
        active = baker.make_recipe('cirs.department', name='A active')
        inactive = baker.make_recipe('cirs.department', name='B inactive', active=False)
        self.assertEqual([(row.pk, row.active) for row in departments()],
                         [(active.pk, True), (inactive.pk, False)])

    def test_a_system_without_departments(self):
        self.assertEqual(departments(), ())

    def test_one_query_whatever_the_number_of_departments(self):
        baker.make_recipe('cirs.department')
        with self.assertNumQueries(1):
            departments()
        baker.make_recipe('cirs.department', _quantity=25)
        with self.assertNumQueries(1):
            self.assertEqual(len(departments()), 26)


class MailTest(SimpleTestCase):

    def test_mail_is_set_up_with_a_server_other_than_localhost_and_a_sender(self):
        cases = (('localhost', 'cirs@example.org', False), ('LocalHost', 'cirs@example.org', False),
                 ('', 'cirs@example.org', False), ('  ', 'cirs@example.org', False),
                 ('mail.example.org', '', False), ('mail.example.org', '   ', False),
                 ('mail.example.org', 'cirs@example.org', True),
                 (' mail.example.org ', ' cirs@example.org ', True))
        for host, sender, expected in cases:
            with self.subTest(host=host, sender=sender):
                with override_settings(EMAIL_HOST=host, DEFAULT_FROM_EMAIL=sender):
                    self.assertIs(mail_configured(), expected)


@FAST_HASHERS
class SystemStatusTest(TestCase):

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def test_the_parts_come_together(self):
        create_user('boss', superuser=True)
        baker.make_recipe('cirs.department')
        with override_settings(BACKUP_STATUS_DIR=str(self.folder), EMAIL_HOST='mail.example.org',
                               DEFAULT_FROM_EMAIL='cirs@example.org'):
            status = system_status(NOW)
        self.assertEqual(status.version, cirs.__version__)
        self.assertEqual(counts(status.roles)[Role.SUPERUSER], (1, 0))
        self.assertEqual(status.stale.total, 0)
        self.assertEqual(len(status.departments), 1)
        self.assertEqual([row.name for row in status.departments_without_qm],
                         [status.departments[0].name])
        self.assertTrue(status.mail_configured)
        self.assertEqual(status.backup.state, BackupState.NO_STATUS_FILE)

    @override_settings(BACKUP_STATUS_DIR='')
    def test_without_a_backup_folder_the_backup_is_not_set_up(self):
        self.assertEqual(system_status(NOW).backup.state, BackupState.NOT_SET_UP)

    def test_the_backup_is_measured_at_the_same_moment_as_the_accounts(self):
        note = self.folder / 'letzte-sicherung'
        note.write_text('labcirs.dump 5', encoding='utf-8')
        seconds = (NOW - timedelta(hours=27)).timestamp()
        os.utime(note, (seconds, seconds))
        with override_settings(BACKUP_STATUS_DIR=str(self.folder)):
            self.assertEqual(system_status(NOW).backup.state, BackupState.STALE)
            self.assertEqual(system_status(NOW - 2 * timedelta(hours=1)).backup.state,
                             BackupState.FRESH)

    def test_four_queries_whatever_the_number_of_accounts_and_departments(self):
        create_user('boss', superuser=True)
        with self.assertNumQueries(4):
            system_status(NOW)
        for number in range(30):
            create_role(Reviewer, 'qm%d' % number)
            age(create_user('user%d' % number), last_login=NOW - 300 * DAY)
        baker.make_recipe('cirs.department', _quantity=12)
        with self.assertNumQueries(4):
            self.assertEqual(system_status(NOW).stale.total, 30)

    def test_no_query_reads_an_incident(self):
        department = baker.make_recipe('cirs.department')
        for _ in range(3):
            make_incident(department)
        with CaptureQueriesContext(connection) as queries:
            system_status(NOW)
        for query in queries.captured_queries:
            for table in INCIDENT_TABLES:
                self.assertNotIn(table, query['sql'])

    def test_the_status_cannot_be_changed(self):
        status = system_status(NOW)
        with self.assertRaises(AttributeError):
            status.version = 'x'
        self.assertIsInstance(status.roles, tuple)
        self.assertIsInstance(status.departments, tuple)
        self.assertIsInstance(status.stale.rows, tuple)
