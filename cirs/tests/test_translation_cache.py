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

from django.core.cache import cache
from django.test import TestCase
from django.utils import translation

from cirs.models import (CriticalIncident, Department, LabCIRSConfig,
                         PublishableIncident, PublishableIncidentTranslation,
                         Reporter)

from .helpers import create_role


class TranslationsAreNotKeptPerProcessTest(TestCase):
    """
    gunicorn runs several workers and each has its own cache (the default LocMemCache). A
    translation that parler keeps in that cache stays old in the other workers after the QM edited
    it: the acceptance run showed the old login info on the login page for every second request.
    A change that another process made must be seen at once, so parler does not cache.
    """

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        reporter = create_role(Reporter, 'cache-reporter')
        self.department = Department.objects.create(
            label='cache', name='Cache', reporter=reporter, active=True)

    def another_worker_updates(self, model, **values):
        """What a save in another worker looks like here: the row changes, no cache is touched."""
        model.objects.update(**values)

    def test_login_info_changed_in_another_worker_is_seen(self):
        config = LabCIRSConfig.objects.get(department=self.department)
        config.set_current_language('de')
        config.login_info = 'old text'
        config.save()
        with translation.override('de'):
            self.assertEqual(LabCIRSConfig.objects.get(pk=config.pk).login_info, 'old text')
        self.another_worker_updates(LabCIRSConfig._parler_meta.root_model, login_info='new text')
        with translation.override('de'):
            self.assertEqual(LabCIRSConfig.objects.get(pk=config.pk).login_info, 'new text')

    def test_title_of_a_published_case_changed_in_another_worker_is_seen(self):
        incident = CriticalIncident.objects.create(
            department=self.department, date='2026-01-01', incident='x', reason='x',
            immediate_action='x', preventability='avoidable', public=True)
        case = PublishableIncident.objects.create(critical_incident=incident, publish=True)
        case.create_translation('de', incident='alt')
        with translation.override('de'):
            self.assertEqual(PublishableIncident.objects.get(pk=case.pk).incident, 'alt')
        self.another_worker_updates(PublishableIncidentTranslation, incident='neu')
        with translation.override('de'):
            self.assertEqual(PublishableIncident.objects.get(pk=case.pk).incident, 'neu')
