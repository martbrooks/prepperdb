import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
DEVELOPMENT = os.environ.get("PREPPERDB_DEVELOPMENT") == "1"
DEBUG = False
https_option = os.environ.get("PREPPERDB_HTTPS", "0" if DEVELOPMENT else "1")
if https_option not in {"0", "1"}:
    raise ImproperlyConfigured("PREPPERDB_HTTPS must be 0 or 1.")
HTTPS = https_option == "1"
SECRET_KEY = os.environ.get("PREPPERDB_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEVELOPMENT:
        raise ImproperlyConfigured("PREPPERDB_SECRET_KEY is required.")
    SECRET_KEY = "development-only-key-never-use-for-production-1234567890"
if not DEVELOPMENT and (len(SECRET_KEY) < 50 or SECRET_KEY.startswith("development-only-")):
    raise ImproperlyConfigured(
        "PREPPERDB_SECRET_KEY must be a strong, production-only secret of at least 50 characters."
    )
ALLOWED_HOSTS = [
    s.strip()
    for s in os.environ.get(
        "PREPPERDB_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]" if DEVELOPMENT else ""
    ).split(",")
    if s.strip()
]
if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
    raise ImproperlyConfigured("Explicit PREPPERDB_ALLOWED_HOSTS are required.")
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("PREPPERDB_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]
TRUSTED_PROXIES = set(filter(None, os.environ.get("PREPPERDB_TRUSTED_PROXIES", "").split(",")))
STATE_DIR = Path(os.environ.get("PREPPERDB_STATE_DIR", BASE_DIR / "var")).resolve()
STATIC_ROOT = Path(os.environ.get("PREPPERDB_STATIC_ROOT", STATE_DIR / "static")).resolve()
DB_PATH = STATE_DIR / "stock.sqlite3"
if (
    STATIC_ROOT == BASE_DIR
    or BASE_DIR.is_relative_to(STATIC_ROOT)
    or DB_PATH.is_relative_to(STATIC_ROOT)
):
    raise ImproperlyConfigured("Static root must not expose source or database files.")
if not DEVELOPMENT and "PREPPERDB_STATE_DIR" not in os.environ:
    raise ImproperlyConfigured("PREPPERDB_STATE_DIR is required in production.")
STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DB_PATH,
        "OPTIONS": {
            "timeout": 5,
            "transaction_mode": "IMMEDIATE",
            "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;",
        },
        "TEST": {"NAME": STATE_DIR / "test.sqlite3"},
    }
}
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "stock",
]
MIDDLEWARE = [
    "stock.middleware.ProxyHeaders",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "stock.middleware.PrivateResponses",
]
ROOT_URLCONF = "prepperdb.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]
WSGI_APPLICATION = "prepperdb.wsgi.application"
AUTH_USER_MODEL = "stock.User"
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_NAME = "prepperdb_session"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = HTTPS
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 7
CSRF_COOKIE_SECURE = HTTPS
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_SSL_REDIRECT = HTTPS
SECURE_HSTS_SECONDS = 31536000 if HTTPS else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
# HSTS applies to these exact hosts. We do not control arbitrary child hosts,
# and an internal domain cannot join the public preload list.
SILENCED_SYSTEM_CHECKS = ["security.W005", "security.W021"]
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Europe/London"
USE_TZ = True
STATIC_URL = "/static/"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        if DEVELOPMENT
        else "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"
    },
}
# collectstatic runs under the deployment helper's private umask. Its disposable
# public asset directories still need to be traversable by nginx.
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o755
FILE_UPLOAD_PERMISSIONS = 0o644
STATICFILES_DIRS = [BASE_DIR / "static"]
STATICFILES_FINDERS = [
    "django.contrib.staticfiles.finders.FileSystemFinder",
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
    "stock.staticfiles.GeneratedImageFinder",
]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
DATA_UPLOAD_MAX_MEMORY_SIZE = 256 * 1024
LOGIN_LIMIT = 5
LOGIN_WINDOW_SECONDS = 15 * 60
