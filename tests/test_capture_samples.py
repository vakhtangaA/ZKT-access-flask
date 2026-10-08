import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import capture_samples

USER_ROWS = [
    {'CardNo': '2409341497', 'Pin': '20541', 'Password': '1234', 'Group': '0', 'StartTime': '0', 'EndTime': '0', 'SuperAuthorize': '0'},
    {'CardNo': '2410329641', 'Pin': '561', 'Password': '', 'Group': '0', 'StartTime': '0', 'EndTime': '0', 'SuperAuthorize': '0'},
    {'CardNo': '3110348897', 'Pin': '10603', 'Password': '', 'Group': '0', 'StartTime': '0', 'EndTime': '0', 'SuperAuthorize': '0'},
]
AUTHORIZE_ROWS = [
    {'Pin': '20541', 'AuthorizeTimezoneId': '1', 'AuthorizeDoorId': '15'},
    {'Pin': '561', 'AuthorizeTimezoneId': '1', 'AuthorizeDoorId': '5'},
    {'Pin': '561', 'AuthorizeTimezoneId': '2', 'AuthorizeDoorId': '8'},
    {'Pin': '99999', 'AuthorizeTimezoneId': '1', 'AuthorizeDoorId': '1'},
]
TRANSACTION_ROWS = [
    {'Cardno': '2409341497', 'Pin': '20541', 'Verified': '1', 'DoorID': '2', 'EventType': '0', 'InOutState': '0', 'Time_second': '100'},
    {'Cardno': '2410329641', 'Pin': '561', 'Verified': '1', 'DoorID': '2', 'EventType': '23', 'InOutState': '0', 'Time_second': '200'},
    {'Cardno': '0', 'Pin': '0', 'Verified': '200', 'DoorID': '1', 'EventType': '202', 'InOutState': '0', 'Time_second': '300'},
    {'Cardno': '0', 'Pin': '0', 'Verified': '200', 'DoorID': '1', 'EventType': '4242', 'InOutState': '0', 'Time_second': '400'},
]
REAL_VALUES = ['2409341497', '20541', '2410329641', '561', '3110348897', '10603', '99999', '1234']
FIELD_FOR_QUERY = {'pin': ('Pin',), 'card': ('Cardno', 'CardNo')}


class FakeRecord:
    def __init__(self, raw):
        self._raw_data = raw
        self.raw_data = raw


class FakeTable:
    def __init__(self, rows, honor_filters, filters=None):
        self.rows = rows
        self.honor_filters = honor_filters
        self.filters = filters or {}

    def where(self, **filters):
        return FakeTable(self.rows, self.honor_filters, {**self.filters, **filters})

    def __iter__(self):
        rows = self.rows

        if self.filters:
            if not self.honor_filters:
                return iter([])

            rows = [
                row for row in rows
                if all(any(row.get(raw) == value for raw in FIELD_FOR_QUERY[field]) for field, value in self.filters.items())
            ]

        return iter([FakeRecord(row) for row in rows])


class FakeController:
    def __init__(self, honor_filters=True, failing_tables=()):
        self.tables = {
            'User': USER_ROWS,
            'UserAuthorize': AUTHORIZE_ROWS,
            'Transaction': TRANSACTION_ROWS,
            'Timezone': [{'TimezoneId': '1', 'SunTime1': '86340'}],
            'Holiday': [],
        }
        self.honor_filters = honor_filters
        self.failing_tables = failing_tables
        self.sdk = SimpleNamespace(get_device_data_count=lambda name: len(self.tables[name]))
        self.parameters = SimpleNamespace(serial_number='SN-1', lock_count=4, reader_count=4, aux_in_count=4, aux_out_count=4)
        self.doors = [
            SimpleNamespace(number=number, parameters=SimpleNamespace(
                open_time_tz=1 if number == 2 else 0, active_time_tz=1, punch_interval=0, cancel_open_day=0, verify_mode=1,
            ))
            for number in (1, 2, 3, 4)
        ]

    def table(self, name):
        if name in self.failing_tables:
            raise RuntimeError(f'{name} unreadable')

        return FakeTable(self.tables[name], self.honor_filters)


class AnonymizerTest(unittest.TestCase):
    def test_replaces_identities_with_other_digits_of_the_same_length(self):
        anonymizer = capture_samples.Anonymizer()

        for field, value in (('Pin', '20541'), ('CardNo', '2409341497'), ('Cardno', '2409341497'), ('Password', '1234')):
            replaced = anonymizer.value(field, value)

            self.assertNotEqual(value, replaced)
            self.assertEqual(len(value), len(replaced))
            self.assertTrue(replaced.isdigit())

    def test_the_same_value_always_gets_the_same_replacement_and_different_values_differ(self):
        anonymizer = capture_samples.Anonymizer()

        self.assertEqual(anonymizer.value('Pin', '20541'), anonymizer.value('Pin', '20541'))
        self.assertEqual(anonymizer.value('CardNo', '1'), anonymizer.value('Cardno', '1'))
        self.assertNotEqual(anonymizer.value('Pin', '20541'), anonymizer.value('Pin', '20542'))

    def test_keeps_empty_zero_and_other_fields_and_can_be_switched_off(self):
        anonymizer = capture_samples.Anonymizer()

        self.assertEqual('', anonymizer.value('Pin', ''))
        self.assertEqual('0', anonymizer.value('Cardno', '0'))
        self.assertEqual('23', anonymizer.value('EventType', '23'))
        self.assertEqual('20541', capture_samples.Anonymizer(enabled=False).value('Pin', '20541'))


class CaptureTest(unittest.TestCase):
    def snapshot(self, **controller_options):
        return capture_samples.capture(FakeController(**controller_options), 'C3-400', rows_to_keep=2)

    def test_the_snapshot_is_json_and_contains_no_real_pin_card_or_password(self):
        text = json.dumps(self.snapshot(), ensure_ascii=False)

        for real_value in REAL_VALUES:
            self.assertNotIn(f'"{real_value}"', text)
            self.assertNotIn(f',{real_value},', text)
            self.assertNotIn(f',{real_value}"', text)
            self.assertNotIn(f'"{real_value},', text)

    def test_real_values_are_kept_when_anonymizing_is_off(self):
        snapshot = capture_samples.capture(
            FakeController(), 'C3-400', anonymizer=capture_samples.Anonymizer(enabled=False)
        )

        self.assertIn('2409341497', json.dumps(snapshot))
        self.assertFalse(snapshot['anonymized'])

    def test_raw_head_keeps_the_controller_header_and_the_requested_number_of_rows(self):
        table = self.snapshot()['tables']['UserAuthorize']

        self.assertEqual('Pin,AuthorizeTimezoneId,AuthorizeDoorId', table['raw_head'][0])
        self.assertEqual(3, len(table['raw_head']))
        self.assertEqual(4, table['rows_read'])
        self.assertEqual(4, table['count_reported_by_controller'])

    def test_authorization_masks_are_decoded_from_the_raw_value(self):
        authorization = self.snapshot()['analysis']['authorization']

        self.assertEqual({'15': 1, '5': 1, '8': 1, '1': 1}, {mask: item['rows'] for mask, item in authorization['masks'].items()})
        self.assertEqual([1, 3], authorization['masks']['5']['doors'])
        self.assertEqual([4], authorization['masks']['8']['doors'])
        self.assertEqual([1, 2, 3, 4], authorization['masks']['15']['doors'])
        self.assertEqual(1, authorization['pins_with_more_than_one_row'])
        self.assertEqual({'1': 3, '2': 1}, authorization['timezones'])

    def test_user_analysis_finds_users_and_rows_that_do_not_match(self):
        users = self.snapshot()['analysis']['users']

        self.assertEqual(3, users['users'])
        self.assertEqual(1, users['users_without_authorization_rows'])
        self.assertEqual(1, users['authorization_rows_without_user'])

    def test_transaction_analysis_names_events_and_flags_unknown_codes(self):
        transactions = self.snapshot()['analysis']['transactions']

        self.assertEqual('Access Denied', transactions['event_codes']['23']['name'])
        self.assertEqual('Exit button Open', transactions['event_codes']['202']['name'])
        self.assertEqual('not in the pyzkaccess event list', transactions['event_codes']['4242']['name'])
        self.assertEqual({'2': 2, '1': 2}, transactions['doors'])

    def test_a_controller_that_honours_filters_is_reported_as_honouring_them(self):
        probes = self.snapshot(honor_filters=True)['filter_probes']

        for name in ('User.pin', 'UserAuthorize.pin', 'Transaction.pin', 'Transaction.card'):
            self.assertTrue(probes[name]['existing']['honored'], name)
            self.assertTrue(probes[name]['missing']['honored'], name)

    def test_a_controller_that_ignores_filters_is_reported_with_the_counts_that_prove_it(self):
        probes = self.snapshot(honor_filters=False)['filter_probes']

        existing = probes['Transaction.card']['existing']
        self.assertFalse(existing['honored'])
        self.assertEqual(1, existing['expected'])
        self.assertEqual(0, existing['returned'])
        self.assertTrue(probes['Transaction.card']['missing']['honored'])

    def test_door_settings_show_which_door_is_held_open(self):
        settings = self.snapshot()['door_settings']

        self.assertEqual('1', settings['2']['open_time_tz'])
        self.assertEqual('0', settings['1']['open_time_tz'])
        self.assertEqual('SN-1', self.snapshot()['device']['serial_number'])

    def test_one_unreadable_table_does_not_lose_the_rest(self):
        snapshot = self.snapshot(failing_tables=('Holiday',))

        self.assertIn('error', snapshot['tables']['Holiday'])
        self.assertEqual(4, snapshot['tables']['UserAuthorize']['rows_read'])
        self.assertIn('authorization', snapshot['analysis'])

    def test_events_can_be_skipped(self):
        snapshot = capture_samples.capture(FakeController(), 'C3-400', skip_transactions=True)

        self.assertNotIn('Transaction', snapshot['tables'])
        self.assertNotIn('transactions', snapshot['analysis'])
        self.assertNotIn('Transaction.pin', snapshot['filter_probes'])


class MainTest(unittest.TestCase):
    def run_main(self, zk_factory, *extra_arguments):
        with tempfile.TemporaryDirectory() as directory:
            output = os.path.join(directory, 'snapshot.json')
            arguments = ['capture_samples.py', '--ip', '10.0.0.15', '--out', output, *extra_arguments]

            with patch('sys.argv', arguments), patch('capture_samples.ZKAccess', zk_factory), patch('builtins.print'):
                code = capture_samples.main()

            written = open(output, encoding='utf-8').read() if os.path.exists(output) else None

        return code, written

    def test_an_unreachable_controller_exits_with_an_error_and_writes_nothing(self):
        code, written = self.run_main(MagicMock(side_effect=RuntimeError('cannot connect')))

        self.assertEqual(1, code)
        self.assertIsNone(written)

    def test_a_reachable_controller_writes_an_anonymized_snapshot(self):
        context = MagicMock()
        context.__enter__.return_value = FakeController()
        context.__exit__.return_value = False

        code, written = self.run_main(MagicMock(return_value=context))

        self.assertEqual(0, code)
        self.assertTrue(json.loads(written)['anonymized'])
        self.assertNotIn('2409341497', written)


if __name__ == '__main__':
    unittest.main()
