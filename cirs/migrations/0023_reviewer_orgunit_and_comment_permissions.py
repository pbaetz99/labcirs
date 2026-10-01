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

# Reviewers get the new permissions (organisational units, and view_comment for the comment
# inline of the admin). On a fresh database post_migrate has not created the permissions yet
# at this point, so they are created first.

from django.contrib.auth.management import create_permissions
from django.db import migrations

CODES = ('add_orgunit', 'change_orgunit', 'view_orgunit', 'view_comment')


def grant(apps, schema_editor):
    app_config = apps.get_app_config('cirs')
    app_config.models_module = True
    create_permissions(app_config, apps=apps, verbosity=0)
    app_config.models_module = None
    perms = list(apps.get_model('auth', 'Permission').objects.filter(
        codename__in=CODES, content_type__app_label='cirs'))
    for reviewer in apps.get_model('cirs', 'Reviewer').objects.select_related('user'):
        reviewer.user.user_permissions.add(*perms)


class Migration(migrations.Migration):

    dependencies = [
        ('cirs', '0022_orgunit_and_incident_org_unit'),
    ]

    operations = [
        migrations.RunPython(grant, migrations.RunPython.noop),
    ]
