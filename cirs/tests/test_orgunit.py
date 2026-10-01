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

from importlib import import_module

from django.contrib.auth.models import Permission
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.models import ProtectedError
from django.test import TestCase
from django.urls import reverse
from django.utils import translation
from model_bakery import baker

from cirs.forms import org_unit_choices
from cirs.models import CriticalIncident, OrgUnit

from .test_anonymous import VALID

DATA_MIGRATION = '0023_reviewer_orgunit_and_comment_permissions'
PERMISSIONS = ('cirs.add_orgunit', 'cirs.change_orgunit', 'cirs.view_orgunit', 'cirs.view_comment')


class OrgUnitChoicesTest(TestCase):

    def test_choices_group_units_with_children(self):
        a = OrgUnit.objects.create(name='A', position=1)
        a1 = OrgUnit.objects.create(name='a1', parent=a)
        a2 = OrgUnit.objects.create(name='a2', parent=a)
        b = OrgUnit.objects.create(name='B', position=2)
        OrgUnit.objects.create(name='C', position=3, active=False)
        OrgUnit.objects.create(name='a3', parent=a, active=False)
        self.assertEqual(org_unit_choices()[1:],
                         [('A', [(a1.pk, 'a1'), (a2.pk, 'a2')]), (b.pk, 'B')])
        with translation.override('en'):
            self.assertEqual(str(org_unit_choices()[0][1]), 'No answer')

    def test_parent_with_only_inactive_children_is_a_plain_choice(self):
        a = OrgUnit.objects.create(name='A')
        OrgUnit.objects.create(name='a1', parent=a, active=False)
        self.assertEqual(org_unit_choices()[1:], [(a.pk, 'A')])

    def test_str_shows_parent_and_name(self):
        a = OrgUnit.objects.create(name='A')
        self.assertEqual(str(a), 'A')
        self.assertEqual(str(OrgUnit.objects.create(name='a1', parent=a)), 'A › a1')


class OrgUnitTranslationTest(TestCase):

    def test_german_names_of_the_model_and_its_fields(self):
        # Guards against stray continuation lines in the .po file.
        with translation.override('de'):
            self.assertEqual(OrgUnit._meta.verbose_name, 'Organisationseinheit')
            self.assertEqual(OrgUnit._meta.verbose_name_plural, 'Organisationseinheiten')
            self.assertEqual(OrgUnit._meta.get_field('position').verbose_name, 'Reihenfolge')
            self.assertEqual(OrgUnit._meta.get_field('parent').verbose_name,
                             'Übergeordnete Einheit')


class OrgUnitCreateTest(TestCase):

    def setUp(self):
        self.dept = baker.make_recipe('cirs.department')
        self.url = reverse('create_incident', kwargs={'dept': self.dept.label})
        self.a = OrgUnit.objects.create(name='A')
        self.a1 = OrgUnit.objects.create(name='a1', parent=self.a)

    def test_create_without_org_unit(self):
        response = self.client.post(self.url, VALID)
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(CriticalIncident.objects.get().org_unit)

    def test_create_with_org_unit(self):
        response = self.client.post(self.url, dict(VALID, org_unit=self.a1.pk))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(CriticalIncident.objects.get().org_unit, self.a1)

    def test_create_with_inactive_org_unit_is_rejected(self):
        self.a1.active = False
        self.a1.save()
        response = self.client.post(self.url, dict(VALID, org_unit=self.a1.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(CriticalIncident.objects.count(), 0)

    def test_form_renders_grouped_select_with_german_label_and_hint(self):
        response = self.client.get(self.url, HTTP_ACCEPT_LANGUAGE='de')
        self.assertContains(response, 'Wo ist es passiert?')
        self.assertContains(response, 'Freiwillig. In kleinen Einheiten kann diese Angabe '
                                      'Rückschlüsse auf Sie erlauben.')
        self.assertContains(response, '<option value="" selected>keine Angabe</option>')
        self.assertContains(response, '<optgroup label="A">')
        self.assertContains(response, '<option value="{}">a1</option>'.format(self.a1.pk))


class OrgUnitProtectionAndPrivacyTest(TestCase):

    def test_used_unit_cannot_be_deleted(self):
        unit = OrgUnit.objects.create(name='A')
        baker.make_recipe('cirs.public_ci', department=baker.make_recipe('cirs.department'),
                          org_unit=unit)
        with self.assertRaises(ProtectedError):
            unit.delete()

    def test_unit_with_children_cannot_be_deleted(self):
        unit = OrgUnit.objects.create(name='A')
        OrgUnit.objects.create(name='a1', parent=unit)
        with self.assertRaises(ProtectedError):
            unit.delete()

    def test_org_unit_not_in_published_list(self):
        dept = baker.make_recipe('cirs.department')
        unit = OrgUnit.objects.create(name='Synthetic Ward Alpha')
        baker.make_recipe('cirs.published_incident', critical_incident__department=dept,
                          critical_incident__org_unit=unit)
        response = self.client.get(dept.get_absolute_url())
        self.assertEqual(len(response.context['object_list']), 1)
        self.assertNotContains(response, unit.name)


class ReviewerPermissionsTest(TestCase):

    def test_reviewer_gets_permissions(self):
        user = baker.make_recipe('cirs.reviewer').user
        for perm in PERMISSIONS:
            self.assertTrue(user.has_perm(perm), perm)
        self.assertFalse(user.has_perm('cirs.delete_orgunit'))

    def test_reviewer_can_open_orgunit_admin_and_comment_inline(self):
        dept = baker.make_recipe('cirs.department')
        reviewer = baker.make_recipe('cirs.reviewer')
        dept.reviewers.add(reviewer)
        ci = baker.make_recipe('cirs.public_ci', department=dept,
                               org_unit=OrgUnit.objects.create(name='A'))
        self.client.force_login(reviewer.user)
        self.assertEqual(self.client.get(reverse('admin:cirs_orgunit_changelist')).status_code, 200)
        response = self.client.get(reverse('admin:cirs_criticalincident_change', args=(ci.pk,)))
        self.assertContains(response, 'A')
        self.assertContains(response, 'id_comments-TOTAL_FORMS')

    def test_data_migration_grants_permissions_to_existing_reviewers(self):
        # Fresh databases run the migration before post_migrate creates any permission.
        reviewer = baker.make_recipe('cirs.reviewer')
        Permission.objects.filter(codename__in=[p.split('.')[1] for p in PERMISSIONS]).delete()
        reviewer.user.user_permissions.clear()
        # As in a real migrate run, the state contains all migrations applied before.
        state_apps = MigrationExecutor(connection).loader.project_state(
            [('cirs', DATA_MIGRATION), ('contenttypes', '0002_remove_content_type_name')]).apps
        import_module('cirs.migrations.' + DATA_MIGRATION).grant(state_apps, None)
        user = type(reviewer.user).objects.get(pk=reviewer.user.pk)
        for perm in PERMISSIONS:
            self.assertTrue(user.has_perm(perm), perm)
