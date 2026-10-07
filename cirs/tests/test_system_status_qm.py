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

"""A department has a QM when an account that may work as one is assigned to it: a switched off
account and a superuser (who is no QM) do not count."""

from django.test import TestCase
from model_bakery import baker

from cirs.system_status import system_status


class QmOfADepartmentTest(TestCase):

    def setUp(self):
        self.dept = baker.make_recipe('cirs.department', name='Station Eins')
        self.reviewer = baker.make_recipe('cirs.reviewer')
        self.dept.reviewers.add(self.reviewer)

    def without_qm(self):
        return [row.name for row in system_status().departments_without_qm]

    def test_a_department_with_an_active_qm_is_not_listed(self):
        self.assertNotIn('Station Eins', self.without_qm())

    def test_a_department_whose_only_qm_account_is_switched_off_is_listed(self):
        self.reviewer.user.is_active = False
        self.reviewer.user.save()
        self.assertIn('Station Eins', self.without_qm())

    def test_a_department_whose_only_reviewer_is_a_superuser_is_listed(self):
        self.reviewer.user.is_superuser = True
        self.reviewer.user.save()
        self.assertIn('Station Eins', self.without_qm())
