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

from django.test import override_settings
from django.urls import reverse
from model_bakery import baker
from parameterized import parameterized
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select

from cirs.models import (CriticalIncident, Department, LabCIRSConfig,
                         PublishableIncident, Reporter, Reviewer)
from cirs.tests.helpers import create_role, create_user

from .base import LOG_OUT, FunctionalTest, make_published_case


def get_admin_url(instance, operation='change'):
    """Return the admin url for an object instance.

    Parameters
    ----------
    instance: object
        Object instance which should be accessed in the backend
    operation: str
        Desired operation to perform. Default is 'change' as the most common one.

    Returns
    -------
    str
        Url of the object in the backend

    """
    admin_url = reverse(
        'admin:{}_{}_{}'.format(
            instance._meta.app_label, instance._meta.model_name, operation
        ),
        args=(instance.pk,)
    )
    return admin_url


class AddRolesAndDepartmentBackendTest(FunctionalTest):
    
    def setUp(self):
        super(AddRolesAndDepartmentBackendTest, self).setUp()
        self.user = create_user('cirs_user')
        self.reporter = Reporter.objects.create(user=self.reporter)
        self.reviewer = Reviewer.objects.create(user=self.reviewer)
        self.en_dict = {
            'label': 'EN',
            'name': 'Experimenting Nerds',
            'reporter': self.reporter,
            'active': True,
        }
        
        # quick login for the  admin
        self.quick_backend_login()
         
    @parameterized.expand([
        ('reporter', 'Reporters'), 
        ('reviewer', 'Reviewers')
    ])
    def test_admin_can_set_user_role_as(self, role, class_name):
        self.click_link_with_text(class_name)
        self.click_link_case_insensitive('Add {}'.format(role))
        Select(
            self.find(By.ID, 'id_user')
        ).select_by_visible_text(self.user.username)
        self.find(By.NAME, '_save').click()
        self.wait.until(
            EC.element_to_be_clickable((By.LINK_TEXT, self.user.username)),
            message=('could not find %s' % self.user.username)
        )


    # Admin can chose only use who does not has any role assigned 
    # (including superuser)
    @parameterized.expand([('reporter',), ('reviewer',)])
    def test_only_user_without_role_in_select(self, role):
        self.browser.get(self.live_server_url + '/admin/cirs/{}/add/'.format(role))
        select = Select(self.find(By.ID, 'id_user'))
        options = [opt.text for opt in select.options]
        # there is also an empty choice
        self.assertListEqual(options, ['---------', self.user.username])


    def test_admin_can_set_department(self):
        # Admin goes to the backend 
        self.click_link_with_text("Departments")
        self.click_link_case_insensitive("Add department")
        # Now he enters the data for the new department
        self.find_input_and_enter_text('id_label', self.en_dict['label'])
        self.find_input_and_enter_text('id_name', self.en_dict['name'])
        Select(
            self.find(By.ID, 'id_reporter')
        ).select_by_visible_text(str(self.en_dict['reporter']))
        Select(
            self.find(By.ID, 'id_reviewers_from')
        ).select_by_visible_text('reviewer')
        self.find(By.ID, 'id_reviewers_add').click()
        self.find(By.NAME, '_save').click()
        # The name of the department is equal to the label set
        self.wait.until(
            EC.element_to_be_clickable((By.LINK_TEXT, self.en_dict['label'])),
            message=('could not find {}'.format(self.en_dict['label']))
        )
        
    @parameterized.expand([('reporter', 'id_reporter'), 
                           ('reviewer', 'id_reviewers_from')])
    def test_only_role_user_in_role_select_for(self, role, elem_id):
        # In the select dialogs only users assigned to roles are visible
        self.browser.get(self.live_server_url + '/admin/cirs/department/add/')
        select = Select(self.find(By.ID, elem_id))
        options = [opt.text for opt in select.options]
        expected = ['---------', role]
        if role == 'reviewer':
            expected = [role]
        self.assertListEqual(options, expected,
            'found {} instead {}'.format(', '.join(options), ', '.join(expected)))


    def test_assigned_reporter_not_visible_for_new_dept(self):
        # If a reporter is assigned to an department, he is not visible in
        # the dialog for a new department anymore 
        Department.objects.create(**self.en_dict)
        new_reporter = create_role(Reporter, 'new_reporter')
        self.browser.get(self.live_server_url + '/admin/cirs/department/add/')
        select = Select(self.find(By.ID, 'id_reporter'))
        options = [opt.text for opt in select.options]
        expected = ['---------', str(new_reporter)]
        self.assertListEqual(options, expected,
            'found {} instead {}'.format(', '.join(options), ', '.join(expected)))

    def test_admin_can_modify_departments_name(self):
        dept = Department.objects.create(**self.en_dict)
        dept.reviewers.add(self.reviewer)
        self.browser.get(self.live_server_url + get_admin_url(dept))
        self.find_input_and_enter_text('id_name', 'The best lab in the world')
        self.find(By.NAME, '_save').click()
        self.wait.until(
            EC.element_to_be_clickable((By.LINK_TEXT, self.en_dict['label'])),
            message=('could not find {}'.format(self.en_dict['label']))
        )


@override_settings(PARLER_DEFAULT_LANGUAGE_CODE=u'en')   
class SecurityFrontendTest(FunctionalTest):
    """
    The login page: a user without role sees an error message, the account of a reporter is
    refused (reporters do not log in), a reviewer without department sees an error message,
    a reviewer with department gets into the admin.
    """
    def setUp(self):
        super(SecurityFrontendTest, self).setUp()
        self.dept = baker.make_recipe('cirs.department')

    def login_to_department(self, user):
        """Opens the login page for the department of the test and logs in."""
        self.browser.get('{}{}?next={}'.format(
            self.live_server_url, reverse('login'), self.dept.get_absolute_url()))
        self.login_user(user.username, user.username)

    def get_alert(self, css_class):
        return self.wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, '#anmeldemeldung.' + css_class)))

    def assert_logged_out(self):
        self.find(By.LINK_TEXT, 'Log in')
        self.assert_absent(By.XPATH, LOG_OUT)

    def test_log_out_and_error_message_for_user_without_role(self):
        from cirs.views import MISSING_ROLE_MSG  # necessary only here so far
        user = create_user('cirs_user')
        self.login_to_department(user)

        error_alert = self.get_alert('ui-alert--danger')
        self.assertEqual(error_alert.text, str(MISSING_ROLE_MSG))
        self.assert_logged_out()

    def test_reporter_account_is_refused_and_pointed_to_the_report_form(self):
        from cirs.views import REPORTER_LOGIN_MSG
        reporter = create_role(Reporter, self.reporter)
        baker.make_recipe('cirs.department', reporter=reporter)
        self.browser.get(self.live_server_url + reverse('login'))
        self.login_user(self.REPORTER, self.REPORTER_PASSWORD)

        info = self.get_alert('ui-alert--info')
        self.assertEqual(info.text, str(REPORTER_LOGIN_MSG))
        self.assertCurrentUrlIs(reverse('login'))
        self.assert_logged_out()

    def test_log_out_and_error_message_for_reviewer_without_department(self):
        # necessary only here so far
        from cirs.views import MISSING_DEPARTMENT_MSG
        role = create_role(Reviewer, 'rev')
        self.login_to_department(role.user)

        error_alert = self.get_alert('ui-alert--danger')
        self.assertEqual(error_alert.text, str(MISSING_DEPARTMENT_MSG))
        self.assert_logged_out()

    def test_reviewer_with_department_lands_on_the_qm_overview(self):
        reviewer = create_role(Reviewer, 'rev')
        self.dept.reviewers.add(reviewer)
        self.browser.get(self.live_server_url + reverse('login'))
        self.login_user(reviewer.user.username, reviewer.user.username)
        self.assertCurrentUrlIs(reverse('qm_overview'))
        self.assertEqual(self.find(By.TAG_NAME, 'h1').text, 'Overview')

    def test_login_for_a_page_leads_back_to_that_page(self):
        reviewer = create_role(Reviewer, 'rev')
        self.dept.reviewers.add(reviewer)
        self.login_to_department(reviewer.user)
        self.assertCurrentUrlIs(self.dept.get_absolute_url())


class SecurityFrontendDirectAccessTest(FunctionalTest):
    def setUp(self):
        super(SecurityFrontendDirectAccessTest, self).setUp()
        self.dept = baker.make_recipe('cirs.department')
        self.create_url = reverse('create_incident', kwargs={'dept': self.dept.label})

    def assert_report_form_open_and_logged_out(self):
        self.assertCurrentUrlIs(self.create_url)
        self.field_id('Date of incident')  # waits until the form is there
        self.find(By.LINK_TEXT, 'Log in')
        self.assert_absent(By.XPATH, LOG_OUT)

    def test_anonymous_can_access_create_incident_view(self):
        # no login for the report form
        self.browser.get(self.live_server_url + self.create_url)
        self.assert_report_form_open_and_logged_out()

    def test_old_reporter_session_ends_and_the_create_incident_view_opens(self):
        user = create_role(Reporter, 'rep').user
        self.quick_login(user, self.create_url)
        self.assert_report_form_open_and_logged_out()

    def test_redirect_reviewer_from_create_incident_view_to_list(self):
        user = create_role(Reviewer, 'rev').user
        self.dept.reviewers.add(user.reviewer)
        self.quick_login(user, self.create_url)
        self.assertCurrentUrlIs(self.dept.get_absolute_url())

    def test_user_without_role_is_logged_out_and_the_create_incident_view_opens(self):
        user = create_user('cirs_user')
        self.quick_login(user, self.create_url)
        self.assert_report_form_open_and_logged_out()

# TODO: Reviewer should not see departments and reviewers(?). Probably also not reporters
# although he should may change reporter password for own department

class AccessDataWithMultipleDepts(FunctionalTest):
        
    def setUp(self):
        super(AccessDataWithMultipleDepts, self).setUp()
        self.rep = create_role(Reporter, 'rep')
        self.rep2 = create_role(Reporter, 'rep2')
        self.rev = create_role(Reviewer, 'rev')
        self.rev2 = create_role(Reviewer, 'rev2')
        self.dept = baker.make_recipe('cirs.department', reporter=self.rep)
        self.dept.reviewers.add(self.rev)
        self.dept2 = baker.make_recipe('cirs.department', reporter=self.rep2)
        self.dept2.reviewers.add(self.rev2)
        self.pi = make_published_case(self.dept, 'Published incident of the first department')
        self.ci = self.pi.critical_incident
        self.pi2 = make_published_case(self.dept2, 'Published incident of the second department')
        self.ci2 = self.pi2.critical_incident
   
    def get_test_cases():  # @NoSelf
        return[
            # published cases are open to everybody: no login ...
            ('anonymous', 'pi', 'pi2'),
            ('anonymous', 'pi2', 'pi'),
            # ... and the reviewers see the same
            ('rev', 'pi', 'pi2'),
            ('rev2', 'pi2', 'pi'),
        ]
    
    @parameterized.expand(get_test_cases)
    def test_role_sees_only_published_incidents_from_his_department(self, user, pi1, pi2):
       
        # a visitor sees only incidents associated with the department of the address
        role = getattr(self, user, None)  # None: nobody logged in
        own_pi = getattr(self, pi1)
        alien_pi = getattr(self, pi2)
        target_url = own_pi.critical_incident.department.get_absolute_url()
        if role:
            self.quick_login(role.user, target_url)
        else:
            self.browser.get(self.live_server_url + target_url)

        incidents = self.get_column_from_table_as_list(column=1)

        self.assertIn(own_pi.incident, incidents)
        self.assertNotIn(alien_pi.incident, incidents)


    def get_test_reviewers():  # @NoSelf
        return [
            ('rev',),
            ('rev2',)
        ]

    @parameterized.expand(get_test_reviewers)
    def test_reviewer_sees_only_cis_of_his_dept_in_backend(self, user):
        role = getattr(self, user)
        own_item = CriticalIncident.objects.filter(
            department__in=role.departments.all()).first().incident
        foreign_item = CriticalIncident.objects.exclude(
            department__in=role.departments.all()).first().incident
        self.check_admin_table_for_items(role.user, CriticalIncident, own_item, foreign_item)

    @parameterized.expand(get_test_reviewers)
    def test_reviewer_sees_only_pis_of_his_dept_in_backend(self, user):
        role = getattr(self, user)
        own_item = PublishableIncident.objects.filter(
            critical_incident__department__in=role.departments.all()).first().incident
        foreign_item = PublishableIncident.objects.exclude(
            critical_incident__department__in=role.departments.all()).first().incident
        self.check_admin_table_for_items(role.user, PublishableIncident, own_item, foreign_item)

    @parameterized.expand([
        (CriticalIncident, 'incident'),
        (PublishableIncident, 'incident')
    ])
    def test_admin_sees_no_incidents(self, model_cls, field):
        for incident in model_cls.objects.all():
            self.check_admin_table_for_items(self.admin, model_cls, absent=getattr(incident, field))

class ConfigurationForDepartments(FunctionalTest):

    def test_admin_can_go_to_department_config_after_dept_is_created(self):
        dept = baker.make_recipe('cirs.department')
        self.quick_backend_login(target_url=reverse('admin:cirs_labcirsconfig_changelist'))
        self.click_link_with_text('LabCIRS configuration for {}'.format(dept.label))

    def test_reviewer_sees_only_config_of_own_department(self):
        dept1, dept2 = baker.make_recipe('cirs.department', _quantity=2)
        reviewer = create_role(Reviewer, 'rev')
        dept1.reviewers.add(reviewer)
        self.check_admin_table_for_items(
            reviewer.user, LabCIRSConfig, str(dept1.labcirsconfig), str(dept2.labcirsconfig))
