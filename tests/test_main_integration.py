import pathlib
import sys
import unittest
from unittest.mock import MagicMock, mock_open, patch

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import main


class MainDeviceIntegrationTest(unittest.TestCase):
    def test_resolve_device_model_maps_aliases_and_defaults(self):
        self.assertIs(main.ZK100, main.resolve_device_model('c3-100'))
        self.assertIs(main.ZK200, main.resolve_device_model('acp-200'))
        self.assertIs(main.ZK400, main.resolve_device_model('zk400'))
        self.assertIs(main.ZK200, main.resolve_device_model(None))
        self.assertIs(main.ZK200, main.resolve_device_model('unknown-model'))

    def test_build_connstr_normalizes_timeout_and_password(self):
        self.assertEqual(
            'protocol=TCP,ipaddress=10.0.0.15,port=4370,timeout=4000,passwd=',
            main.build_connstr('10.0.0.15', 4370, 'not-a-number', None),
        )

    def test_delete_user_retries_and_succeeds_on_second_attempt(self):
        successful_context = MagicMock()
        successful_context.__enter__.return_value = MagicMock()
        successful_context.__exit__.return_value = False

        with patch('main.ZKAccess', side_effect=[Exception('first failure'), successful_context]) as zkteco, patch(
            'main.ping_host',
            return_value='Ping successful',
        ), patch('main.get_local_time', return_value='2026-04-17 00:00:00'), patch('main.open', mock_open()), patch(
            'main.print'
        ):
            result = main.delete_user('12345', '54321', '10.0.0.15', 4370)

        self.assertTrue(result)
        self.assertEqual(2, zkteco.call_count)

    def test_delete_user_returns_false_after_two_failed_attempts(self):
        with patch('main.ZKAccess', side_effect=[Exception('first failure'), Exception('second failure')]) as zkteco, patch(
            'main.ping_host',
            return_value='Ping successful',
        ), patch('main.capture_exception') as capture_exception, patch(
            'main.get_local_time',
            return_value='2026-04-17 00:00:00',
        ), patch('main.time.sleep'), patch('main.open', mock_open()), patch(
            'main.print'
        ):
            result = main.delete_user('12345', '54321', '10.0.0.15', 4370)

        self.assertFalse(result)
        self.assertEqual(2, zkteco.call_count)
        capture_exception.assert_called_once()

    def test_get_users_retries_and_succeeds_on_second_attempt(self):
        zk_instance = MagicMock()
        zk_instance.table.return_value = [
            MagicMock(pin='200', card='100'),
            MagicMock(pin='201', card='101'),
        ]

        successful_context = MagicMock()
        successful_context.__enter__.return_value = zk_instance
        successful_context.__exit__.return_value = False

        with patch('main.ZKAccess', side_effect=[Exception('first failure'), successful_context]) as zkteco, patch(
            'main.ping_host',
            return_value='Ping successful',
        ), patch('main.write_log'), patch('main.get_local_time', return_value='2026-04-17 00:00:00'), patch(
            'main.open',
            mock_open(),
        ), patch('main.print'):
            result = main.get_users('10.0.0.15', 4370)

        self.assertEqual(
            {
                '200': {'card': '100', 'pin': '200'},
                '201': {'card': '101', 'pin': '201'},
            },
            result,
        )
        self.assertEqual(2, zkteco.call_count)

    def test_get_users_returns_empty_dict_after_two_failures(self):
        with patch('main.ZKAccess', side_effect=[Exception('first failure'), Exception('second failure')]) as zkteco, patch(
            'main.ping_host',
            return_value='Ping successful',
        ), patch('main.capture_exception') as capture_exception, patch('main.write_log'), patch(
            'main.get_local_time',
            return_value='2026-04-17 00:00:00',
        ), patch('main.time.sleep'), patch(
            'main.open',
            mock_open(),
        ), patch('main.print'):
            result = main.get_users('10.0.0.15', 4370)

        self.assertEqual({}, result)
        self.assertEqual(2, zkteco.call_count)
        capture_exception.assert_called_once()

    def test_add_user_builds_expected_door_access_tuple(self):
        zk_instance = MagicMock()
        successful_context = MagicMock()
        successful_context.__enter__.return_value = zk_instance
        successful_context.__exit__.return_value = False
        user_authorize = MagicMock()
        user_authorize.with_zk.return_value = user_authorize

        with patch('main.ZKAccess', return_value=successful_context), patch('main.UserAuthorize', return_value=user_authorize) as authorize_class, patch(
            'main.get_local_time',
            return_value='2026-04-17 00:00:00',
        ), patch(
            'main.open',
            mock_open(),
        ), patch('main.print'):
            result = main.add_user('12345', '54321', '10.0.0.15', 4370, [1, 3])

        self.assertTrue(result)
        authorize_class.assert_called_once_with(pin='54321', timezone_id=1, doors=(True, False, True, False))

    def test_add_user_reports_to_sentry_after_two_failed_attempts(self):
        with patch('main.ZKAccess', side_effect=[Exception('first failure'), Exception('second failure')]) as zkteco, patch(
            'main.ping_host',
            return_value='Ping successful',
        ), patch('main.capture_exception') as capture_exception, patch(
            'main.get_local_time',
            return_value='2026-04-17 00:00:00',
        ), patch('main.time.sleep'), patch('main.open', mock_open()), patch('main.print'):
            result = main.add_user('12345', '54321', '10.0.0.15', 4370, [1, 3])

        self.assertFalse(result)
        self.assertEqual(2, zkteco.call_count)
        capture_exception.assert_called_once()

    def test_delete_user_uses_custom_timeout_password_and_model(self):
        successful_context = MagicMock()
        successful_context.__enter__.return_value = MagicMock()
        successful_context.__exit__.return_value = False

        with patch('main.ZKAccess', return_value=successful_context) as zkteco, patch(
            'main.get_local_time',
            return_value='2026-04-17 00:00:00',
        ), patch('main.open', mock_open()), patch('main.print'):
            result = main.delete_user(
                '12345',
                '54321',
                '10.0.0.15',
                4370,
                timeout=10000,
                password='secret',
                model='C3-400',
            )

        self.assertTrue(result)
        zkteco.assert_called_once_with(
            connstr='protocol=TCP,ipaddress=10.0.0.15,port=4370,timeout=10000,passwd=secret',
            device_model=main.ZK400,
        )
