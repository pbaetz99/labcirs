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

"""Photos of reports must not carry metadata (GPS, camera, author, ...).

Uploads are re-encoded from their pixels only, so nothing else survives.
"""

import io
import uuid

from django.utils.translation import gettext_lazy as _
from PIL import Image, ImageOps

MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_PHOTO_PIXELS = 50_000_000
ALLOWED_FORMATS = ('JPEG', 'PNG', 'GIF', 'WEBP')


FORMAT_ERROR = _('Please upload a photo in JPEG, PNG, GIF or WebP format.')


class PhotoError(ValueError):
    """The upload is not an acceptable photo; the message is user-facing."""


def sanitize_image(fileobj, upload=True):
    """Return the pixels of a photo as (bytes, 'jpg' | 'png').

    upload=False is for photos already stored: they were never restricted, so any
    format Pillow can open is accepted (converted to PNG) and the byte limit is not applied.
    """
    if upload:
        size = getattr(fileobj, 'size', None)
        if size is None:
            size = fileobj.seek(0, io.SEEK_END)
            fileobj.seek(0)
        if size > MAX_PHOTO_BYTES:
            raise PhotoError(_('The photo is larger than 10 MB.'))
    try:
        img = Image.open(fileobj)
    except Exception as error:
        raise PhotoError(FORMAT_ERROR) from error
    # Checked before load(): a few KB of PNG can decode to gigabytes.
    if img.width * img.height > MAX_PHOTO_PIXELS:
        raise PhotoError(_('The photo has more than 50 megapixels.'))
    # Camera JPEGs with previews or gain maps are multi-picture files.
    fmt = 'JPEG' if img.format == 'MPO' else img.format
    if upload and fmt not in ALLOWED_FORMATS:
        raise PhotoError(FORMAT_ERROR)
    try:
        img.load()
        img = ImageOps.exif_transpose(img)
        has_alpha = img.mode in ('RGBA', 'LA', 'PA') or 'transparency' in img.info
        mode = 'RGBA' if has_alpha and fmt != 'JPEG' else 'RGB'
        clean = Image.new(mode, img.size)
        clean.paste(img.convert(mode))
        out = io.BytesIO()
        if fmt == 'JPEG':
            clean.save(out, 'JPEG', quality=90)
            ext = 'jpg'
        else:
            clean.save(out, 'PNG')
            ext = 'png'
    except Exception as error:
        # Truncated or malformed files raise all kinds of errors.
        raise PhotoError(FORMAT_ERROR) from error
    return out.getvalue(), ext


def random_photo_name(ext):
    return uuid.uuid4().hex + '.' + ext
