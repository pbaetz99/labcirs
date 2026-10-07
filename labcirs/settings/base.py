"""
Django settings for labcirs project.

Upgraded from 1.6 to 1.9

For more information on this file, see
https://docs.djangoproject.com/en/1.9/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/1.9/ref/settings/
"""

import json
import os
from django.core.exceptions import ImproperlyConfigured
from django.utils.translation import gettext_lazy as _
# Build paths inside the project like this: os.path.join(BASE_DIR, ...)
from os.path import dirname, abspath, join as join_path
BASE_DIR = dirname(dirname(dirname(abspath(__file__))))

local_config_file = join_path(BASE_DIR, "labcirs/settings/local_config.json")

ENV_PREFIX = 'LABCIRS_'
_MISSING = object()


def _read_config_file(path):
    """Returns the content of the JSON config file, or an empty dict if there is none."""
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except ValueError as err:
        raise ImproperlyConfigured(f'Invalid JSON in {path}: {err}') from err


def get_local_setting(setting_item, default=None, config_file=local_config_file):
    """Returns a setting from the environment, the config file or the default (in this order).

    The environment variable is LABCIRS_<setting_item>. Its value is decoded as JSON
    if possible, otherwise it is used as plain text. An empty value is replaced by the
    default. Without a default the setting is required.
    """
    raw = os.environ.get(ENV_PREFIX + setting_item)
    if raw is not None:
        try:
            value = json.loads(raw)
        except ValueError:
            value = raw
    else:
        value = _read_config_file(config_file).get(setting_item, _MISSING)
    if value is _MISSING:
        if default is None:
            raise ImproperlyConfigured(
                f'Set {ENV_PREFIX}{setting_item} or {setting_item} in {config_file}')
        return default
    if value == '' and default is not None:
        return default
    return value


BOOLEAN_WORDS = {'true': True, 'yes': True, 'on': True, '1': True,
                 'false': False, 'no': False, 'off': False, '0': False}


def get_bool_setting(setting_item, default, config_file=local_config_file):
    """Like get_local_setting, but only accepts a real boolean.

    Text such as "False" would be true in a condition, and DEBUG on in production shows
    tracebacks. So besides JSON true/false only the words true/false, yes/no, on/off and 1/0 are
    accepted, in any case and with surrounding spaces. Anything else raises ImproperlyConfigured.
    """
    value = get_local_setting(setting_item, default, config_file)
    if isinstance(value, bool):
        return value
    try:
        return BOOLEAN_WORDS[str(value).strip().lower()]
    except KeyError:
        raise ImproperlyConfigured(
            f'{ENV_PREFIX}{setting_item} (or {setting_item} in {config_file}) must be one of '
            f'true, false, yes, no, on, off, 1, 0 (any case), not {value!r}') from None


def get_positive_int_setting(setting_item, default, config_file=local_config_file):
    """Like get_local_setting, but only accepts a whole number of at least 1.

    A limit in days or a smallest cell of 0 would switch the rule off without a sign, and JSON
    lets true, 14.0 and "14" pass as values (true is a number in Python). Anything but a whole
    number from 1 on raises ImproperlyConfigured.
    """
    value = get_local_setting(setting_item, default, config_file)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ImproperlyConfigured(
            f'{ENV_PREFIX}{setting_item} (or {setting_item} in {config_file}) must be a whole '
            f'number of at least 1, not {value!r}')
    return value


def default_language_code(languages):
    """Returns the first configured language code, or 'en' if there is none."""
    return next(iter(languages), 'en')

# SECURITY WARNING: keep the secret key used in production secret!
# The secret key has to be generated separately for each server. scripts/setup.sh generates
# one and stores it in .env as LABCIRS_SECRET_KEY.

SECRET_KEY = get_local_setting('SECRET_KEY')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = get_bool_setting('DEBUG', False)

ALLOWED_HOSTS = get_local_setting('ALLOWED_HOSTS')  # ['*',] #local


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin.apps.SimpleAdminConfig',
    'django.contrib.admindocs',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'cirs',
    'multiselectfield',
    'parler',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.locale.LocaleMiddleware', #local
    # Early, before any view or form can send a NUL byte to PostgreSQL; after LocaleMiddleware,
    # so that its 400 page speaks the language of the visitor.
    'cirs.middleware.RejectNulBytesMiddleware',  # local
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'labcirs.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [join_path(BASE_DIR, 'templates')],  # local
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.contrib.auth.context_processors.auth',
                'django.template.context_processors.debug',
                'django.template.context_processors.i18n',
                'django.template.context_processors.media',
                'django.template.context_processors.static',
                'django.template.context_processors.tz',
                'django.contrib.messages.context_processors.messages',
                'django.template.context_processors.request',  # local
                'cirs.context_processors.cirs_data',  # local
            ],
        },
    },
]

WSGI_APPLICATION = 'labcirs.wsgi.application'


# Database
# https://docs.djangoproject.com/en/1.9/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': get_local_setting('DB_ENGINE', 'django.db.backends.sqlite3'),
        'NAME': get_local_setting('DB_NAME', join_path(BASE_DIR, 'db.sqlite3')),
        # The following settings are not used with sqlite3:
        'USER': get_local_setting('DB_USER', ''),
        'PASSWORD': get_local_setting('DB_PASSWORD', ''),
        'HOST': get_local_setting('DB_HOST', ''),
        'PORT': get_local_setting('DB_PORT', ''),
    }
}

# Internationalization
# https://docs.djangoproject.com/en/1.9/topics/i18n/

_languages = get_local_setting('LANGUAGES')

LANGUAGES = tuple((k, _(v)) for k, v in _languages.items())

LANGUAGE_CODE = get_local_setting('LANGUAGE_CODE', default_language_code(_languages))

USE_I18N = True

USE_TZ = True

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Change in the local settings file only if LabCIRS is not mounted in the root of the web server
# needed for static and media url

ROOT_URL = get_local_setting('ROOT_URL', '')

LOGIN_URL = ROOT_URL + '/login/'

STATIC_URL = ROOT_URL + '/static/'

STATIC_ROOT = join_path(dirname(BASE_DIR), 'static')

TIME_ZONE = get_local_setting('TIME_ZONE', 'UTC')

STATICFILES_DIRS = (join_path(BASE_DIR, 'static'),)


# A QM account opens every report of its departments and the proxy only slows a guessing down,
# so a new password must be long and not a common one. Existing passwords stay valid.
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
     'OPTIONS': {'min_length': 12}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_AGE = 60 * 60 # one hour, not logged out users will leave a ghost session in db!
SESSION_SAVE_EVERY_REQUEST = True
# set to false in local_config.json if your server has no https!!!
SESSION_COOKIE_SECURE = get_bool_setting('SESSION_COOKIE_SECURE', True)

MEDIA_ROOT = join_path(dirname(BASE_DIR), 'media')
MEDIA_URL = ROOT_URL + '/media/'
# get local name of the organization. Default is LabCIRS if the value in the json file is empty
ORGANIZATION = get_local_setting('ORGANIZATION', 'LabCIRS')

# Branding. Logo and theme CSS live outside the repo, e.g. served by nginx under /branding/.
LOGO_URL = get_local_setting('LOGO_URL', '')  # empty: word mark from ORGANIZATION
THEME_CSS_URL = get_local_setting('THEME_CSS_URL', '')  # overrides the --ui-... colour tokens
IMPRINT_URL = get_local_setting('IMPRINT_URL', '')  # footer links are shown only when set
PRIVACY_URL = get_local_setting('PRIVACY_URL', '')
SOURCE_URL = get_local_setting('SOURCE_URL', 'https://github.com/pbaetz99/labcirs')  # AGPL
# Display name in page title, top bar, admin and footer
SITE_NAME = get_local_setting('SITE_NAME', 'LabCIRS')
# Address of the site as plain text in mails to reporters (never a link, the site is reachable
# from the intranet only), e.g. https://cirs.example.org. Empty: the mails carry no address.
SITE_URL = get_local_setting('SITE_URL', '')
# False: the reporting form does not ask for the consent to publish, incidents count as consented
# (public=True). Publishing is still up to the QM (PublishableIncident).
ASK_PUBLICATION_CONSENT = get_bool_setting('ASK_PUBLICATION_CONSENT', True)

# QM area. Days after which a report that is still new counts as "without processing".
QM_OVERDUE_DAYS = get_positive_int_setting('QM_OVERDUE_DAYS', 14)
# The smallest number that print view and CSV of the evaluations show: a number above 0 but below
# this is replaced by "< n". Raising it protects small units more.
REPORT_MIN_CELL = get_positive_int_setting('REPORT_MIN_CELL', 3)
if REPORT_MIN_CELL < 3:
    # "< 2" can only mean 1, and 1 hides nothing: the rule would promise more than it does
    raise ImproperlyConfigured(f'{ENV_PREFIX}REPORT_MIN_CELL must be at least 3, not {REPORT_MIN_CELL}')
# Folder with the status note of the last backup (admin start page). Empty: not set up.
BACKUP_STATUS_DIR = get_local_setting('BACKUP_STATUS_DIR', '')
if not isinstance(BACKUP_STATUS_DIR, str):
    raise ImproperlyConfigured(
        f'{ENV_PREFIX}BACKUP_STATUS_DIR must be a path as text, not {BACKUP_STATUS_DIR!r}')

# Email settings
EMAIL_HOST = get_local_setting('EMAIL_HOST', 'localhost')
EMAIL_HOST_PASSWORD = get_local_setting('EMAIL_HOST_PASSWORD', '')
EMAIL_HOST_USER = get_local_setting('EMAIL_HOST_USER', '')
EMAIL_PORT = get_local_setting('EMAIL_PORT', 25)
EMAIL_USE_TLS = get_bool_setting('EMAIL_USE_TLS', False)
EMAIL_USE_SSL = get_bool_setting('EMAIL_USE_SSL', False)
if EMAIL_USE_TLS and EMAIL_USE_SSL:
    # Django would only raise a ValueError when the first mail is sent.
    raise ImproperlyConfigured(
        f'{ENV_PREFIX}EMAIL_USE_TLS and {ENV_PREFIX}EMAIL_USE_SSL cannot both be true: '
        'STARTTLS (usually port 587) or implicit TLS (usually port 465), not both')
# Seconds. A mail server that does not answer would hold the reporter's request otherwise (the
# timeout is an OSError, notify_on_creation logs it and goes on).
EMAIL_TIMEOUT = get_local_setting('EMAIL_TIMEOUT', 10)
EMAIL_SUBJECT_PREFIX = '[LabCIRS] '

# Parler
PARLER_DEFAULT_LANGUAGE_CODE = get_local_setting('PARLER_DEFAULT_LANGUAGE_CODE', 'en')
# Parler would keep translations in the Django cache. The default cache lives in the memory of one
# process and gunicorn starts several workers, so an edit of the QM (login info, a published case)
# would stay invisible in the other workers for minutes. The tables are small: read them every time.
PARLER_ENABLE_CACHING = False

PARLER_LANGUAGES = {
    None: (
        tuple({'code': lang,} for lang in get_local_setting('PARLER_LANGUAGES'))
    ),
    'default': {
        'fallbacks': get_local_setting('PARLER_LANGUAGES'), # use all languages
        'hide_untranslated': False,   # the default; let .active_translations() return fallbacks too.
    }
}

ALL_LANGUAGES_MANDATORY_DEFAULT = get_bool_setting('ALL_LANGUAGES_MANDATORY_DEFAULT', True)

if ALL_LANGUAGES_MANDATORY_DEFAULT is True:
    DEFAULT_MANDATORY_LANGUAGES = get_local_setting('PARLER_LANGUAGES')
else:
    DEFAULT_MANDATORY_LANGUAGES = PARLER_DEFAULT_LANGUAGE_CODE

LOCALE_PATHS = [join_path(BASE_DIR, 'locale')]
DEFAULT_FROM_EMAIL = get_local_setting('DEFAULT_FROM_EMAIL', '')
# There is no ADMINS setting: LabCIRS sends no error mails (see LOGGING below), so nothing would
# read it. Errors go to the container log.


# --- Production operation: reverse proxy, static files, logging ---

# Set only if the proxy sets X-Forwarded-Proto (the bundled nginx does), otherwise clients could
# fake https.
BEHIND_PROXY = get_bool_setting('BEHIND_PROXY', False)
if BEHIND_PROXY:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Origins with scheme (and port, if not standard) that may send POST requests through the proxy,
# e.g. ["https://cirs.example.org"]. The proxy passes the host name without the port.
CSRF_TRUSTED_ORIGINS = get_local_setting('CSRF_TRUSTED_ORIGINS', [])
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
# Django's own page brings a <style> block and English text; this one is styled and translated.
CSRF_FAILURE_VIEW = 'cirs.views.csrf_failure'

# WhiteNoise serves the static files from STATIC_ROOT, directly behind the security middleware.
MIDDLEWARE.insert(MIDDLEWARE.index('django.middleware.security.SecurityMiddleware') + 1,
                  'whitenoise.middleware.WhiteNoiseMiddleware')
# production.py replaces the static files storage, the tests would need a manifest for it.
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}

# Logs go to stderr (the container log). The messages hold the URL path and tracebacks, never the
# client address or the user agent. gunicorn logs no requests, see docker/entrypoint.sh.
# The 'django' logger replaces Django's default handlers, in particular mail_admins: its error
# mails would carry the request data (user agent, headers, POST data) if ADMINS were ever set.
# In development (DEBUG) Django's default logging applies.
if not DEBUG:
    LOGGING = {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {'plain': {'format': '%(asctime)s %(levelname)s %(name)s %(message)s'}},
        'handlers': {'console': {'class': 'logging.StreamHandler', 'formatter': 'plain'}},
        'root': {'handlers': ['console'], 'level': 'WARNING'},
        'loggers': {'django': {'handlers': ['console'], 'level': 'INFO', 'propagate': False}},
    }
