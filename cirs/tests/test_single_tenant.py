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

import re
from importlib import import_module
from urllib.parse import urlparse

from django.contrib.admin.models import ADDITION, LogEntry
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core import mail
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase
from django.urls import reverse
from model_bakery import baker

from cirs.models import Reviewer

from .helpers import create_role, create_user


class HomeTest(TestCase):

    def test_single_active_department_redirects(self):
        dept = baker.make_recipe('cirs.department')
        response = self.client.get(reverse('labcirs_home'))
        self.assertRedirects(response, reverse('incidents_for_department',
                                               kwargs={'dept': dept.label}),
                             fetch_redirect_response=False)

    def test_inactive_departments_are_ignored(self):
        active = baker.make_recipe('cirs.department')
        baker.make_recipe('cirs.department', active=False)
        response = self.client.get(reverse('labcirs_home'))
        self.assertRedirects(response, active.get_absolute_url(),
                             fetch_redirect_response=False)

    def test_two_active_departments_show_list(self):
        depts = baker.make_recipe('cirs.department', _quantity=2)
        response = self.client.get(reverse('labcirs_home'))
        self.assertEqual(response.status_code, 200)
        for dept in depts:
            self.assertContains(response, dept.label)

    def test_superuser_goes_to_admin(self):
        baker.make_recipe('cirs.department')  # a single department must not win
        self.client.force_login(create_user('admin', superuser=True))
        response = self.client.get(reverse('labcirs_home'))
        self.assertRedirects(response, reverse('admin:index'),
                             fetch_redirect_response=False)

    def test_reviewer_with_one_active_of_two_departments_is_redirected(self):
        rev = create_role(Reviewer, 'rev')
        active = baker.make_recipe('cirs.department')
        inactive = baker.make_recipe('cirs.department', active=False)
        active.reviewers.add(rev)
        inactive.reviewers.add(rev)
        self.client.force_login(rev.user)
        response = self.client.get(reverse('labcirs_home'))
        self.assertRedirects(response, active.get_absolute_url(),
                             fetch_redirect_response=False)


class RegistrationRemovedTest(TestCase):

    def test_register_url_is_gone(self):
        self.assertEqual(self.client.get('/accounts/register/').status_code, 404)

    def test_password_reset_page_available(self):
        self.assertEqual(self.client.get(reverse('password_reset')).status_code, 200)

    def test_failed_login_links_to_password_reset(self):
        response = self.client.post(reverse('login'), {'username': 'x', 'password': 'y'})
        self.assertContains(response, reverse('password_reset'))

    def test_registration_tables_dropped(self):
        tables = connection.introspection.table_names()
        self.assertNotIn('registration_registrationprofile', tables)
        self.assertNotIn('registration_supervisedregistrationprofile', tables)

    def test_migration_removes_registration_content_types(self):
        # What a v5.2.1 database still holds: content types of the removed app, their permissions
        # (one granted to a user) and a log entry that points to one of them.
        user = create_user('rev')
        for model in ('registrationprofile', 'supervisedregistrationprofile'):
            content_type = ContentType.objects.create(app_label='registration', model=model)
            permission = Permission.objects.create(
                content_type=content_type, codename='add_' + model, name='Can add ' + model)
            user.user_permissions.add(permission)
        entry = LogEntry.objects.create(user=user, content_type=content_type, object_id='1',
                                        object_repr='old', action_flag=ADDITION)
        name = '0025_remove_registration_content_types'
        state_apps = MigrationExecutor(connection).loader.project_state([('cirs', name)]).apps

        import_module('cirs.migrations.' + name).remove_content_types(state_apps, None)

        self.assertFalse(ContentType.objects.filter(app_label='registration').exists())
        self.assertFalse(Permission.objects.filter(content_type__app_label='registration').exists())
        self.assertTrue(ContentType.objects.filter(app_label='cirs').exists())
        entry.refresh_from_db()
        self.assertIsNone(entry.content_type)
        self.assertEqual(user.user_permissions.filter(codename__contains='registrationprofile').count(), 0)


class PasswordResetTest(TestCase):

    def test_reset_link_from_email_sets_new_password(self):
        user = create_user('rev')
        response = self.client.post(reverse('password_reset'), {'email': user.email})
        self.assertRedirects(response, reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox), 1)
        link = re.search(r'https?://\S+', mail.outbox[0].body).group()

        response = self.client.get(urlparse(link).path, follow=True)
        form_url = response.redirect_chain[-1][0]
        new_password = 'synthetic-test-password'
        response = self.client.post(form_url, {'new_password1': new_password,
                                               'new_password2': new_password})

        self.assertRedirects(response, reverse('password_reset_complete'))
        self.assertTrue(self.client.login(username='rev', password=new_password))
