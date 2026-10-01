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

import io
from datetime import date, timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, no_translations
from django.db import transaction
from PIL import Image, ImageDraw

from cirs.models import (Comment, CriticalIncident, Department, LabCIRSConfig,
                         OrgUnit, PublishableIncident, Reporter, Reviewer)
from cirs.photos import random_photo_name

DEPARTMENT_LABEL = 'demo'
DEPARTMENT_NAME = 'Demo-Abteilung'
QM_USERNAME = 'qm-demo'
ADMIN_USERNAME = 'admin-demo'
REPORTER_USERNAME = 'reporter-demo'
# .test never resolves: the mails of the demo accounts cannot reach anybody.
EMAIL_DOMAIN = 'example.test'
LEGACY_CODE = 'ab#d$f-9'  # an old 8 character code with special characters

# Two levels: unit A and B with two sub-units each.
ORG_UNITS = (
    ('Einheit A', ('Untereinheit A1', 'Untereinheit A2')),
    ('Einheit B', ('Untereinheit B1', 'Untereinheit B2')),
)

LOGIN_INFO = {
    'de': 'Demo: Das Konto erhalten Sie vom Qualitätsmanagement.',
    'en': 'Demo: You get the account from the quality management.',
}

# All texts are invented. Reports and cases are German with an English translation, because
# publishing needs every language of the installation.
# key: the position in the list (used by COMMENTS), status, org unit, consent, photo,
# published as a case (title, description, measures: German and English)
REPORTS = (
    {'incident': 'Demo 1: Das Etikett eines Probenbehälters war nicht lesbar.',
     'reason': 'Demo: Der Drucker war fast leer.',
     'immediate_action': 'Demo: Behälter neu beschriften, Drucker prüfen.',
     'preventability': 'avoidable', 'status': 'new', 'public': True, 'org_unit': 'Untereinheit A1',
     'photo': True, 'days_ago': 3},
    {'incident': 'Demo 2: Ein Gerät wurde nach der Reinigung nicht freigegeben.',
     'reason': 'Demo: Die Checkliste lag nicht am Arbeitsplatz.',
     'immediate_action': 'Demo: Checkliste am Gerät aushängen.',
     'preventability': 'avoidable', 'status': 'in process', 'public': True,
     'org_unit': 'Untereinheit B1', 'days_ago': 11,
     'review': {'action': 'Demo: Checkliste laminieren und am Gerät befestigen.',
                'responsibilty': 'Demo-Station B', 'risk': 'middle', 'frequency': 'occasional',
                'hazard': 'low',
                'category': ['organisation/communication', 'technique/methods']}},
    {'incident': 'Demo 3: Eine Lieferung wurde im falschen Raum abgestellt.',
     'reason': 'Demo: Die Beschilderung fehlte.',
     'immediate_action': 'Demo: Raumschilder anbringen.',
     'preventability': 'indistinct', 'status': 'under supervision', 'public': True,
     'org_unit': 'Untereinheit A2', 'days_ago': 25, 'code': LEGACY_CODE,
     'review': {'action': 'Demo: Neue Schilder hängen seit dem Berichtsmonat.',
                'responsibilty': 'Demo-Haustechnik', 'risk': 'low', 'frequency': 'seldom',
                'hazard': 'very low', 'category': ['infrastructure']}},
    {'incident': 'Demo 4: Ein Formular wurde doppelt ausgefüllt.',
     'reason': 'Demo: Zwei Vorlagen waren im Umlauf.',
     'immediate_action': 'Demo: Alte Vorlage entfernen.',
     'preventability': 'avoidable', 'status': 'completed', 'public': True,
     'org_unit': 'Untereinheit B2', 'photo': True, 'days_ago': 70,
     'review': {'action': 'Demo: Es gibt nur noch eine Vorlage im Intranet.',
                'responsibilty': 'Demo-Verwaltung', 'risk': 'low', 'frequency': 'seldom',
                'hazard': 'very low', 'category': ['organisation/communication']},
     'case': {'de': ('Doppelt ausgefülltes Formular',
                     'Zwei Vorlagen desselben Formulars waren gleichzeitig im Umlauf.',
                     'Es gibt nur noch eine Vorlage, sie liegt im Intranet.'),
              'en': ('Form filled out twice',
                     'Two templates of the same form were in use at the same time.',
                     'Only one template is left, it is kept in the intranet.')}},
    {'incident': 'Demo 5: Ein Wagen blockierte kurz einen Flur.',
     'reason': 'Demo: Es gab keinen Abstellplatz.',
     'immediate_action': 'Demo: Abstellplatz markieren.',
     'preventability': 'not avoidable', 'status': 'under supervision', 'public': True,
     'days_ago': 120,
     'review': {'action': 'Demo: Ein Platz am Ende des Flurs ist markiert.',
                'responsibilty': 'Demo-Pflegeleitung', 'risk': 'low', 'frequency': 'occasional',
                'hazard': 'low', 'category': ['infrastructure', 'other']},
     'case': {'de': ('Wagen im Flur',
                     'Ein Wagen stand kurz im Flur und versperrte den Weg.',
                     'Ein Abstellplatz am Ende des Flurs ist markiert.'),
              'en': ('Cart in the corridor',
                     'A cart briefly stood in the corridor and blocked the way.',
                     'A parking spot at the end of the corridor is marked.')}},
    {'incident': 'Demo 6: Eine Anweisung war missverständlich formuliert.',
     'reason': 'Demo: Der Text hatte zwei Lesarten.',
     'immediate_action': 'Demo: Text klarer fassen.',
     'preventability': 'indistinct', 'status': 'new', 'public': False,
     'org_unit': 'Untereinheit A1', 'days_ago': 1},
)

# (index in REPORTS, who writes, days ago, text). 'qm' is the reviewer, 'reporter' the technical
# reporter account of the department, which writes the replies of anonymous reporters.
COMMENTS = (
    (1, 'qm', 8, 'Demo: Wo hing die Checkliste vorher?'),
    (1, 'reporter', 7, 'Demo: Sie hing im Nebenraum.'),
    (3, 'qm', 60, 'Demo: Danke, die Vorlage ist entfernt.'),
    (4, 'qm', 100, 'Demo: Wir markieren einen Abstellplatz.'),
    (4, 'reporter', 99, 'Demo: Das wäre gut.'),
)


def parler_codes():
    return [lang['code'] for lang in settings.PARLER_LANGUAGES[None]]


def text_in(texts, code):
    """The text for a language, English for a language that has no text of its own."""
    return texts.get(code, texts['en'])


def demo_photo():
    """A plain synthetic picture, as JPEG bytes."""
    image = Image.new('RGB', (640, 480), (200, 214, 232))
    draw = ImageDraw.Draw(image)
    draw.rectangle((60, 60, 580, 420), outline=(30, 74, 140), width=6)
    draw.ellipse((220, 140, 420, 340), fill=(30, 74, 140))
    draw.text((250, 380), 'DEMO', fill=(30, 74, 140))
    out = io.BytesIO()
    image.save(out, 'JPEG', quality=85)
    return out.getvalue()


class Command(BaseCommand):
    help = ('Creates synthetic demo data for trying out and acceptance tests: one department, '
            'the reviewer qm-demo, the superuser admin-demo, six reports in every status, '
            'two published cases, replies and organisational units A and B with sub-units. '
            'Calling it again changes nothing (objects are found by name, label or text). '
            'The passwords come from the call. Never use it on a system with real data.')

    def add_arguments(self, parser):
        parser.add_argument('--qm-password', required=True,
                            help='Password of the reviewer qm-demo (set again on every call).')
        parser.add_argument('--admin-password', required=True,
                            help='Password of the superuser admin-demo (set again on every call).')
        parser.add_argument('--extra-published', type=int, default=0, metavar='N',
                            help='Also create N more published cases, to try out paging '
                                 '(25 cases a page). Default 0.')

    @no_translations
    @transaction.atomic
    def handle(self, *args, **options):
        department, reviewer = self.create_accounts(options['qm_password'],
                                                    options['admin_password'])
        units = self.create_org_units()
        self.create_login_info(department)
        reports = [self.create_report(department, data, units) for data in REPORTS]
        for index, who, days_ago, text in COMMENTS:
            author = reviewer.user if who == 'qm' else department.reporter.user
            Comment.objects.get_or_create(
                critical_incident=reports[index], author=author, text=text,
                defaults={'created': date.today() - timedelta(days=days_ago)})
        for number in range(1, options['extra_published'] + 1):
            reports.append(self.create_extra_case(department, number))
        self.print_summary(department, reports)

    def create_accounts(self, qm_password, admin_password):
        reporter_user, created = User.objects.get_or_create(
            username=REPORTER_USERNAME,
            defaults={'first_name': 'Reporter', 'last_name': 'Demo',
                      'email': '%s@%s' % (REPORTER_USERNAME, EMAIL_DOMAIN)})
        if created:
            reporter_user.set_unusable_password()  # only the author of anonymous replies
            reporter_user.save()
        reporter = Reporter.objects.get_or_create(user=reporter_user)[0]

        qm_user, created = User.objects.get_or_create(
            username=QM_USERNAME,
            defaults={'first_name': 'QM', 'last_name': 'Demo',
                      'email': '%s@%s' % (QM_USERNAME, EMAIL_DOMAIN)})
        qm_user.set_password(qm_password)
        qm_user.save()
        reviewer = Reviewer.objects.get_or_create(user=qm_user)[0]  # save() sets the permissions

        admin_user, created = User.objects.get_or_create(
            username=ADMIN_USERNAME,
            defaults={'first_name': 'Admin', 'last_name': 'Demo',
                      'email': '%s@%s' % (ADMIN_USERNAME, EMAIL_DOMAIN)})
        admin_user.is_staff = admin_user.is_superuser = True
        admin_user.set_password(admin_password)
        admin_user.save()

        department, created = Department.objects.get_or_create(
            label=DEPARTMENT_LABEL,
            defaults={'name': DEPARTMENT_NAME, 'reporter': reporter, 'active': True})
        department.reviewers.add(reviewer)
        return department, reviewer

    def create_org_units(self):
        units = {}
        position = 0  # one running number: the lists show every unit directly below its parent
        for name, children in ORG_UNITS:
            position += 1
            parent = units[name] = OrgUnit.objects.get_or_create(
                name=name, parent=None, defaults={'position': position})[0]
            for child_name in children:
                position += 1
                units[child_name] = OrgUnit.objects.get_or_create(
                    name=child_name, parent=parent, defaults={'position': position})[0]
        return units

    def create_login_info(self, department):
        config = LabCIRSConfig.objects.get(department=department)  # made with the department
        for code in parler_codes():
            if not config.has_translation(code):
                config.create_translation(code, login_info=text_in(LOGIN_INFO, code))

    def create_report(self, department, data, units):
        today = date.today()
        review = dict(data.get('review', {}))
        if review:  # the review block is complete, the review date inside the time of the report
            review['review_date'] = today - timedelta(days=data['days_ago'] // 2)
        incident, created = CriticalIncident.objects.get_or_create(
            department=department, incident=data['incident'],
            defaults=dict(
                date=today - timedelta(days=data['days_ago']),
                reported=today - timedelta(days=max(data['days_ago'] - 1, 0)),
                reason=data['reason'], immediate_action=data['immediate_action'],
                preventability=data['preventability'], public=data['public'],
                status=data['status'], comment_code=data.get('code', ''),
                org_unit=units[data['org_unit']] if 'org_unit' in data else None,
                **review))
        if created and data.get('photo'):
            incident.photo.save(random_photo_name('jpg'), ContentFile(demo_photo()))
        if 'case' in data:
            self.create_case(incident, data['case'])
        return incident

    def create_case(self, incident, texts):
        """The published case of a report, with a text in every language."""
        case = PublishableIncident.objects.filter(critical_incident=incident).first()
        if case is None:
            case = PublishableIncident.objects.create(critical_incident=incident, publish=True)
        for code in parler_codes():
            if not case.has_translation(code):
                title, description, measures = text_in(texts, code)
                case.create_translation(code, incident=title, description=description,
                                        measures_and_consequences=measures)

    def create_extra_case(self, department, number):
        incident, created = CriticalIncident.objects.get_or_create(
            department=department, incident='Demo extra %02d: Ein erfundener Fall.' % number,
            defaults=dict(
                date=date.today() - timedelta(days=9 * number), reason='Demo: erfunden.',
                immediate_action='Demo: erfunden.', preventability='indistinct', public=True,
                status='completed'))
        self.create_case(incident, {
            'de': ('Demofall %02d' % number, 'Ein erfundener Fall für das Blättern.',
                   'Keine Maßnahmen nötig.'),
            'en': ('Demo case %02d' % number, 'An invented case to try out paging.',
                   'No measures needed.')})
        return incident

    def print_summary(self, department, reports):
        write = self.stdout.write
        write('Demo data ready. Department: %s (/incidents/%s/)' % (department.name, department.label))
        write('Accounts: %s (reviewer), %s (superuser); the passwords are those of the call.'
              % (QM_USERNAME, ADMIN_USERNAME))
        write('Reports (code, status, consent to publish, text):')
        for incident in reports[:len(REPORTS)]:
            write('  %-16s %-18s %-3s %s' % (incident.comment_code, incident.status,
                                             'yes' if incident.public else 'no', incident.incident))
        if len(reports) > len(REPORTS):
            write('Plus %d more published cases (--extra-published).' % (len(reports) - len(REPORTS)))
