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

        self.assertEqual({'online': True, 'error': None}, result)
        zkteco.assert_called_once_with(
            connstr='protocol=TCP,ipaddress=10.0.0.15,port=4370,timeout=9000,passwd=',
            device_model=main.ZK400,
        )

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

        self.assertEqual({'online': False, 'error': 'SDK error -307: Connection attempt failed'}, result)
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
