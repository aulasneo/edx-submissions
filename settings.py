import os
import warnings

from django.core.cache import CacheKeyWarning
from django.utils.crypto import get_random_string

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEBUG = True
TEMPLATE_DEBUG = DEBUG

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': 'submissions_db',
        'TEST': {
            'NAME': 'submissions_test_db',
        }
    },
    'read_replica': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': 'submissions_read_replica_db',
        'TEST': {
            'MIRROR': 'default',
        },
    },
}

# New DB primary keys default to an IntegerField.
DEFAULT_AUTO_FIELD = 'django.db.models.AutoField'

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'default_loc_mem',
    },
}

ROOT_URLCONF = 'urls'
SITE_ID = 1
USE_TZ = True

SECRET_KEY = get_random_string(50, 'abcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*(-_=+)')

# Silence cache key warnings
# https://docs.djangoproject.com/en/1.4/topics/cache/#cache-key-warnings
warnings.simplefilter("ignore", CacheKeyWarning)

INSTALLED_APPS = (
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.sites',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.admin',
    'django.contrib.admindocs',
    'release_util',

    # Submissions
    'submissions'
)

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'OPTIONS': {
            'context_processors': [
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

MIDDLEWARE = (
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware'
)

TEST_APPS = ('submissions',)

# URL to use when referring to static files located in STATIC_ROOT.
STATIC_URL = '/static/'

# Additional locations of static files
STATICFILES_DIRS = [
    # Add paths to your static files directories here
    # Example: os.path.join(BASE_DIR, "static"),
]

# The absolute path to the directory where collectstatic will collect static files for deployment.
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

# URL to use when referring to media files located in MEDIA_ROOT.
MEDIA_URL = '/media/'

# The absolute path to the directory where media files will be stored.
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')
