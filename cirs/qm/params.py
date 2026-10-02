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

"""The GET parameters of the work list: their names, their values, the address they make.

One place for what the overview links to and the list reads, so that a link cannot name a
filter the list does not know. The names are German, like the paths of the QM pages.

    stand              a status of the model: new, in process, under supervision, completed
    wo                 the id of an organisational unit, or "keine" for the incidents without one
    kategorie          a category of the model
    risiko             a risk of the model
    von, bis           a day, ISO 8601 (2026-01-31): reported from, reported to, both inclusive
    wartet             1: only incidents that wait for the QM
    ohne_bearbeitung   1: only incidents without processing
    q                  a search term, up to 200 characters
    sort               nr, gemeldet or aktivitaet, with a leading minus for the descending order
    page               the page, from 1

worklist_url() builds an address and refuses what is out of range, because a wrong value in a
link is a mistake of the program. parse_params() reads an address and drops what is out of range,
because a person can write anything into the address bar. It says which parameters it dropped, so
the page can tell that a filter did not apply.
"""

import re
from datetime import date
from urllib.parse import urlencode

from django.urls import reverse

from cirs.models import CATEGORY_CHOICES, RISK_CHOICES, STATUS_CHOICES

FILTERS = ('stand', 'wo', 'kategorie', 'risiko', 'von', 'bis', 'wartet', 'ohne_bearbeitung', 'q')
SORTS = ('nr', 'gemeldet', 'aktivitaet')
PARAMS = FILTERS + ('sort', 'page')
MAX_SEARCH_LENGTH = 200
MAX_NUMBER = 2**31 - 1  # what an id or a page may be: a larger number cannot be one
NO_PLACE = 'keine'  # the value of wo for the incidents that name no place

DAY = re.compile(r'\d{4}-\d{2}-\d{2}')  # date.fromisoformat takes more forms than this one


def _choice(choices):
    keys = {key for key, _ in choices}

    def clean(text):
        if text not in keys:
            raise ValueError(text)
        return text
    return clean


def _number(text):
    if not text.isascii() or not text.isdecimal() or not 1 <= int(text) <= MAX_NUMBER:
        raise ValueError(text)
    return int(text)


def _place(text):
    return text if text == NO_PLACE else _number(text)


def _day(text):
    if not DAY.fullmatch(text):
        raise ValueError(text)
    return date.fromisoformat(text)


def _flag(text):
    if text != '1':
        raise ValueError(text)
    return True


def _search(text):
    if len(text) > MAX_SEARCH_LENGTH:
        raise ValueError(text)
    return text


def _sort(text):
    if text.removeprefix('-') not in SORTS:
        raise ValueError(text)
    return text


# text of a parameter -> its value, or ValueError
CLEANERS = {
    'stand': _choice(STATUS_CHOICES), 'wo': _place, 'kategorie': _choice(CATEGORY_CHOICES),
    'risiko': _choice(RISK_CHOICES), 'von': _day, 'bis': _day, 'wartet': _flag,
    'ohne_bearbeitung': _flag, 'q': _search, 'sort': _sort, 'page': _number,
}


def _text(value):
    if value is True:
        return '1'
    if isinstance(value, date):
        return value.isoformat()  # a datetime gives a text with the time, which is out of range
    return str(value).strip()


def worklist_url(**params):
    """
    The address of the work list. None, False and '' leave a parameter out. The parameters come in
    the order of PARAMS, so equal calls give equal addresses. Raises ValueError for a name that is
    not a parameter and for a value out of range.
    """
    unknown = set(params) - set(PARAMS)
    if unknown:
        raise ValueError('Unknown parameters: %s' % ', '.join(sorted(unknown)))
    query = []
    for name in PARAMS:
        value = params.get(name)
        if value is None or value is False:
            continue
        text = _text(value)
        if text:
            CLEANERS[name](text)  # ValueError for a value out of range
            query.append((name, text))
    url = reverse('qm_incidents')
    return url + '?' + urlencode(query) if query else url


def parse_params(query):
    """
    The parameters of a request (request.GET) as ({name: value}, [names dropped]). Values are
    typed: numbers as int, days as date, flags as True. An empty value is no filter and not a
    mistake. Other parameters are not the business of the list and are not named.
    """
    values, dropped = {}, []
    for name in PARAMS:
        text = query.get(name, '').strip()
        if not text:
            continue
        try:
            values[name] = CLEANERS[name](text)
        except ValueError:
            dropped.append(name)
    return values, dropped
