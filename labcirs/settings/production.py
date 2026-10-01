from .base import *

# Hashed and compressed static files with a manifest. Only here, because the tests would need
# a manifest (collectstatic) for it. The strict class: collectstatic fails on a reference to a
# file that does not exist (a template or stylesheet that still names a removed library).
STORAGES['staticfiles'] = {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'}
