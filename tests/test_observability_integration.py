import pathlib
import sys
import unittest
from unittest.mock import MagicMock, patch

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import observability


class ObservabilityIntegrationTest(unittest.TestCase):
    def test_initialize_sentry_returns_false_when_sdk_is_unavailable(self):
        with patch.dict('os.environ', {}, clear=True), patch.object(observability, 'sentry_sdk', None), patch.object(
            observability,
            'FlaskIntegration',
            MagicMock(),
        ):
            self.assertFalse(observability.initialize_sentry())

    def test_initialize_sentry_bootstraps_sdk_when_dsn_is_present(self):
        sentry_sdk = MagicMock()
        flask_integration = MagicMock(return_value='flask-integration')

        with patch.dict(
            'os.environ',
            {
                'SENTRY_DSN': 'https://examplePublicKey@o0.ingest.sentry.io/0',
                'SENTRY_SEND_DEFAULT_PII': 'true',
                'SENTRY_TRACES_SAMPLE_RATE': '0.25',
                'SENTRY_PROFILE_SESSION_SAMPLE_RATE': '0.1',
                'SENTRY_ENVIRONMENT': 'production',
                'SENTRY_RELEASE': 'zkt-access-flask@1.2.3',
            },
            clear=True,
        ), patch.object(observability, 'sentry_sdk', sentry_sdk), patch.object(
            observability,
            'FlaskIntegration',
            flask_integration,
        ):
            self.assertTrue(observability.initialize_sentry())

        flask_integration.assert_called_once_with()
        sentry_sdk.init.assert_called_once_with(
            dsn='https://examplePublicKey@o0.ingest.sentry.io/0',
            integrations=['flask-integration'],
            send_default_pii=True,
            traces_sample_rate=0.25,
            profile_session_sample_rate=0.1,
            profile_lifecycle='trace',
            environment='production',
            release='zkt-access-flask@1.2.3',
        )

    def test_capture_exception_sets_tags_before_sending(self):
        scope = MagicMock()
        sentry_sdk = MagicMock()
        sentry_sdk.push_scope.return_value.__enter__.return_value = scope

        with patch.object(observability, 'sentry_sdk', sentry_sdk):
            error = RuntimeError('boom')
            observability.capture_exception(error, operation='add_user', device_ip='10.0.0.15', model=None)

        scope.set_tag.assert_any_call('operation', 'add_user')
        scope.set_tag.assert_any_call('device_ip', '10.0.0.15')
        sentry_sdk.capture_exception.assert_called_once_with(error)
