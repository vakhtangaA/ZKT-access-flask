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


class QueueManagerIntegrationTest(unittest.TestCase):
    def test_add_user_serializes_requests_for_same_device(self):
        state = {
            'current': 0,
            'max_concurrent': 0,
        }
        state_lock = threading.Lock()
        first_started = threading.Event()

        def fake_add_user(card, pin, ip, port, doors=None, timeout=4000, password='', model=None):
            with state_lock:
                state['current'] += 1
                state['max_concurrent'] = max(state['max_concurrent'], state['current'])
                first_started.set()

            time.sleep(0.05)

            with state_lock:
                state['current'] -= 1

            return True

        with patch('queue_manager.add_user_func', side_effect=fake_add_user) as add_user_func:
            with ThreadPoolExecutor(max_workers=2) as executor:
                first_call = executor.submit(queue_manager.add_user, '100', '200', '10.0.0.15', 4370)
                self.assertTrue(first_started.wait(timeout=1))
                second_call = executor.submit(queue_manager.add_user, '101', '201', '10.0.0.15', 4370)

                self.assertTrue(first_call.result(timeout=1))
                self.assertTrue(second_call.result(timeout=1))

        self.assertEqual(2, add_user_func.call_count)
        self.assertEqual(1, state['max_concurrent'])

    def test_add_user_allows_parallel_requests_for_different_devices(self):
        state = {
            'current': 0,
            'max_concurrent': 0,
        }
        state_lock = threading.Lock()
        first_started = threading.Event()

        def fake_add_user(card, pin, ip, port, doors=None, timeout=4000, password='', model=None):
            with state_lock:
                state['current'] += 1
                state['max_concurrent'] = max(state['max_concurrent'], state['current'])
                first_started.set()

            time.sleep(0.05)

            with state_lock:
                state['current'] -= 1

            return True

        with patch('queue_manager.add_user_func', side_effect=fake_add_user) as add_user_func:
            with ThreadPoolExecutor(max_workers=2) as executor:
                first_call = executor.submit(queue_manager.add_user, '100', '200', '10.0.0.15', 4370)
                self.assertTrue(first_started.wait(timeout=1))
                second_call = executor.submit(queue_manager.add_user, '101', '201', '10.0.0.16', 4370)

                self.assertTrue(first_call.result(timeout=1))
                self.assertTrue(second_call.result(timeout=1))

        self.assertEqual(2, add_user_func.call_count)
        self.assertGreaterEqual(state['max_concurrent'], 2)

    def test_add_and_delete_user_serialize_requests_for_same_device(self):
        state = {
            'current': 0,
            'max_concurrent': 0,
        }
        state_lock = threading.Lock()
        first_started = threading.Event()

        def fake_add_user(card, pin, ip, port, doors=None, timeout=4000, password='', model=None):
            with state_lock:
                state['current'] += 1
                state['max_concurrent'] = max(state['max_concurrent'], state['current'])
                first_started.set()

            time.sleep(0.05)

            with state_lock:
                state['current'] -= 1

            return True

        def fake_delete_user(card, pin, ip, port, timeout=4000, password='', model=None):
            with state_lock:
                state['current'] += 1
                state['max_concurrent'] = max(state['max_concurrent'], state['current'])

            time.sleep(0.05)

            with state_lock:
                state['current'] -= 1

            return True

        with patch('queue_manager.add_user_func', side_effect=fake_add_user) as add_user_func, patch(
            'queue_manager.delete_user_func',
            side_effect=fake_delete_user,
        ) as delete_user_func:
            with ThreadPoolExecutor(max_workers=2) as executor:
                first_call = executor.submit(queue_manager.add_user, '100', '200', '10.0.0.15', 4370)
                self.assertTrue(first_started.wait(timeout=1))
                second_call = executor.submit(queue_manager.delete_user, '101', '201', '10.0.0.15', 4370)

                self.assertTrue(first_call.result(timeout=1))
                self.assertTrue(second_call.result(timeout=1))

        self.assertEqual(1, add_user_func.call_count)
        self.assertEqual(1, delete_user_func.call_count)
        self.assertEqual(1, state['max_concurrent'])

    def test_add_and_delete_user_allow_parallel_requests_for_different_devices(self):
        state = {
            'current': 0,
            'max_concurrent': 0,
        }
        state_lock = threading.Lock()
        first_started = threading.Event()

        def fake_add_user(card, pin, ip, port, doors=None, timeout=4000, password='', model=None):
            with state_lock:
                state['current'] += 1
                state['max_concurrent'] = max(state['max_concurrent'], state['current'])
                first_started.set()

            time.sleep(0.05)

            with state_lock:
                state['current'] -= 1

            return True

        def fake_delete_user(card, pin, ip, port, timeout=4000, password='', model=None):
            with state_lock:
                state['current'] += 1
                state['max_concurrent'] = max(state['max_concurrent'], state['current'])

            time.sleep(0.05)

            with state_lock:
                state['current'] -= 1

            return True

        with patch('queue_manager.add_user_func', side_effect=fake_add_user) as add_user_func, patch(
            'queue_manager.delete_user_func',
            side_effect=fake_delete_user,
        ) as delete_user_func:
            with ThreadPoolExecutor(max_workers=2) as executor:
                first_call = executor.submit(queue_manager.add_user, '100', '200', '10.0.0.15', 4370)
                self.assertTrue(first_started.wait(timeout=1))
                second_call = executor.submit(queue_manager.delete_user, '101', '201', '10.0.0.16', 4370)

                self.assertTrue(first_call.result(timeout=1))
                self.assertTrue(second_call.result(timeout=1))

        self.assertEqual(1, add_user_func.call_count)
        self.assertEqual(1, delete_user_func.call_count)
        self.assertGreaterEqual(state['max_concurrent'], 2)


if __name__ == '__main__':
    unittest.main()
