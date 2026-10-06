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

"""A new password must be long and not a common one: a QM account opens every report of its
departments, and the proxy only slows a guessing down."""

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.test import TestCase

from .helpers import create_user


class PasswordPolicyTest(TestCase):

    def test_a_short_password_is_refused(self):
        with self.assertRaises(ValidationError):
            validate_password('qm2026-x')

    def test_a_common_password_is_refused(self):
        with self.assertRaises(ValidationError):
            validate_password('password1234')

    def test_a_password_of_digits_only_is_refused(self):
        with self.assertRaises(ValidationError):
            validate_password('739105284617')

    def test_a_password_like_the_name_of_the_account_is_refused(self):
        user = create_user('qm.reviewer')
        with self.assertRaises(ValidationError):
            validate_password('qm.reviewer2026', user)

    def test_a_long_password_that_is_not_common_is_accepted(self):
        validate_password('Wiese-Lampe-Vierzehn-Kaffee')
