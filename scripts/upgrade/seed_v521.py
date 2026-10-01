# -*- coding: utf-8 -*-
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

"""Fills a LabCIRS v5.2.1 database with synthetic data for scripts/test-upgrade-from-v5.sh.

PYTHON 2.7 ONLY: it runs with the ORM of v5.2.1 (Django 1.11), inside the old container:

    python - < scripts/upgrade/seed_v521.py

Needs SEED_PASSWORD in the environment (the password of all accounts). The last output line is
"EXPECTED {json}": the values that scripts/upgrade/check_upgraded.py expects after the upgrade.
Nothing here is real data.
"""

from __future__ import print_function, unicode_literals

import io
import json
import os
import struct
from datetime import date

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'labcirs.settings.production')

import django
django.setup()

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from PIL import Image
from registration.models import SupervisedRegistrationProfile

from cirs.models import Comment, CriticalIncident, Department, PublishableIncident, Reporter, Reviewer

EXIF_MARKER = 'SEED-EXIF-MARKER'


def exif_with_artist(text):
    """The smallest EXIF block Pillow 6 writes into a JPEG: one ASCII tag, Artist (0x013B)."""
    data = text.encode('ascii') + b'\x00'
    header = b'Exif\x00\x00II*\x00\x08\x00\x00\x00'  # byte order, magic, offset of the first IFD
    # one entry: tag, type 2 (ASCII), count, offset of the text (8 header + 2 + 12 + 4 bytes in)
    entry = struct.pack(str('<HHII'), 0x013B, 2, len(data), 26)
    return header + struct.pack(str('<H'), 1) + entry + struct.pack(str('<I'), 0) + data


password = os.environ['SEED_PASSWORD']

admin = User.objects.create_superuser('seed-admin', 'seed-admin@example.org', password)
reporter = Reporter.objects.create(
    user=User.objects.create_user('seed-reporter', 'seed-reporter@example.org', password))
reviewer = Reviewer.objects.create(
    user=User.objects.create_user('seed-reviewer', 'seed-reviewer@example.org', password))

# A self-registered account that was never activated: it owns rows in the registration tables,
# which the upgrade has to drop without blocking the deletion of users.
pending = User.objects.create_user('seed-pending', 'seed-pending@example.org', password)
pending.is_active = False
pending.save()
SupervisedRegistrationProfile.objects.create(user=pending, activation_key='a' * 40)

department = Department.objects.create(
    label='test', name='Test department', reporter=reporter, active=True)
department.reviewers.add(reviewer)

incidents = [
    dict(date=date(2020, 5, 4), reported=date(2020, 5, 5), public=True,
         incident='A pipette tip fell into the sample, the vessel broke (\xe4\xf6\xfc\xdf)',
         reason='Tired after a long shift', immediate_action='Use a tray for the tips',
         preventability='avoidable', action='Tray bought', responsibilty='Lab lead',
         review_date=date(2020, 5, 20), status='completed', risk='low', frequency='seldom',
         hazard='low', category=['technique/methods', 'other']),
    dict(date=date(2020, 6, 1), reported=date(2020, 6, 2), public=True,
         incident='The freezer alarm was off', reason='Switched off for cleaning',
         immediate_action='Check the alarm after cleaning', preventability='not avoidable',
         status='in process'),
    dict(date=date(2020, 7, 9), reported=date(2020, 7, 9), public=False,
         incident='A door was left open', reason='Unknown', immediate_action='Close the door',
         preventability='indistinct'),
]
created = []
for values in incidents:
    values = dict(values)
    category = values.pop('category', [])
    created.append(CriticalIncident.objects.create(department=department, category=category, **values))
first, second, third = created

# a photo with metadata, as a phone would write it
image = io.BytesIO()
Image.new('RGB', (64, 48), (200, 30, 30)).save(image, 'JPEG', exif=exif_with_artist(EXIF_MARKER))
second.photo.save('seed-photo.jpg', ContentFile(image.getvalue()))

published = PublishableIncident.objects.create(critical_incident=first, publish=True)
published.create_translation('en', incident='Pipette tips fell into a sample',
                             description='Description of the first case',
                             measures_and_consequences='A tray is used now')
published.create_translation('de', incident='Pipettenspitzen fielen in eine Probe',
                             description='Beschreibung des ersten Falls',
                             measures_and_consequences='Jetzt wird eine Schale benutzt')

comment = Comment.objects.create(
    critical_incident=second, author=reviewer.user, created=date(2020, 6, 3), status='open',
    text='Who switched the alarm off?')

expected = {
    'counts': {'departments': 1, 'incidents': 3, 'published': 1, 'comments': 1, 'users': 4},
    'department': 'test',
    'incidents': [{
        'id': i.id, 'code': i.comment_code, 'date': str(i.date), 'reported': str(i.reported),
        'public': i.public, 'incident': i.incident, 'reason': i.reason, 'status': i.status,
        'category': list(i.category), 'photo': i.photo.name or ''} for i in created],
    'published': {'incident_id': first.id, 'en': 'Pipette tips fell into a sample',
                  'de': 'Pipettenspitzen fielen in eine Probe'},
    'comment': {'incident_id': second.id, 'text': comment.text, 'author': 'seed-reviewer'},
    'photo_marker': EXIF_MARKER,
    'pending_user': 'seed-pending',
    'reviewer': 'seed-reviewer',
    'reporter': 'seed-reporter',
}
print('EXPECTED ' + json.dumps(expected))
