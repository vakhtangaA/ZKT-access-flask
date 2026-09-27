import pathlib
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
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

    def test_restart_device_calls_sdk_restart(self):
        zk_instance = MagicMock()
        successful_context = MagicMock()
        successful_context.__enter__.return_value = zk_instance
        successful_context.__exit__.return_value = False

        with patch('main.ZKAccess', return_value=successful_context) as zkteco, patch(
            'main.get_local_time',
            return_value='2026-04-17 00:00:00',
        ), patch('main.open', mock_open()):
            result = main.restart_device('10.0.0.15', 4370, timeout=9000, model='C3-400')

        self.assertTrue(result)
        zkteco.assert_called_once_with(
            connstr='protocol=TCP,ipaddress=10.0.0.15,port=4370,timeout=9000,passwd=',
            device_model=main.ZK400,
        )
        zk_instance.restart.assert_called_once_with()

    def test_restart_device_returns_false_when_sdk_fails(self):
        with patch('main.ZKAccess', side_effect=Exception('restart failed')), patch(
            'main.capture_exception'
        ) as capture_exception, patch('main.get_local_time', return_value='2026-04-17 00:00:00'), patch(
            'main.open',
            mock_open(),
        ):
            result = main.restart_device('10.0.0.15', 4370, model='C3-400')

        self.assertFalse(result)
        capture_exception.assert_called_once()

    def test_check_device_connects_without_reading_or_mutating_data(self):
        successful_context = MagicMock()
        successful_context.__enter__.return_value = MagicMock()
        successful_context.__exit__.return_value = False

        with patch('main.ZKAccess', return_value=successful_context) as zkteco, patch(
            'main.get_local_time',
            return_value='2026-04-17 00:00:00',
        ), patch('main.open', mock_open()):
            result = main.check_device('10.0.0.15', 4370, timeout=9000, model='C3-400')

        self.assertTrue(result)
        zkteco.assert_called_once_with(
            connstr='protocol=TCP,ipaddress=10.0.0.15,port=4370,timeout=9000,passwd=',
            device_model=main.ZK400,
        )

    def test_check_device_returns_false_when_sdk_fails(self):
        with patch('main.ZKAccess', side_effect=Exception('health check failed')), patch(
            'main.capture_exception'
        ) as capture_exception, patch('main.get_local_time', return_value='2026-04-17 00:00:00'), patch(
            'main.open',
            mock_open(),
        ):
            result = main.check_device('10.0.0.15', 4370, model='C3-400')

        self.assertFalse(result)
        capture_exception.assert_called_once()

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

    def test_add_users_upserts_user_and_authorization_records_in_one_connection(self):
        zk_instance = MagicMock()
        user_table = MagicMock()
        authorization_table = MagicMock()
        zk_instance.table.side_effect = lambda name: {
            'User': user_table,
            'UserAuthorize': authorization_table,
        }[name]
        successful_context = MagicMock()
        successful_context.__enter__.return_value = zk_instance
        successful_context.__exit__.return_value = False

        users = [
            {'card': '12345', 'pin': '54321', 'doors': [1, 3]},
            {'card': '12346', 'pin': '54322', 'doors': [2]},
        ]

        with patch('main.ZKAccess', return_value=successful_context) as zkteco, patch(
            'main.get_local_time',
            return_value='2026-04-17 00:00:00',
        ), patch('main.open', mock_open()), patch('main.print'):
            result = main.add_users(
                users,
                '10.0.0.15',
                4370,
                timeout=9000,
                model='C3-400',
                operation_id='operation-1',
            )

        self.assertTrue(result['success'])
        self.assertEqual('operation-1', result['operation_id'])
        self.assertEqual(2, result['succeeded'])
        self.assertEqual(1, zkteco.call_count)
        zk_instance.table.assert_any_call('User')
        zk_instance.table.assert_any_call('UserAuthorize')
        user_table.upsert.assert_called_once()
        authorization_table.upsert.assert_called_once()

    def test_add_users_retries_only_after_a_failed_sdk_operation(self):
        successful_context = MagicMock()
        successful_context.__enter__.return_value = MagicMock()
        successful_context.__exit__.return_value = False

        with patch('main.ZKAccess', side_effect=[Exception('temporary failure'), successful_context]) as zkteco, patch(
            'main.time.sleep'
        ) as sleep, patch('main.get_local_time', return_value='2026-04-17 00:00:00'), patch(
            'main.open',
            mock_open(),
        ), patch('main.print'):
            result = main.add_users(
                [{'card': '12345', 'pin': '54321'}],
                '10.0.0.15',
                4370,
                operation_id='operation-2',
            )

        self.assertTrue(result['success'])
        self.assertEqual(2, zkteco.call_count)
        sleep.assert_called_once_with(0.5)

    def test_delete_users_deletes_records_and_narrows_kept_doors_in_one_connection(self):
        zk_instance = MagicMock()
        user_table = MagicMock()
        authorization_table = MagicMock()
        zk_instance.table.side_effect = lambda name: {
            'User': user_table,
            'UserAuthorize': authorization_table,
        }[name]
        successful_context = MagicMock()
        successful_context.__enter__.return_value = zk_instance
        successful_context.__exit__.return_value = False

        users = [
            {'card': '12345', 'pin': '54321', 'doors': [1]},
            {'card': '12346', 'pin': '54322'},
        ]

        with patch('main.ZKAccess', return_value=successful_context) as zkteco, patch(
            'main.get_local_time',
            return_value='2026-04-17 00:00:00',
        ), patch('main.open', mock_open()), patch('main.print'):
            result = main.delete_users(
                users,
                '10.0.0.15',
                4370,
                timeout=9000,
                model='C3-400',
                operation_id='operation-9',
            )

        self.assertTrue(result['success'])
        self.assertEqual('operation-9', result['operation_id'])
        self.assertEqual(2, result['succeeded'])
        self.assertEqual(1, result['rewritten'])
        self.assertEqual(1, zkteco.call_count)

        # A user who keeps doors is never deleted, so a failed write cannot drop them from the device.
        deleted_records = user_table.delete.call_args[0][0]
        self.assertEqual(['54322'], [record['pin'] for record in deleted_records])

        rewritten_records = user_table.upsert.call_args[0][0]
        self.assertEqual(['54321'], [record['pin'] for record in rewritten_records])
        self.assertEqual(
            [(True, False, False, False)],
            [record['doors'] for record in authorization_table.upsert.call_args[0][0]],
        )

    def test_delete_users_does_not_rewrite_when_no_doors_are_kept(self):
        zk_instance = MagicMock()
        user_table = MagicMock()
        authorization_table = MagicMock()
        zk_instance.table.side_effect = lambda name: {
            'User': user_table,
            'UserAuthorize': authorization_table,
        }[name]
        successful_context = MagicMock()
        successful_context.__enter__.return_value = zk_instance
        successful_context.__exit__.return_value = False

        with patch('main.ZKAccess', return_value=successful_context), patch(
            'main.get_local_time',
            return_value='2026-04-17 00:00:00',
        ), patch('main.open', mock_open()), patch('main.print'):
            result = main.delete_users(
                [{'card': '12345', 'pin': '54321'}],
                '10.0.0.15',
                4370,
                operation_id='operation-10',
            )

        self.assertTrue(result['success'])
        self.assertEqual(0, result['rewritten'])
        user_table.delete.assert_called_once()
        user_table.upsert.assert_not_called()
        authorization_table.upsert.assert_not_called()

    def test_delete_users_keeps_the_user_on_the_device_when_narrowing_fails(self):
        zk_instance = MagicMock()
        user_table = MagicMock()
        authorization_table = MagicMock()
        authorization_table.upsert.side_effect = Exception('write failed')
        zk_instance.table.side_effect = lambda name: {
            'User': user_table,
            'UserAuthorize': authorization_table,
        }[name]
        failing_context = MagicMock()
        failing_context.__enter__.return_value = zk_instance
        failing_context.__exit__.return_value = False

        with patch('main.ZKAccess', return_value=failing_context), patch('main.time.sleep'), patch(
            'main.capture_exception'
        ), patch('main.get_local_time', return_value='2026-04-17 00:00:00'), patch(
            'main.open',
            mock_open(),
        ), patch('main.print'):
            result = main.delete_users(
                [{'card': '12345', 'pin': '54321', 'doors': [1]}],
                '10.0.0.15',
                4370,
                operation_id='operation-12',
            )

        self.assertFalse(result['success'])
        user_table.delete.assert_not_called()

    def test_delete_users_reports_every_card_as_failed_after_exhausting_retries(self):
        with patch('main.ZKAccess', side_effect=Exception('device offline')) as zkteco, patch(
            'main.time.sleep'
        ) as sleep, patch('main.capture_exception') as capture_exception, patch(
            'main.get_local_time',
            return_value='2026-04-17 00:00:00',
        ), patch('main.open', mock_open()), patch('main.print'):
            result = main.delete_users(
                [{'card': '12345', 'pin': '54321'}, {'card': '12346', 'pin': '54322'}],
                '10.0.0.15',
                4370,
                operation_id='operation-11',
            )

        self.assertFalse(result['success'])
        self.assertEqual(2, result['failed'])
        self.assertEqual(0, result['succeeded'])
        self.assertTrue(all(item['success'] is False for item in result['results']))
        self.assertEqual(main.MAX_WRITE_ATTEMPTS, zkteco.call_count)
        sleep.assert_called_once_with(0.5)
        capture_exception.assert_called_once()

    def test_add_user_serializes_same_device_requests_when_called_directly(self):
        state = {
            'current': 0,
            'max_concurrent': 0,
        }
        state_lock = threading.Lock()
        first_started = threading.Event()

        class FakeZkContext:
            def __enter__(self_inner):
                with state_lock:
                    state['current'] += 1
                    state['max_concurrent'] = max(state['max_concurrent'], state['current'])
                    first_started.set()

                time.sleep(0.05)

                zk_instance = MagicMock()
                zk_instance.table.return_value.where.return_value.delete_all.return_value = None

                return zk_instance

            def __exit__(self_inner, exc_type, exc, tb):
                with state_lock:
                    state['current'] -= 1

                return False

        fake_user = MagicMock()
        fake_user.with_zk.return_value = fake_user
        fake_user_authorize = MagicMock()
        fake_user_authorize.with_zk.return_value = fake_user_authorize

        with patch('main.ZKAccess', side_effect=lambda *args, **kwargs: FakeZkContext()), patch(
            'main.User',
            return_value=fake_user,
        ), patch(
            'main.UserAuthorize',
            return_value=fake_user_authorize,
        ), patch('main.get_local_time', return_value='2026-04-17 00:00:00'), patch('main.open', mock_open()), patch(
            'main.print'
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first_call = executor.submit(main.add_user, '12345', '54321', '10.0.0.15', 4370, [1, 2])
                self.assertTrue(first_started.wait(timeout=1))
                second_call = executor.submit(main.add_user, '12346', '54322', '10.0.0.15', 4370, [1, 2])

                self.assertTrue(first_call.result(timeout=1))
                self.assertTrue(second_call.result(timeout=1))

        self.assertEqual(1, state['max_concurrent'])

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
