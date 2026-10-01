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

import re
from urllib.parse import urlsplit

from django.contrib.auth.models import Permission, User

from cirs.models import Reporter, Reviewer, group_code


def create_user(name=None, superuser=False):
    if superuser is True:
        user = User.objects.create_superuser(name, '%s@localhost' % name, name)
    else:
        user = User.objects.create_user(name, '%s@localhost' % name, name)
    return user

def create_role(role_cls, name):
    if (role_cls != Reporter) and (role_cls != Reviewer):
        raise TypeError('This function can be used with Reporter or Reviewer '
                        'models only. Instead {} was used!'.format(role_cls))
    if type(name) == str:
        role = role_cls.objects.create(user=create_user(name))
    elif type(name) == User:
        role = role_cls.objects.create(user=name)
    else:
        raise TypeError('You have to proviede either a name for new user or an '
                        'existing user. But you provided {} which is '
                        '{}!'.format(name, type(name)))
    return role


def code_markup(code):
    """The code element of the success page: every group of four in a span of its own."""
    groups = group_code(code).split()
    return '<code class="ui-meldecode">%s</code>' % ' '.join(
        '<span class="ui-meldecode__gruppe">%s</span>' % group for group in groups)


def create_user_with_perm(name, codename):
    user = create_user(name)
    permission = Permission.objects.get(codename=codename)
    user.user_permissions.add(permission)
    return user


# What a Content-Security-Policy without 'unsafe-inline' blocks (nginx sends one for all pages,
# /admin/ included). Attributes are searched only inside tags, so text about them is no hit.
CSP_VIOLATIONS = (
    ('style attribute', re.compile(r'<[a-z][^>]*\sstyle\s*=', re.I)),
    ('style block', re.compile(r'<style[\s>]', re.I)),
    ('inline script', re.compile(r'<script(?![^>]*\ssrc\s*=)[^>]*>', re.I)),
    ('event handler', re.compile(r'<[a-z][^>]*\son[a-z]+\s*=', re.I)),
    ('javascript url', re.compile(r'<[a-z][^>]*\s(?:href|src|action|formaction)\s*=\s*'
                                  r'["\']?\s*javascript:', re.I)),
)


# Scripts, images and stylesheets come from this host (no CDN, no web fonts): the URL of a
# script or image `src` or of a `link` `href` must not have a host part. A quoted or unquoted value.
ASSET_URL = re.compile(r'<(?:script|img)\b[^>]*\ssrc\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+))'
                       r'|<link\b[^>]*\shref\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+))', re.I)


def foreign_asset_urls(html):
    """Returns the script, image and stylesheet URLs in html that name a host."""
    urls = (next(group for group in match.groups() if group is not None)
            for match in ASSET_URL.finditer(html))
    return [url for url in urls if urlsplit(url.strip()).netloc]


def csp_violations(html):
    """Returns the names of the constructs in html that the strict policy would block."""
    names = [name for name, pattern in CSP_VIOLATIONS if pattern.search(html)]
    return names + ['external url'] * bool(foreign_asset_urls(html))
