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

"""What the admin start page knows about the last backup.

LabCIRS makes no backups and never looks at the backup files. The backup script of the operator
writes a short status file into a folder of its own after every backup that worked, and the app
reads only that file: letzte-sicherung, whose first line is the name and the size in bytes of the
backup file ("labcirs-2026-10-06.dump 1234567"). The time of the backup is the modification time
of the status file, so what is written inside cannot make a backup look younger than it is. The
folder is meant to be mounted into the container read-only.

Whatever is wrong with the folder or the file ends in a state that the page can put in words,
never in an error: the start page must open exactly when the backup does not work. The answer
never holds the folder, and the name from the file is shown as a name only (its last part,
without control characters), because another program writes the file.
"""

import logging
import os
import re
import stat
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from enum import StrEnum

from django.utils import timezone

logger = logging.getLogger(__name__)

STATUS_FILE_NAME = 'letzte-sicherung'
# A backup every night, and two hours of room for a long one.
STALE_AFTER_HOURS = 26
STALE_AFTER = timedelta(hours=STALE_AFTER_HOURS)
# Clocks of host and container may differ a little; more than this in the future is not a backup
# that was just made but a time that cannot be trusted.
FUTURE_TOLERANCE = timedelta(minutes=10)
MAX_STATUS_FILE_BYTES = 4096
MAX_NAME_LENGTH = 120

# A link in place of the status file is refused when the file is opened, if the system can do that
# (POSIX); read_backup_status checks for a link before opening as well, for the others.
NO_FOLLOW = getattr(os, 'O_NOFOLLOW', 0)
# Opening a pipe for reading waits for a writer unless told not to.
NO_WAIT = getattr(os, 'O_NONBLOCK', 0)
EPOCH = datetime(1970, 1, 1, tzinfo=dt_timezone.utc)
# "<name> <size>": the size is the last word and only digits; a name may hold spaces.
NAME_AND_SIZE = re.compile(r'(?:(?P<name>.*?)[ \t]+)?(?P<size>[0-9]{1,15})')
PATH_SEPARATORS = re.compile(r'[\\/]')


class BackupState(StrEnum):
    NOT_SET_UP = 'not_set_up'          # no folder in the settings
    NO_FOLDER = 'no_folder'            # the folder is not there (not mounted?)
    NO_STATUS_FILE = 'no_status_file'  # a folder without the status file, or with an empty one
    UNREADABLE = 'unreadable'          # the status file is no plain file, or the system refuses it
    FUTURE = 'future'                  # the status file is dated after now
    STALE = 'stale'                    # the last backup is older than STALE_AFTER
    FRESH = 'fresh'


@dataclass(frozen=True)
class BackupStatus:
    state: BackupState
    made_at: datetime | None = None
    file_name: str = ''
    size: int | None = None


class StatusFileRefused(Exception):
    """The status file is something else than a plain file, for instance a link or a pipe."""


def read_backup_status(directory, now=None):
    """The status of the last backup from the status file in directory (the setting
    BACKUP_STATUS_DIR). now is the moment the age is measured at (default: the clock). Never raises.
    """
    if not directory.strip():
        return BackupStatus(BackupState.NOT_SET_UP)
    try:
        raw, made_at = _read_status_file(os.path.join(directory, STATUS_FILE_NAME))
    except (FileNotFoundError, NotADirectoryError):
        return BackupStatus(
            BackupState.NO_STATUS_FILE if _is_folder(directory) else BackupState.NO_FOLDER)
    except ValueError:  # a NUL byte in the path, which no file can have
        return BackupStatus(BackupState.NO_FOLDER)
    except (OSError, StatusFileRefused) as error:
        # The class only: the message of an OSError names the path.
        logger.warning('The backup status file cannot be used (%s)', type(error).__name__)
        return BackupStatus(BackupState.UNREADABLE)
    if not raw.strip():
        return BackupStatus(BackupState.NO_STATUS_FILE)  # empty: as if it were not there
    name, size = _parse_status_file(raw)
    return BackupStatus(_state_at(made_at, now or timezone.now()), made_at, name, size)


def _is_folder(path):
    try:
        return stat.S_ISDIR(os.stat(path).st_mode)
    except (OSError, ValueError):
        return False


def _read_status_file(path):
    """The first bytes of the status file and the time of its last change.

    It must be a plain file: a link, a pipe or a folder is refused before it is opened (so that a
    pipe cannot make the page wait) and, because the file can be exchanged in between, on the
    open file once more.
    """
    if not stat.S_ISREG(os.lstat(path).st_mode):
        raise StatusFileRefused
    descriptor = os.open(path, os.O_RDONLY | NO_FOLLOW | NO_WAIT)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise StatusFileRefused
        raw = os.read(descriptor, MAX_STATUS_FILE_BYTES)
    finally:
        os.close(descriptor)
    return raw, EPOCH + timedelta(microseconds=info.st_mtime_ns // 1000)


def _parse_status_file(raw):
    """(name, size) from the first line that is not blank; '' and None for what is not there."""
    text = raw.decode('utf-8', errors='replace')
    line = next((line.strip() for line in text.splitlines() if line.strip()), '')
    match = NAME_AND_SIZE.fullmatch(line)
    if match is None:
        return _plain_name(line), None
    return _plain_name(match.group('name') or ''), int(match.group('size'))


def _plain_name(text):
    """The last part of text as a name to show: no way to a folder, no control or direction
    characters (a right-to-left override can turn "gnp.exe" into "exe.png"), not too long."""
    name = PATH_SEPARATORS.split(text)[-1]
    name = ''.join(char for char in name if not unicodedata.category(char).startswith('C')).strip()
    return '' if name in ('.', '..') else name[:MAX_NAME_LENGTH]


def _state_at(made_at, now):
    age = now - made_at
    if -age > FUTURE_TOLERANCE:
        return BackupState.FUTURE
    return BackupState.STALE if age > STALE_AFTER else BackupState.FRESH
