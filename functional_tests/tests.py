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

from datetime import date

from django.core import mail
from django.test import override_settings
from django.urls import reverse
from model_bakery import baker
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select

from cirs.models import Reporter, Reviewer, group_code
from cirs.tests.helpers import create_role
from cirs.tests.tests import generate_three_incidents

from .base import FunctionalTest

incident_date = date(2015, 7, 24)
test_incident = {'date': incident_date,
                 'incident': 'A strang incident happened',
                 'reason': 'No one knows',
                 'immediate_action': 'No action possible',
                 'preventability': 'indistinct',
                 'public': True,
                 }


class FunctionalTestWithBackendLogin(FunctionalTest):

    def go_to_test_incident_as_reviewer(self):
        self.dept.reviewers.add(create_role(Reviewer, self.reviewer))
        self.browser.get(self.live_server_url + reverse('admin:index'))
        self.login_user(username=self.REVIEWER, password=self.REVIEWER_PASSWORD)
        self.wait.until(EC.presence_of_element_located((By.ID, 'site-name')))
        self.assertIn("/admin/", self.browser.current_url)
        self.click_link_with_text('Critical incidents')
        self.click_link_with_text(test_incident['incident'])


class CriticalIncidentListTest(FunctionalTestWithBackendLogin):
    
    def setUp(self):
        super(CriticalIncidentListTest, self).setUp()
        create_role(Reporter, self.reporter)
        self.dept = baker.make_recipe('cirs.department', reporter=self.reporter.reporter)
        self.dept.labcirsconfig.mandatory_languages=['en']
        self.dept.labcirsconfig.save()
        self.create_url = reverse('create_incident', kwargs={'dept': self.dept.label})

    def test_user_can_add_incident_with_photo(self):
        # the reporter needs no login: the report form is open
        self.browser.get(self.live_server_url + self.dept.get_absolute_url())
        self.click_link_with_text('Report incident')
        self.assertCurrentUrlIs(self.create_url)

        # the reporter enters incident data
        self.enter_test_incident(with_photo=True)
        # check for success
        self.assertCurrentUrlIs(reverse('success', kwargs={'dept': self.dept.label}))

        # the reviewer has to "publish" the incident
        self.go_to_test_incident_as_reviewer()
        # uncollapse the review panel
        self.open_review_panel()
        Select(self.find(By.ID,
            'id_status')).select_by_value("in process")
        for field in ('incident', 'description', 'measures_and_consequences'):
            #for lang in ('de', 'en'):  # TODO: import languages from settings
            self.find_input_and_enter_text(
                'id_publishableincident-0-{}'.format(field), "a")
        self.scroll_and_click(By.ID, 'id_publishableincident-0-publish')
        self.save_in_admin()
        headers1 = self.find_all(By.TAG_NAME, 'h1')
        self.assertIn("Select Critical incident to change", [header1.text for header1 in headers1])
        # logout and check as anonymous visitor if the photo is offered
        self.logout_backend()
        self.browser.get(self.live_server_url + self.dept.get_absolute_url())
        # check if all expected fields are present in the table
        EXPECTED_HEADERS = [u'Month/year', u'Incident', u'Description',
                            u'Measures and consequences', u'Photo']
        header_elements = self.get_rows_from_table()[0].find_elements(By.TAG_NAME, 'th')
        # textContent: the style shows the headers in capitals, and .text would say so
        table_headers_list = [header.get_attribute('textContent').strip() for header in header_elements]
        self.assertListEqual(EXPECTED_HEADERS, table_headers_list)

        # the photo is a plain link, no modal
        self.find(By.PARTIAL_LINK_TEXT, 'View photo')

    def test_new_publishes_incidents_are_displayed_first(self):
        """ Creates new critical incidents with published incidents and checks
        if the new appear in the upper row independent of the order of the
        critical incidents."""
        # import the generator from unit test

        generate_three_incidents(self.dept)

        # Now a visitor goes to the list and should see the list of
        # published incidents in order b, a, c
        self.browser.get(self.live_server_url + self.dept.get_absolute_url())
        self.assertListEqual(self.get_column_from_table_as_list(column=1), ['b', 'a', 'c'])

    @override_settings(EMAIL_HOST='smtp.example.com')
    def test_send_email_after_reporter_creates_an_incident(self):
        config = self.dept.labcirsconfig
        config.send_notification = True
        config.notification_sender_email = 'labcirs@labcirs.edu'
        config.notification_recipients.add(self.reviewer)
        config.save()
        self.browser.get(self.live_server_url + self.create_url)

        # reporter enters incident data
        self.enter_test_incident()

        # check if incident was sent by email
        self.assertEqual(len(mail.outbox), 1)  # @UndefinedVariable
        self.assertEqual(mail.outbox[0].subject, 'New critical incident')

    def test_no_address_field_without_sender_address(self):
        # Without DEFAULT_FROM_EMAIL (the development default) no mail can be sent, so the form
        # does not ask for an address.
        self.browser.get(self.live_server_url + self.create_url)
        self.field_id('Date of incident')  # waits until the form is there
        self.assert_absent(By.XPATH, '//label[starts-with(normalize-space(.), "E-mail for notifications")]')

    @override_settings(DEFAULT_FROM_EMAIL='labcirs@labcirs.edu')
    def test_reporter_can_leave_an_address_and_gets_the_code_by_mail(self):
        self.browser.get(self.live_server_url + self.create_url)
        self.fill_field('E-mail for notifications', 'reporter@example.org')

        code = self.enter_test_incident()

        self.assertEqual(len(mail.outbox), 1)  # @UndefinedVariable
        self.assertEqual(mail.outbox[0].to, ['reporter@example.org'])
        # the mail shows the code in groups of four, as the success page does
        self.assertIn(group_code(code), mail.outbox[0].body)
