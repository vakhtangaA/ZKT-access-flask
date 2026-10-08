"""Read-only snapshot of what a ZKTeco controller really returns.

Run it on the machine that has the PullSDK, once per controller model you care about:

    python capture_samples.py --ip 185.146.179.173 --model C3-400

It only reads tables, counts and parameters. It never writes to the controller, and it takes a
normal session, so run it when nothing else is syncing that controller.

The JSON file it writes is meant to be shared, so cards, pins and passwords are replaced by
different numbers of the same length (the same value always maps to the same replacement).
Pass --no-anonymize to keep the real values.

What it records, and why:
  * the raw header and rows of each table, exactly as the controller sent them, so a fake
    controller can reproduce the real format;
  * how the authorization masks, timezones and events are really distributed;
  * whether the controller honours a pin or card filter on each table (the controller ignored a
    card filter on the transaction table once, so the app now filters in Python);
  * the door settings that decide whether a door is held open on a schedule.
"""
import argparse
import json
import random
import sys
from collections import Counter
from datetime import datetime

from pyzkaccess import ZKAccess
from pyzkaccess.enums import EVENT_TYPES
from pyzkaccess.tables import UserAuthorize

from main import allowed_door_numbers, build_connstr, resolve_device_model

TABLES = ('User', 'UserAuthorize', 'Transaction', 'Timezone', 'Holiday')
IDENTITY_FIELDS = {'pin': 'pin', 'cardno': 'card'}
SECRET_FIELDS = ('password',)
MISSING_VALUE = '999999999'


class Anonymizer:
    """Replaces pins, cards and passwords with other digits of the same length.

    A value always maps to the same replacement, so rows still line up across tables. Empty values
    and "0" (an event without a card, for example) are kept as they are.
    """

    def __init__(self, enabled=True, seed=20261004):
        self.enabled = enabled
        self._random = random.Random(seed)
        self._maps = {'pin': {}, 'card': {}, 'secret': {}}

    def value(self, field, value):
        kind = self._kind(field)

        if not self.enabled or kind is None or value in ('', '0', None):
            return value

        mapping = self._maps[kind]

        if value not in mapping:
            mapping[value] = self._fresh(value, set(mapping.values()))

        return mapping[value]

    def row(self, row):
        return {field: self.value(field, value) for field, value in row.items()}

    @staticmethod
    def _kind(field):
        lowered = field.lower()

        if lowered in SECRET_FIELDS:
            return 'secret'

        return IDENTITY_FIELDS.get(lowered)

    def _fresh(self, original, used):
        length = len(original)

        for _ in range(1000):
            first = str(self._random.randint(1, 9)) if length > 1 else str(self._random.randint(0, 9))
            candidate = first + ''.join(self._random.choice('0123456789') for _ in range(length - 1))

            if candidate not in used and candidate != original:
                return candidate

        return 'x' * length


def raw_row(record):
    """The values exactly as the controller returned them, under the controller's own headers."""
    return dict(getattr(record, '_raw_data', None) or record.raw_data)


def read_table(zk, name):
    return [raw_row(record) for record in zk.table(name)]


def describe_table(zk, name, rows, rows_to_keep, anonymizer):
    headers = list(rows[0].keys()) if rows else []
    kept = [anonymizer.row(row) for row in rows[:rows_to_keep]]
    result = {
        'rows_read': len(rows),
        'headers': headers,
        'raw_head': [','.join(headers)] + [','.join(row.get(header, '') for header in headers) for row in kept],
    }

    try:
        result['count_reported_by_controller'] = zk.sdk.get_device_data_count(name)
    except Exception as exception:
        result['count_reported_by_controller'] = f'error: {exception}'

    return result


def doors_of_mask(mask):
    try:
        doors = UserAuthorize().with_raw_data({'AuthorizeDoorId': mask}).doors
    except (TypeError, ValueError):
        return []

    return allowed_door_numbers(doors)


def authorize_analysis(rows):
    masks = Counter()
    timezones = Counter()
    rows_per_pin = Counter()

    for row in rows:
        masks[row.get('AuthorizeDoorId', '')] += 1
        timezones[row.get('AuthorizeTimezoneId', '')] += 1
        rows_per_pin[row.get('Pin', '')] += 1

    return {
        'rows': len(rows),
        'distinct_pins': len(rows_per_pin),
        'masks': {
            mask: {'rows': count, 'doors': doors_of_mask(mask)}
            for mask, count in sorted(masks.items(), key=lambda item: -item[1])
        },
        'timezones': dict(timezones),
        'pins_with_more_than_one_row': sum(1 for count in rows_per_pin.values() if count > 1),
    }


def user_analysis(user_rows, authorize_rows):
    user_pins = {row.get('Pin') for row in user_rows}
    authorized_pins = {row.get('Pin') for row in authorize_rows}

    return {
        'users': len(user_rows),
        'users_without_authorization_rows': len(user_pins - authorized_pins),
        'authorization_rows_without_user': len(authorized_pins - user_pins),
        'groups': dict(Counter(row.get('Group', '') for row in user_rows)),
        'super_authorize': dict(Counter(row.get('SuperAuthorize', '') for row in user_rows)),
    }


def transaction_analysis(rows):
    codes = Counter()
    doors = Counter()
    times = []

    for row in rows:
        codes[row.get('EventType', '')] += 1
        doors[row.get('DoorID', '')] += 1

        if row.get('Time_second'):
            times.append(row['Time_second'])

    def name(code):
        try:
            return EVENT_TYPES[int(code)].__doc__
        except (KeyError, ValueError):
            return 'not in the pyzkaccess event list'

    return {
        'events': len(rows),
        'event_codes': {
            code: {'events': count, 'name': name(code)}
            for code, count in sorted(codes.items(), key=lambda item: -item[1])
        },
        'doors': dict(doors),
        'raw_time_examples': times[:3] + times[-3:],
        'distinct_pins': len({row.get('Pin') for row in rows}),
    }


def filter_probe(zk, table_name, rows, query_field, raw_key, anonymizer):
    """Ask the controller to filter on a value and compare with filtering the full read ourselves."""
    sample = next((row.get(raw_key) for row in rows if row.get(raw_key) not in (None, '', '0')), None)

    if sample is None:
        return {'skipped': f'no {raw_key} value to probe with'}

    probes = {}

    for label, value in (('existing', sample), ('missing', MISSING_VALUE)):
        expected = sum(1 for row in rows if row.get(raw_key) == value)

        try:
            returned = len(list(zk.table(table_name).where(**{query_field: value})))
        except Exception as exception:
            probes[label] = {'error': str(exception)}
            continue

        probes[label] = {
            'value': anonymizer.value(raw_key, value) if label == 'existing' else value,
            'expected': expected,
            'returned': returned,
            'honored': expected == returned,
        }

    return probes


def device_info(zk, model):
    info = {'model_argument': model}

    for attribute in ('serial_number', 'lock_count', 'reader_count', 'aux_in_count', 'aux_out_count'):
        try:
            info[attribute] = getattr(zk.parameters, attribute)
        except Exception as exception:
            info[attribute] = f'error: {exception}'

    return info


def door_settings(zk):
    settings = {}

    for door in zk.doors:
        number = str(getattr(door, 'number', len(settings) + 1))
        settings[number] = {}

        for attribute in ('open_time_tz', 'active_time_tz', 'punch_interval', 'cancel_open_day', 'verify_mode'):
            try:
                settings[number][attribute] = str(getattr(door.parameters, attribute))
            except Exception as exception:
                settings[number][attribute] = f'error: {exception}'

    return settings


def section(name, build):
    try:
        return build()
    except Exception as exception:
        print(f'  ! {name} failed: {exception}', file=sys.stderr)

        return {'error': str(exception)}


def capture(zk, model, rows_to_keep=40, anonymizer=None, skip_transactions=False):
    anonymizer = anonymizer or Anonymizer()
    tables = {}
    rows = {}

    for name in TABLES:
        if name == 'Transaction' and skip_transactions:
            continue

        def read(table_name=name):
            rows[table_name] = read_table(zk, table_name)

            return describe_table(zk, table_name, rows[table_name], rows_to_keep, anonymizer)

        tables[name] = section(f'table {name}', read)

    analysis = {}

    if 'UserAuthorize' in rows:
        analysis['authorization'] = section('authorization analysis', lambda: authorize_analysis(rows['UserAuthorize']))

    if 'User' in rows and 'UserAuthorize' in rows:
        analysis['users'] = section('user analysis', lambda: user_analysis(rows['User'], rows['UserAuthorize']))

    if 'Transaction' in rows:
        analysis['transactions'] = section('transaction analysis', lambda: transaction_analysis(rows['Transaction']))

    probes = {}

    for table_name, query_field, raw_key in (
        ('User', 'pin', 'Pin'),
        ('UserAuthorize', 'pin', 'Pin'),
        ('Transaction', 'pin', 'Pin'),
        ('Transaction', 'card', 'Cardno'),
    ):
        if table_name in rows:
            probes[f'{table_name}.{query_field}'] = section(
                f'filter probe {table_name}.{query_field}',
                lambda t=table_name, q=query_field, r=raw_key: filter_probe(zk, t, rows[t], q, r, anonymizer),
            )

    return {
        'captured_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'anonymized': anonymizer.enabled,
        'device': section('device info', lambda: device_info(zk, model)),
        'door_settings': section('door settings', lambda: door_settings(zk)),
        'tables': tables,
        'analysis': analysis,
        'filter_probes': probes,
    }


def print_summary(snapshot):
    print('\nSummary')
    print(f"  device: {snapshot['device']}")

    for name, table in snapshot['tables'].items():
        print(f"  {name}: {table.get('rows_read', table)} rows, headers {table.get('headers')}")

    authorization = snapshot['analysis'].get('authorization', {})

    for mask, details in list(authorization.get('masks', {}).items())[:8]:
        print(f"  mask {mask}: {details['rows']} rows -> doors {details['doors']}")

    for name, probe in snapshot['filter_probes'].items():
        print(f'  filter {name}: {probe}')

    print(f"  door settings: {snapshot['door_settings']}")


def main():
    parser = argparse.ArgumentParser(description='Read-only snapshot of a ZKTeco controller.')
    parser.add_argument('--ip', required=True)
    parser.add_argument('--port', type=int, default=4370)
    parser.add_argument('--model', default='C3-400')
    parser.add_argument('--password', default='')
    parser.add_argument('--timeout', type=int, default=10000)
    parser.add_argument('--rows', type=int, default=40, help='raw rows to keep per table')
    parser.add_argument('--skip-transactions', action='store_true', help='do not read the event table')
    parser.add_argument('--no-anonymize', action='store_true', help='keep real cards, pins and passwords')
    parser.add_argument('--out', help='output file (default: controller_samples_<ip>_<time>.json)')
    arguments = parser.parse_args()

    connstr = build_connstr(arguments.ip, arguments.port, arguments.timeout, arguments.password)
    output = arguments.out or f"controller_samples_{arguments.ip.replace('.', '-')}_{datetime.now():%Y%m%d-%H%M%S}.json"

    print(f'Connecting to {arguments.ip}:{arguments.port} ({arguments.model}), read only ...')

    try:
        with ZKAccess(connstr=connstr, device_model=resolve_device_model(arguments.model)) as zk:
            snapshot = capture(
                zk,
                arguments.model,
                rows_to_keep=arguments.rows,
                anonymizer=Anonymizer(enabled=not arguments.no_anonymize),
                skip_transactions=arguments.skip_transactions,
            )
    except Exception as exception:
        print(f'Could not read the controller: {exception!r}', file=sys.stderr)
        print('Nothing was written. Check the IP, the port, that this machine has the PullSDK, and that no other session is using the controller.', file=sys.stderr)

        return 1

    with open(output, 'w', encoding='utf-8') as handle:
        json.dump(snapshot, handle, ensure_ascii=False, indent=2)

    print_summary(snapshot)
    print(f"\nWritten to {output} ({'anonymized' if snapshot['anonymized'] else 'REAL VALUES, do not share'})")

    return 0


if __name__ == '__main__':
    sys.exit(main())
