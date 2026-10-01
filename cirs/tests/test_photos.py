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
import os
import shutil
import stat
import tempfile
from types import SimpleNamespace
from unittest import mock, skipIf

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import translation
from model_bakery import baker
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from cirs.models import CriticalIncident
from cirs.photos import (MAX_PHOTO_BYTES, MAX_PHOTO_PIXELS, PhotoError,
                         random_photo_name, sanitize_image)

from .test_anonymous import VALID


def image_bytes(fmt, size=(8, 8), mode='RGB', **save_args):
    buf = io.BytesIO()
    Image.new(mode, size, (200, 30, 30)).save(buf, fmt, **save_args)
    return buf.getvalue()


def jpeg(size=(8, 8), exif_make=None, orientation=None, **save_args):
    exif = Image.Exif()
    if exif_make:
        exif[0x010F] = exif_make
    if orientation:
        exif[0x0112] = orientation
    return image_bytes('JPEG', size, exif=exif, **save_args)


def mpo(size=(8, 8), exif_make=None):
    exif = Image.Exif()
    exif[0x010F] = exif_make
    buf = io.BytesIO()
    Image.new('RGB', size, (200, 30, 30)).save(
        buf, 'MPO', save_all=True, exif=exif,
        append_images=[Image.new('RGB', size, (30, 200, 30))])
    return buf.getvalue()


def broken_png():
    # Header is fine, the pixel data is cut off.
    img = Image.frombytes('RGB', (64, 64), os.urandom(64 * 64 * 3))
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    return buf.getvalue()[:len(buf.getvalue()) // 2]


def huge_png():
    # Tiny on disk, but a bit more pixels than accepted.
    side = int(MAX_PHOTO_PIXELS ** 0.5) + 10
    buf = io.BytesIO()
    Image.new('1', (side, side)).save(buf, 'PNG')
    return buf.getvalue()


def open_bytes(data):
    return Image.open(io.BytesIO(data))


class SanitizeImageTest(TestCase):

    def test_jpeg_metadata_removed(self):
        source = jpeg(exif_make='TestCam', comment=b'geheim', icc_profile=b'x')
        # Guard: the fixture really carries what has to disappear.
        self.assertEqual(len(open_bytes(source).getexif()), 1)
        self.assertIn('comment', open_bytes(source).info)
        self.assertIn('icc_profile', open_bytes(source).info)
        data, ext = sanitize_image(io.BytesIO(source))
        img = open_bytes(data)
        self.assertEqual((ext, len(img.getexif())), ('jpg', 0))
        self.assertNotIn('comment', img.info)
        self.assertNotIn('icc_profile', img.info)

    def test_orientation_applied(self):
        data, ext = sanitize_image(io.BytesIO(jpeg(size=(40, 20), orientation=6)))
        self.assertEqual(open_bytes(data).size, (20, 40))
        self.assertEqual(len(open_bytes(data).getexif()), 0)

    def test_png_text_chunk_removed(self):
        info = PngInfo()
        info.add_text('Author', 'Somebody')
        source = image_bytes('PNG', pnginfo=info)
        self.assertIn('Author', open_bytes(source).info)
        data, ext = sanitize_image(io.BytesIO(source))
        self.assertEqual(ext, 'png')
        self.assertNotIn('Author', open_bytes(data).info)

    def test_gif_becomes_png(self):
        data, ext = sanitize_image(io.BytesIO(image_bytes('GIF')))
        self.assertEqual((ext, open_bytes(data).format), ('png', 'PNG'))

    def test_webp_becomes_png(self):
        data, ext = sanitize_image(io.BytesIO(image_bytes('WEBP')))
        self.assertEqual((ext, open_bytes(data).format), ('png', 'PNG'))

    def test_transparency_is_kept(self):
        source = image_bytes('PNG', mode='RGBA')
        data, ext = sanitize_image(io.BytesIO(source))
        self.assertEqual(open_bytes(data).mode, 'RGBA')

    def test_too_large_rejected(self):
        with self.assertRaises(PhotoError) as caught:
            sanitize_image(SimpleNamespace(size=MAX_PHOTO_BYTES + 1))
        with translation.override('de'):
            self.assertEqual(str(caught.exception), 'Das Foto ist größer als 10 MB.')

    def test_non_image_rejected(self):
        with self.assertRaises(PhotoError):
            sanitize_image(io.BytesIO(b'kein bild'))

    def test_tiff_rejected(self):
        with self.assertRaises(PhotoError):
            sanitize_image(io.BytesIO(image_bytes('TIFF')))

    def test_too_many_pixels_rejected_before_decoding(self):
        source = huge_png()
        self.assertLess(len(source), MAX_PHOTO_BYTES)
        with mock.patch('PIL.ImageFile.ImageFile.load') as load:
            with self.assertRaises(PhotoError) as caught:
                sanitize_image(io.BytesIO(source))
        load.assert_not_called()
        with translation.override('de'):
            self.assertEqual(str(caught.exception), 'Das Foto hat mehr als 50 Megapixel.')

    def test_mpo_is_treated_like_jpeg(self):
        source = mpo(exif_make='TestCam')
        self.assertEqual(open_bytes(source).format, 'MPO')
        self.assertEqual(len(open_bytes(source).getexif()), 1)
        data, ext = sanitize_image(io.BytesIO(source))
        img = open_bytes(data)
        self.assertEqual((ext, img.format, len(img.getexif())), ('jpg', 'JPEG', 0))

    def test_truncated_image_rejected(self):
        with self.assertRaises(PhotoError):
            sanitize_image(io.BytesIO(broken_png()))

    def test_unexpected_pillow_errors_become_photo_errors(self):
        for error in (ValueError, SyntaxError, EOFError, OverflowError):
            with mock.patch('cirs.photos.ImageOps.exif_transpose', side_effect=error('x')):
                with self.assertRaises(PhotoError):
                    sanitize_image(io.BytesIO(jpeg()))

    def test_random_name(self):
        self.assertRegex(random_photo_name('jpg'), r'^[0-9a-f]{32}\.jpg$')
        self.assertNotEqual(random_photo_name('jpg'), random_photo_name('jpg'))


class MediaRootMixin:

    def setUp(self):
        super().setUp()
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)


class UploadTest(MediaRootMixin, TestCase):

    def setUp(self):
        super().setUp()
        self.dept = baker.make_recipe('cirs.department')
        self.url = reverse('create_incident', kwargs={'dept': self.dept.label})

    def post(self, upload, **extra):
        return self.client.post(self.url, dict(VALID, photo=upload), **extra)

    def test_upload_gets_random_name_without_exif(self):
        response = self.post(SimpleUploadedFile('IMG_1234.jpg', jpeg(exif_make='TestCam'),
                                                content_type='image/jpeg'))
        self.assertEqual(response.status_code, 302)
        ci = CriticalIncident.objects.get()
        self.assertRegex(ci.photo.name, r'^photos/\d{4}/\d{2}/\d{2}/[0-9a-f]{32}\.jpg$')
        self.assertNotIn('IMG_1234', ci.photo.name)
        with ci.photo.open('rb') as f:
            self.assertEqual(len(Image.open(f).getexif()), 0)

    def test_upload_error_is_form_error_not_500(self):
        response = self.post(SimpleUploadedFile('scan.tiff', image_bytes('TIFF'),
                                                content_type='image/tiff'),
                             HTTP_ACCEPT_LANGUAGE='de')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Bitte laden Sie ein Foto im Format JPEG, PNG, GIF oder WebP hoch.')
        self.assertEqual(CriticalIncident.objects.count(), 0)

    def test_non_image_upload_gets_same_message(self):
        response = self.post(SimpleUploadedFile('x.jpg', b'kein bild', content_type='image/jpeg'),
                             HTTP_ACCEPT_LANGUAGE='de')
        self.assertContains(response, 'Bitte laden Sie ein Foto im Format JPEG, PNG, GIF oder WebP hoch.')

    def test_pixel_bomb_is_form_error_not_500(self):
        response = self.post(SimpleUploadedFile('big.png', huge_png(), content_type='image/png'),
                             HTTP_ACCEPT_LANGUAGE='de')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Das Foto hat mehr als 50 Megapixel.')
        self.assertEqual(CriticalIncident.objects.count(), 0)

    def test_truncated_png_is_form_error_not_500(self):
        response = self.post(SimpleUploadedFile('cut.png', broken_png(), content_type='image/png'),
                             HTTP_ACCEPT_LANGUAGE='de')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Bitte laden Sie ein Foto im Format JPEG, PNG, GIF oder WebP hoch.')
        self.assertEqual(CriticalIncident.objects.count(), 0)

    def test_mpo_upload_is_stored_as_jpg_without_exif(self):
        response = self.post(SimpleUploadedFile('IMG_1.jpg', mpo(exif_make='TestCam'),
                                                content_type='image/jpeg'))
        self.assertEqual(response.status_code, 302)
        ci = CriticalIncident.objects.get()
        self.assertRegex(ci.photo.name, r'\.jpg$')
        with ci.photo.open('rb') as f:
            self.assertEqual(len(Image.open(f).getexif()), 0)

    def test_report_without_photo_still_works(self):
        self.assertEqual(self.client.post(self.url, VALID).status_code, 302)
        self.assertFalse(CriticalIncident.objects.get().photo)


class StripCommandTest(MediaRootMixin, TestCase):
    DIR = 'photos/2025/01/01/'

    def make_incident(self, name, data, mode=None):
        path = self.path(name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)
        if mode is not None:
            os.chmod(path, mode)
        return baker.make_recipe('cirs.public_ci', photo=name)

    def path(self, name):
        return os.path.join(self.media, name)

    def run_command(self):
        out = io.StringIO()
        self.status = 0
        try:
            call_command('strip_photo_metadata', stdout=out)
        except SystemExit as exit_request:
            self.status = exit_request.code
        return out.getvalue()

    def test_command_renames_and_strips(self):
        old = self.DIR + 'Mueller_Station3.jpg'
        ci = self.make_incident(old, jpeg(exif_make='TestCam'))
        output = self.run_command()
        ci.refresh_from_db()
        self.assertRegex(ci.photo.name, r'^photos/2025/01/01/[0-9a-f]{32}\.jpg$')
        self.assertIn('cleaned %s -> %s' % (old, ci.photo.name), output)
        self.assertFalse(os.path.exists(self.path(old)))
        with ci.photo.open('rb') as f:
            img = Image.open(f)
            self.assertEqual((img.format, len(img.getexif())), ('JPEG', 0))

    def test_gif_named_png_is_cleaned_as_png(self):
        ci = self.make_incident(self.DIR + 'foto.PNG', image_bytes('GIF'))
        self.run_command()
        ci.refresh_from_db()
        self.assertRegex(ci.photo.name, r'/[0-9a-f]{32}\.png$')
        with ci.photo.open('rb') as f:
            self.assertEqual(Image.open(f).format, 'PNG')

    def test_second_run_leaves_clean_files_alone(self):
        ci = self.make_incident(self.DIR + 'alt.jpg', jpeg(exif_make='TestCam'))
        self.run_command()
        ci.refresh_from_db()
        path = self.path(ci.photo.name)
        with open(path, 'rb') as f:
            before = (f.read(), os.stat(path).st_mtime_ns)
        output = self.run_command()
        self.assertIn('already clean ' + ci.photo.name, output)
        with open(path, 'rb') as f:
            self.assertEqual((f.read(), os.stat(path).st_mtime_ns), before)

    def test_fresh_upload_counts_as_clean(self):
        name = self.DIR + random_photo_name('jpg')
        self.make_incident(name, jpeg())
        self.assertIn('already clean ' + name, self.run_command())

    @skipIf(os.name == 'nt', 'POSIX file modes')
    def test_file_mode_is_kept(self):
        ci = self.make_incident(self.DIR + 'alt.jpg', jpeg(exif_make='TestCam'), mode=0o640)
        self.run_command()
        ci.refresh_from_db()
        self.assertEqual(stat.S_IMODE(os.stat(self.path(ci.photo.name)).st_mode), 0o640)

    def assert_converted(self, ci, old, ext, fmt):
        ci.refresh_from_db()
        self.assertRegex(ci.photo.name, r'^photos/2025/01/01/[0-9a-f]{32}\.%s$' % ext)
        self.assertFalse(os.path.exists(self.path(old)))
        with ci.photo.open('rb') as f:
            img = Image.open(f)
            self.assertEqual(img.format, fmt)
            self.assertEqual(len(img.getexif()), 0)
            return dict(img.info)

    def test_gif_with_comment_is_converted_to_png(self):
        old = self.DIR + 'Station3.gif'
        source = image_bytes('GIF', comment=b'geheim')
        self.assertEqual(open_bytes(source).info['comment'], b'geheim')
        ci = self.make_incident(old, source)
        self.assertIn('cleaned %s -> ' % old, self.run_command())
        info = self.assert_converted(ci, old, 'png', 'PNG')
        self.assertNotIn('comment', info)

    def test_webp_with_exif_is_converted_to_png(self):
        old = self.DIR + 'Mueller.webp'
        exif = Image.Exif()
        exif[0x010F] = 'TestCam'
        source = image_bytes('WEBP', exif=exif)
        self.assertEqual(len(open_bytes(source).getexif()), 1)
        ci = self.make_incident(old, source)
        self.run_command()
        self.assert_converted(ci, old, 'png', 'PNG')

    def test_other_formats_under_any_suffix_are_converted(self):
        for old, fmt in ((self.DIR + 'scan.tiff', 'TIFF'), (self.DIR + 'bild.bmp', 'BMP'),
                         (self.DIR + 'ohne_endung', 'PNG')):
            ci = self.make_incident(old, image_bytes(fmt))
            self.run_command()
            self.assert_converted(ci, old, 'png', 'PNG')

    def test_command_ignores_upload_size_limit(self):
        ci = self.make_incident(self.DIR + 'gross.jpg', jpeg())
        with mock.patch('cirs.photos.MAX_PHOTO_BYTES', 10):
            self.run_command()
        self.assert_converted(ci, self.DIR + 'gross.jpg', 'jpg', 'JPEG')

    @skipIf(os.name == 'nt', 'directories cannot be synced on Windows')
    def test_new_file_and_directory_are_synced(self):
        self.make_incident(self.DIR + 'alt.jpg', jpeg())
        with mock.patch('os.fsync', wraps=os.fsync) as fsync:
            self.run_command()
        # new file, directory after the new entry, directory after removing the old one
        self.assertEqual(fsync.call_count, 3)

    def test_bad_files_are_reported_and_the_next_one_is_processed(self):
        bad = self.DIR + 'kaputt.jpg'
        missing = self.DIR + 'weg.png'
        bad_incident = self.make_incident(bad, b'kein bild')
        self.make_incident(missing, image_bytes('PNG'))
        os.unlink(self.path(missing))
        huge = self.DIR + 'riesig.png'
        self.make_incident(huge, huge_png())
        good = self.make_incident(self.DIR + 'gut.jpg', jpeg(exif_make='TestCam'))
        output = self.run_command()
        self.assertIn('failed %s: cannot identify image file' % bad, output)
        self.assertIn('failed %s: [Errno 2] No such file or directory' % missing, output)
        self.assertIn('failed %s: The photo has more than 50 megapixels.' % huge, output)
        self.assertNotIn('skipped', output)
        good.refresh_from_db()
        self.assertRegex(good.photo.name, r'/[0-9a-f]{32}\.jpg$')
        with open(self.path(bad), 'rb') as f:
            self.assertEqual(f.read(), b'kein bild')
        bad_incident.refresh_from_db()
        self.assertEqual(bad_incident.photo.name, bad)
        self.assertEqual(sorted(os.listdir(self.path(self.DIR))),
                         sorted([os.path.basename(good.photo.name), 'kaputt.jpg', 'riesig.png']))

    def test_failed_db_update_keeps_old_file_and_removes_new_one(self):
        old = self.DIR + 'alt.jpg'
        data = jpeg(exif_make='TestCam')
        ci = self.make_incident(old, data)
        with mock.patch.object(CriticalIncident, 'save', side_effect=RuntimeError('db down')):
            output = self.run_command()
        self.assertIn('failed %s: db down' % old, output)
        self.assertEqual(self.status, 1)
        self.assertEqual(os.listdir(self.path(self.DIR)), ['alt.jpg'])
        with open(self.path(old), 'rb') as f:
            self.assertEqual(f.read(), data)
        ci.refresh_from_db()
        self.assertEqual(ci.photo.name, old)

    def test_failed_write_removes_partial_new_file(self):
        old = self.DIR + 'alt.jpg'
        data = jpeg(exif_make='TestCam')
        ci = self.make_incident(old, data)
        with mock.patch('os.fsync', side_effect=OSError('disk full')):
            output = self.run_command()
        self.assertIn('failed %s: disk full' % old, output)
        self.assertEqual(os.listdir(self.path(self.DIR)), ['alt.jpg'])
        with open(self.path(old), 'rb') as f:
            self.assertEqual(f.read(), data)
        ci.refresh_from_db()
        self.assertEqual(ci.photo.name, old)

    def test_failed_cleanup_does_not_mask_the_original_error(self):
        old = self.DIR + 'alt.jpg'
        self.make_incident(old, jpeg())
        with mock.patch('os.fsync', side_effect=OSError('disk full')),                 mock.patch('os.unlink', side_effect=PermissionError('no rights')):
            output = self.run_command()
        self.assertIn('failed %s: disk full' % old, output)

    def test_summary_line_and_exit_status(self):
        self.make_incident(self.DIR + 'alt.jpg', jpeg(exif_make='TestCam'))
        self.make_incident(self.DIR + random_photo_name('png'), image_bytes('PNG'))
        output = self.run_command()
        self.assertEqual(output.splitlines()[-1], '1 cleaned, 1 already clean, 0 failed')
        self.assertEqual(self.status, 0)
        self.make_incident(self.DIR + 'kaputt.jpg', b'kein bild')
        output = self.run_command()
        self.assertEqual(output.splitlines()[-1], '0 cleaned, 2 already clean, 1 failed')
        self.assertEqual(self.status, 1)

    def test_old_file_not_removed_is_reported_and_fails_the_run(self):
        # The new file and the database are fine, but the old file still carries the metadata and
        # the identifying name: the operator must see it, also through the exit status.
        old = self.DIR + 'alt.jpg'
        ci = self.make_incident(old, jpeg(exif_make='TestCam'))
        with mock.patch('os.unlink', side_effect=PermissionError('no rights')):
            output = self.run_command()
        ci.refresh_from_db()
        self.assertIn('cleaned %s -> %s (old file not removed: no rights)' % (old, ci.photo.name),
                      output)
        self.assertEqual(output.splitlines()[-1],
                         '1 cleaned, 0 already clean, 0 failed, 1 old file(s) not removed')
        self.assertEqual(self.status, 1)
        self.assertTrue(os.path.exists(self.path(old)))
        self.assertRegex(ci.photo.name, r'/[0-9a-f]{32}\.jpg$')

    def test_clean_name_must_match_completely(self):
        name = self.DIR + random_photo_name('jpg') + chr(10)
        self.make_incident(name, jpeg())
        self.assertTrue(self.run_command().startswith('cleaned '))
