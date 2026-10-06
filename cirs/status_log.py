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

"""How the status log of an incident is shown to the QM, on its page and in the admin.

The first entry of the log, if it says "new", is the report itself. Reports are anonymous, and the
time of day of an anonymous report (three in the morning, from a ward) says who may have written
it. So that entry has its day and no time. Every later entry is a change that the QM made, and
keeps its time.
"""

from datetime import datetime
from typing import NamedTuple

from django.db.models import OuterRef, Subquery

from .models import IncidentStatusChange

SUBMISSION_STATUS = 'new'


class LogRow(NamedTuple):
    """An entry for display: the name of its status, its moment, and whether the time of day of
    the moment may be shown."""
    status: str
    moment: datetime
    with_time: bool


def shows_time(status, entry_id, first_entry_id):
    """Whether the moment of an entry is shown with its time of day: not for the first entry of
    its log if that entry says "new", because that is the report."""
    return not (status == SUBMISSION_STATUS and entry_id == first_entry_id)


def log_rows(entries):
    """The entries of one incident, in the order of the log, as LogRow."""
    entries = list(entries)
    first_entry_id = entries[0].pk if entries else None
    return [LogRow(str(entry.get_status_display()), entry.changed_at,
                   shows_time(entry.status, entry.pk, first_entry_id))
            for entry in entries]


def with_first_entry(entries):
    """The entries of a queryset, each with first_entry_id: the first entry of the log of its
    incident, so that a row can tell whether it is the report without a query of its own."""
    first = (IncidentStatusChange.objects.filter(incident=OuterRef('incident'))
             .order_by('changed_at', 'pk').values('pk')[:1])
    return entries.annotate(first_entry_id=Subquery(first))
