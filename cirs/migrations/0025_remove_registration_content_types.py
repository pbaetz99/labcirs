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

# The removed django-registration-redux app leaves two content types and their permissions behind
# (migration 0020 drops its tables). remove_stale_contenttypes does not touch them: it only looks
# at apps that are still installed. The permissions would stay in the permission list of the user
# admin.

from django.db import migrations


def remove_content_types(apps, schema_editor):
    # delete() cascades to the permissions and their links to users and groups, and clears the
    # content type of old log entries. Needs the models of auth and admin in the state, see
    # dependencies.
    apps.get_model('contenttypes', 'ContentType').objects.filter(app_label='registration').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('cirs', '0024_reportercontact'),
        ('contenttypes', '0002_remove_content_type_name'),
        ('auth', '0012_alter_user_first_name_max_length'),
        ('admin', '0003_logentry_add_action_flag_choices'),
    ]

    operations = [
        migrations.RunPython(remove_content_types, migrations.RunPython.noop),
    ]
