import pathlib
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import device_locks


class DeviceLocksIntegrationTest(unittest.TestCase):
    def test_get_device_lock_normalizes_ip_and_port_values(self):
        first_lock = device_locks.get_device_lock('10.0.0.15', 4370)
        second_lock = device_locks.get_device_lock(' 10.0.0.15 ', '4370')

        self.assertIs(first_lock, second_lock)

    def test_with_device_lock_allows_nested_calls_for_same_device(self):
        result = device_locks.with_device_lock(
            '10.0.0.15',
            4370,
            lambda: device_locks.with_device_lock('10.0.0.15', 4370, lambda: 'nested-ok'),
        )

        self.assertEqual('nested-ok', result)

    def test_with_device_lock_serializes_same_device_operations(self):
        state = {
            'current': 0,
            'max_concurrent': 0,
        }
        state_lock = threading.Lock()
        first_started = threading.Event()

        def protected_operation():
            with state_lock:
                state['current'] += 1
                state['max_concurrent'] = max(state['max_concurrent'], state['current'])
                first_started.set()

            time.sleep(0.05)

            with state_lock:
                state['current'] -= 1

            return True

        with ThreadPoolExecutor(max_workers=2) as executor:
            first_call = executor.submit(device_locks.with_device_lock, '10.0.0.15', 4370, protected_operation)
            self.assertTrue(first_started.wait(timeout=1))
            second_call = executor.submit(device_locks.with_device_lock, '10.0.0.15', 4370, protected_operation)

            self.assertTrue(first_call.result(timeout=1))
            self.assertTrue(second_call.result(timeout=1))

        self.assertEqual(1, state['max_concurrent'])
