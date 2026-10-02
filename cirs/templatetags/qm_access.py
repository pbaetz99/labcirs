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

"""Whether a page may show the links to the QM pages."""

from django import template

from cirs.qm.access import reviewer_of

register = template.Library()


@register.filter
def is_qm(user):
    """Whether the user gets into the QM pages: the check of the access (reviewer_of), not the
    mere existence of a reviewer role, which a superuser may have without any access."""
    return bool(getattr(user, 'is_authenticated', False) and reviewer_of(user))
