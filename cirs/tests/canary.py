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

"""Data of a department the QM under test does not belong to, to prove that it never leaks.

Every text of the foreign department holds the word CANARY: its name and label, an organisational
unit, an incident with a category and a risk, a published case with a title and measures, and a
comment. A page for the QM of another department must not show the word anywhere, so a test of a
QM page only has to ask assertNoCanary(page). The pages add to this as they grow: a page that
counts or lists incidents also checks that the numbers and the numbers of the incidents in
canary_incidents are not those of the foreign department.

    class MyTest(CanaryMixin, TestCase):
        @classmethod
        def setUpTestData(cls):
            cls.make_canary()
"""

from datetime import date

from model_bakery import baker

from cirs.models import Comment, OrgUnit, PublishableIncident

from .helpers import make_incident

CANARY = 'Kanarienvogel'


class CanaryMixin:

    @classmethod
    def make_canary(cls, reported=None):
        """The foreign department and its data. The incidents are reported on the day `reported`
        (today by default): a page that counts a period gives them away only inside it."""
        reported = reported or date.today()
        cls.canary_dept = baker.make_recipe(
            'cirs.department', label='kanarienvogel', name=CANARY + '-Abteilung',
            reporter__user__username='kanarienvogel-melder')
        group = baker.make(OrgUnit, name=CANARY + '-Gruppe')
        unit = baker.make(OrgUnit, name=CANARY + '-Station', parent=group)
        cls.canary_incidents = [
            make_incident(cls.canary_dept, reported=reported, status='in process',
                          incident='%s Meldung %d' % (CANARY, number), org_unit=unit,
                          category=['knowledge/training'], risk='high', preventability='avoidable')
            for number in range(3)]
        case = PublishableIncident.objects.create(critical_incident=cls.canary_incidents[0])
        for language in ('en', 'de'):
            case.create_translation(language, incident=CANARY + ' Titel',
                                    description=CANARY + ' Beschreibung',
                                    measures_and_consequences=CANARY + ' Maßnahmen')
        case.publish = True
        case.save()
        baker.make(Comment, critical_incident=cls.canary_incidents[1],
                   author=cls.canary_dept.reporter.user, text=CANARY + ' Kommentar')

    def assertNoCanary(self, text, msg=None):
        """text, a page or a file, holds nothing of the foreign department."""
        if isinstance(text, bytes):
            text = text.decode('utf-8-sig')
        self.assertNotIn(CANARY.lower(), text.lower(), msg)
