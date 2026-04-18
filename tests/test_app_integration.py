import pathlib
import sys
import unittest
from unittest.mock import mock_open, patch

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app as app_module
import queue_manager


class FlaskRouteIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app_module.app.config['TESTING'] = True
        cls.client = app_module.app.test_client()

    @classmethod
    def tearDownClass(cls):
        queue_manager.request_queue.put(None)
        queue_manager.thread.join(timeout=1)

    def test_remove_user_route_reports_failure_when_delete_user_fails(self):
        with patch('app.delete_user', return_value=False), patch('app.get_local_time', return_value='2026-04-17 00:00:00'), patch(
            'app.open',
            mock_open(),
        ), patch('app.print'):
            response = self.client.post('/controller/user/remove/', json={
                'card': '12345',
                'pin': '54321',
                'ip': '10.0.0.15',
                'port': 4370,
            })

        self.assertEqual(200, response.status_code)
        self.assertFalse(response.get_json()['success'])

    def test_set_user_route_reports_failure_when_add_user_fails(self):
        with patch('app.add_user', return_value=False), patch('app.get_local_time', return_value='2026-04-17 00:00:00'), patch(
            'app.open',
            mock_open(),
        ), patch('app.print'):
            response = self.client.post('/controller/user/set/', json={
                'card': '12345',
                'pin': '54321',
                'ip': '10.0.0.15',
                'port': 4370,
                'doors': [1, 2],
            })

        self.assertEqual(200, response.status_code)
        self.assertFalse(response.get_json()['success'])

    def test_remove_user_route_forwards_optional_device_settings(self):
        with patch('app.delete_user', return_value=True) as delete_user, patch(
            'app.get_local_time',
            return_value='2026-04-17 00:00:00',
        ), patch('app.open', mock_open()), patch('app.print'):
            response = self.client.post('/controller/user/remove/', json={
                'card': '12345',
                'pin': '54321',
                'ip': '10.0.0.15',
                'port': 4370,
                'timeout': 10000,
                'password': 'secret',
                'model': 'ZK400',
            })

        self.assertEqual(200, response.status_code)
        delete_user.assert_called_once_with(
            card='12345',
            pin='54321',
            ip='10.0.0.15',
            port=4370,
            timeout=10000,
            password='secret',
            model='ZK400',
        )

    def test_set_user_route_forwards_optional_device_settings(self):
        with patch('app.add_user', return_value=True) as add_user, patch(
            'app.get_local_time',
            return_value='2026-04-17 00:00:00',
        ), patch('app.open', mock_open()), patch('app.print'):
            response = self.client.post('/controller/user/set/', json={
                'card': '12345',
                'pin': '54321',
                'ip': '10.0.0.15',
                'port': 4370,
                'doors': [1, 4],
                'timeout': 10000,
                'password': 'secret',
                'model': 'C3-400',
            })

        self.assertEqual(200, response.status_code)
        add_user.assert_called_once_with(
            card='12345',
            pin='54321',
            ip='10.0.0.15',
            port=4370,
            doors=[1, 4],
            timeout=10000,
            password='secret',
            model='C3-400',
        )

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
            response = self.client.post('/controller/users/', json={
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
