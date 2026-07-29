from pyzkaccess import ZKAccess, ZK200, ZK100, ZK400
from pyzkaccess.tables import User, UserAuthorize
from datetime import datetime
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

                try:
                    zk.table('UserAuthorize').where(pin=pin).delete_all()
                except:
                    pass

                userAuthorize = UserAuthorize(pin=pin, timezone_id=1, doors=door_access).with_zk(zk)
                userAuthorize.save()
                print(f"[{get_local_time()}] Authorized To Doors: {door_access}")
                write_output(f"[{get_local_time()}] Authorized To Doors: {door_access}")

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

                    try:
                        zk.table('UserAuthorize').where(pin=pin).delete_all()
                    except:
                        pass

                    userAuthorize = UserAuthorize(pin=pin, timezone_id=1, doors=door_access).with_zk(zk)
                    userAuthorize.save()
                    print(f"[{get_local_time()}] Authorized To Doors: {door_access}")
                    write_output(f"[{get_local_time()}] Authorized To Doors: {door_access}")

                return True
            except Exception as ex:
                text = f"[{get_local_time()}] Exception when adding user! Device: {ip} - {str(ex)} + '\n' + {ping_host(ip)} + '\n'"
                write_output(text)
                capture_exception(ex, device_ip=ip, operation='add_user', port=port, model=model)
                print(text + "\n")
                return False

        return True

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
    def operation():
        connstr = build_connstr(ip, port, timeout, password)
        device_model = resolve_device_model(model)

        try:
            with ZKAccess(connstr=connstr, device_model=device_model):
                return True
        except Exception as ex:
            text = f"[{get_local_time()}] Exception when checking device health: {ip}:{port} - {str(ex)}"
            write_output(text)
            capture_exception(ex, device_ip=ip, operation='check_device', port=port, model=model)
            return False

    return with_device_lock(ip, port, operation)
