from datetime import datetime
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
from pyzkaccess.tables import UserAuthorize


def authorization_row(pin, timezone_id, mask):
    return UserAuthorize().with_raw_data({
        'Pin': pin,
        'AuthorizeTimezoneId': str(timezone_id),
        'AuthorizeDoorId': mask,
    })


class FirmwareUserTable:
    """A User table that drops pins the way a C3-200 does.

    Measured on 185.146.179.174 (2026-10-14): a write that holds even one pin the controller
    already has skips every new pin in it and still succeeds; a write of new pins only stores
    them all. Pins in `refused` are never stored, however they are written.
    """

    def __init__(self, pins=(), refused=(), failing_reads=()):
        self.pins = set(pins)
        self.refused = set(refused)
        self.failing_reads = set(failing_reads)
        self.reads = 0
        self.upserts = []
        self.deleted = []

    def __iter__(self):
        self.reads += 1
        if self.reads in self.failing_reads:
            raise Exception('GetDeviceData failed: SDK error -11: Data parameter error')

        return iter([MagicMock(pin=pin) for pin in sorted(self.pins)])

    def upsert(self, records):
        records = list(records)
        self.upserts.append(records)
        mixed = any(record.pin in self.pins for record in records)
        self.pins |= {
            record.pin for record in records
            if record.pin not in self.refused and (record.pin in self.pins or not mixed)
        }

    def delete(self, records):
        records = list(records)
        self.deleted.append(records)
        self.pins -= {record['pin'] for record in records}


def zk_with_tables(user_table, authorization_table=None):
    authorization_table = authorization_table or MagicMock()
    zk_instance = MagicMock()
    zk_instance.table.side_effect = lambda name: {
        'User': user_table,
        'UserAuthorize': authorization_table,
    }[name]
    context = MagicMock()
    context.__enter__.return_value = zk_instance
    context.__exit__.return_value = False

    return context


class MainDeviceIntegrationTest(unittest.TestCase):
    def test_tbilisi_now_runs_unmocked_and_is_four_hours_ahead_of_utc(self):
        # Every clock sync failed with "name 'tz' is not defined" (2026-10-12) while the tests mocked it.
        now = main.tbilisi_now()
        utc_now = datetime.utcnow()

        self.assertIsNone(now.tzinfo)
        self.assertLess(abs((now - utc_now).total_seconds() - 4 * 3600), 5)

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

        self.assertEqual({'online': True, 'error': None, 'clock': None}, result)
        zkteco.assert_called_once_with(
            connstr='protocol=TCP,ipaddress=10.0.0.15,port=4370,timeout=9000,passwd=',
            device_model=main.ZK400,
        )

    def check_clock(self, reads, sync_after_seconds=120):
        """Run a health check against a controller whose clock reads return `reads` in turn."""

        class FakeParameters:
            def __init__(self):
                self.reads = list(reads)
                self.written = []

            @property
            def datetime(self):
                value = self.reads.pop(0)

                if isinstance(value, Exception):
                    raise value

                return value

            @datetime.setter
            def datetime(self, value):
                self.written.append(value)

        parameters = FakeParameters()
        zk_instance = MagicMock()
        zk_instance.parameters = parameters
        successful_context = MagicMock()
        successful_context.__enter__.return_value = zk_instance
        successful_context.__exit__.return_value = False

        with patch('main.ZKAccess', return_value=successful_context), patch(
            'main.tbilisi_now', return_value=datetime(2026, 10, 11, 12, 0, 0),
        ), patch('main.get_local_time', return_value='2026-10-11 12:00:00'), patch('main.open', mock_open()):
            result = main.check_device('10.0.0.15', 4370, model='C3-200', sync_clock_after_seconds=sync_after_seconds)

        return result, parameters.written

    def test_check_device_leaves_a_clock_that_is_close_enough(self):
        result, written = self.check_clock([datetime(2026, 10, 11, 12, 1, 0)])

        self.assertTrue(result['online'])
        self.assertEqual({'controller_time': '2026-10-11 12:01:00', 'drift_seconds': 60, 'synced': False, 'error': None}, result['clock'])
        self.assertEqual([], written)

    def test_check_device_sets_a_reset_clock_to_tbilisi_time(self):
        result, written = self.check_clock([datetime(2000, 1, 1, 0, 17, 15), datetime(2026, 10, 11, 12, 0, 0)])

        self.assertEqual([datetime(2026, 10, 11, 12, 0, 0)], written)
        self.assertEqual('2000-01-01 00:17:15', result['clock']['controller_time'])
        self.assertTrue(result['clock']['synced'])
        self.assertLess(result['clock']['drift_seconds'], -800000000)
        self.assertIsNone(result['clock']['error'])

    def test_check_device_does_not_trust_a_clock_read_that_ignores_the_write(self):
        result, written = self.check_clock([datetime(2000, 1, 1), datetime(2000, 1, 1)])

        self.assertEqual(1, len(written))
        self.assertTrue(result['clock']['synced'])
        self.assertIsNone(result['clock']['drift_seconds'])
        self.assertIn('does not follow', result['clock']['error'])

    def test_check_device_still_sets_the_clock_when_it_cannot_be_read(self):
        result, written = self.check_clock([Exception('SDK error -2'), Exception('SDK error -2')])

        self.assertEqual([datetime(2026, 10, 11, 12, 0, 0)], written)
        self.assertTrue(result['online'])
        self.assertTrue(result['clock']['synced'])
        self.assertIsNone(result['clock']['drift_seconds'])

    def test_check_device_reports_a_failed_clock_write_without_going_offline(self):
        class FailingParameters:
            @property
            def datetime(self):
                return datetime(2000, 1, 1)

            @datetime.setter
            def datetime(self, value):
                raise Exception('SDK error -3')

        zk_instance = MagicMock()
        zk_instance.parameters = FailingParameters()
        successful_context = MagicMock()
        successful_context.__enter__.return_value = zk_instance
        successful_context.__exit__.return_value = False

        with patch('main.ZKAccess', return_value=successful_context), patch(
            'main.tbilisi_now', return_value=datetime(2026, 10, 11, 12, 0, 0),
        ), patch('main.get_local_time', return_value='2026-10-11 12:00:00'), patch('main.open', mock_open()):
            result = main.check_device('10.0.0.15', 4370, sync_clock_after_seconds=120)

        self.assertTrue(result['online'])
        self.assertFalse(result['clock']['synced'])
        self.assertIn('SDK error -3', result['clock']['error'])

    def control_door_sdk_calls(self, **kwargs):
        zk_instance = MagicMock()
        successful_context = MagicMock()
        successful_context.__enter__.return_value = zk_instance
        successful_context.__exit__.return_value = False

        with patch('main.ZKAccess', return_value=successful_context) as zkteco, patch(
            'main.get_local_time',
            return_value='2026-10-09 00:00:00',
        ), patch('main.open', mock_open()):
            result = main.control_door('10.0.0.15', 4370, timeout=9000, model='C3-400', **kwargs)

        zkteco.assert_called_once_with(
            connstr='protocol=TCP,ipaddress=10.0.0.15,port=4370,timeout=9000,passwd=',
            device_model=main.ZK400,
        )

        return result, zk_instance.sdk.control_device.call_args_list

    def test_control_door_open_ends_normal_open_before_switching_the_lock_relay_for_the_given_seconds(self):
        result, calls = self.control_door_sdk_calls(door=3, action='open', seconds=7)

        self.assertTrue(result)
        self.assertEqual([((4, 3, 0, 0, 0),), ((1, 3, 1, 7, 0),)], calls)

    def test_control_door_hold_open_switches_the_lock_relay_to_normal_open(self):
        result, calls = self.control_door_sdk_calls(door=2, action='hold_open')

        self.assertTrue(result)
        self.assertEqual([((1, 2, 1, 255, 0),)], calls)

    def test_control_door_close_ends_normal_open_before_switching_the_lock_relay_off(self):
        result, calls = self.control_door_sdk_calls(door=4, action='close')

        self.assertTrue(result)
        self.assertEqual([((4, 4, 0, 0, 0),), ((1, 4, 1, 0, 0),)], calls)

    def test_control_door_runs_under_the_device_lock(self):
        with patch('main.with_device_lock', return_value=True) as with_device_lock:
            result = main.control_door('10.0.0.15', 4370, door=1, action='close', model='C3-400')

        self.assertTrue(result)
        self.assertEqual(('10.0.0.15', 4370), with_device_lock.call_args.args[:2])

    def test_control_door_returns_false_and_reports_when_sdk_fails(self):
        with patch('main.ZKAccess', side_effect=Exception('SDK error -307')), patch(
            'main.capture_exception'
        ) as capture_exception, patch('main.get_local_time', return_value='2026-10-09 00:00:00'), patch(
            'main.open',
            mock_open(),
        ):
            result = main.control_door('10.0.0.15', 4370, door=1, action='open', seconds=5, model='C3-400')

        self.assertFalse(result)
        capture_exception.assert_called_once()
        self.assertEqual('control_door', capture_exception.call_args.kwargs['operation'])

    # Recorded from a C3-200 on 2026-10-09: door 1 held open, then door 2 opened by a card.
    RTSTATE_DOOR_1_ON = 'type=rtstate\ttime=2026-10-09 23:07:39\tsensor=00\trelay=01\talarm=00000000'
    RTSTATE_DOOR_2_ON = 'type=rtstate\ttime=2026-10-09 23:10:55\tsensor=00\trelay=02\talarm=00000000'
    RTLOG_EVENT = 'type=rtlog\ttime=2026-10-09 23:02:58\tpin=0\tcardno=0\teventaddr=1\tevent=205\tinoutstatus=2\tverifytype=200\tindex=406376'

    def test_parse_relay_state_reads_one_bit_per_door_from_the_lowest(self):
        self.assertEqual({
            'relays': [{'door': 1, 'on': True}, {'door': 2, 'on': False}],
            'changed_at': '2026-10-09 23:07:39',
        }, main.parse_relay_state(self.RTSTATE_DOOR_1_ON, 2))
        self.assertEqual(
            [{'door': 1, 'on': False}, {'door': 2, 'on': True}, {'door': 3, 'on': False}, {'door': 4, 'on': False}],
            main.parse_relay_state(self.RTSTATE_DOOR_2_ON, 4)['relays'],
        )

    def test_parse_relay_state_keeps_doors_1_to_8_in_the_first_byte(self):
        line = 'type=rtstate\ttime=2026-10-09 23:46:27\tsensor=00\trelay=0400\talarm=00000000'

        self.assertEqual(
            [{'door': 1, 'on': False}, {'door': 2, 'on': False}, {'door': 3, 'on': True}, {'door': 4, 'on': False}],
            main.parse_relay_state(line, 4)['relays'],
        )

    def test_parse_relay_state_ignores_events_and_garbage(self):
        self.assertIsNone(main.parse_relay_state(self.RTLOG_EVENT, 2))
        self.assertIsNone(main.parse_relay_state('', 2))
        self.assertIsNone(main.parse_relay_state('type=rtstate\ttime=x\trelay=zz', 2))

    def read_relay_state(self, answers):
        """Run read_relay_state against a controller whose GetRTLogExt gives these answers in turn.

        An answer is the buffer text, or a negative SDK error code.
        """
        answers = list(answers)
        zk_instance = MagicMock()

        def get_rt_log_ext(_handle, buffer, _size):
            answer = answers.pop(0)

            if isinstance(answer, int):
                return answer

            buffer.value = answer.encode()
            return len(answer)

        zk_instance.sdk.dll.GetRTLogExt.side_effect = get_rt_log_ext
        successful_context = MagicMock()
        successful_context.__enter__.return_value = zk_instance
        successful_context.__exit__.return_value = False

        with patch('main.ZKAccess', return_value=successful_context), patch('main.time.sleep'), patch(
            'main.get_local_time', return_value='2026-10-09 00:00:00',
        ), patch('main.open', mock_open()), patch('main.capture_exception') as capture_exception:
            result = main.read_relay_state('10.0.0.15', 4370, model='C3-200')

        return result, zk_instance, capture_exception

    def test_read_relay_state_drops_queued_events_until_the_status_record(self):
        result, zk_instance, _ = self.read_relay_state([
            self.RTLOG_EVENT + '\r\n' + self.RTLOG_EVENT + '\r\n',
            self.RTSTATE_DOOR_1_ON + '\r\n',
        ])

        self.assertEqual([{'door': 1, 'on': True}, {'door': 2, 'on': False}], result['relays'])
        self.assertEqual(2, zk_instance.sdk.dll.GetRTLogExt.call_count)
        zk_instance.sdk.dll.GetRTLog.assert_not_called()

    def test_read_relay_state_gives_up_when_the_cache_never_empties(self):
        result, zk_instance, _ = self.read_relay_state([self.RTLOG_EVENT + '\r\n'] * main.RELAY_STATE_READS)

        self.assertIsNone(result)
        self.assertEqual(main.RELAY_STATE_READS, zk_instance.sdk.dll.GetRTLogExt.call_count)

    def test_read_relay_state_returns_none_and_reports_an_sdk_error(self):
        result, _, capture_exception = self.read_relay_state([-2])

        self.assertIsNone(result)
        self.assertEqual('read_relay_state', capture_exception.call_args.kwargs['operation'])

    def test_read_relay_state_runs_under_the_device_lock(self):
        with patch('main.with_device_lock', return_value=None) as with_device_lock:
            main.read_relay_state('10.0.0.15', 4370, model='C3-200')

        self.assertEqual(('10.0.0.15', 4370), with_device_lock.call_args.args[:2])

    def test_check_device_reports_the_sdk_error_without_sending_it_to_sentry(self):
        with patch('main.ZKAccess', side_effect=Exception('SDK error -307: Connection attempt failed')), patch(
            'main.capture_exception'
        ) as capture_exception, patch('main.get_local_time', return_value='2026-04-17 00:00:00'), patch(
            'main.open',
            mock_open(),
        ):
            result = main.check_device('10.0.0.15', 4370, model='C3-400')

        self.assertEqual({'online': False, 'error': 'SDK error -307: Connection attempt failed', 'clock': None}, result)
        capture_exception.assert_not_called()

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

    def test_pyzkaccess_decodes_every_door_bit(self):
        # pyzkaccess < 1.2 reported all four doors for every mask; this fails on those versions.
        def doors_of(mask):
            return main.allowed_door_numbers(authorization_row('1', 1, mask).doors)

        self.assertEqual([], doors_of('0'))
        self.assertEqual([1], doors_of('1'))
        self.assertEqual([1, 3], doors_of('5'))
        self.assertEqual([4], doors_of('8'))
        self.assertEqual([1, 2, 3, 4], doors_of('15'))

    def test_get_users_with_doors_decodes_the_raw_mask_and_merges_rows_per_pin(self):
        zk_instance = MagicMock()
        tables = {
            'UserAuthorize': [
                authorization_row('200', 1, '5'),
                authorization_row('200', 2, '8'),
                authorization_row('201', 1, '0'),
            ],
            'User': [
                MagicMock(pin='200', card='100'),
                MagicMock(pin='201', card='101'),
                MagicMock(pin='202', card='102'),
            ],
        }
        zk_instance.table.side_effect = lambda name: tables[name]

        successful_context = MagicMock()
        successful_context.__enter__.return_value = zk_instance
        successful_context.__exit__.return_value = False

        with patch('main.ZKAccess', return_value=successful_context), patch(
            'main.get_local_time',
            return_value='2026-04-17 00:00:00',
        ), patch('main.open', mock_open()), patch('main.print'):
            result = main.get_users_with_doors('10.0.0.15', 4370)

        self.assertEqual(
            {
                '200': {
                    'card': '100',
                    'pin': '200',
                    'doors': [1, 3, 4],
                    'authorizations': [
                        {'timezone_id': 1, 'mask': '5', 'doors': [1, 3]},
                        {'timezone_id': 2, 'mask': '8', 'doors': [4]},
                    ],
                },
                '201': {
                    'card': '101',
                    'pin': '201',
                    'doors': [],
                    'authorizations': [{'timezone_id': 1, 'mask': '0', 'doors': []}],
                },
                '202': {'card': '102', 'pin': '202', 'doors': [], 'authorizations': []},
            },
            result,
        )

    def test_get_users_with_doors_returns_none_after_two_failures(self):
        with patch('main.ZKAccess', side_effect=[Exception('first failure'), Exception('second failure')]) as zkteco, patch(
            'main.capture_exception',
        ) as capture_exception, patch('main.get_local_time', return_value='2026-04-17 00:00:00'), patch(
            'main.time.sleep',
        ), patch('main.open', mock_open()), patch('main.print'):
            result = main.get_users_with_doors('10.0.0.15', 4370)

        self.assertIsNone(result)
        self.assertEqual(2, zkteco.call_count)
        capture_exception.assert_called_once()

    def _transactions_result(self, records, **filters):
        zk_instance = MagicMock()
        transaction_table = MagicMock()
        transaction_table.__iter__.side_effect = lambda: iter(records)
        zk_instance.table.return_value = transaction_table

        successful_context = MagicMock()
        successful_context.__enter__.return_value = zk_instance
        successful_context.__exit__.return_value = False

        with patch('main.ZKAccess', return_value=successful_context), patch(
            'main.ZKDatetimeUtils.zkctime_to_datetime',
            side_effect=lambda value: datetime(2026, 10, 3, 23, 0, int(value)),
        ), patch('main.get_local_time', return_value='2026-04-17 00:00:00'), patch('main.open', mock_open()), patch('main.print'):
            result = main.get_transactions('10.0.0.15', 4370, **filters)

        zk_instance.table.assert_called_with('Transaction')
        zk_instance.table.return_value.where.assert_not_called()

        return result

    def test_get_transactions_without_a_limit_returns_every_matching_event(self):
        records = [
            MagicMock(raw_data={'EventType': '0', 'Time_second': str(second), 'Pin': '500080', 'Cardno': '504438413', 'DoorID': '1'})
            for second in range(1, 4)
        ]

        result = self._transactions_result(records, limit=None)

        self.assertEqual(['2026-10-03 23:00:03', '2026-10-03 23:00:02', '2026-10-03 23:00:01'], [event['time'] for event in result])

    def test_get_transactions_returns_newest_events_with_codes_and_names(self):
        records = [
            MagicMock(raw_data={'EventType': '0', 'Time_second': '1', 'Pin': '500080', 'Cardno': '504438413', 'DoorID': '1'}),
            MagicMock(raw_data={'EventType': '23', 'Time_second': '2', 'Pin': '500080', 'Cardno': '504438413', 'DoorID': '2'}),
        ]

        result = self._transactions_result(records, pin='500080', limit=1)

        self.assertEqual(
            [{
                'time': '2026-10-03 23:00:02',
                'pin': '500080',
                'card': '504438413',
                'door': '2',
                'event_code': 23,
                'event': 'Access Denied',
            }],
            result,
        )

    def test_get_transactions_matches_card_and_pin_here_and_not_on_the_controller(self):
        records = [
            MagicMock(raw_data={'EventType': '0', 'Time_second': '1', 'Pin': '20541', 'Cardno': '2409341497', 'DoorID': '2'}),
            MagicMock(raw_data={'EventType': '0', 'Time_second': '2', 'Pin': '561', 'Cardno': '2410329641', 'DoorID': '2'}),
            MagicMock(raw_data={'EventType': '0', 'Time_second': '3', 'Pin': '10603', 'Cardno': '3110348897', 'DoorID': '1'}),
        ]

        by_card = self._transactions_result(records, card=' 2409341497 ')
        by_pin = self._transactions_result(records, pin='561')
        unmatched = self._transactions_result(records, card='999')
        everything = self._transactions_result(records)

        self.assertEqual(['20541'], [event['pin'] for event in by_card])
        self.assertEqual(['2410329641'], [event['card'] for event in by_pin])
        self.assertEqual([], unmatched)
        self.assertEqual(['10603', '561', '20541'], [event['pin'] for event in everything])

    def test_get_transactions_treats_percent_in_a_pin_as_any_run_of_characters(self):
        records = [
            MagicMock(raw_data={'EventType': '0', 'Time_second': '1', 'Pin': '41030', 'Cardno': '1', 'DoorID': '1'}),
            MagicMock(raw_data={'EventType': '0', 'Time_second': '2', 'Pin': '41031', 'Cardno': '2', 'DoorID': '1'}),
            MagicMock(raw_data={'EventType': '0', 'Time_second': '3', 'Pin': '410', 'Cardno': '3', 'DoorID': '1'}),
            MagicMock(raw_data={'EventType': '0', 'Time_second': '4', 'Pin': '74103', 'Cardno': '4', 'DoorID': '1'}),
            MagicMock(raw_data={'EventType': '0', 'Time_second': '5', 'Pin': '4.03', 'Cardno': '5', 'DoorID': '1'}),
        ]

        def pins(pattern):
            return sorted(event['pin'] for event in self._transactions_result(records, pin=pattern))

        self.assertEqual(['41030', '41031'], pins('4103%'))
        self.assertEqual(['41030', '41031', '74103'], pins('%4103%'))
        self.assertEqual(['41031'], pins('%31'))
        self.assertEqual(['4.03', '410', '41030', '41031', '74103'], pins('%'))
        self.assertEqual(['4.03'], pins('4.03'), 'characters other than % match only themselves')
        self.assertEqual(['410'], pins('410'), 'without % the pin must match exactly')

    def test_get_transactions_matches_any_pin_of_a_list(self):
        records = [
            MagicMock(raw_data={'EventType': '0', 'Time_second': '1', 'Pin': '76630', 'Cardno': '1', 'DoorID': '1'}),
            MagicMock(raw_data={'EventType': '0', 'Time_second': '2', 'Pin': '76631', 'Cardno': '2', 'DoorID': '1'}),
            MagicMock(raw_data={'EventType': '0', 'Time_second': '3', 'Pin': '766300', 'Cardno': '3', 'DoorID': '1'}),
            MagicMock(raw_data={'EventType': '0', 'Time_second': '4', 'Pin': '7663', 'Cardno': '4', 'DoorID': '1'}),
        ]

        result = self._transactions_result(records, pin=['7663', '76630', '76631'])

        self.assertEqual(['7663', '76631', '76630'], [event['pin'] for event in result])

    def test_get_transactions_keeps_only_the_requested_event_types(self):
        records = [
            MagicMock(raw_data={'EventType': '0', 'Time_second': '1', 'Pin': '500080', 'Cardno': '504438413', 'DoorID': '1'}),
            MagicMock(raw_data={'EventType': '23', 'Time_second': '2', 'Pin': '500080', 'Cardno': '504438413', 'DoorID': '2'}),
            MagicMock(raw_data={'EventType': '27', 'Time_second': '3', 'Pin': '', 'Cardno': '111', 'DoorID': '1'}),
        ]

        denied = self._transactions_result(records, event_codes=[23])
        denied_or_unregistered = self._transactions_result(records, event_codes=[23, 27])
        everything = self._transactions_result(records, event_codes=[])

        self.assertEqual([23], [event['event_code'] for event in denied])
        self.assertEqual([27, 23], [event['event_code'] for event in denied_or_unregistered])
        self.assertEqual(3, len(everything))

    def test_event_time_bound_covers_whole_days_and_rejects_other_formats(self):
        self.assertIsNone(main.event_time_bound(None, end_of_day=False))
        self.assertIsNone(main.event_time_bound('', end_of_day=True))
        self.assertEqual('2026-10-01 00:00:00', main.event_time_bound('2026-10-01', end_of_day=False))
        self.assertEqual('2026-10-01 23:59:59', main.event_time_bound('2026-10-01', end_of_day=True))
        self.assertEqual('2026-10-01 08:30:00', main.event_time_bound('2026-10-01 08:30:00', end_of_day=True))

        for value in ('yesterday', '2026-13-40', '01.10.2026', '2026-10-01 8:30'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                main.event_time_bound(value, end_of_day=False)

    def test_get_transactions_keeps_only_events_inside_the_date_range(self):
        times = {
            '1': datetime(2026, 9, 30, 23, 59, 59),
            '2': datetime(2026, 10, 1, 0, 0, 0),
            '3': datetime(2026, 10, 3, 23, 59, 59),
            '4': datetime(2026, 10, 4, 0, 0, 0),
        }
        records = [
            MagicMock(raw_data={'EventType': '0', 'Time_second': key, 'Pin': '1', 'Cardno': '1', 'DoorID': '1'})
            for key in times
        ]
        zk_instance = MagicMock()
        transaction_table = MagicMock()
        transaction_table.__iter__.side_effect = lambda: iter(records)
        zk_instance.table.return_value = transaction_table
        successful_context = MagicMock()
        successful_context.__enter__.return_value = zk_instance
        successful_context.__exit__.return_value = False

        def read(**filters):
            with patch('main.ZKAccess', return_value=successful_context), patch(
                'main.ZKDatetimeUtils.zkctime_to_datetime',
                side_effect=lambda value: times[value],
            ), patch('main.get_local_time', return_value='2026-04-17 00:00:00'), patch('main.open', mock_open()), patch('main.print'):
                return [event['time'] for event in main.get_transactions('10.0.0.15', 4370, **filters)]

        self.assertEqual(['2026-10-03 23:59:59', '2026-10-01 00:00:00'], read(date_from='2026-10-01', date_to='2026-10-03'))
        self.assertEqual(['2026-10-04 00:00:00', '2026-10-03 23:59:59', '2026-10-01 00:00:00'], read(date_from='2026-10-01'))
        self.assertEqual(['2026-10-01 00:00:00', '2026-09-30 23:59:59'], read(date_to='2026-10-01'))
        self.assertEqual(4, len(read()))

    def test_get_transactions_returns_none_after_two_failures(self):
        with patch('main.ZKAccess', side_effect=[Exception('first failure'), Exception('second failure')]), patch(
            'main.capture_exception',
        ) as capture_exception, patch('main.get_local_time', return_value='2026-04-17 00:00:00'), patch(
            'main.time.sleep',
        ), patch('main.open', mock_open()), patch('main.print'):
            result = main.get_transactions('10.0.0.15', 4370, pin='500080')

        self.assertIsNone(result)
        capture_exception.assert_called_once()

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

    def _written_door_access(self, doors):
        zk_instance = MagicMock()
        authorization_table = MagicMock()
        zk_instance.table.side_effect = lambda name: authorization_table if name == 'UserAuthorize' else MagicMock()
        successful_context = MagicMock()
        successful_context.__enter__.return_value = zk_instance
        successful_context.__exit__.return_value = False
        user = {'card': '12345', 'pin': '54321'}

        if doors is not None:
            user['doors'] = doors

        with patch('main.ZKAccess', return_value=successful_context), patch(
            'main.get_local_time',
            return_value='2026-04-17 00:00:00',
        ), patch('main.open', mock_open()), patch('main.print'):
            result = main.add_users([user], '10.0.0.15', 4370)

        self.assertTrue(result['success'])

        return authorization_table.upsert.call_args.args[0][0]['doors']

    def test_add_users_writes_exactly_the_requested_doors(self):
        self.assertEqual((True, False, True, False), self._written_door_access([1, 3]))
        self.assertEqual((False, True, False, True), self._written_door_access([2, 4]))

    def test_add_users_grants_every_door_when_no_doors_are_given(self):
        self.assertEqual((True, True, True, True), self._written_door_access(None))

    def test_add_users_upserts_user_and_authorization_records_in_one_connection(self):
        user_table = FirmwareUserTable()
        authorization_table = MagicMock()
        successful_context = zk_with_tables(user_table, authorization_table)
        zk_instance = successful_context.__enter__.return_value

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
        self.assertEqual(1, len(user_table.upserts))
        authorization_table.upsert.assert_called_once()
        # Valid from 2000-01-01, so a controller whose clock reset to that day still lets the cards in.
        self.assertEqual(
            [('12345', '54321', '20000101', '99991231'), ('12346', '54322', '20000101', '99991231')],
            [(record.raw_data['CardNo'], record.raw_data['Pin'], record.raw_data['StartTime'], record.raw_data['EndTime'])
             for record in user_table.upserts[0]],
        )

    def _write(self, operation, users, user_table, operation_id='operation-split'):
        with patch('main.ZKAccess', return_value=zk_with_tables(user_table)), patch(
            'main.get_local_time',
            return_value='2026-04-17 00:00:00',
        ), patch('main.open', mock_open()), patch('main.print'):
            return operation(users, '10.0.0.15', 4370, operation_id=operation_id)

    def test_add_users_stores_new_pins_next_to_pins_the_controller_already_has(self):
        # The resync of 2026-10-14 sent 824 held pins and 17 new coworker pins in one write; the 17 never landed.
        user_table = FirmwareUserTable(pins={'51510', '51511'})

        result = self._write(
            main.add_users,
            [
                {'card': '795665084', 'pin': '51510'},
                {'card': '1314596814', 'pin': '12010'},
                {'card': '722355820', 'pin': '51511'},
                {'card': '310913662', 'pin': '12030'},
            ],
            user_table,
        )

        self.assertEqual({'51510', '51511', '12010', '12030'}, user_table.pins)
        self.assertEqual(
            [['51510', '51511'], ['12010', '12030']],
            [[record.pin for record in write] for write in user_table.upserts],
        )
        self.assertEqual(4, result['succeeded'])

    def test_add_users_reports_a_card_the_controller_did_not_store_as_failed(self):
        user_table = FirmwareUserTable(pins={'51510'}, refused={'12010'})

        result = self._write(
            main.add_users,
            [{'card': '795665084', 'pin': '51510'}, {'card': '1314596814', 'pin': '12010'}],
            user_table,
        )

        # The batch itself ran, so Laravel keeps the stored card and retries only the dropped one.
        self.assertTrue(result['success'])
        self.assertEqual(1, result['succeeded'])
        self.assertEqual(1, result['failed'])
        self.assertEqual(
            [('51510', True), ('12010', False)],
            [(item['pin'], item['success']) for item in result['results']],
        )
        self.assertIn('did not store', result['results'][1]['error'])

    def test_add_users_reads_the_user_table_again_after_a_failed_read(self):
        # A read straight after a 31-row write once failed with -11 on 185.146.179.174 (2026-10-14).
        user_table = FirmwareUserTable(pins={'51510'}, failing_reads={2})

        with patch('main.time.sleep'):
            result = self._write(
                main.add_users,
                [{'card': '795665084', 'pin': '51510'}, {'card': '1314596814', 'pin': '12010'}],
                user_table,
            )

        # The read is repeated, not the whole batch.
        self.assertEqual(2, result['succeeded'])
        self.assertEqual(2, len(user_table.upserts))

    def test_delete_users_narrows_a_pin_the_controller_does_not_have_yet(self):
        user_table = FirmwareUserTable(pins={'51510', '51520'})

        result = self._write(
            main.delete_users,
            [
                {'card': '795665084', 'pin': '51510', 'doors': [1]},
                {'card': '1314596814', 'pin': '12010', 'doors': [1]},
                {'card': '555845829', 'pin': '51520'},
            ],
            user_table,
        )

        self.assertEqual({'51510', '12010'}, user_table.pins)
        self.assertEqual(3, result['succeeded'])

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

    def _run_batch(self, operation, users, existing_rows):
        zk_instance = MagicMock()
        authorization_table = MagicMock()
        authorization_table.__iter__.side_effect = lambda: iter(existing_rows)
        zk_instance.table.side_effect = lambda name: authorization_table if name == 'UserAuthorize' else MagicMock()
        successful_context = MagicMock()
        successful_context.__enter__.return_value = zk_instance
        successful_context.__exit__.return_value = False

        with patch('main.ZKAccess', return_value=successful_context), patch(
            'main.get_local_time',
            return_value='2026-04-17 00:00:00',
        ), patch('main.open', mock_open()), patch('main.print'):
            result = operation(users, '10.0.0.15', 4370)

        self.assertTrue(result['success'])

        return authorization_table

    def test_add_users_clears_other_authorization_rows_of_a_pin_before_writing(self):
        existing_rows = [
            authorization_row('54321', 1, '15'),
            authorization_row('54321', 2, '15'),
            authorization_row('54322', 1, '1'),
            authorization_row('99999', 2, '15'),
        ]

        authorization_table = self._run_batch(
            main.add_users,
            [{'card': '12345', 'pin': '54321', 'doors': [1]}, {'card': '12346', 'pin': '54322', 'doors': [1]}],
            existing_rows,
        )

        # Only the pin with more than its timezone-1 row loses its rows; other pins are untouched.
        cleared = authorization_table.delete.call_args[0][0]
        self.assertEqual([('54321', 1), ('54321', 2)], [(row.pin, row.timezone_id) for row in cleared])
        authorization_table.upsert.assert_called_once()

    def test_add_users_leaves_a_single_timezone_one_row_to_the_upsert(self):
        authorization_table = self._run_batch(
            main.add_users,
            [{'card': '12345', 'pin': '54321', 'doors': [1]}],
            [authorization_row('54321', 1, '15')],
        )

        authorization_table.delete.assert_not_called()

    def test_delete_users_clears_every_authorization_row_of_a_deleted_pin(self):
        existing_rows = [
            authorization_row('54321', 1, '15'),
            authorization_row('54322', 1, '3'),
            authorization_row('54322', 2, '4'),
        ]

        authorization_table = self._run_batch(
            main.delete_users,
            [{'card': '12345', 'pin': '54321', 'doors': [1]}, {'card': '12346', 'pin': '54322'}],
            existing_rows,
        )

        cleared = authorization_table.delete.call_args[0][0]
        self.assertEqual([('54322', 1), ('54322', 2)], [(row.pin, row.timezone_id) for row in cleared])

    def test_a_failed_clear_does_not_stop_the_write(self):
        zk_instance = MagicMock()
        authorization_table = MagicMock()
        authorization_table.__iter__.side_effect = Exception('read failed')
        zk_instance.table.side_effect = lambda name: authorization_table if name == 'UserAuthorize' else MagicMock()
        successful_context = MagicMock()
        successful_context.__enter__.return_value = zk_instance
        successful_context.__exit__.return_value = False

        with patch('main.ZKAccess', return_value=successful_context) as zkteco, patch(
            'main.get_local_time',
            return_value='2026-04-17 00:00:00',
        ), patch('main.open', mock_open()), patch('main.print'):
            result = main.add_users([{'card': '12345', 'pin': '54321', 'doors': [1]}], '10.0.0.15', 4370)

        self.assertTrue(result['success'])
        self.assertEqual(1, zkteco.call_count)
        authorization_table.upsert.assert_called_once()

    def test_delete_users_deletes_records_and_narrows_kept_doors_in_one_connection(self):
        user_table = FirmwareUserTable(pins={'54321', '54322'})
        authorization_table = MagicMock()
        successful_context = zk_with_tables(user_table, authorization_table)

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
        deleted_records = user_table.deleted[0]
        self.assertEqual(['54322'], [record['pin'] for record in deleted_records])

        rewritten_records = user_table.upserts[0]
        self.assertEqual([('54321', '20000101')], [(record.pin, record.raw_data['StartTime']) for record in rewritten_records])
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

    def test_add_users_serializes_same_device_requests_when_called_directly(self):
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

        with patch('main.ZKAccess', side_effect=lambda *args, **kwargs: FakeZkContext()), patch('main.get_local_time', return_value='2026-04-17 00:00:00'), patch('main.open', mock_open()), patch(
            'main.print'
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first_call = executor.submit(main.add_users, [{'card': '12345', 'pin': '54321', 'doors': [1, 2]}], '10.0.0.15', 4370)
                self.assertTrue(first_started.wait(timeout=1))
                second_call = executor.submit(main.add_users, [{'card': '12346', 'pin': '54322', 'doors': [1, 2]}], '10.0.0.15', 4370)

                self.assertTrue(first_call.result(timeout=1)['success'])
                self.assertTrue(second_call.result(timeout=1)['success'])

        self.assertEqual(1, state['max_concurrent'])
