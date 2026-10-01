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

"""Checks a database that came from LabCIRS v5.2.1 against the values scripts/upgrade/seed_v521.py
wrote. Used by scripts/test-upgrade-from-v5.sh, inside the new app container:

    python - < scripts/upgrade/check_upgraded.py

Needs EXPECTED (the "EXPECTED {json}" line of the seed script, without the prefix) and
SEED_PASSWORD in the environment. Prints one line per check, exits 1 if any check failed.
"""

import json
import os
import re
import sys

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'labcirs.settings.production')

import django
django.setup()

from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.models import Permission, User
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.db import connection, transaction
from django.db.migrations.recorder import MigrationRecorder
from django.test import Client
from PIL import Image

from cirs.models import Comment, CriticalIncident, Department, PublishableIncident

expected = json.loads(os.environ['EXPECTED'])
failures = []


def check(name, ok, detail=''):
    print(('ok    ' if ok else 'FAIL  ') + name + ('' if ok else ': ' + detail))
    if not ok:
        failures.append(name)


def same(name, actual, wanted):
    check(name, actual == wanted, 'expected %r, got %r' % (wanted, actual))


# 1. Row counts, as before the dump
published = PublishableIncident.objects.filter(publish=True)
same('departments', Department.objects.count(), expected['counts']['departments'])
same('incidents', CriticalIncident.objects.count(), expected['counts']['incidents'])
same('published cases', published.count(), expected['counts']['published'])
same('comments', Comment.objects.count(), expected['counts']['comments'])
same('users', User.objects.count(), expected['counts']['users'])
same('department label', Department.objects.get().label, expected['department'])

# 2. Content of every incident: text, dates, code (must not change: reporters still hold them)
for want in expected['incidents']:
    incident = CriticalIncident.objects.get(pk=want['id'])
    label = 'incident %d' % want['id']
    same(label + ' text', (incident.incident, incident.reason, incident.status),
         (want['incident'], want['reason'], want['status']))
    same(label + ' dates', (str(incident.date), str(incident.reported)),
         (want['date'], want['reported']))
    same(label + ' code unchanged', incident.comment_code, want['code'])
    same(label + ' consent', incident.public, want['public'])
    same(label + ' categories', list(incident.category), want['category'])
    same(label + ' org_unit empty', incident.org_unit, None)

# 3. Published case with both translations, comment with its author
case = published.get()
same('published case belongs to its incident', case.critical_incident_id,
     expected['published']['incident_id'])
for code in ('en', 'de'):
    same('translation ' + code, case.get_translation(code).incident, expected['published'][code])
comment = Comment.objects.get()
same('comment', (comment.critical_incident_id, comment.text, comment.author.username),
     (expected['comment']['incident_id'], expected['comment']['text'],
      expected['comment']['author']))

# 4. The reviewer has the new permissions (data migration), and the old ones
reviewer = User.objects.get(username=expected['reviewer'])
for perm in ('view_orgunit', 'add_orgunit', 'change_orgunit', 'view_comment',
             'change_criticalincident', 'change_publishableincident'):
    check('reviewer has cirs.' + perm, reviewer.has_perm('cirs.' + perm))
check('reporter is not staff', not User.objects.get(username=expected['reporter']).is_staff)

# 5. Registration app is gone: tables, content types, permissions, and users can be deleted
tables = [t for t in connection.introspection.table_names() if t.startswith('registration_')]
same('no registration tables', tables, [])
same('no registration content types',
     ContentType.objects.filter(app_label='registration').count(), 0)
same('no registration permissions',
     Permission.objects.filter(content_type__app_label='registration').count(), 0)
try:
    with transaction.atomic():
        User.objects.get(username=expected['pending_user']).delete()
        raise RuntimeError('roll back')
except RuntimeError:
    check('user with an old registration profile can be deleted', True)
except Exception as error:
    check('user with an old registration profile can be deleted', False, repr(error))

# 6. Migrations: the squashed migration is recorded, nothing is left to apply
recorded = set(MigrationRecorder.Migration.objects.filter(app='cirs')
               .values_list('name', flat=True))
check('cirs migrations recorded',
      {'0001_squashed_0019_reinitialized', '0024_reportercontact'} <= recorded,
      'recorded: %s' % sorted(recorded))
try:
    call_command('migrate', check_unapplied=True, verbosity=0)
    check('migrate --check', True)
except SystemExit as error:
    check('migrate --check', False, 'exit status %s' % error.code)

# 7. Photo: new random name, old file gone, no metadata (strip_photo_metadata ran before)
photo_incident = next(w for w in expected['incidents'] if w['photo'])
incident = CriticalIncident.objects.get(pk=photo_incident['id'])
check('photo has a random name',
      re.fullmatch(r'photos/\d{4}/\d{2}/\d{2}/[0-9a-f]{32}\.jpg', incident.photo.name),
      incident.photo.name)
check('old photo file is removed',
      not os.path.exists(os.path.join(settings.MEDIA_ROOT, photo_incident['photo'])))
with incident.photo.open('rb') as f:
    data = f.read()
check('marker is not in the photo bytes', expected['photo_marker'].encode() not in data)
with Image.open(incident.photo.path) as image:
    check('photo has no EXIF data', not image.getexif() and 'exif' not in image.info)
    same('photo size', image.size, (64, 48))

# 8. The upgraded data works in the new app (anonymous visitors, strict CSP, production settings)
host = settings.ALLOWED_HOSTS[0].lstrip('.')
password = os.environ['SEED_PASSWORD']
check('reviewer authenticates with the old password hash',
      authenticate(username=expected['reviewer'], password=password) is not None)
old = expected['incidents'][0]
client = Client(HTTP_HOST=host)
response = client.post('/incidents/test/search/', {'incident_code': old['code']})
check('old code opens the report',
      response.status_code == 302 and response.url.endswith('/%d/' % old['id']),
      'status %s %s' % (response.status_code, getattr(response, 'url', '')))
response = client.get('/incidents/test/%d/' % old['id'])
check('detail page of the report',
      response.status_code == 200 and old['incident'] in response.content.decode(),
      'status %s' % response.status_code)
response = Client(HTTP_HOST=host, HTTP_ACCEPT_LANGUAGE='en').get('/incidents/test/')
check('public list shows the published case',
      response.status_code == 200 and expected['published']['en'] in response.content.decode(),
      'status %s' % response.status_code)
client = Client(HTTP_HOST=host)
check('reviewer can log in', client.login(username=expected['reviewer'], password=password))
for path in ('/admin/', '/admin/cirs/criticalincident/', '/admin/cirs/orgunit/',
             '/admin/cirs/publishableincident/'):
    same('admin page ' + path, client.get(path).status_code, 200)

print()
if failures:
    print('%d of the checks failed: %s' % (len(failures), ', '.join(failures)))
    sys.exit(1)
print('all checks passed')
