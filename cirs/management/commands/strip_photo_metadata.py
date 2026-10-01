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

import contextlib
import os
import posixpath
import re
import stat
import sys
from collections import Counter

from django.core.management.base import BaseCommand, no_translations

from cirs.models import CriticalIncident
from cirs.photos import random_photo_name, sanitize_image

CLEAN_NAME = re.compile(r'[0-9a-f]{32}\.(jpg|png)')


def fsync_dir(path):
    # Makes the new or removed directory entry durable; not possible everywhere (Windows).
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


class Command(BaseCommand):
    help = ("Converts every stored photo into a clean JPEG or PNG (no metadata) with a random "
            "file name in the same folder. Photos that already have a random name are left "
            "alone, files that cannot be read as an image are reported as failed and not touched. "
            "The last line is a summary; the exit status is 1 if any file failed or an old file "
            "could not be removed (it still carries the metadata and the old name).")

    @no_translations
    def handle(self, *args, **options):
        # Output goes to the operator's terminal only, never to a log: old names may identify people.
        counts = Counter()
        for incident in CriticalIncident.objects.exclude(photo='').exclude(photo=None):
            name = incident.photo.name
            try:
                status, line = self.clean(incident)
            except Exception as error:
                status, line = 'failed', 'failed %s: %s' % (name, error.__cause__ or error)
            self.stdout.write(line)
            counts[status] += 1
        summary = ('%d cleaned, %d already clean, %d failed'
                   % (counts['cleaned'] + counts['stale'], counts['already'], counts['failed']))
        if counts['stale']:
            summary += ', %d old file(s) not removed' % counts['stale']
        self.stdout.write(summary)
        if counts['failed'] or counts['stale']:
            sys.exit(1)

    def clean(self, incident):
        name = incident.photo.name
        directory, base = posixpath.split(name)
        if CLEAN_NAME.fullmatch(base):
            return 'already', 'already clean ' + name
        with incident.photo.open('rb') as f:
            data, ext = sanitize_image(f, upload=False)
        old_path = incident.photo.path
        new_name = posixpath.join(directory, random_photo_name(ext))
        new_path = incident.photo.storage.path(new_name)
        # Order: new file on disk, then the database, then the old file is removed.
        try:
            with open(new_path, 'xb') as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            fsync_dir(os.path.dirname(new_path))
            os.chmod(new_path, stat.S_IMODE(os.stat(old_path).st_mode))
            incident.photo = new_name
            incident.save(update_fields=['photo'])
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(new_path)
            raise
        try:
            os.unlink(old_path)
            fsync_dir(os.path.dirname(old_path))
        except OSError as error:
            return 'stale', 'cleaned %s -> %s (old file not removed: %s)' % (name, new_name, error)
        return 'cleaned', 'cleaned %s -> %s' % (name, new_name)
