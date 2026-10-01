LabCIRS
=======

LabCIRS is a lightweight, anonymous Critical Incident Reporting System (CIRS). Staff report incidents without logging in and get a code. With the code they follow the status of their report and answer questions from the quality management (QM). The QM reviews reports in the Django admin and publishes edited cases.

This is a fork of `LabCIRS <https://github.com/major-s/labcirs>`_ by Sebastian Major, who developed it for research laboratories at the Charité Berlin. Background: `A Laboratory Critical Incident and Error Reporting System for Experimental Biomedicine <https://doi.org/10.1371/journal.pbio.2000705>`_ (PLOS Biology). The fork runs on Django 5.2 LTS, Python 3.13 and PostgreSQL 17 in Docker, and replaces Bootstrap, jQuery and DataTables with a plain stylesheet. It changes how reporting works: no login for reporters, a code with status and replies, optional organisational units and reporter e-mail, strict privacy defaults. See ``CHANGES.rst``.

Intranet only
-------------

Run LabCIRS inside your network, not on the internet. Published cases can be read without a login, and the login of QM and admins has no second factor.

Quick start
-----------

You need Docker with the Compose plugin (2.24 or newer), a host name and a TLS certificate::

    git clone https://github.com/pbaetz99/labcirs.git
    cd labcirs
    bash scripts/setup.sh        # creates .env with random secrets
    nano .env                    # server name, allowed hosts, languages, mail
    mkdir tls && cp /path/to/fullchain.pem /path/to/privkey.pem tls/
    docker compose up -d --build
    docker compose logs app      # repeat until it shows "Listening at": the app migrates first
    docker compose run --rm app python manage.py createsuperuser

Then log in at ``https://<your host>/login/``, add a department with a reporter account and a reviewer in the admin, and give your staff the address ``/incidents/<department label>/create/``.

Documentation
-------------

- `docs/quickstart.md <docs/quickstart.md>`_: installation step by step, first accounts, backups, tests
- `docs/configuration.md <docs/configuration.md>`_: every setting, mail, security defaults, running on the host network
- `docs/upgrade.md <docs/upgrade.md>`_: moving an installation from v5, v6 or v7
- `docs/branding.md <docs/branding.md>`_: name, logo, colours, footer links

Included software
-----------------

Two files in this repository were not written for LabCIRS: ``static/css/core.css`` and ``static/js/formular.js``. The first is a design-system stylesheet, the second a small script for forms. They carry no licence notice of their own and are covered by the AGPL of this repository. No other JavaScript, font or image from a third party is bundled. The pages use system fonts and load nothing from other hosts.

Python packages (Django, Pillow, django-parler, django-multiselectfield, psycopg, gunicorn, whitenoise) are installed from PyPI when the image is built, see ``requirements.txt``. The images ``python``, ``postgres`` and ``nginx`` come from Docker Hub.

Acknowledgements
----------------

The development of the multi-department version was sponsored by the `Stiftung Charité <http://www.stiftung-charite.de>`_. Thanks to Claudia Kurreck, Nikolas Offenhauser and Ingo Przesdzing for ideas and testing.

License
-------

Copyright (C) 2016-2025 Sebastian Major <sebastian.major@charite.de>

Copyright (C) 2026 Philipp Bätz (this fork)

LabCIRS is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as
published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version.

LabCIRS is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with LabCIRS.
If not, see <https://www.gnu.org/licenses/>.
