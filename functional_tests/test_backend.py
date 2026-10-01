# Copyright (C) 2018-2025 Sebastian Major
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

from django.conf import settings
from django.contrib.auth.models import User
from django.urls.base import reverse
from model_bakery import baker
from parameterized import parameterized
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.select import Select

from cirs.models import Reviewer
from cirs.tests.helpers import create_role

from .base import FunctionalTest
from .test_multiorganization import get_admin_url


class CriticalIncidentBackendTest(FunctionalTest):
    """
    Added when incident was extended with category
    """

    def test_reviewer_can_chose_category_of_incident(self):
        ci = baker.make_recipe('cirs.public_ci')
        ci.department.reviewers.add(create_role(Reviewer, self.reviewer))
        admin_url = reverse('admin:cirs_criticalincident_change', args=(ci.pk,))
        self.quick_login(self.reviewer, admin_url)
        # uncollapse the review panel
        self.open_review_panel()
        Select(self.find(By.ID,
            'id_status')).select_by_value("in process")
        self.find(By.ID, 'id_category')
    
    


class AdminIndexTest(FunctionalTest):
    """The names in the index of the admin are long German compounds."""

    def test_german_names_are_hyphenated(self):
        # "Organisationseinheiten" is wider than its cell at 320 px. With lang="de" and
        # hyphens: auto the browser hyphenates it instead of cutting it in the middle.
        self.quick_backend_login(self.admin)
        self.browser.add_cookie({'name': settings.LANGUAGE_COOKIE_NAME, 'value': 'de', 'path': '/'})
        self.browser.get(self.live_server_url + reverse('admin:index'))
        link = self.find(By.LINK_TEXT, 'Organisationseinheiten')
        self.assertEqual(self.find(By.TAG_NAME, 'html').get_attribute('lang'), 'de')
        self.assertEqual(link.value_of_css_property('hyphens'), 'auto')


class ConfigurationInBackend(FunctionalTest):
    """Reviewer can specify information about login data, shown on the login page.

    Reporters do not log in anymore, the login is for the reviewers.
    """

    LOGIN_INFO = "You can find the login data for this demo installation at "
    LINK_TEXT = "the demo login data page"
    # Not this server: the validator of the URL field wants a domain with a dot or localhost,
    # and the test server has the host name of its container ("app").
    LINK_URL = "https://example.org/demo-login-data"

    def test_reviewer_can_set_the_message_text(self):
        login_url = self.live_server_url + reverse('login')
        dept = baker.make_recipe('cirs.department')
        reviewer = baker.make_recipe('cirs.reviewer')
        dept.reviewers.add(reviewer)
        self.quick_backend_login(reviewer.user)
        self.click_link_with_text('LabCIRS configuration')
        self.click_link_with_text(str(dept.labcirsconfig))
        self.find_input_and_enter_text('id_login_info', self.LOGIN_INFO)
        self.find_input_and_enter_text('id_login_info_url', self.LINK_URL)
        self.find_input_and_enter_text('id_login_info_link_text', self.LINK_TEXT)
        self.save_in_admin()
        self.logout_backend()
        # the login page, opened for the department, shows the info
        self.browser.get('{}?next={}'.format(login_url, dept.get_absolute_url()))
        current_login_info = self.wait.until(EC.presence_of_element_located((
            By.XPATH, '//div[contains(@class, "ui-alert__body")][contains(., "{}")]'.format(
                self.LOGIN_INFO)))).text
        self.assertIn(self.LOGIN_INFO, current_login_info)
        link = self.find(By.LINK_TEXT, self.LINK_TEXT)
        self.assertEqual(link.get_attribute('href'), self.LINK_URL)


class AccessRestriction(FunctionalTest):
    
    def setUp(self):
        super(AccessRestriction, self).setUp()
        self.rev, self.rev2 = baker.make_recipe('cirs.reviewer', _quantity=2)
        self.dept, self.dept2 = baker.make_recipe('cirs.department', _quantity=2)
        self.dept.reviewers.add(self.rev)
        self.dept2.reviewers.add(self.rev2)
    
    @parameterized.expand(['rep2', 'rev1', 'rev2'])
    def test_reviewer_can_see_only_users_which_are_reporters_in_his_departments(self, username):
        # check if the username really exist. Now we rely on model bakery
        if User.objects.get(username=username):
            self.check_admin_table_for_items(self.rev.user, User, self.dept.reporter.user.username, username)
        else:
            self.fail('user {} does not exist'.format(username))

    WANTED_INPUTS = ['csrfmiddlewaretoken', 'username', 'first_name', 'last_name', '_save', '_continue']
    PROHIBITED_INPUTS = ['email', 'is_active', 'is_staff', 'is_superuser', 'last_login_0',
                         'last_login_1', 'date_joined_0', 'date_joined_1', 'initial-date_joined_0',
                         'initial-date_joined_1']

    @parameterized.expand(WANTED_INPUTS)
    def test_reviewer_can_change_only_username_and_password_of_reporter_user(self, input_name):
        target = get_admin_url(self.dept.reporter.user)
        self.quick_login(self.rev.user, target)
        inputs = self.find_all(By.TAG_NAME, 'input')
        input_names  = [inpt.get_attribute('name') for inpt in inputs]
        self.assertIn(input_name, input_names)

    @parameterized.expand(PROHIBITED_INPUTS)
    def test_reviewer_cannot_see_important_fields_of_reporter_user(self, input_name):
        target = get_admin_url(self.dept.reporter.user)
        self.quick_login(self.rev.user, target)
        inputs = self.find_all(By.TAG_NAME, 'input')
        input_names  = [inpt.get_attribute('name') for inpt in inputs]
        self.assertNotIn(input_name, input_names)
        
    def test_reviewer_cannot_see_select_boxes_in_reporter_user_change_page(self):
        target = get_admin_url(self.dept.reporter.user)
        self.quick_login(self.rev.user, target)
        self.find(By.NAME, '_save')  # the page is there
        self.assert_absent(By.TAG_NAME, 'select')
        
    def test_reviewer_cannot_access_unlisted_users_by_direct_link(self):
        target = get_admin_url(self.dept2.reporter.user)
        self.quick_login(self.rev.user, target)
        redirect_url = '{}{}'.format(self.live_server_url, reverse('admin:index'))
        self.assertEqual(self.browser.current_url, redirect_url)
