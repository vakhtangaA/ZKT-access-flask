import os

try:
    import sentry_sdk
    from sentry_sdk.integrations.flask import FlaskIntegration
except ImportError:  # pragma: no cover - exercised when dependency is not installed yet.
    sentry_sdk = None
    FlaskIntegration = None


TRUTHY_VALUES = {'1', 'true', 'yes', 'on'}
DEFAULT_SENTRY_DSN = 'https://1217eef052a5412d32e9be184428749e@o4510356635910144.ingest.de.sentry.io/4511242078453840'
DEFAULT_SEND_DEFAULT_PII = True


def parse_bool(value, default=False):
    if value is None:
        return default

    return str(value).strip().lower() in TRUTHY_VALUES


def parse_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def initialize_sentry():
    dsn = os.getenv('SENTRY_DSN', DEFAULT_SENTRY_DSN)

    if not dsn or sentry_sdk is None or FlaskIntegration is None:
        return False

    sentry_sdk.init(
        dsn=dsn,
        integrations=[FlaskIntegration()],
        send_default_pii=parse_bool(os.getenv('SENTRY_SEND_DEFAULT_PII'), DEFAULT_SEND_DEFAULT_PII),
        traces_sample_rate=parse_float(os.getenv('SENTRY_TRACES_SAMPLE_RATE')),
        profile_session_sample_rate=parse_float(os.getenv('SENTRY_PROFILE_SESSION_SAMPLE_RATE')),
        profile_lifecycle=os.getenv('SENTRY_PROFILE_LIFECYCLE', 'trace'),
        environment=os.getenv('SENTRY_ENVIRONMENT'),
        release=os.getenv('SENTRY_RELEASE'),
    )

    return True


def capture_exception(exception, **context):
    if sentry_sdk is None:
        return

    with sentry_sdk.push_scope() as scope:
        for key, value in context.items():
            if value is not None:
                scope.set_tag(key, value)

        sentry_sdk.capture_exception(exception)
