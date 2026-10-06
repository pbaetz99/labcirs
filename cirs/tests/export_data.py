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

"""Data and a base class for the tests of what is printed or exported from the evaluations.

The data has twelve incidents that fall in cells of four and more in every series of the
evaluation, and three that fall in cells of one and two in every series: the months, the areas,
the risk, the frequency, the hazard, the preventability and the categories. Which of the two
small cells holds the one and which the two is the variant: "a" or "b". The numbers that are shown
are the same in both, and only what is hidden differs, so an output that is made from what is
shown must be the same for both. A foreign department has data of its own, for the same period.
"""

import csv
import io
from datetime import date

from django.urls import reverse
from model_bakery import baker

from cirs.models import CriticalIncident, OrgUnit

from .canary import CanaryMixin
from .helpers import make_incident
from .test_qm_overview import TODAY, at
from .test_qm_reports import DE, ReportBase, publish

PERIOD = {'von_monat': 1, 'von_jahr': 2026, 'bis_monat': 10, 'bis_jahr': 2026}
EXPORT_URL = {'print': 'qm_reports_print', 'csv': 'qm_reports_csv'}

# What the three incidents that swing have in common: the first is always in the first cell, the
# last always in the second, and the middle one in the second cell for variant "a" and in the first
# for variant "b". The first cell then has 1 and 2, the second 2 and 1.
SWING = (
    # reported in the first small month, area, risk, preventability, frequency, hazard, category
    {'month': (2, 10), 'area': 'diag', 'risk': 'middle', 'preventability': 'indistinct',
     'frequency': 'frequent', 'hazard': 'moderate', 'category': ['other']},
    {'month': (9, 3), 'area': 'verw', 'risk': '', 'preventability': 'not avoidable',
     'frequency': '', 'hazard': 'high', 'category': ['infrastructure']},
)


class Units:
    """The organisational units of the data: two groups with incidents, one of them with a unit
    below it, and two small groups for the incidents that swing."""

    def __init__(self):
        self.labor = OrgUnit.objects.create(name='Labor')
        self.station = OrgUnit.objects.create(name='Station A', parent=self.labor)
        self.pflege = OrgUnit.objects.create(name='Pflege')
        self.diag = OrgUnit.objects.create(name='Diagnostik')
        self.verw = OrgUnit.objects.create(name='Verwaltung')
        self.leer = OrgUnit.objects.create(name='Leer')  # a group without incidents


def make_bulk(dept, units):
    """Twelve incidents in groups of four: reported in January, March and June, the first two
    groups completed (after 10 and after 15 days), the third taken up after 4 days."""
    histories = (
        ((1, 1), [('new', at(1, 1)), ('in process', at(1, 3)), ('completed', at(1, 11))]),
        ((3, 5), [('new', at(3, 5)), ('in process', at(3, 6)), ('completed', at(3, 20))]),
        ((6, 5), [('new', at(6, 5)), ('in process', at(6, 9))]))
    incidents = []
    for number in range(12):
        (month, day), history = histories[number // 4]
        first_half = number < 6
        incidents.append(make_incident(
            dept, reported=date(2026, month, day), history=history,
            org_unit=(units.station if number < 3 else units.labor) if first_half
            else units.pflege,
            risk='low' if first_half else 'high', preventability='avoidable',
            frequency='seldom' if first_half else 'occasional', hazard='low',
            category=['knowledge/training'] if first_half else ['technique/methods']))
    return incidents


def make_swing(dept, units, variant):
    """Three incidents that are all still new. Their small cells hold 1 and 2 (variant "a") or 2
    and 1 (variant "b"); the first and the last are the same in both."""
    first, second = SWING
    areas = {'diag': units.diag, 'verw': units.verw}
    kinds = [first, second if variant == 'a' else first, second]
    incidents = []
    for kind in kinds:
        month, day = kind['month']
        incidents.append(make_incident(
            dept, reported=date(2026, month, day + len(incidents)),
            history=[('new', at(month, day + len(incidents)))], org_unit=areas[kind['area']],
            risk=kind['risk'], preventability=kind['preventability'],
            frequency=kind['frequency'], hazard=kind['hazard'], category=kind['category']))
    return incidents


def make_data(dept, units, variant):
    """The twelve and the three. A published case of the first of the three, and in variant "b" a
    second one of the one that swings."""
    bulk = make_bulk(dept, units)
    swing = make_swing(dept, units, variant)
    publish(swing[0], 'Titel Eins', 'Maßnahme Eins')
    if variant == 'b':
        publish(swing[1], 'Titel Zwei', 'Maßnahme Zwei')
    return bulk + swing


def csv_rows(content):
    """The rows of a CSV file as a list of lists, from the bytes of the response."""
    return list(csv.reader(io.StringIO(content.decode('utf-8-sig')), delimiter=';'))


class ExportCase(CanaryMixin, ReportBase):
    """A department with data, and a foreign one that must never show. `variant` is the one
    that is built; `build('b')` replaces it."""

    variant = 'a'

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.make_canary(reported=TODAY)
        cls.units = Units()

    def setUp(self):
        super().setUp()
        self.incidents = make_data(self.dept, self.units, self.variant)

    def build(self, variant):
        """The data of another variant instead of the one that is there."""
        CriticalIncident.objects.filter(department=self.dept).delete()
        self.incidents = make_data(self.dept, self.units, variant)

    def export(self, kind, params=PERIOD, **extra):
        return self.client.get(reverse(EXPORT_URL[kind]), params, **{**DE, **extra})

    def print_html(self, params=PERIOD, **extra):
        response = self.export('print', params, **extra)
        self.assertEqual(response.status_code, 200, params)
        return response.content.decode()

    def download(self, params=PERIOD, **extra):
        response = self.export('csv', params, **extra)
        self.assertEqual(response.status_code, 200, params)
        return response

    def rows(self, params=PERIOD, **extra):
        return csv_rows(self.download(params, **extra).content)



def second_department(reviewer, name='Station Zwei'):
    """A second department of the reviewer."""
    second = baker.make_recipe('cirs.department', name=name)
    second.reviewers.add(reviewer)
    return second
