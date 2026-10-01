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

"""Mails to the reporter of an incident, if the reporter left an address (ReporterContact).

The reporter stays anonymous towards the QM: the mails carry only what the reporter already
sees on the page (the code, the status label, the reply of the QM), never a field of the
report, and no link (the site is reachable from the intranet only). The address is not logged.
"""

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.utils import translation
from django.utils.translation import gettext as _

from .models import ReporterContact, group_code

logger = logging.getLogger('cirs')


EVENTS = ('received', 'status', 'reply')


def notify_reporter(incident, event, comment=None):
    """
    Mails the reporter of incident about event: 'received' (the code), 'status' (the new status,
    or the end of the notifications once the incident is completed) or 'reply' (comment is the
    reply of the QM). Does nothing without ReporterContact. Whatever goes wrong with the mail
    (server down, invalid sender, ...) is logged only: it must not cost the reporter the code,
    the QM a saved status or reply. An unknown event is a programming error and raises.
    """
    if event not in EVENTS:
        raise ValueError('Unknown event %r' % (event,))
    # Not incident.reporter_contact: that one is cached and stays after the contact is deleted.
    contact = ReporterContact.objects.filter(incident=incident).first()
    if contact is None:
        return
    try:
        # The site language, not that of the request (as for the QM notifications).
        with translation.override(settings.LANGUAGE_CODE):
            if event == 'received':
                subject = _('Your report has been received')
                text = _('Your report has been received. Your code is: {code}. With this code '
                         'you can see the status of your report and answer questions. Keep this '
                         'message safe.').format(code=group_code(incident.comment_code))
            elif event == 'reply':
                subject = _('New reply to your report')
                text = _('The quality management has replied to you:') + '\n\n' + comment.text
            elif event == 'status':
                if incident.status == 'completed':
                    subject = _('Your report is closed')
                    text = _('Your report is closed. Your e-mail address has been deleted, you '
                             'will receive no more messages.')
                else:
                    subject = _('New status of your report')
                    text = _('The status of your report has changed: {status}.').format(
                        status=incident.get_reporter_status_display())
            text += '\n\n' + _('You can reply in-house under “My report” with your code. '
                               'Do not reply to this e-mail.')
            if settings.SITE_URL:
                text += '\n' + _('Address on the internal network: {url}').format(
                    url=settings.SITE_URL)
        send_mail(subject, text, None, [contact.email], fail_silently=False)
    except Exception as error:
        # No traceback and no message of the error: SMTP servers repeat the address in their
        # answers, and the address is what makes the reporter identifiable. Not only
        # SMTPException and OSError: the SMTP backend raises ValueError for an invalid sender.
        logger.error('Mail to the reporter of incident %s failed: %s', incident.pk,
                     type(error).__name__)
