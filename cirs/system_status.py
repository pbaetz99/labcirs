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

"""The system status on the start page of the admin, for superusers.

It says how the system is run, not what is reported in it: accounts by role, accounts that have not
logged in for a long time, departments that have no QM, whether mail can be sent and when the
last backup was made. There is no number or text of an incident anywhere in it, and no query
reads one. The numbers of the accounts come from a fixed number of queries, whatever the number of
accounts.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from django.conf import settings
from django.contrib.auth.models import User
from django.db.models import Count, Exists, F, OuterRef, Q
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

import cirs

from .backup_status import STALE_AFTER_HOURS, BackupStatus, read_backup_status
from .models import Department, Reviewer

STALE_ACCOUNT_DAYS = 180
STALE_ACCOUNT_LIST_LENGTH = 20


class Role(StrEnum):
    SUPERUSER = 'superuser'
    QM = 'qm'
    REPORTER = 'reporter'
    NONE = 'none'


ROLE_LABELS = {
    Role.SUPERUSER: _('Superuser'),
    Role.QM: _('QM'),
    # The technical account of a department, the author of the replies of the reporting persons.
    Role.REPORTER: _('Reporter account'),
    Role.NONE: _('No role'),
}

# The role of an account is the first one that fits, in this order. A superuser is no QM and no
# reporter account, even with such a role added behind the scenes (the same rule as for the access
# to the QM pages), so each account counts once and the roles add up to all accounts.
ROLE_CONDITIONS = {
    Role.SUPERUSER: Q(is_superuser=True),
    Role.QM: Q(is_superuser=False, reviewer__isnull=False),
    Role.REPORTER: Q(is_superuser=False, reviewer__isnull=True, reporter__isnull=False),
    Role.NONE: Q(is_superuser=False, reviewer__isnull=True, reporter__isnull=True),
}


@dataclass(frozen=True)
class RoleCount:
    role: Role
    accounts: int
    inactive: int

    @property
    def label(self):
        return ROLE_LABELS[self.role]


@dataclass(frozen=True)
class StaleAccount:
    pk: int
    username: str
    role: Role
    since: datetime  # the last login, or the day the account was made if it never logged in
    never_logged_in: bool

    @property
    def label(self):
        return ROLE_LABELS[self.role]


@dataclass(frozen=True)
class StaleAccounts:
    rows: tuple[StaleAccount, ...]
    total: int

    @property
    def hidden(self):
        return self.total - len(self.rows)


@dataclass(frozen=True)
class DepartmentRow:
    pk: int
    name: str
    active: bool
    has_qm: bool
    notifies: bool


@dataclass(frozen=True)
class SystemStatus:
    version: str
    roles: tuple[RoleCount, ...]
    stale: StaleAccounts
    departments: tuple[DepartmentRow, ...]
    mail_configured: bool
    backup: BackupStatus

    @property
    def departments_without_qm(self):
        return tuple(row for row in self.departments if not row.has_qm)

    # The limits that the page names, so that its text and the checks cannot drift apart.
    @property
    def backup_stale_hours(self):
        return STALE_AFTER_HOURS

    @property
    def stale_account_days(self):
        return STALE_ACCOUNT_DAYS


def account_numbers():
    """The accounts of each role and how many of them are switched off: one query."""
    columns = {}
    for role, condition in ROLE_CONDITIONS.items():
        columns['accounts_' + role] = Count('pk', filter=condition)
        columns['inactive_' + role] = Count('pk', filter=condition & Q(is_active=False))
    totals = User.objects.aggregate(**columns)
    return tuple(RoleCount(role, totals['accounts_' + role], totals['inactive_' + role])
                 for role in Role)


def stale_accounts(now=None, limit=STALE_ACCOUNT_LIST_LENGTH):
    """The accounts that have not logged in for over STALE_ACCOUNT_DAYS days: the oldest `limit`
    of them and the number of all. Accounts that are switched off do not count, and neither do
    the reporter accounts, which never log in. Two queries."""
    now = now or timezone.now()
    candidates = (User.objects.filter(is_active=True)
                  .exclude(ROLE_CONDITIONS[Role.REPORTER])
                  .annotate(last_seen=Coalesce('last_login', 'date_joined'))
                  .filter(last_seen__lt=now - timedelta(days=STALE_ACCOUNT_DAYS)))
    rows = (candidates.annotate(is_qm=Exists(Reviewer.objects.filter(user=OuterRef('pk'))))
            .order_by('last_seen', 'username')
            .values_list('pk', 'username', 'is_superuser', 'is_qm', 'last_seen', 'last_login')
            [:limit])
    return StaleAccounts(
        tuple(StaleAccount(pk, username, _role_of(is_superuser, is_qm), last_seen, last_login is None)
              for pk, username, is_superuser, is_qm, last_seen, last_login in rows),
        candidates.count())


def _role_of(is_superuser, is_qm):
    """The role of an account that is no reporter account (those are left out before)."""
    if is_superuser:
        return Role.SUPERUSER
    return Role.QM if is_qm else Role.NONE


def departments():
    """Every department with whether it has a QM and whether the QM is notified of new
    incidents, by name: one query."""
    rows = (Department.objects
            .annotate(has_qm=Exists(Reviewer.objects.filter(
                departments=OuterRef('pk'), user__is_active=True, user__is_superuser=False)),
                      notifies=F('labcirsconfig__send_notification'))
            .order_by('name')
            .values_list('pk', 'name', 'active', 'has_qm', 'notifies'))
    # A department whose configuration is missing has none to switch on.
    return tuple(DepartmentRow(pk, name, active, has_qm, bool(notifies))
                 for pk, name, active, has_qm, notifies in rows)


def mail_configured():
    """Whether mail can be sent: a mail server other than the local one, and a sender address."""
    server = str(settings.EMAIL_HOST).strip().lower()
    return server not in ('', 'localhost') and bool(str(settings.DEFAULT_FROM_EMAIL).strip())


def system_status(now=None):
    """The whole status: four queries and a look at the backup folder."""
    now = now or timezone.now()
    return SystemStatus(
        version=cirs.__version__,
        roles=account_numbers(),
        stale=stale_accounts(now),
        departments=departments(),
        mail_configured=mail_configured(),
        backup=read_backup_status(settings.BACKUP_STATUS_DIR, now),
    )
