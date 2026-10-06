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

"""The status of the last backup, read from the folder that the backup script of the operator
writes to. Only the status file in it counts, whatever else lies there; nothing that goes wrong
with the folder or the status file is an error, and the folder itself is never part of the
answer."""

import errno
import logging
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from unittest import mock, skipUnless

from django.test import SimpleTestCase

from cirs import backup_status
from cirs.backup_status import (MAX_NAME_LENGTH, MAX_STATUS_FILE_BYTES, STALE_AFTER,
                                STATUS_FILE_NAME, BackupState, read_backup_status)

CONTENT = 'labcirs-2026-10-06.dump 1234567\n'
MADE = datetime(2026, 10, 6, 3, 15, tzinfo=dt_timezone.utc)
HOUR = timedelta(hours=1)
POSIX_ONLY = 'needs the POSIX way to open a file without following a link'


def set_time(path, moment):
    """The modification time of path, which is the time of the backup. The access time is put
    far away from it, so that a test fails if the access time is read instead."""
    seconds = moment.timestamp()
    os.utime(path, (seconds - 100 * 24 * 3600, seconds))


class StatusFolderTestCase(SimpleTestCase):

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        # the warnings of the unreadable cases would fill the output of the run
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

    def write(self, name, content=CONTENT, moment=None):
        path = self.folder / name
        path.write_bytes(content if isinstance(content, bytes) else content.encode('utf-8'))
        if moment is not None:
            set_time(path, moment)
        return path

    def read(self, now=MADE + HOUR, directory=None):
        return read_backup_status(str(self.folder if directory is None else directory), now=now)

    def assertNoFolder(self, status):
        """The folder is a path of the server: no part of the answer may carry it."""
        self.assertNotIn(self.folder.name, repr(status))


class NotSetUpAndMissingTest(StatusFolderTestCase):

    def test_without_a_folder_nothing_is_set_up(self):
        for setting in ('', '   '):
            status = read_backup_status(setting)
            self.assertEqual(status.state, BackupState.NOT_SET_UP, repr(setting))
            self.assertIsNone(status.made_at)

    def test_a_folder_that_does_not_exist(self):
        status = self.read(directory=self.folder / 'missing')
        self.assertEqual(status.state, BackupState.NO_FOLDER)
        self.assertNoFolder(status)

    def test_a_path_that_is_a_file_is_no_folder(self):
        status = self.read(directory=self.write('somefile', 'x'))
        self.assertEqual(status.state, BackupState.NO_FOLDER)

    def test_a_path_the_system_cannot_use_is_no_folder_and_no_error(self):
        # a NUL byte (possible in the JSON file of the settings) makes the system call raise
        # ValueError, not OSError
        self.assertEqual(read_backup_status('/backup\x00status').state, BackupState.NO_FOLDER)

    def test_an_empty_folder_has_no_status_file(self):
        self.assertEqual(self.read().state, BackupState.NO_STATUS_FILE)

    def test_a_link_to_a_folder_is_followed(self):
        # the setting is the operator's own; only the status file inside is written by somebody else
        self.write(STATUS_FILE_NAME, moment=MADE)
        link = self.folder.parent / (self.folder.name + '-link')
        link.symlink_to(self.folder, target_is_directory=True)
        self.addCleanup(link.unlink)
        self.assertEqual(self.read(directory=link).state, BackupState.FRESH)


class StatusFileTest(StatusFolderTestCase):

    def test_the_status_file_has_the_name_the_backup_script_writes(self):
        self.assertEqual(STATUS_FILE_NAME, 'letzte-sicherung')

    def test_the_time_is_the_modification_time_of_the_status_file(self):
        self.write(STATUS_FILE_NAME, moment=MADE)
        status = self.read()
        self.assertEqual(status.state, BackupState.FRESH)
        self.assertEqual(status.made_at, MADE)
        self.assertIsNotNone(status.made_at.tzinfo)

    def test_name_and_size_come_from_the_content(self):
        self.write(STATUS_FILE_NAME, 'labcirs-2026-10-06.dump 1234567\n', MADE)
        status = self.read()
        self.assertEqual((status.file_name, status.size), ('labcirs-2026-10-06.dump', 1234567))
        self.assertNoFolder(status)

    def test_a_name_with_spaces_keeps_them(self):
        self.write(STATUS_FILE_NAME, 'my backup 2026.dump 100', MADE)
        status = self.read()
        self.assertEqual((status.file_name, status.size), ('my backup 2026.dump', 100))

    def test_a_tab_may_separate_name_and_size(self):
        self.write(STATUS_FILE_NAME, 'labcirs.dump\t42', MADE)
        self.assertEqual((self.read().file_name, self.read().size), ('labcirs.dump', 42))

    def test_a_status_file_without_a_size_or_without_a_name(self):
        self.write(STATUS_FILE_NAME, 'labcirs.dump\n', MADE)
        self.assertEqual((self.read().file_name, self.read().size), ('labcirs.dump', None))
        self.write(STATUS_FILE_NAME, '98765\n', MADE)
        self.assertEqual((self.read().file_name, self.read().size), ('', 98765))

    def test_a_size_in_another_form_is_left_in_the_name_and_never_guessed(self):
        self.write(STATUS_FILE_NAME, 'labcirs.dump 1.2G', MADE)
        status = self.read()
        self.assertEqual((status.file_name, status.size), ('labcirs.dump 1.2G', None))

    def test_only_the_first_line_counts(self):
        self.write(STATUS_FILE_NAME, '\n\nfirst.dump 1\nsecond.dump 2\n', MADE)
        status = self.read()
        self.assertEqual((status.file_name, status.size), ('first.dump', 1))

    def test_a_status_file_that_is_not_text_is_no_error(self):
        self.write(STATUS_FILE_NAME, b'\xff\xfe\x00backup.dump 7\n', MADE)
        status = self.read()
        self.assertEqual(status.state, BackupState.FRESH)
        self.assertEqual(status.made_at, MADE)

    def test_only_the_beginning_of_a_large_status_file_is_read(self):
        self.write(STATUS_FILE_NAME, 'big.dump 5\n' + 'x' * (10 * MAX_STATUS_FILE_BYTES), MADE)
        read = mock.Mock(wraps=os.read)
        with mock.patch.object(backup_status.os, 'read', read):
            status = self.read()
        self.assertEqual((status.file_name, status.size), ('big.dump', 5))
        self.assertLessEqual(read.call_args.args[1], MAX_STATUS_FILE_BYTES + 1)


class FileNameInTheStatusFileTest(StatusFolderTestCase):
    """What the status file says is shown on the page, so it is never taken for a path: only the
    last part of it is a name, and what cannot be a name is dropped."""

    def name_of(self, content):
        self.write(STATUS_FILE_NAME, content, MADE)
        return self.read().file_name

    def test_a_path_leaves_only_its_last_part(self):
        self.assertEqual(self.name_of('/var/backups/labcirs/labcirs.dump 5'), 'labcirs.dump')
        self.assertEqual(self.name_of('..\\..\\Backups\\labcirs.dump 5'), 'labcirs.dump')
        self.assertEqual(self.name_of('C:\\Backups\\labcirs.dump 5'), 'labcirs.dump')

    def test_going_up_out_of_the_folder_leaves_only_the_name(self):
        self.assertEqual(self.name_of('../../../etc/passwd 1234'), 'passwd')

    def test_a_name_that_is_only_a_way_is_dropped(self):
        for content in ('.. 5', '../.. 5', '. 5', '/ 5', '../ 5', './ 5', '/etc/ 5'):
            self.assertEqual(self.name_of(content), '', content)

    def test_control_and_direction_characters_are_dropped(self):
        # a right-to-left override would show "exe.txt" for a file that ends in ".txt.exe"
        self.assertEqual(self.name_of('back\u202eup\x1b[31m.dump\u200b 5'), 'backup[31m.dump')

    def test_a_long_name_is_cut(self):
        self.assertEqual(len(self.name_of('a' * 5 * MAX_NAME_LENGTH + ' 5')), MAX_NAME_LENGTH)

    def test_markup_stays_as_it_is_for_the_template_to_escape(self):
        self.assertEqual(self.name_of('<img src=x onerror=alert(1)>.dump 7'),
                         '<img src=x onerror=alert(1)>.dump')


class AgeTest(StatusFolderTestCase):

    def test_up_to_26_hours_is_fresh_and_over_it_is_stale(self):
        self.write(STATUS_FILE_NAME, moment=MADE)
        self.assertEqual(STALE_AFTER, timedelta(hours=26))
        for age, state in ((timedelta(0), BackupState.FRESH),
                           (STALE_AFTER - timedelta(seconds=1), BackupState.FRESH),
                           (STALE_AFTER, BackupState.FRESH),
                           (STALE_AFTER + timedelta(seconds=1), BackupState.STALE),
                           (timedelta(days=30), BackupState.STALE)):
            with self.subTest(age=age):
                self.assertEqual(self.read(now=MADE + age).state, state)

    def test_a_stale_status_file_still_says_when_and_what(self):
        self.write(STATUS_FILE_NAME, moment=MADE)
        status = self.read(now=MADE + timedelta(days=3))
        self.assertEqual(status.state, BackupState.STALE)
        self.assertEqual((status.made_at, status.file_name, status.size),
                         (MADE, 'labcirs-2026-10-06.dump', 1234567))

    def test_with_the_real_clock_yesterday_is_fresh_and_two_days_ago_is_stale(self):
        path = self.write(STATUS_FILE_NAME)
        now = time.time()
        for hours, state in ((1, BackupState.FRESH), (25, BackupState.FRESH),
                             (27, BackupState.STALE), (48, BackupState.STALE)):
            with self.subTest(hours=hours):
                os.utime(path, (now, now - hours * 3600))
                self.assertEqual(read_backup_status(str(self.folder)).state, state)

    def test_a_time_in_the_future_cannot_be_trusted(self):
        # it would hide a backup that is not made any more
        self.write(STATUS_FILE_NAME, moment=MADE)
        self.assertEqual(self.read(now=MADE - timedelta(days=1)).state, BackupState.FUTURE)
        # a few minutes of difference between clocks are no reason to doubt
        self.assertEqual(self.read(now=MADE - timedelta(minutes=5)).state, BackupState.FRESH)


class OtherFilesTest(StatusFolderTestCase):

    def test_only_the_status_file_is_read_whatever_else_lies_in_the_folder(self):
        self.write(STATUS_FILE_NAME, 'right.dump 10', MADE)
        # newer, and with a size of their own
        later = MADE + timedelta(hours=5)
        self.write('labcirs-2026-10-07.dump', 'wrong.dump 20', later)
        self.write('.letzte-sicherung', 'hidden.dump 30', later)
        self.write(STATUS_FILE_NAME + '.tmp', 'half.dump 40', later)
        self.write(STATUS_FILE_NAME + '.part', 'part.dump 50', later)
        self.write('empty', '', later)
        status = self.read(now=later + HOUR)
        self.assertEqual((status.state, status.made_at, status.file_name, status.size),
                         (BackupState.FRESH, MADE, 'right.dump', 10))

    def test_without_the_status_file_nothing_else_stands_in_for_it(self):
        self.write('labcirs-2026-10-07.dump', 'wrong.dump 20', MADE)
        self.write('.letzte-sicherung', 'hidden.dump 30', MADE)
        self.write(STATUS_FILE_NAME + '.tmp', 'half.dump 40', MADE)
        self.write(STATUS_FILE_NAME + '.part', 'part.dump 50', MADE)
        self.write('empty', '', MADE)
        (self.folder / 'subfolder').mkdir()
        self.assertEqual(self.read().state, BackupState.NO_STATUS_FILE)

    def test_an_empty_status_file_is_skipped(self):
        for content in ('', '\n', '  \t\n  \n'):
            with self.subTest(content=content):
                self.write(STATUS_FILE_NAME, content, MADE)
                self.assertEqual(self.read().state, BackupState.NO_STATUS_FILE)


@skipUnless(os.name == 'posix', POSIX_ONLY)
class StatusFileThatCannotBeReadTest(StatusFolderTestCase):

    def assertUnreadable(self, status):
        self.assertEqual(status.state, BackupState.UNREADABLE)
        self.assertEqual((status.made_at, status.file_name, status.size), (None, '', None))
        self.assertNoFolder(status)

    def test_a_status_file_that_is_a_folder(self):
        (self.folder / STATUS_FILE_NAME).mkdir()
        self.assertUnreadable(self.read())

    def test_a_status_file_that_is_a_pipe_does_not_make_the_page_wait(self):
        # opened for reading, a pipe without a writer blocks, if the opening is not told otherwise
        os.mkfifo(self.folder / STATUS_FILE_NAME)
        self.assertUnreadable(self.read())

    def test_a_status_file_the_system_refuses(self):
        self.write(STATUS_FILE_NAME, moment=MADE)
        for code in (errno.EACCES, errno.EIO, errno.EMFILE, errno.ENAMETOOLONG, errno.ELOOP):
            with self.subTest(error=errno.errorcode[code]):
                with mock.patch.object(backup_status.os, 'open', side_effect=OSError(code, 'x')):
                    self.assertUnreadable(self.read())

    def test_the_log_names_the_kind_of_error_and_never_the_folder(self):
        logging.disable(logging.NOTSET)
        self.write(STATUS_FILE_NAME, moment=MADE)
        with mock.patch.object(backup_status.os, 'open', side_effect=PermissionError(
                errno.EACCES, 'Permission denied', str(self.folder / STATUS_FILE_NAME))):
            with self.assertLogs(backup_status.logger, 'WARNING') as logged:
                self.read()
        self.assertEqual(len(logged.records), 1)
        self.assertIn('PermissionError', logged.output[0])
        self.assertNotIn(self.folder.name, logged.output[0])

    def test_a_status_file_that_fails_while_it_is_read(self):
        self.write(STATUS_FILE_NAME, moment=MADE)
        with mock.patch.object(backup_status.os, 'read', side_effect=OSError(errno.EIO, 'x')):
            self.assertUnreadable(self.read())

    def test_the_status_file_is_closed_whatever_happens_while_it_is_read(self):
        self.write(STATUS_FILE_NAME, moment=MADE)
        closed = []
        real_close = os.close
        with mock.patch.object(backup_status.os, 'read', side_effect=OSError(errno.EIO, 'x')), \
                mock.patch.object(backup_status.os, 'close',
                                  side_effect=lambda fd: (closed.append(fd), real_close(fd))):
            self.read()
        self.assertEqual(len(closed), 1)

    def test_a_status_file_without_the_right_to_read_it(self):
        if os.geteuid() == 0:
            self.skipTest('root reads every file')
        path = self.write(STATUS_FILE_NAME, moment=MADE)
        path.chmod(0)
        self.addCleanup(path.chmod, 0o600)
        self.assertUnreadable(self.read())

    def test_a_folder_that_cannot_be_entered(self):
        if os.geteuid() == 0:
            self.skipTest('root enters every folder')
        self.write(STATUS_FILE_NAME, moment=MADE)
        self.folder.chmod(0)
        self.addCleanup(self.folder.chmod, 0o700)
        self.assertUnreadable(self.read())


@skipUnless(os.name == 'posix', POSIX_ONLY)
class LinksAreNotFollowedTest(StatusFolderTestCase):
    """The status file is written by another program. A link in its place would let the page show
    what some other file of the server holds, so a link is no status file."""

    def setUp(self):
        super().setUp()
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        self.outside = Path(outside.name)
        self.secret = self.outside / 'secret'
        self.secret.write_text('SECRET-CONTENT.dump 99', encoding='utf-8')
        set_time(self.secret, MADE)

    def test_a_link_to_another_file_is_not_followed(self):
        (self.folder / STATUS_FILE_NAME).symlink_to(self.secret)
        status = self.read()
        self.assertEqual(status.state, BackupState.UNREADABLE)
        self.assertNotIn('SECRET', repr(status))
        self.assertIsNone(status.made_at)

    def test_a_link_to_a_file_in_the_folder_is_not_followed_either(self):
        self.write('real-status-file', 'real.dump 1', MADE)
        (self.folder / STATUS_FILE_NAME).symlink_to('real-status-file')
        self.assertEqual(self.read().state, BackupState.UNREADABLE)

    def test_a_link_that_leads_nowhere(self):
        (self.folder / STATUS_FILE_NAME).symlink_to(self.folder / 'does-not-exist')
        self.assertEqual(self.read().state, BackupState.UNREADABLE)

    def test_a_link_up_and_out_of_the_folder(self):
        (self.folder / STATUS_FILE_NAME).symlink_to(os.path.relpath(self.secret, self.folder))
        status = self.read()
        self.assertEqual(status.state, BackupState.UNREADABLE)
        self.assertNotIn('SECRET', repr(status))

    def test_a_link_to_a_system_file(self):
        (self.folder / STATUS_FILE_NAME).symlink_to('/etc/passwd')
        status = self.read()
        self.assertEqual(status.state, BackupState.UNREADABLE)
        self.assertIsNone(status.made_at)

    def test_a_link_is_refused_where_the_system_alone_would_not_refuse_it(self):
        # on a system that cannot open "without following", the check before the opening stands
        (self.folder / STATUS_FILE_NAME).symlink_to(self.secret)
        with mock.patch.object(backup_status, 'NO_FOLLOW', 0):
            self.assertEqual(self.read().state, BackupState.UNREADABLE)


class NothingIsEverRaisedTest(StatusFolderTestCase):

    def test_whatever_the_system_answers(self):
        for code in (errno.EACCES, errno.ENOENT, errno.ENOTDIR, errno.EIO, errno.ENAMETOOLONG,
                     errno.ESTALE, errno.EMFILE, errno.EINTR):
            with self.subTest(error=errno.errorcode[code]):
                with mock.patch.object(backup_status.os, 'stat', side_effect=OSError(code, 'x')), \
                        mock.patch.object(backup_status.os, 'lstat', side_effect=OSError(code, 'x')), \
                        mock.patch.object(backup_status.os, 'open', side_effect=OSError(code, 'x')):
                    status = self.read()
                self.assertIn(status.state, (BackupState.NO_FOLDER, BackupState.NO_STATUS_FILE,
                                             BackupState.UNREADABLE))
                self.assertNoFolder(status)

    def test_the_answer_is_a_value_that_cannot_be_changed(self):
        status = read_backup_status('')
        with self.assertRaises(AttributeError):
            status.state = BackupState.FRESH
