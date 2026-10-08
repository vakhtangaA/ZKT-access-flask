import pathlib
import sys
import unittest
from unittest.mock import MagicMock, mock_open, patch

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app as app_module
import main
import queue_manager


class FlaskRouteIntegrationTest(unittest.TestCase):
    AUTH_HEADERS = {'Authorization': 'Bearer test-secret'}

    @classmethod
    def setUpClass(cls):
        app_module.app.config['TESTING'] = True
        cls.client = app_module.app.test_client()
        cls.shared_secret_patcher = patch('app.get_shared_secret', return_value='test-secret')
        cls.shared_secret_patcher.start()

    @classmethod
    def tearDownClass(cls):
        cls.shared_secret_patcher.stop()

    def test_users_with_doors_route_returns_users_and_their_doors(self):
        expected_users = {
            '200': {'card': '100', 'pin': '200', 'doors': [1, 3]},
        }

        with patch('app.get_users_with_doors', return_value=expected_users) as get_users_with_doors:
            response = self.client.post('/controller/users/doors/', headers=self.AUTH_HEADERS, json={
                'ip': '10.0.0.15',
                'port': 4370,
                'model': 'C3-400',
            })

        self.assertEqual(200, response.status_code)
        self.assertTrue(response.get_json()['success'])
        self.assertEqual(expected_users, response.get_json()['users'])
        get_users_with_doors.assert_called_once_with(
            '10.0.0.15',
            4370,
            timeout=10000,
            password='',
            model='C3-400',
        )

    def test_users_with_doors_route_reports_unreadable_device(self):
        with patch('app.get_users_with_doors', return_value=None):
            response = self.client.post('/controller/users/doors/', headers=self.AUTH_HEADERS, json={
                'ip': '10.0.0.15',
            })

        self.assertEqual(502, response.status_code)
        self.assertFalse(response.get_json()['success'])

    def test_users_with_doors_route_requires_ip(self):
        response = self.client.post('/controller/users/doors/', headers=self.AUTH_HEADERS, json={})

        self.assertEqual(422, response.status_code)

    def test_transactions_route_returns_events_for_a_pin(self):
        expected_events = [
            {'time': '2026-10-03 23:50:00', 'pin': '500080', 'card': '504438413', 'door': '2', 'event_code': 23, 'event': 'Access Denied'},
        ]

        with patch('app.get_transactions', return_value=expected_events) as get_transactions:
            response = self.client.post('/controller/transactions/', headers=self.AUTH_HEADERS, json={
                'ip': '10.0.0.15',
                'port': 4370,
                'model': 'C3-400',
                'pin': '500080',
                'limit': 10,
            })

        self.assertEqual(200, response.status_code)
        self.assertTrue(response.get_json()['success'])
        self.assertEqual(expected_events, response.get_json()['transactions'])
        get_transactions.assert_called_once_with(
            '10.0.0.15',
            4370,
            timeout=10000,
            password='',
            model='C3-400',
            pin='500080',
            card=None,
            limit=10,
            event_codes=None,
            date_from=None,
            date_to=None,
        )

    def test_transactions_route_requires_ip(self):
        response = self.client.post('/controller/transactions/', headers=self.AUTH_HEADERS, json={'pin': '500080'})

        self.assertEqual(422, response.status_code)

    def test_transactions_route_lists_every_event_without_a_pin_or_card(self):
        with patch('app.get_transactions', return_value=[]) as get_transactions:
            response = self.client.post('/controller/transactions/', headers=self.AUTH_HEADERS, json={'ip': '10.0.0.15'})

        self.assertEqual(200, response.status_code)
        get_transactions.assert_called_once_with(
            '10.0.0.15',
            4370,
            timeout=10000,
            password='',
            model=None,
            pin=None,
            card=None,
            limit=50,
            event_codes=None,
            date_from=None,
            date_to=None,
        )

    def test_transactions_route_reports_unreadable_device(self):
        with patch('app.get_transactions', return_value=None):
            response = self.client.post('/controller/transactions/', headers=self.AUTH_HEADERS, json={
                'ip': '10.0.0.15',
                'pin': '500080',
            })

        self.assertEqual(502, response.status_code)
        self.assertFalse(response.get_json()['success'])

    def test_transactions_route_passes_event_codes_and_rejects_bad_ones(self):
        with patch('app.get_transactions', return_value=[]) as get_transactions:
            accepted = self.client.post('/controller/transactions/', headers=self.AUTH_HEADERS, json={
                'ip': '10.0.0.15',
                'event_codes': [23, '27'],
            })
            rejected = self.client.post('/controller/transactions/', headers=self.AUTH_HEADERS, json={
                'ip': '10.0.0.15',
                'event_codes': ['denied'],
            })

        self.assertEqual(200, accepted.status_code)
        self.assertEqual([23, 27], get_transactions.call_args.kwargs['event_codes'])
        self.assertEqual(422, rejected.status_code)
        get_transactions.assert_called_once()

    def test_set_users_route_rejects_doors_that_would_not_mean_a_specific_set(self):
        for doors in ([], '1', [0], [True], {'1': 1}):
            with self.subTest(doors=doors), patch('app.add_users') as add_users:
                response = self.client.post('/controller/users/set/', headers=self.AUTH_HEADERS, json={
                    'ip': '10.0.0.15',
                    'users': [{'card': '12345', 'pin': '54321', 'doors': doors}],
                })

                self.assertEqual(422, response.status_code)
                self.assertFalse(response.get_json()['success'])
                add_users.assert_not_called()

    def test_transactions_route_passes_dates_and_rejects_bad_ones(self):
        with patch('app.get_transactions', return_value=[]) as get_transactions:
            accepted = self.client.post('/controller/transactions/', headers=self.AUTH_HEADERS, json={
                'ip': '10.0.0.15',
                'date_from': '2026-10-01',
                'date_to': '2026-10-03 12:00:00',
            })
            rejected = [
                self.client.post('/controller/transactions/', headers=self.AUTH_HEADERS, json={'ip': '10.0.0.15', 'date_from': value})
                for value in ('yesterday', '2026-13-40', '01.10.2026')
            ]

        self.assertEqual(200, accepted.status_code)
        self.assertEqual('2026-10-01', get_transactions.call_args.kwargs['date_from'])
        self.assertEqual('2026-10-03 12:00:00', get_transactions.call_args.kwargs['date_to'])
        self.assertEqual([422, 422, 422], [response.status_code for response in rejected])
        get_transactions.assert_called_once()

    def test_home_route_returns_success(self):
        with patch('app.get_local_time', return_value='2026-04-17 00:00:00'), patch('app.open', mock_open()), patch('app.print'):
            response = self.client.get('/')

        self.assertEqual(200, response.status_code)
        self.assertTrue(response.get_json()['success'])

    def test_controller_routes_require_bearer_token(self):
        protected_routes = [
            '/ping/',
            '/controller/users/set/',
            '/controller/users/remove/',
            '/controller/users/',
            '/controller/users/doors/',
            '/controller/transactions/',
            '/controller/restart/',
            '/controller/health/',
        ]

        for route in protected_routes:
            with self.subTest(route=route):
                response = self.client.post(route, json={})

                self.assertEqual(401, response.status_code)
                self.assertFalse(response.get_json()['success'])

    def test_restart_route_requires_bearer_token(self):
        with patch('app.get_shared_secret', return_value='test-secret'):
            response = self.client.post('/controller/restart/', json={
                'ip': '10.0.0.15',
                'port': 4370,
            })

        self.assertEqual(401, response.status_code)
        self.assertFalse(response.get_json()['success'])

    def test_restart_route_forwards_controller_settings(self):
        with patch('app.get_shared_secret', return_value='test-secret'), patch(
            'app.restart_device',
            return_value=True,
        ) as restart_device:
            response = self.client.post('/controller/restart/', headers={
                'Authorization': 'Bearer test-secret',
            }, json={
                'ip': '10.0.0.15',
                'port': 4370,
                'timeout': 9000,
                'password': 'secret',
                'model': 'C3-400',
            })

        self.assertEqual(200, response.status_code)
        self.assertTrue(response.get_json()['success'])
        restart_device.assert_called_once_with(
            ip='10.0.0.15',
            port=4370,
            timeout=9000,
            password='secret',
            model='C3-400',
        )

    def test_health_route_requires_bearer_token(self):
        with patch('app.get_shared_secret', return_value='test-secret'):
            response = self.client.post('/controller/health/', json={
                'ip': '10.0.0.15',
                'port': 4370,
            })

        self.assertEqual(401, response.status_code)
        self.assertFalse(response.get_json()['success'])

    def test_health_route_checks_controller_with_bearer_token(self):
        with patch('app.get_shared_secret', return_value='test-secret'), patch(
            'app.check_device',
            return_value={'online': True, 'error': None},
        ) as check_device:
            response = self.client.post('/controller/health/', headers={
                'Authorization': 'Bearer test-secret',
            }, json={
                'ip': '10.0.0.15',
                'port': 4370,
                'timeout': 9000,
                'password': 'secret',
                'model': 'C3-400',
            })

        self.assertEqual(200, response.status_code)
        self.assertTrue(response.get_json()['success'])
        check_device.assert_called_once_with(
            ip='10.0.0.15',
            port=4370,
            timeout=9000,
            password='secret',
            model='C3-400',
        )

    def test_health_route_uses_short_default_device_timeout(self):
        with patch('app.get_shared_secret', return_value='test-secret'), patch(
            'app.check_device',
            return_value={'online': True, 'error': None},
        ) as check_device:
            response = self.client.post('/controller/health/', headers={
                'Authorization': 'Bearer test-secret',
            }, json={
                'ip': '10.0.0.15',
                'port': 4370,
                'model': 'C3-400',
            })

        self.assertEqual(200, response.status_code)
        check_device.assert_called_once_with(
            ip='10.0.0.15',
            port=4370,
            timeout=5000,
            password='',
            model='C3-400',
        )

    def test_health_route_returns_the_device_error_as_the_message(self):
        with patch('app.get_shared_secret', return_value='test-secret'), patch(
            'app.check_device',
            return_value={'online': False, 'error': 'SDK error -307: Connection attempt failed'},
        ):
            response = self.client.post('/controller/health/', headers={
                'Authorization': 'Bearer test-secret',
            }, json={
                'ip': '10.0.0.15',
                'port': 4370,
            })

        self.assertEqual(200, response.status_code)
        self.assertEqual({
            'success': False,
            'message': 'SDK error -307: Connection attempt failed',
        }, response.get_json())

    def test_ping_route_returns_ping_result(self):
        with patch('app.ping_host_endpoint', return_value=False) as ping_host_endpoint, patch(
            'app.get_local_time',
            return_value='2026-04-17 00:00:00',
        ), patch('app.open', mock_open()), patch('app.print'):
            response = self.client.post('/ping/', headers=self.AUTH_HEADERS, json={
                'ip': '10.0.0.15',
            })

        self.assertEqual(200, response.status_code)
        self.assertFalse(response.get_json()['success'])
        ping_host_endpoint.assert_called_once_with('10.0.0.15')

    def test_users_route_forwards_optional_device_settings_and_returns_users_payload(self):
        expected_users = {
            '200': {
                'card': '100',
                'pin': '200',
            },
        }

        with patch('app.get_users', return_value=expected_users) as get_users, patch(
            'app.get_local_time',
            return_value='2026-04-17 00:00:00',
        ), patch('app.open', mock_open()), patch('app.print'):
            response = self.client.post('/controller/users/', headers=self.AUTH_HEADERS, json={
                'ip': '10.0.0.15',
                'port': 4370,
                'timeout': 10000,
                'password': 'secret',
                'model': 'C3-400',
            })

        self.assertEqual(200, response.status_code)
        self.assertEqual(expected_users, response.get_json()['users'])
        get_users.assert_called_once_with(
            '10.0.0.15',
            4370,
            timeout=10000,
            password='secret',
            model='C3-400',
        )

    def test_bulk_set_users_route_forwards_operation_and_returns_progress(self):
        expected_result = {
            'success': True,
            'operation_id': 'operation-1',
            'total': 2,
            'succeeded': 2,
            'failed': 0,
            'results': [
                {'card': '12345', 'pin': '54321', 'success': True},
                {'card': '12346', 'pin': '54322', 'success': True},
            ],
        }

        with patch('app.add_users', return_value=expected_result) as add_users, patch(
            'app.get_shared_secret',
            return_value='test-secret',
        ):
            response = self.client.post('/controller/users/set/', headers=self.AUTH_HEADERS, json={
                'ip': '10.0.0.15',
                'port': 4370,
                'model': 'C3-400',
                'operation_id': 'operation-1',
                'users': [
                    {'card': '12345', 'pin': '54321', 'doors': [1, 3]},
                    {'card': '12346', 'pin': '54322', 'doors': [2]},
                ],
            })

        self.assertEqual(200, response.status_code)
        self.assertEqual(expected_result, response.get_json())
        add_users.assert_called_once_with(
            users=[
                {'card': '12345', 'pin': '54321', 'doors': [1, 3]},
                {'card': '12346', 'pin': '54322', 'doors': [2]},
            ],
            ip='10.0.0.15',
            port=4370,
            timeout=4000,
            password='',
            model='C3-400',
            operation_id='operation-1',
        )

    def test_bulk_remove_users_route_forwards_operation_and_returns_progress(self):
        expected_result = {
            'success': True,
            'operation_id': 'operation-9',
            'total': 2,
            'succeeded': 2,
            'failed': 0,
            'rewritten': 1,
            'results': [
                {'card': '12345', 'pin': '54321', 'success': True},
                {'card': '12346', 'pin': '54322', 'success': True},
            ],
        }

        with patch('app.delete_users', return_value=expected_result) as delete_users, patch(
            'app.get_shared_secret',
            return_value='test-secret',
        ):
            response = self.client.post('/controller/users/remove/', headers=self.AUTH_HEADERS, json={
                'ip': '10.0.0.15',
                'port': 4370,
                'model': 'C3-400',
                'operation_id': 'operation-9',
                'users': [
                    {'card': '12345', 'pin': '54321', 'doors': [1]},
                    {'card': '12346', 'pin': '54322'},
                ],
            })

        self.assertEqual(200, response.status_code)
        self.assertEqual(expected_result, response.get_json())
        delete_users.assert_called_once_with(
            users=[
                {'card': '12345', 'pin': '54321', 'doors': [1]},
                {'card': '12346', 'pin': '54322'},
            ],
            ip='10.0.0.15',
            port=4370,
            timeout=4000,
            password='',
            model='C3-400',
            operation_id='operation-9',
        )

    def test_bulk_remove_users_route_rejects_incomplete_payloads(self):
        with patch('app.get_shared_secret', return_value='test-secret'):
            missing_ip = self.client.post('/controller/users/remove/', headers=self.AUTH_HEADERS, json={
                'users': [{'card': '12345', 'pin': '54321'}],
            })
            missing_card = self.client.post('/controller/users/remove/', headers=self.AUTH_HEADERS, json={
                'ip': '10.0.0.15',
                'users': [{'pin': '54321'}],
            })

        self.assertEqual(422, missing_ip.status_code)
        self.assertFalse(missing_ip.get_json()['success'])
        self.assertEqual(422, missing_card.status_code)
        self.assertEqual([0], missing_card.get_json()['invalid_indexes'])

    def test_bulk_routes_reject_null_identifiers_and_malformed_doors(self):
        users = [
            {'card': None, 'pin': '54321'},
            {'card': '12345', 'pin': None},
            {'card': True, 'pin': '54321'},
            {'card': '12345', 'pin': '54321', 'doors': 3},
            {'card': '12345', 'pin': '54321', 'doors': ['1', '3']},
            {'card': '12345', 'pin': '54321', 'doors': []},
            {'card': '12345', 'pin': '54321', 'doors': [0]},
            {'card': 12345, 'pin': 54321, 'doors': [1, 3]},
            {'card': '12345', 'pin': '54321', 'doors': None},
        ]

        for route, target in (('/controller/users/set/', 'app.add_users'), ('/controller/users/remove/', 'app.delete_users')):
            with self.subTest(route=route), patch(target) as batch_writer:
                response = self.client.post(route, headers=self.AUTH_HEADERS, json={
                    'ip': '10.0.0.15',
                    'users': users,
                })

                self.assertEqual(422, response.status_code)
                self.assertEqual([0, 1, 2, 3, 4, 5, 6], response.get_json()['invalid_indexes'])
                batch_writer.assert_not_called()

    def test_bulk_remove_users_route_reports_controller_failure_as_bad_gateway(self):
        failed_result = {
            'success': False,
            'operation_id': 'operation-10',
            'total': 1,
            'succeeded': 0,
            'failed': 1,
            'rewritten': 0,
            'message': 'Failed to remove users from controller',
            'results': [{'card': '12345', 'pin': '54321', 'success': False, 'error': 'boom'}],
        }

        with patch('app.delete_users', return_value=failed_result), patch(
            'app.get_shared_secret',
            return_value='test-secret',
        ):
            response = self.client.post('/controller/users/remove/', headers=self.AUTH_HEADERS, json={
                'ip': '10.0.0.15',
                'operation_id': 'operation-10',
                'users': [{'card': '12345', 'pin': '54321'}],
            })

        self.assertEqual(502, response.status_code)
        self.assertEqual(failed_result, response.get_json())

    def test_users_route_uses_queue_manager_bridge(self):
        with patch('queue_manager.get_users_func', return_value={'200': {'card': '100', 'pin': '200'}}) as get_users_func, patch(
            'app.get_local_time',
            return_value='2026-04-17 00:00:00',
        ), patch('app.open', mock_open()), patch('app.print'):
            response = self.client.post('/controller/users/', headers=self.AUTH_HEADERS, json={
                'ip': '10.0.0.15',
                'port': 4370,
                'timeout': 9000,
                'password': 'secret',
                'model': 'C3-400',
            })

        self.assertEqual(200, response.status_code)
        self.assertEqual({'200': {'card': '100', 'pin': '200'}}, response.get_json()['users'])
        get_users_func.assert_called_once_with(
            '10.0.0.15',
            4370,
            timeout=9000,
            password='secret',
            model='C3-400',
        )

    def test_set_users_route_reaches_main_add_users_through_queue_manager(self):
        zk_instance = MagicMock()
        successful_context = MagicMock()
        successful_context.__enter__.return_value = zk_instance
        successful_context.__exit__.return_value = False

        with patch('main.ZKAccess', return_value=successful_context) as zkteco, patch(
            'app.get_local_time',
            return_value='2026-04-17 00:00:00',
        ), patch('main.get_local_time', return_value='2026-04-17 00:00:00'), patch(
            'app.open',
            mock_open(),
        ), patch('main.open', mock_open()), patch('app.print'), patch('main.print'):
            response = self.client.post('/controller/users/set/', headers=self.AUTH_HEADERS, json={
                'ip': '10.0.0.15',
                'port': 4370,
                'timeout': 9000,
                'password': 'secret',
                'model': 'C3-400',
                'users': [{'card': '12345', 'pin': '54321', 'doors': [1, 3]}],
            })

        self.assertEqual(200, response.status_code)
        self.assertTrue(response.get_json()['success'])
        zkteco.assert_called_once_with(
            connstr='protocol=TCP,ipaddress=10.0.0.15,port=4370,timeout=9000,passwd=secret',
            device_model=main.ZK400,
        )
