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

import shutil
import tempfile
from datetime import date
from io import StringIO

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from cirs.forms import normalize_code
from cirs.models import (Comment, CriticalIncident, Department, LabCIRSConfig,
                         OrgUnit, PublishableIncident, Reporter, Reviewer,
                         STATUS_CHOICES)

QM_PASSWORD = 'qm-test-password-1'
ADMIN_PASSWORD = 'admin-test-password-2'
PARLER_CODES = [lang['code'] for lang in settings.PARLER_LANGUAGES[None]]


def counts():
    return {model.__name__: model.objects.count()
            for model in (User, Reporter, Reviewer, Department, LabCIRSConfig, OrgUnit,
                          CriticalIncident, PublishableIncident, Comment)}


class SeedDemoDataTest(TestCase):

    def setUp(self):
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=media)
        override.enable()
        self.addCleanup(override.disable)

    def seed(self, **options):
        out = StringIO()
        call_command('seed_demo_data', qm_password=QM_PASSWORD, admin_password=ADMIN_PASSWORD,
                     stdout=out, **options)
        return out.getvalue()

    def test_creates_the_demo_data(self):
        self.seed()
        self.assertEqual(counts(), {
            'User': 3, 'Reporter': 1, 'Reviewer': 1, 'Department': 1, 'LabCIRSConfig': 1,
            'OrgUnit': 6, 'CriticalIncident': 6, 'PublishableIncident': 2, 'Comment': 5})

    def test_second_call_gives_the_same_counts(self):
        self.seed()
        first = counts()
        photos = sorted(CriticalIncident.objects.exclude(photo='').values_list('photo', flat=True))
        self.seed()
        self.assertEqual(counts(), first)
        # no second copy of the photos either
        self.assertEqual(
            sorted(CriticalIncident.objects.exclude(photo='').values_list('photo', flat=True)),
            photos)

    def test_second_call_keeps_what_the_qm_changed(self):
        self.seed()
        incident = CriticalIncident.objects.get(status='new', public=True)
        incident.status = 'in process'
        incident.save()
        self.seed()
        incident.refresh_from_db()
        self.assertEqual(incident.status, 'in process')

    def test_accounts_and_passwords_come_from_the_call(self):
        self.seed()
        qm = User.objects.get(username='qm-demo')
        self.assertTrue(qm.check_password(QM_PASSWORD))
        self.assertTrue(qm.is_staff)
        self.assertTrue(hasattr(qm, 'reviewer'))
        self.assertTrue(qm.email)
        admin = User.objects.get(username='admin-demo')
        self.assertTrue(admin.check_password(ADMIN_PASSWORD))
        self.assertTrue(admin.is_superuser)
        self.assertTrue(admin.email)
        # the technical author of anonymous replies: nobody logs in with it
        self.assertFalse(User.objects.get(username='reporter-demo').has_usable_password())

    def test_a_new_password_in_the_second_call_replaces_the_first(self):
        self.seed()
        call_command('seed_demo_data', qm_password='another-qm-password',
                     admin_password='another-admin-password', stdout=StringIO())
        self.assertTrue(User.objects.get(username='qm-demo').check_password('another-qm-password'))
        self.assertTrue(
            User.objects.get(username='admin-demo').check_password('another-admin-password'))

    def test_passwords_are_required(self):
        with self.assertRaises(CommandError):
            call_command('seed_demo_data', stdout=StringIO())
        self.assertEqual(User.objects.count(), 0)

    def test_the_passwords_are_not_printed(self):
        output = self.seed()
        self.assertNotIn(QM_PASSWORD, output)
        self.assertNotIn(ADMIN_PASSWORD, output)

    def test_one_active_department_with_the_qm_and_the_reporter(self):
        self.seed()
        department = Department.objects.get()
        self.assertTrue(department.active)
        self.assertEqual([r.user.username for r in department.reviewers.all()], ['qm-demo'])
        self.assertEqual(department.reporter.user.username, 'reporter-demo')

    def test_reports_cover_every_status_and_a_missing_consent(self):
        self.seed()
        statuses = set(CriticalIncident.objects.values_list('status', flat=True))
        self.assertEqual(statuses, {code for code, _ in STATUS_CHOICES})
        self.assertEqual(CriticalIncident.objects.filter(public=False).count(), 1)
        self.assertTrue(CriticalIncident.objects.filter(public=True).count() >= 2)

    def test_reports_have_unique_codes_and_one_has_a_legacy_code(self):
        self.seed()
        codes = list(CriticalIncident.objects.values_list('comment_code', flat=True))
        self.assertEqual(len(set(codes)), 6)
        self.assertEqual(sorted(len(code) for code in codes), [8, 16, 16, 16, 16, 16])
        # the old 8 character code with special characters
        self.assertEqual(normalize_code(' AB#D$F-9 '), 'ab#d$f-9')
        self.assertIn('ab#d$f-9', codes)

    def test_reports_do_not_lie_in_the_future(self):
        self.seed()
        self.assertFalse(CriticalIncident.objects.filter(date__gt=date.today()).exists())

    def test_two_published_cases_with_all_languages(self):
        self.seed()
        published = PublishableIncident.objects.filter(publish=True)
        self.assertEqual(published.count(), 2)
        for case in published:
            self.assertEqual(case.translation_status, 'complete')
            for code in PARLER_CODES:
                self.assertTrue(case.safe_translation_getter('incident', language_code=code))
            self.assertTrue(case.critical_incident.public)
        self.assertTrue(any(case.critical_incident.photo for case in published))

    def test_review_block_of_a_report_in_process_is_filled(self):
        self.seed()
        incident = CriticalIncident.objects.filter(status='in process').get()
        for field in ('action', 'responsibilty', 'review_date', 'risk', 'frequency', 'hazard',
                      'category'):
            self.assertTrue(getattr(incident, field), field)
        incident.full_clean()  # a state the admin accepts

    def test_replies_from_the_qm_and_from_the_reporter(self):
        self.seed()
        authors = set(Comment.objects.values_list('author__username', flat=True))
        self.assertEqual(authors, {'qm-demo', 'reporter-demo'})

    def test_org_units_a_and_b_with_two_sub_units_each(self):
        self.seed()
        top = OrgUnit.objects.filter(parent=None)
        self.assertEqual(top.count(), 2)
        for unit in top:
            self.assertEqual(unit.children.count(), 2)
            self.assertTrue(unit.active)
        self.assertTrue(CriticalIncident.objects.exclude(org_unit=None).exists())

    def test_login_info_is_set_in_every_language(self):
        self.seed()
        config = LabCIRSConfig.objects.get()
        self.assertEqual(config.translation_status, 'complete')

    def test_extra_published_cases_for_paging(self):
        self.seed(extra_published=30)
        self.assertEqual(PublishableIncident.objects.filter(publish=True).count(), 32)
        self.assertEqual(CriticalIncident.objects.count(), 36)
        before = counts()
        self.seed(extra_published=30)
        self.assertEqual(counts(), before)
        self.assertFalse(CriticalIncident.objects.filter(date__gt=date.today()).exists())

    def test_the_output_lists_the_codes_of_the_demo_reports(self):
        output = self.seed()
        for code in CriticalIncident.objects.values_list('comment_code', flat=True):
            self.assertIn(code, output)
