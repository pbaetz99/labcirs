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

import os
from datetime import date

from django.conf import settings
from django.contrib.auth.models import Permission, User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from model_bakery import baker
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.firefox.options import Options
# TODO: might be necessary or better if geckodriver is not in the path
# from selenium.webdriver.firefox.service import Service as FirefoxService
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

from cirs.models import PublishableIncident

DEFAULT_WAIT = 8
# The table of a page in the design-system layout (every page has at most one)
TABLE = 'table.ui-table'
TEST_INCIDENT_DATE = date(2015, 7, 24)
# The logout button in the top bar (a form: logging out changes state)
LOG_OUT = '//button[normalize-space(.)="Log out"]'


def make_published_case(department, incident):
    """A published case (English text only) of the department, for the list of published cases."""
    case = baker.make_recipe('cirs.public_ci', department=department)
    publishable = PublishableIncident.objects.create(critical_incident=case, publish=True)
    publishable.create_translation('en', incident=incident, description='Synthetic description',
                                   measures_and_consequences='Synthetic measures')
    return publishable


class FunctionalTest(StaticLiveServerTestCase):

    # In the container the browser runs in another container and opens the live server of the
    # test run under the name of this one (compose.dev.yaml sets LIVE_SERVER_HOST=app).
    host = os.environ.get('LIVE_SERVER_HOST', 'localhost')

    @classmethod
    def setUpClass(cls):
        super(FunctionalTest, cls).setUpClass()
        # just to run tests against real test server
        staging_server = settings.STAGING_SERVER
        if staging_server != '':
            cls.live_server_url = 'http://' + staging_server
        # Initialise browser for testing
        # the noninternationalized version is checked
        remote_url = os.environ.get('SELENIUM_REMOTE_URL')
        if remote_url:
            # Firefox in the selenium container; the language is set in the browser profile
            options = Options()
            options.set_preference('intl.accept_languages', 'en')
            cls.browser = webdriver.Remote(command_executor=remote_url, options=options)
        elif settings.BROWSER == 'Chrome':
            chrome_driver_location = settings.CHROME_DRIVER
            options = webdriver.ChromeOptions()
            options.add_argument('--lang=en')
            cls.browser = webdriver.Chrome(
                service=ChromeService(chrome_driver_location), options=options)
        elif settings.BROWSER == 'Firefox':
            options = Options()
            options.set_preference('intl.accept_languages', 'en')
            cls.browser = webdriver.Firefox(options=options)

        # No implicit wait: Firefox can lose an element search that was started before a page
        # change (a login, a redirect) and then fails after the whole time. Every test waits for
        # what it needs explicitly: self.wait, find(), find_all() and the helpers below.
        cls.maxDiff = None
        cls.wait = WebDriverWait(cls.browser, DEFAULT_WAIT)

    @classmethod
    def tearDownClass(cls):
        cls.browser.quit()
        super(FunctionalTest, cls).tearDownClass()

    REPORTER = 'reporter'
    REPORTER_EMAIL = 'reporter@example.com'
    REPORTER_PASSWORD = 'reporter'

    REVIEWER = 'reviewer'
    REVIEWER_EMAIL = 'reviewer@example.com'
    REVIEWER_PASSWORD = 'reviewer'

    ADMIN = 'admin'
    ADMIN_EMAIL = 'admin@example.com'
    ADMIN_PASSWORD = 'admin'

    def setUp(self):
        # Reporters do not log in anymore. The account stays as the technical author of the
        # replies of an anonymous reporter (Department.reporter), and /login/ refuses it.
        self.reporter = User.objects.create_user(
            self.REPORTER, self.REPORTER_EMAIL, self.REPORTER_PASSWORD)
        # Generate reviewer user
        self.reviewer = User.objects.create_user(
            self.REVIEWER, self.REVIEWER_EMAIL, self.REVIEWER_PASSWORD)
        self.reviewer.is_staff = True
        self.reviewer.save()
        for codename in ('change_criticalincident', 'add_publishableincident',
                         'change_publishableincident',
                         'add_labcirsconfig', 'change_labcirsconfig'):
            permission = Permission.objects.get(codename=codename)
            self.reviewer.user_permissions.add(permission)
        self.admin = User.objects.create_superuser(
            self.ADMIN, self.ADMIN_EMAIL, self.ADMIN_PASSWORD)

    # --- finding things: by label, button text and ui-* class, not by Bootstrap class ---

    def click_link_with_text(self, link_text):
        self.wait.until(
            EC.element_to_be_clickable((By.LINK_TEXT, link_text)),
            message=('could not find ' + link_text)
        ).click()

    def click_link_case_insensitive(self, link_text):
        """Clicks a link whose style may show the text in capitals (the object tools of the
        admin do). The text is looked up in the page, not in the picture of it, in any case."""
        lower, upper = 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
        self.wait.until(
            EC.element_to_be_clickable((By.XPATH, '//a[translate(normalize-space(.), "{}", "{}")="{}"]'
                                       .format(lower, upper, link_text.upper()))),
            message=('could not find ' + link_text)
        ).click()

    def click_button(self, text):
        """Clicks the button with this text. The pages render buttons as <button>."""
        self.wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, '//button[normalize-space(.)="{}"]'.format(text))),
            message='could not find button ' + text
        ).click()

    def find_input_and_enter_text(self, identifier, text, method=By.ID):
        element = self.wait.until(
            EC.presence_of_element_located((method, identifier)),
            message=("could not find {} {}".format(identifier, method)),
        )
        self.browser.execute_script("arguments[0].scrollIntoView(true);", element)
        element.send_keys(text)

    def field_id(self, label_text):
        """The id of the field that the label with exactly this text belongs to.

        The first text node of the label is its text, the star of a required field is a span.
        """
        label = self.wait.until(
            EC.presence_of_element_located(
                (By.XPATH, '//label[normalize-space(text()[1])="{}"]'.format(label_text))),
            message='could not find label ' + label_text)
        return label.get_attribute('for')

    def fill_field(self, label_text, text):
        self.find_input_and_enter_text(self.field_id(label_text), text)

    def set_date(self, label_text, day):
        """Enters a date into <input type="date">.

        Typing would depend on the language of the browser (month first for en-US, day first for
        de). That comes from the system, not from the Accept-Language header, so the same keys
        give another date on another machine. The value of the field is always the ISO date
        (yyyy-mm-dd), and the browser submits that.
        """
        field = self.wait.until(EC.presence_of_element_located((By.ID, self.field_id(label_text))))
        self.browser.execute_script("arguments[0].value = arguments[1];", field, day.isoformat())

    def scroll_and_click(self, by, value):
        """Clicks an element in the middle of the window. The tables of the admin are wider than
        the window and an element at the edge of the view can be taken as covered."""
        element = self.find(by, value)
        self.browser.execute_script(
            "arguments[0].scrollIntoView({block: 'center', inline: 'center'});", element)
        element.click()

    def choose_radio(self, legend_text, choice_text):
        """Clicks one choice of a group of radio buttons (a fieldset with a legend)."""
        self.wait.until(
            EC.element_to_be_clickable((By.XPATH, (
                '//fieldset[legend[starts-with(normalize-space(.), "{}")]]'
                '//label[contains(normalize-space(.), "{}")]').format(legend_text, choice_text)))
        ).click()

    def wait_for_text(self, css_selector, text):
        self.wait.until(
            EC.text_to_be_present_in_element((By.CSS_SELECTOR, css_selector), text),
            message='"{}" not found in {}'.format(text, css_selector))

    def assertCurrentUrlIs(self, url):
        """Waits for the browser to be at this path (after a click or a redirect) and asserts it."""
        target_url = '{}{}'.format(self.live_server_url, url)
        try:
            self.wait.until(EC.url_to_be(target_url))
        except TimeoutException:
            self.assertEqual(self.browser.current_url, target_url)

    def find(self, by, value):
        """The element, as soon as it is there: after a click, a redirect or for what a script builds."""
        return self.wait.until(EC.presence_of_element_located((by, value)),
                               message='could not find {} {}'.format(value, by))

    def find_all(self, by, value):
        """The elements, as soon as at least one is there."""
        return self.wait.until(EC.presence_of_all_elements_located((by, value)),
                               message='could not find {} {}'.format(value, by))

    def assert_absent(self, by, value):
        """Asserts that nothing matches. Call it only after something shows that the page is the
        new one (an element of it was found), else it passes on the page that is going away."""
        self.assertEqual(self.browser.find_elements(by, value), [])

    # --- login and logout ---

    def quick_login(self, user, target_url=''):
        self.client.force_login(user)
        cookie = self.client.cookies['sessionid']
        self.browser.get(self.live_server_url + target_url)
        self.browser.add_cookie({'name': 'sessionid', 'value': cookie.value, 'secure': False, 'path': '/'})
        self.browser.get(self.live_server_url + target_url)

    def quick_backend_login(self, user=None, target_url='/admin/'):
        if user is None:
            user = self.admin
        self.quick_login(user, target_url)

    def login_user(self, username=REVIEWER, password=REVIEWER_PASSWORD):
        """
        Loggs user into the frontend of the webproject (login page or login of the admin)
        """
        self.find_input_and_enter_text('username', username, By.NAME)
        self.find_input_and_enter_text('password', password, By.NAME)
        self.find_input_and_enter_text('password', Keys.RETURN, By.NAME)

    def logout(self):
        """Logout in the top bar: a button in a form, because logging out changes state."""
        self.wait.until(EC.element_to_be_clickable((By.XPATH, LOG_OUT))).click()
        self.wait.until(EC.presence_of_element_located((By.LINK_TEXT, "Log in")))

    def logout_backend(self):
        # Wait for the logout button to be clickable.
        # Django 4.2 admin app logout changed from link to form with button
        logout_button = self.wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "//form[@id='logout-form']//button[@type='submit']")
            ),
            message="Logout button not clickable",
        )
        logout_button.click()
        self.wait.until(EC.presence_of_element_located((By.LINK_TEXT, "Log in")))

    # --- the pages of the reporter (no login) and of the admin ---

    def enter_test_incident(self, with_photo=False):
        """Fills in and sends the report form (usable on the create page).

        Returns the code (without the spaces between its groups), which the success page shows
        once.
        """
        self.set_date('Date of incident', TEST_INCIDENT_DATE)
        self.fill_field('Mistake / problem / critical incident', "A strang incident happened")
        self.fill_field('Cause of failure', "No one knows")
        self.fill_field('Immediate action / suggestion', "No action possible")
        Select(self.find(By.ID, self.field_id('Preventability'))
               ).select_by_visible_text("appraisal not possible")
        self.choose_radio('Publication', "I agree that this report will be made public")
        # upload photo
        if with_photo is True:
            self.fill_field('Photo', os.path.join(os.getcwd(), "cirs", "tests", "test.jpg"))
        self.click_button("Submit report")
        # the page shows the code in groups of four, the stored code has no spaces
        return self.wait.until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, 'main code'))).text.replace(' ', '')

    def enter_code(self, code):
        """On the page "My report": enters the code and opens the report."""
        self.fill_field('Incident code', code)
        self.click_button("Check code")
        self.wait.until(EC.presence_of_element_located((By.ID, 'stand-titel')))

    def open_review_panel(self):
        """The review block of an incident in the admin is collapsed (<details>)."""
        self.wait.until(EC.element_to_be_clickable(
            (By.XPATH, '//details[summary/h2[normalize-space(.)="Review"]]/summary'))).click()

    def save_in_admin(self):
        self.find(By.NAME, '_save').click()
        self.find(By.CSS_SELECTOR, 'ul.messagelist li.success')

    def check_admin_table_for_items(self, user, cls_name, present=None, absent=None):
        admin_url = reverse('admin:{}_{}_changelist'.format(cls_name._meta.app_label, cls_name._meta.model_name))
        self.quick_backend_login(user, admin_url)
        self.wait.until(EC.url_contains(admin_url))
        if present:
            self.find(By.LINK_TEXT, present)
        if absent:
            # the page is loaded: the link of the present item was found, or the url matches
            self.assert_absent(By.LINK_TEXT, absent)

    def get_rows_from_table(self, css_selector=TABLE):
        table = self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, css_selector)))
        return table.find_elements(By.TAG_NAME, 'tr')

    def get_column_from_table_as_list(self, css_selector=TABLE, column=0, start_row=1):
        """Returns text content of one column of given table as list.

        :param css_selector: the table, default is the table of the page
        :param column: desired column
        :param start_row: default is row 1 for tables with header, if there is no header, use 0
        """
        rows = self.get_rows_from_table(css_selector)
        return [row.find_elements(By.TAG_NAME, "td")[column].text for row in rows[start_row:]]
