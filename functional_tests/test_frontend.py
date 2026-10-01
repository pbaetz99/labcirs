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
from django.core import mail
from django.test import override_settings
from django.urls import reverse
from model_bakery import baker
from parameterized import parameterized
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC

from cirs.models import CriticalIncident, Department, Reviewer
from cirs.tests.helpers import create_role

from .base import LOG_OUT, FunctionalTest, make_published_case


class FrontendWithDepartments(FunctionalTest):
    """Test frontend after departments were aded to the models."""

    def test_anon_user_sees_list_of_departments_on_home_page(self):

        baker.make_recipe('cirs.department', _quantity=5)

        # Anonymous user visits the home page of LabCIRS server
        self.browser.get(self.live_server_url)

        # and sees a list of all departments: the name is the link, the label stands next to it
        names = self.get_column_from_table_as_list(column=0)
        labels = self.get_column_from_table_as_list(column=1)
        for dept in Department.objects.all():
            self.assertIn(dept.name, names)
            self.assertIn(dept.label, labels)

        # At the top of the page he sees the name of the organization
        logo = self.find(By.CLASS_NAME, 'ui-logo')
        self.assertIn(settings.ORGANIZATION, logo.text)

        # he clicks on one of the links and sees the published cases of the department,
        # without a login
        dept = Department.objects.last()
        self.click_link_with_text(dept.name)
        self.assertCurrentUrlIs(dept.get_absolute_url())
        self.assertIn(dept.name, self.find(By.CLASS_NAME, 'ui-lede').text)
        # and from there the routes of the department: report and "My report"
        self.click_link_with_text('Report incident')
        self.assertCurrentUrlIs(reverse('create_incident', kwargs={'dept': dept.label}))

    def test_anonymous_sees_only_published_cases_of_the_department(self):
        # Reporters do not log in anymore: the published cases are open to everybody
        dept1, dept2 = baker.make_recipe('cirs.department', _quantity=2)
        for dept in (dept1, dept2):
            for number in range(5):
                make_published_case(dept, 'Case {} of {}'.format(number, dept.label))

        # user goes to the incidents list for a department
        self.browser.get(self.live_server_url + dept1.get_absolute_url())
        incidents = self.get_column_from_table_as_list(column=1)

        # and sees table with published incident from this department, but not from another
        self.assertCurrentUrlIs(dept1.get_absolute_url())
        self.assertEqual(len(incidents), 5)
        for number in range(5):
            self.assertIn('Case {} of {}'.format(number, dept1.label), incidents)
        for incident in incidents:
            self.assertNotIn(dept2.label, incident)
        # no login is asked for: he is not logged in, and there is no one to log out
        self.find(By.LINK_TEXT, 'Log in')
        self.assert_absent(By.XPATH, LOG_OUT)

    def test_reviewer_sees_only_incidents_from_one_department_at_once(self):
        # There is reviewer for two departments
        rev = create_role(Reviewer, 'rev')
        dept1, dept2 = baker.make_recipe('cirs.department', _quantity=2)
        for dept in (dept1, dept2):
            for number in range(5):
                make_published_case(dept, 'Case {} of {}'.format(number, dept.label))
            dept.reviewers.add(rev)
        # he logins and goes to the home page:
        self.quick_login(rev.user, reverse('labcirs_home'))
        # he clicks on first department
        self.click_link_with_text(dept1.name)

        # and sees table with incidents for dept1
        self.assertCurrentUrlIs(dept1.get_absolute_url())
        incidents = self.get_column_from_table_as_list(column=1)
        self.assertEqual(len(incidents), 5)
        for incident in incidents:
            self.assertIn(dept1.label, incident)
        # the column with the number of comments is for reviewers only
        # (textContent: the style shows the headers in capitals)
        headers = [th.get_attribute('textContent').strip()
                   for th in self.get_rows_from_table()[0].find_elements(By.TAG_NAME, 'th')]
        self.assertIn('No. of comments', headers)

    def test_reviewer_is_redirected_to_his_dept_from_detail_view_of_ci_from_another_dept(self):
        rev = create_role(Reviewer, 'rev')
        dept1, dept2 = baker.make_recipe('cirs.department', _quantity=2)
        dept1.reviewers.add(rev)
        for dept in (dept1, dept2):
            baker.make_recipe('cirs.public_ci', department=dept)
        # he tries to access_detail view of one incident from dept2
        incident = CriticalIncident.objects.filter(department=dept2).first()
        self.quick_login(rev.user, incident.get_absolute_url())
        # but is redirected to his department page

        self.assertCurrentUrlIs(dept1.get_absolute_url())


class FrontendWithDepartmentsConfig(FunctionalTest):

    def test_each_department_has_own_login_info(self):
        # The login page, opened for a department, shows its own login info.
        # Only reviewers log in; the info says where they find the login data.
        dept1, dept2 = baker.make_recipe('cirs.department', _quantity=2)
        dept1.labcirsconfig.login_info = 'Department 1'
        dept1.labcirsconfig.save()
        dept2.labcirsconfig.login_info = 'Department 2'
        dept2.labcirsconfig.save()
        for dept in (dept1, dept2):
            self.browser.get('{}{}?next={}'.format(
                self.live_server_url, reverse('login'), dept.get_absolute_url()))
            info = self.wait.until(EC.presence_of_element_located((
                By.XPATH, '//div[contains(@class, "ui-alert__body")][contains(., "Department ")]')))
            self.assertEqual(info.text, dept.labcirsconfig.login_info)

    # TODO: This is not frontend test?
    @override_settings(EMAIL_HOST='smtp.example.com')
    def test_each_department_has_own_email_config(self):
        rev1, rev2 = baker.make_recipe('cirs.reviewer', _quantity=2)
        dept1, dept2 = baker.make_recipe('cirs.department', _quantity=2)
        dept1.labcirsconfig.notification_recipients.add(rev1.user)
        dept2.labcirsconfig.notification_recipients.add(rev2.user)

        for dept in (dept1, dept2):
            dept.labcirsconfig.send_notification = True
            dept.labcirsconfig.notification_sender_email = 'labcirs@labcirs.edu'
            dept.labcirsconfig.save()
            self.browser.get(self.live_server_url
                             + reverse('create_incident', kwargs={'dept': dept.label}))
            # reporter enters incident data
            self.enter_test_incident()

            # check if incident was sent by email to correct reviewer

            self.assertEqual(mail.outbox[-1].to[0],
                             dept.labcirsconfig.notification_recipients.first().email)


class RedirectKnownUsers(FunctionalTest):
    """
    If logged in user access pages directly they have to be sometimes redirected
    """
    def setUp(self):
        super(RedirectKnownUsers, self).setUp()
        self.dept = baker.make_recipe('cirs.department')
        self.rev = baker.make_recipe('cirs.reviewer')
        self.dept.reviewers.add(self.rev)

    def test_reviewer_with_one_department_is_redirected_after_home_access(self):
        ## A reviewer with only one department is redirected to the list of department incidents
        self.quick_login(self.rev.user)
        self.assertCurrentUrlIs(self.dept.get_absolute_url())
        # and is still logged in
        self.find(By.XPATH, LOG_OUT)

    def test_old_reporter_session_is_redirected_and_ended_after_home_access(self):
        # Reporters do not log in anymore. An old session of a reporter account ends, the
        # visitor is treated as anonymous (one department: redirected to its list).
        self.quick_login(self.dept.reporter.user)
        self.assertCurrentUrlIs(self.dept.get_absolute_url())
        self.find(By.LINK_TEXT, 'Log in')
        self.assert_absent(By.XPATH, LOG_OUT)

    def test_redirect_reviewer_with_multiple_departments_after_home_access(self):
        dept2, dept3 = baker.make_recipe('cirs.department', _quantity=2)
        dept2.reviewers.add(self.rev)
        self.quick_login(self.rev.user)
        labels = self.get_column_from_table_as_list(column=1)

        for dept in (self.dept, dept2):
            self.assertIn(dept.label, labels)
        self.assertNotIn(dept3.label, labels)

    def test_redirect_admin_from_detail_view(self):
        ci = baker.make_recipe('cirs.public_ci')
        self.quick_login(self.admin, ci.get_absolute_url())
        self.assertCurrentUrlIs(reverse('admin:index'))

    def test_report_is_not_found_under_the_label_of_another_department(self):
        # The reporter entered the code of a report, the address names another department.
        ci = baker.make_recipe('cirs.public_ci', department=self.dept)
        other = baker.make_recipe('cirs.department')
        self.browser.get(self.live_server_url
                         + reverse('incident_search', kwargs={'dept': self.dept.label}))
        self.enter_code(ci.comment_code)
        self.browser.get(self.live_server_url + reverse(
            'incident_detail', kwargs={'dept': other.label, 'pk': ci.pk}))
        self.assertEqual(self.find(By.TAG_NAME, 'h1').text, 'Page not found')

    def test_redirect_superuser_from_department_list(self):
        self.quick_login(self.admin, reverse('departments_list'))
        self.assertCurrentUrlIs(reverse('admin:index'))

    @parameterized.expand([
        ('create_incident',),
        ('incident_search',),
        ('incidents_for_department',)
    ])
    def test_superuser_is_always_redirected(self, view):
        self.quick_login(self.admin, reverse(view, kwargs={'dept': self.dept.label}))
        self.assertCurrentUrlIs(reverse('admin:index'))


class CorrectDepartmentInURL(FunctionalTest):

    def test_old_reporter_session_sees_the_department_of_the_url(self):
        # The department comes from the URL, not from the account: no redirect to "his"
        # department and no message, the session of the reporter account just ends.
        dept1, dept2 = baker.make_recipe('cirs.department', _quantity=2)
        self.quick_login(dept1.reporter.user, dept2.get_absolute_url())
        self.assertCurrentUrlIs(dept2.get_absolute_url())
        lede = self.find(By.CLASS_NAME, 'ui-lede')
        self.assertIn(dept2.name, lede.text)
        self.assertNotIn(dept1.name, lede.text)
        self.find(By.LINK_TEXT, 'Log in')
        self.assert_absent(By.CLASS_NAME, 'ui-alert--warning')

    def test_nav_link_leads_to_list_with_department(self):
        # BUGFIX: After adding the departments, the links points to nowhere as there is no dept
        dept = baker.make_recipe('cirs.department')
        self.browser.get(self.live_server_url
                         + reverse('create_incident', kwargs={'dept': dept.label}))
        self.click_link_with_text("Published cases")
        self.assertCurrentUrlIs(dept.get_absolute_url())
