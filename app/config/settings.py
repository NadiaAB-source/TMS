"""Secure runtime configuration for IQARUS TMS.

Secrets and deployment switches are deliberately supplied through environment
variables. The application will not quietly use a repository secret outside
an explicitly enabled local-development session.
"""

import os
import sys
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured


BASE_DIR = Path(__file__).resolve().parent.parent


def env_flag(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def env_list(name, default=()):
    value = os.getenv(name)
    if value is None:
        return list(default)
    return [item.strip() for item in value.split(",") if item.strip()]


# Django's test client makes plain HTTP requests.  A host environment can set
# ``DEBUG=0`` and ``SECURE_DEPLOYMENT=1`` for production, so detect the test
# command independently instead of allowing those deployment switches to turn
# every test response into an HTTPS redirect.  This exception applies only to
# the process that runs ``manage.py test``; normal commands remain
# secure-by-default unless DEBUG is explicitly enabled.
RUNNING_TESTS = len(sys.argv) > 1 and sys.argv[1] == "test"
# Explicitly prefer Django's development static handling for the isolated test
# process.  A production shell may deliberately export DEBUG=0, which must not
# make a test run behave like a deployed HTTPS-only server.
DEBUG = True if RUNNING_TESTS else env_flag("DEBUG", False)
SECRET_KEY = os.getenv("SECRET_KEY", "").strip()
if not SECRET_KEY:
    if DEBUG:
        # This branch is intentionally unavailable unless a developer opts in.
        SECRET_KEY = "local-development-only-key-never-use-in-deployment"
    else:
        raise ImproperlyConfigured(
            "Set a strong SECRET_KEY environment variable before starting IQARUS TMS."
        )

COLAB_PREVIEW = env_flag("COLAB_PREVIEW", False)
default_allowed_hosts = ["localhost", "127.0.0.1", "[::1]"]
if COLAB_PREVIEW:
    default_allowed_hosts.extend(
        (
            ".colab.research.google.com",
            ".googleusercontent.com",
            ".colab.dev",
        )
    )
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", default_allowed_hosts)
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("ALLOWED_HOSTS must contain at least one host.")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "portal.apps.PortalConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "portal.module_access.ModuleAccessMiddleware",
    "portal.security.ForcePasswordChangeMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "portal.security.SecurityHeadersMiddleware",
]

ROOT_URLCONF = "config.urls"

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
                "portal.context_processors.access_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600,
        conn_health_checks=True,
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
]
PASSWORD_RESET_TIMEOUT = 60 * 60

LANGUAGE_CODE = "en"
TIME_ZONE = "Asia/Amman"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        # The manifest backend correctly fails closed in deployed processes if
        # a referenced asset was not collected.  Tests intentionally render
        # templates before ``collectstatic`` and therefore use Django's plain
        # static storage only for the isolated ``manage.py test`` process.
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if RUNNING_TESTS
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        ),
    },
}

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "my_courses"
LOGOUT_REDIRECT_URL = "login"
REMEMBER_ME_SESSION_AGE = int(
    os.getenv("REMEMBER_ME_SESSION_AGE", str(14 * 24 * 60 * 60))
)
SESSION_COOKIE_AGE = int(os.getenv("SESSION_COOKIE_AGE", str(8 * 60 * 60)))
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_SAVE_EVERY_REQUEST = False
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
LOGIN_MAX_FAILURES = int(os.getenv("LOGIN_MAX_FAILURES", "5"))
LOGIN_ATTEMPT_WINDOW_SECONDS = int(
    os.getenv("LOGIN_ATTEMPT_WINDOW_SECONDS", "900")
)
LOGIN_LOCK_SECONDS = int(os.getenv("LOGIN_LOCK_SECONDS", "900"))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Email credentials belong in environment variables or a managed secret store.
EMAIL_BACKEND = os.getenv(
    "EMAIL_BACKEND",
    "django.core.mail.backends.smtp.EmailBackend",
)
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.office365.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_flag("EMAIL_USE_TLS", True)
EMAIL_TIMEOUT = int(os.getenv("EMAIL_TIMEOUT", "15"))
DEFAULT_FROM_EMAIL = os.getenv(
    "DEFAULT_FROM_EMAIL",
    "IQARUS Training <no-reply@iqarus.com>",
)

# Only configure trusted origins that are needed for the UI host. Colab hosts
# are enabled only when the notebook opts into COLAB_PREVIEW=1.
default_csrf_trusted_origins = ()
if COLAB_PREVIEW:
    default_csrf_trusted_origins = (
        "https://*.colab.research.google.com",
        "https://*.colab.dev",
        "https://*.googleusercontent.com",
    )
CSRF_TRUSTED_ORIGINS = env_list(
    "CSRF_TRUSTED_ORIGINS",
    default_csrf_trusted_origins,
)

# Forwarded headers are accepted only when an operator explicitly enables this
# for a known reverse proxy. Never enable this for a directly exposed server.
TRUSTED_PROXY_IPS = env_list("TRUSTED_PROXY_IPS")
if env_flag("TRUST_PROXY_HEADERS", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True

# Tests must not inherit HTTPS redirect, secure-cookie, or HSTS settings from
# a production-like shell environment.  Django creates an isolated test
# database and test client for this command only; deployed processes still
# require HTTPS by default.
SECURE_DEPLOYMENT = (
    False if RUNNING_TESTS else env_flag("SECURE_DEPLOYMENT", not DEBUG)
)
SECURE_SSL_REDIRECT = SECURE_DEPLOYMENT
SESSION_COOKIE_SECURE = SECURE_DEPLOYMENT
CSRF_COOKIE_SECURE = SECURE_DEPLOYMENT
SECURE_HSTS_SECONDS = 31_536_000 if SECURE_DEPLOYMENT else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_DEPLOYMENT
SECURE_HSTS_PRELOAD = False
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# Existing screens contain small inline style and UI scripts. The policy keeps
# all executable and remote content same-origin while allowing those existing
# local UI blocks. Tighten further with nonces during a future CSP refactor.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "object-src 'none'; "
    "img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self' 'unsafe-inline'; "
    "font-src 'self'; "
    "connect-src 'self'"
)

DATA_UPLOAD_MAX_MEMORY_SIZE = 27 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 27 * 1024 * 1024

# Clean-start choice approved 26 September 2026.
IQARUS_CHRONOLOGICAL_NUMBERING = True
