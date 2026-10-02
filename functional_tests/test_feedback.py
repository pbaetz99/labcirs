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

from django.core import mail
from django.test import override_settings
from django.urls import reverse
from model_bakery import baker
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select

from cirs.models import Comment, CriticalIncident, Reporter, Reviewer
from cirs.tests.helpers import create_role

from .base import TEST_INCIDENT_DATE, FunctionalTest

STATUS_BADGE = 'section[aria-labelledby="stand-titel"] .ui-badge'
REPLY_SAVED = 'Your reply has been saved'


class CriticalIncidentFeedbackTest(FunctionalTest):

    def test_user_can_see_feedback_code(self):
        # Reporters do not log in. The success page shows the code, once.
        dept = baker.make_recipe('cirs.department')
        self.browser.get(self.live_server_url + reverse('create_incident', kwargs={'dept': dept.label}))

        # the reporter enters incident data
        code = self.enter_test_incident()

        # User sees the success page with code for feedback, in a code element
        # there should be only one object in the database
        ci = CriticalIncident.objects.get()
        self.assertEqual(code, ci.comment_code)
        # the date of the <input type="date"> arrived as it was entered
        self.assertEqual(ci.date, TEST_INCIDENT_DATE)
        self.assertEqual(ci.department, dept)

        # the code is not kept: the page shows it only once
        self.browser.refresh()
        self.wait_for_text('.ui-alert', 'shows the code only once')
        self.assert_absent(By.CSS_SELECTOR, 'main code')


class CommentTest(FunctionalTest):

    def setUp(self):
        super(CommentTest, self).setUp()
        self.dept = baker.make_recipe('cirs.department', reporter=create_role(Reporter, self.reporter))
        self.incident = baker.make_recipe('cirs.public_ci', department=self.dept)

    def view_incident_detail(self):
        """The reporter enters the code under "My report". There is no login."""
        self.browser.get(self.live_server_url + self.dept.get_absolute_url())
        self.click_link_with_text('My report')
        self.enter_code(self.incident.comment_code)

    def create_comment(self, comment_text=None):
        # wait until text field present, enter text
        self.fill_field('Your reply', comment_text)
        # and clicks the "Send reply" button.
        self.click_button("Send reply")
        # the page confirms that the reply is saved (it stands at the end of the list)
        self.wait_for_text('.ui-alert', REPLY_SAVED)

    def check_if_comment_in_the_last_reply(self, comment_text, sender):
        """The replies are cards, oldest first. The sender is a role, never a name."""
        reply = self.find_all(By.CSS_SELECTOR, 'article.ui-card')[-1]
        self.assertIn(sender, reply.find_element(By.TAG_NAME, 'h3').text)
        self.assertIn(comment_text, reply.text)

    def test_reporter_can_add_comment(self):
        absolute_incident_url = self.incident.get_absolute_url()
        self.view_incident_detail()

        # reporter enters a comment into the comment text field
        comment_text = "I have some remarks on this incident!"
        self.create_comment(comment_text)

        # and lands afterwards on the same page
        self.assertCurrentUrlIs(absolute_incident_url)
        # the new comment is below
        self.check_if_comment_in_the_last_reply(comment_text, "Reporting person")

    def test_reviewer_can_comment_on_incident(self):
        # reporter entered his comment already
        comment_text = "I have some remarks on this incident!"
        Comment.objects.create(critical_incident=self.incident,
                               author=self.reporter, text=comment_text)
        # reviewer logs in and goes to the incident page
        # he needs a reviewer role
        # and has belong to the department
        create_role(Reviewer, self.reviewer)
        self.incident.department.reviewers.add(self.reviewer.reviewer)
        self.quick_backend_login(self.reviewer, self.incident.get_absolute_url())

        # and sees the coment made by the reporter
        self.check_if_comment_in_the_last_reply(comment_text, "Reporting person")

        # now he can comment himself
        comment_text = "I still have some questions:"
        self.create_comment(comment_text)

        # check if the new comment is the last one
        self.check_if_comment_in_the_last_reply(comment_text, "Quality management")

        # but now there is no email as reviewer made a comment himself
        self.assertEqual(len(mail.outbox), 0)  # @UndefinedVariable

        # TODO: check what happens if therer are multiple recipients. It should send email then

    @override_settings(EMAIL_HOST='smtp.example.com')
    def test_send_email_after_reporter_creates_a_comment(self):
        self.config = self.incident.department.labcirsconfig
        self.config.send_notification = True
        self.config.notification_recipients.add(self.reviewer)
        self.config.notification_sender_email = 'labcirs@labcirs.edu'
        self.config.save()
        self.view_incident_detail()
        comment_text = "I have some remarks on this incident!"
        self.create_comment(comment_text)
        # check if incident was sent by email
        self.assertEqual(len(mail.outbox), 1)  # @UndefinedVariable
        self.assertEqual(mail.outbox[0].subject, 'New LabCIRS comment')


class SecurityTest(FunctionalTest):

    def setUp(self):
        super(SecurityTest, self).setUp()
        self.dept = baker.make_recipe('cirs.department')
        self.incident = baker.make_recipe('cirs.public_ci', department=self.dept)
        self.absolute_incident_url = self.live_server_url + self.incident.get_absolute_url()
        self.search_url = reverse('incident_search',
                                  kwargs={'dept': self.incident.department.label})

    def test_anon_user_cannot_access_incident(self):
        # should go to the page where the code is entered, not to a login page
        self.browser.get(self.absolute_incident_url)
        self.assertCurrentUrlIs(self.search_url)

    def test_old_reporter_session_cannot_access_incident_without_comment_code(self):
        # The reporter account has no access of its own, and its session ends.
        self.quick_login(self.incident.department.reporter.user, self.incident.get_absolute_url())
        self.assertCurrentUrlIs(self.search_url)
        self.find(By.LINK_TEXT, 'Log in')

    def test_reporter_can_access_incident_with_correct_comment_code(self):
        self.browser.get(self.live_server_url + self.search_url)
        self.enter_code(self.incident.comment_code)
        self.assertCurrentUrlIs(self.incident.get_absolute_url())

    def test_code_ignores_spaces_and_case(self):
        # codes are copied from paper
        self.browser.get(self.live_server_url + self.search_url)
        code = self.incident.comment_code
        self.enter_code(' {} {} '.format(code[:8].upper(), code[8:]))
        self.assertCurrentUrlIs(self.incident.get_absolute_url())

    def test_reviewer_can_access_incident_without_code(self):
        # he has to have reviewer role and belong to the department
        create_role(Reviewer, self.reviewer)
        self.incident.department.reviewers.add(self.reviewer.reviewer)
        self.quick_backend_login(self.reviewer)
        self.browser.get(self.absolute_incident_url)
        self.assertCurrentUrlIs(self.incident.get_absolute_url())
        self.assertEqual(self.find(By.TAG_NAME, 'h1').text, 'Report')

    def test_wrong_code_redirects_to_search_page(self):
        # Reporter enters a code which does not exist
        self.browser.get(self.live_server_url + self.search_url)
        self.fill_field('Incident code', 'abcdefgh')
        self.click_button('Check code')

        # After submitting he lands again on the search page
        self.wait.until(EC.presence_of_element_located((By.ID, "id_incident_code")))

        # and sees informatinon that no incident was found
        self.wait_for_text('.ui-alert--danger', "No matching critical incident found!")
        self.assertCurrentUrlIs(self.search_url)

    def test_end_access_closes_the_report_for_the_next_person(self):
        # On a shared computer the reporter ends the access, a back-and-forth does not help
        self.browser.get(self.live_server_url + self.search_url)
        self.enter_code(self.incident.comment_code)
        self.click_button('End access')
        self.wait_for_text('.ui-alert', 'Access to this report has ended.')
        self.assertCurrentUrlIs(self.search_url)
        self.browser.get(self.absolute_incident_url)
        self.assertCurrentUrlIs(self.search_url)


class ReportAndFeedbackFlowTest(FunctionalTest):
    """Report anonymously, follow the report with the code, read status and reply of the QM,
    answer. The whole way without a login for the reporter."""

    def setUp(self):
        super(ReportAndFeedbackFlowTest, self).setUp()
        self.dept = baker.make_recipe('cirs.department')
        self.dept.reviewers.add(create_role(Reviewer, self.reviewer))

    def test_report_status_reply_and_answer(self):
        # An anonymous reporter reports an incident ...
        self.browser.get(self.live_server_url + self.dept.get_absolute_url())
        self.click_link_with_text('Report incident')
        code = self.enter_test_incident()
        # ... and notes the code
        incident = CriticalIncident.objects.get()
        self.assertEqual(code, incident.comment_code)

        # He goes on to "My report" and enters the code
        self.click_link_with_text('View my report')
        self.enter_code(code)
        # and sees the status of the report and that there are no replies yet
        self.assertEqual(self.find(By.CSS_SELECTOR, STATUS_BADGE).text, 'Received')
        self.assertIn('There are no replies yet.', self.find(By.TAG_NAME, 'main').text)

        # On a shared computer he ends the access before he leaves
        self.click_button('End access')
        self.wait_for_text('.ui-alert', 'Access to this report has ended.')

        # The QM logs in (reviewers do) and lands on the overview ...
        self.browser.get(self.live_server_url + reverse('login'))
        self.login_user(self.REVIEWER, self.REVIEWER_PASSWORD)
        self.assertCurrentUrlIs(reverse('qm_overview'))
        # ... opens the admin and changes the status of the report there ...
        self.click_link_with_text('Admin')
        self.wait.until(EC.presence_of_element_located((By.ID, 'site-name')))
        self.click_link_with_text('Critical incidents')
        self.click_link_with_text(incident.incident)
        self.open_review_panel()
        Select(self.find(By.ID, 'id_status')).select_by_value('in process')
        self.save_in_admin()
        # ... and replies on the page of the report
        self.click_link_with_text(incident.incident)
        self.click_link_case_insensitive('View on site')
        self.wait.until(EC.presence_of_element_located((By.ID, 'stand-titel')))
        self.assertEqual(self.find(By.CSS_SELECTOR, STATUS_BADGE).text, 'In progress')
        self.fill_field('Your reply', 'Synthetic question of the QM')
        self.click_button('Send reply')
        self.wait_for_text('.ui-alert', REPLY_SAVED)
        self.logout()

        # The reporter enters the code again ...
        self.browser.get(self.live_server_url + reverse('incident_search', kwargs={'dept': self.dept.label}))
        self.enter_code(code)
        # ... sees the new status and the reply of the QM ...
        self.assertEqual(self.find(By.CSS_SELECTOR, STATUS_BADGE).text, 'In progress')
        replies = self.find_all(By.CSS_SELECTOR, 'article.ui-card')
        self.assertEqual(len(replies), 1)
        self.assertIn('Quality management', replies[0].find_element(By.TAG_NAME, 'h3').text)
        self.assertIn('Synthetic question of the QM', replies[0].text)
        # ... and answers
        self.fill_field('Your reply', 'Synthetic answer of the reporter')
        self.click_button('Send reply')
        self.wait_for_text('.ui-alert', REPLY_SAVED)
        replies = self.find_all(By.CSS_SELECTOR, 'article.ui-card')
        self.assertEqual(len(replies), 2)
        self.assertIn('Reporting person', replies[1].find_element(By.TAG_NAME, 'h3').text)
        self.assertIn('Synthetic answer of the reporter', replies[1].text)

        # who wrote what: the QM and the technical account of the department
        authors = [comment.author for comment in Comment.objects.order_by('pk')]
        self.assertEqual(authors, [self.reviewer, self.dept.reporter.user])
        incident.refresh_from_db()
        self.assertEqual(incident.status, 'in process')
