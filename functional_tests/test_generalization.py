# Copyright (C) 2016-2025 Sebastian Major
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

from django.test import override_settings
from django.urls import reverse
from model_bakery import baker
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select

from cirs.models import LabCIRSConfig

from .base import FunctionalTest


class OrganizationNameTest(FunctionalTest):
    """Testing customization of the name of the organization.

    The name the organization should be interchengable and will be set in
    the local_config.json but if missing, LabCIRS should be used as default.
    """

    def test_default_organization_name(self):
        # The default is set when the settings are read (LABCIRS_ORGANIZATION empty or missing),
        # the test environment sets nothing, so it is the one in the top bar.
        self.browser.get(self.live_server_url)
        organization = self.find(By.CLASS_NAME, 'ui-logo').text
        self.assertEqual(organization, "LabCIRS")

    # enter the organization name into the settings
    ORGANIZATION = 'MyFunnyLab'

    @override_settings(ORGANIZATION=ORGANIZATION)
    def test_custom_organization_name(self):
        self.browser.get(self.live_server_url)
        # the name of the organization stands in the top bar (with the product name next to it)
        # and in the footer
        organization = self.find(By.CLASS_NAME, 'ui-logo').text
        self.assertIn(self.ORGANIZATION, organization)
        self.assertIn(self.ORGANIZATION, self.find(By.CLASS_NAME, 'ui-fuss__traeger').text)


class EmailSettingsInBackend(FunctionalTest):
    """Settings for sending notifications."""
    
    def setUp(self):
        super(EmailSettingsInBackend, self).setUp()
 
        # make simple config in advance and go to the config page
        self.dept = baker.make_recipe('cirs.department')
        reviewer = baker.make_recipe('cirs.reviewer')
        self.dept.reviewers.add(reviewer)
         
        self.config = self.dept.labcirsconfig
        self.config.create_translation('en', login_info="English")
        self.config.save()
        
        admin_url = reverse('admin:cirs_labcirsconfig_change', args=(self.config.pk,))
        self.quick_backend_login(reviewer.user, admin_url)


    @override_settings(EMAIL_HOST='localhost')
    def test_reviewer_can_switch_email_notifications(self):
        """
        Email notifications can be (de)activatet if email settings are set.

        Notifications are only send if a valid(?) SMTP server is defined
        and differs from localhost.
        """

        self.find(By.ID, 'id_send_notification').click()
        self.find(By.NAME, '_save').click()
        error_msg = self.wait.until(
            EC.presence_of_element_located((By.CLASS_NAME, 'errorlist')))
        # could also import the errormessage and check for equality
        self.assertIn('distinct from localhost', error_msg.text)

    def test_only_reviewers_in_the_recipient_list(self):
        recipient_select = Select(
            self.find(By.ID, 'id_notification_recipients_from'))
        options = [opt.text for opt in recipient_select.options]
        expected = [rev.user.username for rev in self.dept.reviewers.all()]
        self.assertListEqual(options, expected,
            'found {} instead {}'.format(', '.join(options), ', '.join(expected)))
        
    @override_settings(EMAIL_HOST='smtp.example.com')
    def test_no_notifications_if_no_recipient(self):
        self.find(By.ID, 'id_send_notification').click()
        self.find(By.NAME, '_save').click()
        error_msg = self.wait.until(
            EC.presence_of_element_located((By.CLASS_NAME, 'errorlist')))
        self.assertIn('at least one notification recipient', error_msg.text)

    @override_settings(EMAIL_HOST='smtp.example.com')
    def test_no_notifications_if_no_sender(self):
        self.config.notification_recipients.add(self.reviewer)
        self.config.save()
        self.find(By.ID, 'id_send_notification').click()
        self.find(By.NAME, '_save').click()
        error_msg = self.wait.until(
            EC.presence_of_element_located((By.CLASS_NAME, 'errorlist')))
        self.assertIn('sender email', error_msg.text)

    def test_enter_sender_email(self):
        self.find_input_and_enter_text('id_notification_sender_email', 'a@test.edu')
        self.save_in_admin()
        config = LabCIRSConfig.objects.first()
        self.assertEqual(config.notification_sender_email, "a@test.edu")

    def test_reviewer_can_enter_notification_text(self):
        self.find_input_and_enter_text('id_notification_text', "New incident")
        self.save_in_admin()
        config = LabCIRSConfig.objects.first()
        self.assertEqual(config.notification_text, "New incident")
