import pathlib
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import queue_manager

USERS = [{'card': '100', 'pin': '200'}]
BATCH_RESULT = {'success': True, 'results': []}


class ConcurrencyProbe:
    """Stands in for a controller operation and records how many ran at the same time."""

    def __init__(self):
        self.current = 0
        self.max_concurrent = 0
        self.first_started = threading.Event()
        self._lock = threading.Lock()

    def operation(self, result):
        def run(*args, **kwargs):
            with self._lock:
                self.current += 1
                self.max_concurrent = max(self.max_concurrent, self.current)
                self.first_started.set()

            time.sleep(0.05)

            with self._lock:
                self.current -= 1

            return result

        return run


class QueueManagerIntegrationTest(unittest.TestCase):
    def run_two(self, first, second):
        """Start `second` once `first` is inside its operation, and return both results."""
        with ThreadPoolExecutor(max_workers=2) as executor:
            first_call = executor.submit(first)
            self.assertTrue(self.probe.first_started.wait(timeout=1))
            second_call = executor.submit(second)

            return first_call.result(timeout=1), second_call.result(timeout=1)

    def setUp(self):
        self.probe = ConcurrencyProbe()

    def test_add_users_serializes_requests_for_same_device(self):
        with patch('queue_manager.add_users_func', side_effect=self.probe.operation(BATCH_RESULT)) as add_users_func:
            self.run_two(
                lambda: queue_manager.add_users(USERS, '10.0.0.15', 4370),
                lambda: queue_manager.add_users(USERS, '10.0.0.15', 4370),
            )

        self.assertEqual(2, add_users_func.call_count)
        self.assertEqual(1, self.probe.max_concurrent)

    def test_add_users_allows_parallel_requests_for_different_devices(self):
        with patch('queue_manager.add_users_func', side_effect=self.probe.operation(BATCH_RESULT)) as add_users_func:
            self.run_two(
                lambda: queue_manager.add_users(USERS, '10.0.0.15', 4370),
                lambda: queue_manager.add_users(USERS, '10.0.0.16', 4370),
            )

        self.assertEqual(2, add_users_func.call_count)
        self.assertGreaterEqual(self.probe.max_concurrent, 2)

    def test_add_and_delete_users_serialize_requests_for_same_device(self):
        with patch('queue_manager.add_users_func', side_effect=self.probe.operation(BATCH_RESULT)), patch(
            'queue_manager.delete_users_func',
            side_effect=self.probe.operation(BATCH_RESULT),
        ) as delete_users_func:
            self.run_two(
                lambda: queue_manager.add_users(USERS, '10.0.0.15', 4370),
                lambda: queue_manager.delete_users(USERS, '10.0.0.15', 4370),
            )

        self.assertEqual(1, delete_users_func.call_count)
        self.assertEqual(1, self.probe.max_concurrent)

    def test_add_and_delete_users_allow_parallel_requests_for_different_devices(self):
        with patch('queue_manager.add_users_func', side_effect=self.probe.operation(BATCH_RESULT)), patch(
            'queue_manager.delete_users_func',
            side_effect=self.probe.operation(BATCH_RESULT),
        ):
            self.run_two(
                lambda: queue_manager.add_users(USERS, '10.0.0.15', 4370),
                lambda: queue_manager.delete_users(USERS, '10.0.0.16', 4370),
            )

        self.assertGreaterEqual(self.probe.max_concurrent, 2)

    def test_add_users_and_get_users_serialize_requests_for_same_device(self):
        listed = {'200': {'card': '100', 'pin': '200'}}

        with patch('queue_manager.add_users_func', side_effect=self.probe.operation(BATCH_RESULT)), patch(
            'queue_manager.get_users_func',
            side_effect=self.probe.operation(listed),
        ):
            _, users = self.run_two(
                lambda: queue_manager.add_users(USERS, '10.0.0.15', 4370),
                lambda: queue_manager.get_users('10.0.0.15', 4370),
            )

        self.assertEqual(listed, users)
        self.assertEqual(1, self.probe.max_concurrent)

    def test_get_users_forwards_optional_device_settings(self):
        with patch('queue_manager.get_users_func', return_value={'200': {'card': '100', 'pin': '200'}}) as get_users_func:
            result = queue_manager.get_users(
                '10.0.0.15',
                4370,
                timeout=9000,
                password='secret',
                model='C3-400',
            )

        self.assertEqual({'200': {'card': '100', 'pin': '200'}}, result)
        get_users_func.assert_called_once_with(
            '10.0.0.15',
            4370,
            timeout=9000,
            password='secret',
            model='C3-400',
        )


if __name__ == '__main__':
    unittest.main()
