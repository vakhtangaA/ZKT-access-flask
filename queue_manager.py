from device_locks import with_device_lock
from main import add_users as add_users_func
from main import check_device as check_device_func
from main import control_door as control_door_func
from main import delete_users as delete_users_func
from main import get_transactions as get_transactions_func
from main import get_users as get_users_func
from main import get_users_with_doors as get_users_with_doors_func
from main import read_relay_state as read_relay_state_func
from main import restart_device as restart_device_func

def add_users(users, ip, port=4370, timeout=4000, password='', model=None, operation_id=None):
    return with_device_lock(
        ip,
        port,
        lambda: add_users_func(
            users,
            ip,
            port,
            timeout=timeout,
            password=password,
            model=model,
            operation_id=operation_id,
        ),
    )

def delete_users(users, ip, port=4370, timeout=4000, password='', model=None, operation_id=None):
    return with_device_lock(
        ip,
        port,
        lambda: delete_users_func(
            users,
            ip,
            port,
            timeout=timeout,
            password=password,
            model=model,
            operation_id=operation_id,
        ),
    )

# Function to handle getting users
def get_users(ip, port, timeout=10000, password='', model=None):
    return with_device_lock(
        ip,
        port,
        lambda: get_users_func(
            ip,
            port,
            timeout=timeout,
            password=password,
            model=model,
        ),
    )



def get_users_with_doors(ip, port, timeout=10000, password='', model=None):
    return with_device_lock(
        ip,
        port,
        lambda: get_users_with_doors_func(
            ip,
            port,
            timeout=timeout,
            password=password,
            model=model,
        ),
    )

def get_transactions(ip, port, timeout=10000, password='', model=None, pin=None, card=None, limit=50, event_codes=None, date_from=None, date_to=None):
    return with_device_lock(
        ip,
        port,
        lambda: get_transactions_func(
            ip,
            port,
            timeout=timeout,
            password=password,
            model=model,
            pin=pin,
            card=card,
            limit=limit,
            event_codes=event_codes,
            date_from=date_from,
            date_to=date_to,
        ),
    )

def restart_device(ip, port=4370, timeout=10000, password='', model=None):
    return with_device_lock(
        ip,
        port,
        lambda: restart_device_func(
            ip,
            port,
            timeout=timeout,
            password=password,
            model=model,
        ),
    )


def check_device(ip, port=4370, timeout=10000, password='', model=None):
    return with_device_lock(
        ip,
        port,
        lambda: check_device_func(
            ip,
            port,
            timeout=timeout,
            password=password,
            model=model,
        ),
    )


def control_door(ip, port=4370, door=1, action='open', seconds=None, timeout=10000, password='', model=None):
    return with_device_lock(
        ip,
        port,
        lambda: control_door_func(
            ip,
            port,
            door=door,
            action=action,
            seconds=seconds,
            timeout=timeout,
            password=password,
            model=model,
        ),
    )


def read_relay_state(ip, port=4370, timeout=10000, password='', model=None):
    return with_device_lock(
        ip,
        port,
        lambda: read_relay_state_func(
            ip,
            port,
            timeout=timeout,
            password=password,
            model=model,
        ),
    )
