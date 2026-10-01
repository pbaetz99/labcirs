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

from .views import bad_request

NUL = '\x00'


class RejectNulBytesMiddleware:
    """
    Answers 400 for a NUL byte in the path, in a key or a value of the query or of the form data.

    PostgreSQL refuses text with a NUL byte ("text fields cannot contain NUL"), so such a value
    fails with a 500 as soon as it reaches a query (the search of the case list, the user name of
    the login, the department label of the URL, ...). No page has a use for it, so one check for
    all callers. The answer is the styled 400 page, in the language of the
    visitor: the middleware stands after LocaleMiddleware. Nothing is logged: scanners send NUL
    bytes all the time and the values are the visitor's input.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self.has_nul(request):
            return bad_request(request, None)
        return self.get_response(request)

    @staticmethod
    def has_nul(request):
        if NUL in request.path_info:
            return True
        # request.POST parses the body once; the CSRF middleware and the views reuse the result
        for data in (request.GET, request.POST):
            for key, values in data.lists():
                if NUL in key or any(NUL in value for value in values):
                    return True
        return False
