from django.conf import settings

import cirs

BRANDING_SETTINGS = ('LOGO_URL', 'THEME_CSS_URL', 'IMPRINT_URL', 'PRIVACY_URL', 'SOURCE_URL',
                     'SITE_NAME')


def cirs_data(request):
    context = {'APP_VERSION': cirs.__version__,
               'ORGANIZATION': settings.ORGANIZATION,
               'SHOW_LANGUAGE_SWITCH': len(settings.LANGUAGES) > 1,
               }
    context.update((name, getattr(settings, name)) for name in BRANDING_SETTINGS)
    return context
