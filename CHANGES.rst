LabCIRS changelog
=================

8.1.0a1 (2026-10-02)
--------------------

A working area for the quality management. This is a first step: the evaluations, their print view and CSV export and the system overview for administrators follow in later releases.

QM area

* After the login a reviewer lands on the overview instead of the admin. The top bar offers Overview, Reports and Evaluations. Only reviewers get in; a superuser is no reviewer and gets a 403. Every number comes from the reports of the reviewer's own departments, and the pages are not cached by the browser.
* Overview (``/qm/``): new reports of the last 7 days and of this month, open reports by status, the reports without processing (still ``new`` after ``QM_OVERDUE_DAYS`` days) and the reports that wait for the QM (the reporter wrote last), a column chart of the last 12 months with incoming and completed reports, and the places the reports come from. The charts are drawn on the server as SVG, with the numbers in a table below each chart and without JavaScript. Months before the status history started show "not recorded" instead of 0.
* Reports (``/qm/meldungen/``): all reports of the reviewer's departments with filters for status, place, category, risk, period, "waits for the QM", "without processing" and a text search. Sortable by number, date of the report and last activity, 25 per page. A filter that cannot be applied is named instead of dropped silently. A reviewer of several departments gets a column with the department.
* The numbers of the overview link to the matching filter of the report list.

Status history

* Every change of the status of a report is logged with its time (migrations ``0026`` and ``0027``). The history starts with this release; reports from before it have no entries, and completed reports count in the charts from their first entry on. The log is shown read-only on the report in the admin.

Settings

* ``QM_OVERDUE_DAYS`` (default 14) and ``REPORT_MIN_CELL`` (default 3, kept for the print view and CSV of the evaluations). Both must be whole numbers of at least 1. See ``docs/configuration.md``.

Fixes

* A login with a bare word as ``next`` no longer ends in an error page.
* The published cases stay in the top bar on the pages of the QM area.

8.0.0a1 (2026-10-01)
--------------------

First release of the fork. It is based on 7.0 and moves LabCIRS to supported software, changes how reporting works and closes known security gaps. Upgrading from 5.2.1 is tested end to end (``scripts/test-upgrade-from-v5.sh``), see ``docs/upgrade.md``.

Platform

* Django 5.2 LTS, Python 3.13, PostgreSQL 17 (psycopg 3). Runs in Docker: the app with gunicorn and whitenoise, PostgreSQL and an nginx proxy for TLS, ``/media``, rate limits and security headers. ``scripts/setup.sh`` creates ``.env``, ``scripts/smoke-test.sh`` checks the stack.
* Settings are read from ``LABCIRS_<NAME>`` environment variables first, then from ``local_config.json``, then the defaults. New keys: ``DEBUG``, ``LANGUAGE_CODE``, ``CSRF_TRUSTED_ORIGINS``, ``BEHIND_PROXY``, ``ASK_PUBLICATION_CONSENT``, ``LOGO_URL``, ``THEME_CSS_URL``, ``IMPRINT_URL``, ``PRIVACY_URL``, ``SOURCE_URL``, ``SITE_URL``, ``SITE_NAME``, ``EMAIL_USE_TLS``, ``EMAIL_USE_SSL``, ``EMAIL_TIMEOUT``. Yes/no values are checked strictly, so ``DEBUG=False`` cannot turn debug pages on by accident.
* Documentation in ``docs/``: quick start, configuration, upgrade, branding.

Reporting

* Reporters need no login. They get a 16-character code (older 8-character codes keep working), see the status of their report in plain language and answer the QM. The code is shown in groups of four, on the page and in mails. A code of the wrong length gets a hint with the number of characters entered instead of "not found". The code is only sent by POST, and the pages that show a code or a report are not cached by the browser.
* Only QM and admins log in. The reporter account of a department stays as the author of anonymous comments and can no longer log in.
* Optional e-mail address for reporters, kept apart from the report and deleted when it is closed. Offered only when ``DEFAULT_FROM_EMAIL`` is set. The field carries a short warning that an address makes the report less anonymous. The mails never contain the report.
* Optional organisational units ("where did it happen") with one level of sub-units, visible to the QM only.
* ``ASK_PUBLICATION_CONSENT`` switches the question about publication on or off.
* With exactly one active department, the start page goes straight to the list of published cases.

Interface

* New look from a plain design-system stylesheet and one small script (``core.css``, ``formular.js``). Bootstrap, jQuery, jQuery UI and DataTables are removed. Every page works without JavaScript. The layout aims at WCAG 2.2 AA. German translations in the formal form.
* Strict Content Security Policy everywhere, the admin included: no inline scripts, styles or event handlers, no external resources.
* Error pages (400, 403, 404, 500, CSRF) in the same layout, without error text, traceback or address.

Security and privacy

* Closed: comments could be posted on any report by any logged-in user. Access is now checked for GET and POST.
* Photos are re-encoded from their pixels (no EXIF, GPS or other metadata), stored under random names and limited to JPEG, PNG, GIF and WebP up to 10 MB and 50 megapixels. ``manage.py strip_photo_metadata`` cleans photos that are already stored.
* nginx rate limits for code checks, new reports and comments (global, no IP address involved) and for the login. No IP addresses or user agents in the logs of nginx, gunicorn and the app. Requests with NUL bytes get a 400.
* Failed e-mail never costs a report: the error is logged and the reporter still sees the code. LabCIRS sends no error mails, so there is no ``ADMINS`` setting.

Fixes

* Reports with status "new" can be saved again in the admin.
* The preventability is shown translated. Missing translations are added. The QM can see comments in the admin.
* Translations of published cases and of the login info are no longer cached per worker. After an edit, some requests still showed the old text.
* In the admin, the three fields of the "publishable incident" inline fit the page width. The incident page no longer scrolls sideways at 1440 px.
* In the admin, the comment block, the review block and the application name are translated, the photo shows once on the incident page, and long German names in the index are hyphenated on narrow screens.

Removed

* Self-registration of new departments (django-registration-redux). Migrations ``0020`` and ``0025`` drop its tables, content types and permissions.
* ``setup.py``, ``makesecretkey``, the Apache templates, the terms-of-service files, model-mommy (replaced by model-bakery) and django-migration-testcase.

Database

* New migrations ``0020`` to ``0025``: drop the registration tables, unique constraints for translations (django-parler 2.4), ``OrgUnit`` and ``CriticalIncident.org_unit``, new permissions for existing reviewers, ``ReporterContact``, drop the registration content types and permissions. Migrations ``0001`` to ``0019`` are unchanged.


7.0 (2025-04-14)
----------------

* Updated Django to 4.2. Tested with Python 3.12.
* Squashed old migrations
* Removed migration tool for from single to multi tenant version. It was still written with Python 2.7 and not used anymore.
* Changed the license to AGPLv3


6.1 (2024-03-24)
----------------

* Updated Django to 3.2 and the dependencies to most recent versions. This caused few code changes.
* Updated bundled Bootstrap 4.6 , JQuery, JQueryUI and DataTables to most recent versions.


6.0 (2021-06-02)
----------------

* Migrated LabCIRS to Python 3 without functional changes. Dependencies will be updated in future versions.


5.2.1 (2020-06-18)
----------------

* Updated required packages to most recent version (Django to recent version from the 1.11 line)
* Bug fix in settings/base.py


5.2 (2019-06-14)
----------------

* Added registration of new departments along with new reviewer and reporter accounts.
  Uses django-registration-redux_.
* Added setup.py which helps dealing with the local config file.
.. _django-registration-redux: https://github.com/macropin/django-registration


5.1 (2019-04-04)
----------------

* Reviewer can now change the name and password of users who are reporters for his departments.

5.0 (2019-03-09)
----------------

* Added support of multiple departments in one instalation. Script for joinig single department instances included.
* Added support for setting translation language, or deactivate translations (by setting supported
  languages to one). This functionality uses django-parler_.
* Dropped support for Django < 1.11
* ``manage.py`` shows default behaviour again
.. _django-parler: https://github.com/django-parler/django-parler

4.1.1 (2018-08-07)
------------------

* Updated used python modules (see requirements.txt).
* LabCIRS works now with Django 1.11 (only small modifications were necessary) - 1.9 still supported even if not recommended
* Updated Bootstrap to 4.1 (the navbar was slightly modified)
* Updated bundled JavaScript and CSS libraries

4.1.0 (2018-06-28)
------------------

* Added feedback functionality for reporter