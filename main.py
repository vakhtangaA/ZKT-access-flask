from pyzkaccess import ZKAccess, ZK200, ZK100, ZK400
from pyzkaccess.common import ZKDatetimeUtils
from pyzkaccess.enums import EVENT_TYPES
from pyzkaccess.tables import User, UserAuthorize
from datetime import datetime
from uuid import uuid4
import ping3
import time
import sys
import pytz
from device_locks import output_lock, with_device_lock
from observability import capture_exception

MODEL_MAP = {
    'C3-100': ZK100,
    'ZK100': ZK100,
    'C3-200': ZK200,
    'ACP-200': ZK200,
    'ZK200': ZK200,
    'C3-400': ZK400,
    'ZK400': ZK400,
}

RETRY_DELAY_SECONDS = 0.5
MAX_WRITE_ATTEMPTS = 2

def get_local_time():
    tz = pytz.timezone('Asia/Tbilisi')
    return datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')

connstr = "protocol=TCP,ipaddress=149.3.34.167,port=4370,timeout=10000,passwd="


def resolve_device_model(model):
    if model is None:
        return ZK200

    normalized_model = str(model).strip().upper()

    return MODEL_MAP.get(normalized_model, ZK200)


def normalize_timeout(timeout, default):
    try:
        return int(timeout)
    except (TypeError, ValueError):
        return default


def build_connstr(ip, port, timeout, password):
    normalized_timeout = normalize_timeout(timeout, 4000)
    normalized_password = '' if password is None else str(password)

    return f"protocol=TCP,ipaddress={ip},port={port},timeout={normalized_timeout},passwd={normalized_password}"


def write_output(message):
    with output_lock:
        with open('output.txt', 'a') as output:
            output.write(message + "\n")


def log_retry_attempt(operation, ip, attempt, exception):
    message = (
        f"[{get_local_time()}] {operation} on device with ip: {ip} failed on TRY #{attempt}: "
        f"{str(exception)}. Retrying once."
    )
    print(message)
    write_output(message)
    time.sleep(RETRY_DELAY_SECONDS)

def ping_host(ip):
    try:
        rtt = ping3.ping(ip)
        if rtt is not None and rtt is not False:
            print(f"[{get_local_time()}] Ping successful. Round-trip time: {rtt} ms")
            write_output(f"[{get_local_time()}] Ping successful. Round-trip time: {rtt} ms")
            return f"Ping successful. Round-trip time: {rtt} ms"
        else:
            print(f"[{get_local_time()}] Ping Failed")
            write_output(f"[{get_local_time()}] Ping Failed")
            return 'Ping Failed'
    except Exception as e:
        write_output(f"[{get_local_time()}] An error occurred: {str(e)}")

def ping_host_endpoint(ip):
    try:
        rtt = ping3.ping(ip)
        if rtt is not None and rtt is not False:
            print(f"[{get_local_time()}] Ping successful. Round-trip time: {rtt} ms")
            write_output(f"[{get_local_time()}] Ping successful. Round-trip time: {rtt} ms")
            return True
        else:
            print('Ping Failed')
            write_output('Ping Failed')
            return False
    except Exception as e:
        print(f"[{get_local_time()}] An error occurred: {str(e)}")
        write_output(f"[{get_local_time()}] An error occurred: {str(e)}")

def write_log(text):
    with output_lock:
        with open('logs/exeptions.txt', 'a') as logFile:
            dr = str(datetime.now())+' - '
            text = dr + text
            logFile.write(text)
            logFile.write('\n')
            logFile.close()

def write_log_success(text):
    with output_lock:
        with open('logs/success.txt', 'a') as logFile:
            dr = str(datetime.now())+' - '
            text = dr + text
            logFile.write(text)
            logFile.write('\n')
            logFile.close()

def write_user_authorization(zk, pin, door_access, ip):
    """Overwrite the door mask of a pin and log what the controller reports back.

    The read-back separates a write the controller ignored from one it applied and later
    replaced: the first logs the old mask here, the second logs the new one.
    """
    try:
        zk.table('UserAuthorize').where(pin=pin).delete_all()
    except Exception as exception:
        write_output(f"[{get_local_time()}] Could not clear authorization rows of pin {pin} on {ip}: {exception}")

    zk.table('UserAuthorize').upsert([{'pin': pin, 'timezone_id': 1, 'doors': door_access}])
    print(f"[{get_local_time()}] Authorized To Doors: {door_access}")
    write_output(f"[{get_local_time()}] Authorized To Doors: {door_access}")

    try:
        stored = [
            {'timezone_id': row.timezone_id, 'doors': [number for number, allowed in enumerate(list(row.doors), start=1) if allowed]}
            for row in zk.table('UserAuthorize').where(pin=pin)
        ]
        write_output(f"[{get_local_time()}] Controller reports authorization of pin {pin} on {ip} after write: {stored}")
    except Exception as exception:
        write_output(f"[{get_local_time()}] Could not read back authorization of pin {pin} on {ip}: {exception}")


def add_user(card, pin, ip, port=4370, doors=None, timeout=4000, password='', model=None):
    def operation():
        print(f"[{get_local_time()}] Adding user with card: {card} and pin: {pin} on device with ip: {ip}")
        write_output(f"[{get_local_time()}] Adding user with card: {card} and pin: {pin} on device with ip: {ip} on TRY #1")
        connstr = build_connstr(ip, port, timeout, password)
        device_model = resolve_device_model(model)

        if doors:
            door_access = (1 in doors, 2 in doors, 3 in doors, 4 in doors)
        else:
            door_access = (True, True, True, True)

        try:
            with ZKAccess(connstr=connstr, device_model=device_model) as zk:
                user = User(card=card, pin=pin, start_time=datetime.now(), end_time=datetime(9999, 12, 31, 23, 59, 59),
                            super_authorize=False).with_zk(zk)
                user.save()
                print(f"[{get_local_time()}] IP: {ip} CARD: {card} ADDED SUCCESS")
                write_output(f"[{get_local_time()}] IP: {ip} CARD: {card} ADDED SUCCESS")

                write_user_authorization(zk, pin, door_access, ip)

            return True
        except Exception as ex:
            log_retry_attempt('Adding user', ip, 1, ex)
            print(f"[{get_local_time()}] Adding user with card: {card} and pin: {pin} on device with ip: {ip} on TRY #2")
            write_output(f"[{get_local_time()}] Adding user with card: {card} and pin: {pin} on device with ip: {ip} on TRY #2")
            try:
                with ZKAccess(connstr=connstr, device_model=device_model) as zk:
                    user = User(card=card, pin=pin, start_time=datetime.now(), end_time=datetime(9999, 12, 31, 23, 59, 59),
                                super_authorize=False).with_zk(zk)
                    user.save()
                    print(f"[{get_local_time()}] IP: {ip} CARD: {card} ADDED SUCCESS ON TRY #2")
                    write_output(f"[{get_local_time()}] IP: {ip} CARD: {card} ADDED SUCCESS ON TRY #2")

                    write_user_authorization(zk, pin, door_access, ip)

                return True
            except Exception as ex:
                text = f"[{get_local_time()}] Exception when adding user! Device: {ip} - {str(ex)} + '\n' + {ping_host(ip)} + '\n'"
                write_output(text)
                capture_exception(ex, device_ip=ip, operation='add_user', port=port, model=model)
                print(text + "\n")
                return False

        return True

    return with_device_lock(ip, port, operation)


def add_users(users, ip, port=4370, timeout=4000, password='', model=None, operation_id=None):
    """Upsert multiple users and their door authorizations in one SDK session."""
    operation_id = operation_id or str(uuid4())
    normalized_users = list(users or [])

    def operation():
        started_at = time.monotonic()

        if not normalized_users:
            return {
                'success': True,
                'operation_id': operation_id,
                'total': 0,
                'succeeded': 0,
                'failed': 0,
                'results': [],
            }

        user_records = []
        authorization_records = []
        for item in normalized_users:
            doors = item.get('doors')
            door_access = (True, True, True, True) if not doors else (
                1 in doors,
                2 in doors,
                3 in doors,
                4 in doors,
            )
            user_records.append({
                'card': str(item.get('card', '')),
                'pin': str(item.get('pin', '')),
                'start_time': datetime.now(),
                'end_time': datetime(9999, 12, 31, 23, 59, 59),
                'super_authorize': False,
            })
            authorization_records.append({
                'pin': str(item.get('pin', '')),
                'timezone_id': 1,
                'doors': door_access,
            })

        last_exception = None
        for attempt in range(1, MAX_WRITE_ATTEMPTS + 1):
            try:
                print(f"[{get_local_time()}] Batch {operation_id} writing {len(normalized_users)} users to {ip} attempt {attempt}")
                with ZKAccess(
                    connstr=build_connstr(ip, port, timeout, password),
                    device_model=resolve_device_model(model),
                ) as zk:
                    zk.table('User').upsert(user_records)
                    zk.table('UserAuthorize').upsert(authorization_records)

                elapsed_ms = int(round((time.monotonic() - started_at) * 1000))
                results = [
                    {
                        'card': item.get('card'),
                        'pin': item.get('pin'),
                        'success': True,
                    }
                    for item in normalized_users
                ]
                write_output(
                    f"[{get_local_time()}] Batch {operation_id} completed on {ip}: "
                    f"{len(normalized_users)} users in {elapsed_ms} ms"
                )
                return {
                    'success': True,
                    'operation_id': operation_id,
                    'total': len(results),
                    'succeeded': len(results),
                    'failed': 0,
                    'elapsed_ms': elapsed_ms,
                    'results': results,
                }
            except Exception as exception:
                last_exception = exception
                if attempt < MAX_WRITE_ATTEMPTS:
                    retry_delay = RETRY_DELAY_SECONDS * (2 ** (attempt - 1))
                    write_output(
                        f"[{get_local_time()}] Batch {operation_id} failed on {ip} "
                        f"attempt {attempt}; retrying in {retry_delay}s: {exception}"
                    )
                    time.sleep(retry_delay)

        elapsed_ms = int(round((time.monotonic() - started_at) * 1000))
        error_message = str(last_exception) if last_exception else 'Unknown batch write failure'
        capture_exception(
            last_exception or RuntimeError(error_message),
            device_ip=ip,
            operation='add_users',
            port=port,
            model=model,
        )
        results = [
            {
                'card': item.get('card'),
                'pin': item.get('pin'),
                'success': False,
                'error': error_message,
            }
            for item in normalized_users
        ]
        return {
            'success': False,
            'operation_id': operation_id,
            'total': len(results),
            'succeeded': 0,
            'failed': len(results),
            'elapsed_ms': elapsed_ms,
            'message': 'Failed to write users to controller',
            'results': results,
        }

    return with_device_lock(ip, port, operation)


def delete_users(users, ip, port=4370, timeout=4000, password='', model=None, operation_id=None):
    """Remove multiple users in one SDK session, narrowing the ones that keep some doors.

    An item without `doors` is deleted from the device. An item with `doors` keeps its user
    record and only has its door mask overwritten, which is how a user keeps entrance access
    after losing the elevator. `userauthorize` is keyed by Pin and AuthorizeTimezoneId, so the
    upsert replaces the mask in place; a failed write never leaves the user off the device.
    """
    operation_id = operation_id or str(uuid4())
    normalized_users = list(users or [])

    def operation():
        started_at = time.monotonic()

        if not normalized_users:
            return {
                'success': True,
                'operation_id': operation_id,
                'total': 0,
                'succeeded': 0,
                'failed': 0,
                'rewritten': 0,
                'results': [],
            }

        delete_records = []
        rewrite_user_records = []
        rewrite_authorization_records = []
        for item in normalized_users:
            card = str(item.get('card', ''))
            pin = str(item.get('pin', ''))
            doors = item.get('doors')

            if not doors:
                delete_records.append({
                    'card': card,
                    'pin': pin,
                    'super_authorize': True,
                })
                continue

            rewrite_user_records.append({
                'card': card,
                'pin': pin,
                'start_time': datetime.now(),
                'end_time': datetime(9999, 12, 31, 23, 59, 59),
                'super_authorize': False,
            })
            rewrite_authorization_records.append({
                'pin': pin,
                'timezone_id': 1,
                'doors': (1 in doors, 2 in doors, 3 in doors, 4 in doors),
            })

        last_exception = None
        for attempt in range(1, MAX_WRITE_ATTEMPTS + 1):
            try:
                print(
                    f"[{get_local_time()}] Batch {operation_id} removing {len(delete_records)} users "
                    f"and narrowing {len(rewrite_user_records)} on {ip} attempt {attempt}"
                )
                with ZKAccess(
                    connstr=build_connstr(ip, port, timeout, password),
                    device_model=resolve_device_model(model),
                ) as zk:
                    if delete_records:
                        zk.table('User').delete(delete_records)

                    if rewrite_user_records:
                        zk.table('User').upsert(rewrite_user_records)
                        zk.table('UserAuthorize').upsert(rewrite_authorization_records)

                elapsed_ms = int(round((time.monotonic() - started_at) * 1000))
                results = [
                    {
                        'card': item.get('card'),
                        'pin': item.get('pin'),
                        'success': True,
                    }
                    for item in normalized_users
                ]
                write_output(
                    f"[{get_local_time()}] Batch {operation_id} removed {len(results)} users on {ip} "
                    f"({len(rewrite_user_records)} rewritten) in {elapsed_ms} ms"
                )
                return {
                    'success': True,
                    'operation_id': operation_id,
                    'total': len(results),
                    'succeeded': len(results),
                    'failed': 0,
                    'rewritten': len(rewrite_user_records),
                    'elapsed_ms': elapsed_ms,
                    'results': results,
                }
            except Exception as exception:
                last_exception = exception
                if attempt < MAX_WRITE_ATTEMPTS:
                    retry_delay = RETRY_DELAY_SECONDS * (2 ** (attempt - 1))
                    write_output(
                        f"[{get_local_time()}] Batch {operation_id} removal failed on {ip} "
                        f"attempt {attempt}; retrying in {retry_delay}s: {exception}"
                    )
                    time.sleep(retry_delay)

        elapsed_ms = int(round((time.monotonic() - started_at) * 1000))
        error_message = str(last_exception) if last_exception else 'Unknown batch removal failure'
        capture_exception(
            last_exception or RuntimeError(error_message),
            device_ip=ip,
            operation='delete_users',
            port=port,
            model=model,
        )
        results = [
            {
                'card': item.get('card'),
                'pin': item.get('pin'),
                'success': False,
                'error': error_message,
            }
            for item in normalized_users
        ]
        return {
            'success': False,
            'operation_id': operation_id,
            'total': len(results),
            'succeeded': 0,
            'failed': len(results),
            'rewritten': 0,
            'elapsed_ms': elapsed_ms,
            'message': 'Failed to remove users from controller',
            'results': results,
        }

    return with_device_lock(ip, port, operation)


def delete_user(card, pin, ip, port, timeout=4000, password='', model=None):
    def operation():
        print(f"[{get_local_time()}] Removing user with card: {card} and pin: {pin} on device with ip: {ip}")
        write_output(f"[{get_local_time()}] Removing user with card: {card} and pin: {pin} on device with ip: {ip} on TRY #1")
        connstr = build_connstr(ip, port, timeout, password)
        device_model = resolve_device_model(model)
        try:
            with ZKAccess(connstr=connstr, device_model=device_model) as zk:
                user = User(card=card, pin=pin,
                            super_authorize=True).with_zk(zk)
                user.delete()
                print(f"[{get_local_time()}] IP: {ip} CARD: {card} REMOVED SUCCESS")
                write_output(f"[{get_local_time()}] IP: {ip} CARD: {card} REMOVED SUCCESS")

            return True
        except Exception as ex:
            log_retry_attempt('Removing user', ip, 1, ex)
            print(f"[{get_local_time()}] Removing user with card: {card} and pin: {pin} on device with ip: {ip} on TRY #2")
            write_output(f"[{get_local_time()}] Removing user with card: {card} and pin: {pin} on device with ip: {ip} on TRY #2")
            try:
                with ZKAccess(connstr=connstr, device_model=device_model) as zk:
                    user = User(card=card, pin=pin,
                                super_authorize=True).with_zk(zk)
                    user.delete()
                    print(f"[{get_local_time()}] IP: {ip} CARD: {card} REMOVED SUCCESS ON TRY #2")
                    write_output(f"[{get_local_time()}] IP: {ip} CARD: {card} REMOVED SUCCESS ON TRY #2")

                return True
            except Exception as ex:
                text = f"[{get_local_time()}] Exception when deleting user! Device: {ip} - {str(ex)} + '\n' + {ping_host(ip)}"
                print(text)
                write_output(text)
                capture_exception(ex, device_ip=ip, operation='delete_user', port=port, model=model)
                return False

        return True

    return with_device_lock(ip, port, operation)


def get_users(ip, port, timeout=10000, password='', model=None):
    def operation():
        connstr = build_connstr(ip, port, timeout, password)
        device_model = resolve_device_model(model)
        res = {}
        try:
            write_output(f"[{get_local_time()}] TRY #1 GETTING USERS ON DEVICE:  {ip} ")
            with ZKAccess(connstr=connstr, device_model=device_model) as zk:
                for record in zk.table('User'):
                    res[record.pin] = {
                                        "card": record.card,
                                        "pin": record.pin,
                                    }
        except Exception as ex:
            text = f"[{get_local_time()}] Exeption when retrieving user lists on try #1! Device: {ip} - {str(ex)} + '\n' + {ping_host(ip)}"
            write_output(text)
            write_log(text)
            log_retry_attempt('Getting users', ip, 1, ex)
            write_output(f"[{get_local_time()}] TRY #2 GETTING USERS ON DEVICE:  {ip} ")
            try:
                with ZKAccess(connstr=connstr, device_model=device_model) as zk:
                    for record in zk.table('User'):
                        res[record.pin] = {
                                            "card": record.card,
                                            "pin": record.pin,
                                        }
            except Exception as ex:
                text = f"[{get_local_time()}] Exeption when retrieving user lists on try #2! Device: {ip} - {str(ex)} + '\n' + {ping_host(ip)}"
                write_output(text)
                write_log(text)
                capture_exception(ex, device_ip=ip, operation='get_users', port=port, model=model)
                return {}

        return res

    return with_device_lock(ip, port, operation)


def get_users_with_doors(ip, port, timeout=10000, password='', model=None):
    """Return every user on the device with the door numbers (1-4) its card may open.

    Returns None when the device could not be read, so an unreachable device is not
    mistaken for one without users.
    """
    def read_users(zk):
        doors_by_pin = {}
        authorizations_by_pin = {}
        for authorization in zk.table('UserAuthorize'):
            allowed = doors_by_pin.setdefault(authorization.pin, set())
            row_doors = [
                door_number
                for door_number, is_allowed in enumerate(list(authorization.doors), start=1)
                if is_allowed
            ]
            allowed.update(row_doors)
            authorizations_by_pin.setdefault(authorization.pin, []).append({
                'timezone_id': getattr(authorization, 'timezone_id', None),
                'doors': row_doors,
            })

        return {
            record.pin: {
                'card': record.card,
                'pin': record.pin,
                'doors': sorted(doors_by_pin.get(record.pin, set())),
                'authorizations': authorizations_by_pin.get(record.pin, []),
            }
            for record in zk.table('User')
        }

    def operation():
        connstr = build_connstr(ip, port, timeout, password)
        device_model = resolve_device_model(model)
        last_exception = None

        for attempt in range(1, MAX_WRITE_ATTEMPTS + 1):
            try:
                write_output(f"[{get_local_time()}] TRY #{attempt} GETTING USERS WITH DOORS ON DEVICE: {ip}")
                with ZKAccess(connstr=connstr, device_model=device_model) as zk:
                    return read_users(zk)
            except Exception as exception:
                last_exception = exception
                if attempt < MAX_WRITE_ATTEMPTS:
                    log_retry_attempt('Getting users with doors', ip, attempt, exception)

        write_output(f"[{get_local_time()}] Exception when retrieving users with doors! Device: {ip} - {last_exception}")
        capture_exception(last_exception, device_ip=ip, operation='get_users_with_doors', port=port, model=model)

        return None

    return with_device_lock(ip, port, operation)


def get_transactions(ip, port, timeout=10000, password='', model=None, pin=None, card=None, limit=50):
    """Return the newest access events the controller logged for a pin or card.

    Each event keeps the raw event code next to its name, so a denied tap (23) can be told
    apart from an opened door (0) per door number. The whole table is read and then matched
    here, so a pin or card search costs the same as an unfiltered one. Returns None when the
    device could not be read.
    """
    def read_events(zk):
        wanted_pin = str(pin).strip() if pin else None
        wanted_card = str(card).strip() if card else None
        events = []
        total = 0

        # The pin and card are matched here and not sent to the controller as an SDK condition:
        # the controller returned nothing for a card that its own unfiltered log contains.
        for record in zk.table('Transaction'):
            total += 1
            raw = record.raw_data

            if wanted_pin and str(raw.get('Pin', '')).strip() != wanted_pin:
                continue

            if wanted_card and str(raw.get('Cardno', '')).strip() != wanted_card:
                continue

            try:
                event_code = int(raw.get('EventType'))
            except (TypeError, ValueError):
                event_code = None

            try:
                event_time = ZKDatetimeUtils.zkctime_to_datetime(raw.get('Time_second')).strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                event_time = None

            events.append({
                'time': event_time,
                'pin': raw.get('Pin'),
                'card': raw.get('Cardno'),
                'door': raw.get('DoorID'),
                'event_code': event_code,
                'event': EVENT_TYPES[event_code].__doc__ if event_code in EVENT_TYPES else 'Unknown event',
            })

        events.sort(key=lambda event: event['time'] or '', reverse=True)
        write_output(f"[{get_local_time()}] Read {total} transactions from {ip}, {len(events)} matched pin={wanted_pin} card={wanted_card}")

        return events[:limit]

    def operation():
        connstr = build_connstr(ip, port, timeout, password)
        device_model = resolve_device_model(model)
        last_exception = None

        for attempt in range(1, MAX_WRITE_ATTEMPTS + 1):
            try:
                write_output(f"[{get_local_time()}] TRY #{attempt} GETTING TRANSACTIONS ON DEVICE: {ip}")
                with ZKAccess(connstr=connstr, device_model=device_model) as zk:
                    return read_events(zk)
            except Exception as exception:
                last_exception = exception
                if attempt < MAX_WRITE_ATTEMPTS:
                    log_retry_attempt('Getting transactions', ip, attempt, exception)

        write_output(f"[{get_local_time()}] Exception when retrieving transactions! Device: {ip} - {last_exception}")
        capture_exception(last_exception, device_ip=ip, operation='get_transactions', port=port, model=model)

        return None

    return with_device_lock(ip, port, operation)


def restart_device(ip, port=4370, timeout=10000, password='', model=None):
    def operation():
        connstr = build_connstr(ip, port, timeout, password)
        device_model = resolve_device_model(model)

        try:
            write_output(f"[{get_local_time()}] Restarting device: {ip}:{port}")
            with ZKAccess(connstr=connstr, device_model=device_model) as zk:
                zk.restart()

            write_output(f"[{get_local_time()}] Restart command sent successfully: {ip}:{port}")
            return True
        except Exception as ex:
            text = f"[{get_local_time()}] Exception when restarting device: {ip}:{port} - {str(ex)}"
            write_output(text)
            capture_exception(ex, device_ip=ip, operation='restart_device', port=port, model=model)
            return False

    return with_device_lock(ip, port, operation)


def check_device(ip, port=4370, timeout=10000, password='', model=None):
    """Report whether the controller accepts a connection, and the SDK error when it does not.

    An unreachable controller is an expected outcome rather than a bug, so it is not sent to Sentry.
    """
    def operation():
        connstr = build_connstr(ip, port, timeout, password)
        device_model = resolve_device_model(model)

        try:
            with ZKAccess(connstr=connstr, device_model=device_model):
                return {'online': True, 'error': None}
        except Exception as ex:
            text = f"[{get_local_time()}] Exception when checking device health: {ip}:{port} - {str(ex)}"
            write_output(text)
            return {'online': False, 'error': str(ex)}

    return with_device_lock(ip, port, operation)
