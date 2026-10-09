from flask import Flask, request, jsonify
import hmac
import os
from device_locks import output_lock
from main import DOOR_ACTIONS, event_time_bound, ping_host_endpoint, resolve_device_model
from observability import initialize_sentry
from queue_manager import add_users, check_device, control_door, delete_users, get_transactions, get_users, get_users_with_doors, restart_device
import sys
from datetime import datetime
import pytz

initialize_sentry()
app = Flask(__name__)

def get_local_time():
    tz = pytz.timezone('Asia/Tbilisi')
    return datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')


class NotAJsonObject(Exception):
    pass


@app.errorhandler(NotAJsonObject)
def not_a_json_object(_error):
    return jsonify({
        'success': False,
        'message': 'The request body must be a JSON object',
    }), 422


def request_body():
    """The request's JSON object, or {} when there is no JSON body.

    Raises NotAJsonObject for an array, string, number or boolean, which the routes
    would otherwise crash on with a 500 when they call .get on it.
    """
    body = request.get_json(silent=True)

    if body is None:
        return {}

    if not isinstance(body, dict):
        raise NotAJsonObject()

    return body


def get_shared_secret():
    return os.environ.get('ZKTECO_SHARED_SECRET', '').strip()


def controller_request_is_authorized():
    secret = get_shared_secret()
    authorization = request.headers.get('Authorization', '')

    if not secret or not authorization.startswith('Bearer '):
        return False

    return hmac.compare_digest(authorization[7:].strip(), secret)


def is_identifier(value):
    """A card or PIN: a non-empty string or an integer, never None or a bool."""
    if isinstance(value, bool):
        return False

    if isinstance(value, int):
        return True

    return isinstance(value, str) and value.strip() != ''


def is_door_list(value):
    """Doors are omitted, or a non-empty list of positive door numbers.

    An empty list is rejected because the batch writers treat "no doors" as every door.
    Numbers above 4 stay accepted and are simply not part of the 4-lock mask, as before.
    """
    if value is None:
        return True

    return isinstance(value, list) and value != [] and all(
        isinstance(door, int) and not isinstance(door, bool) and door >= 1
        for door in value
    )


def is_whole_number(value, low, high):
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def door_control_error(body):
    """Why a door command is invalid, or None. Checked before any SDK session opens."""
    if not body.get('ip'):
        return 'The controller IP is required'

    action = body.get('action')
    if action not in DOOR_ACTIONS:
        return f"The action must be one of: {', '.join(DOOR_ACTIONS)}"

    # Same model resolution the SDK session uses, so the limit matches the doors it can address.
    door_count = len(resolve_device_model(body.get('model')).doors_def)
    if not is_whole_number(body.get('door'), 1, door_count):
        return f'The door must be a whole number from 1 to {door_count}'

    # 0 and 255 mean off and normally open to the SDK, so open accepts only a real duration.
    if action == 'open' and not is_whole_number(body.get('seconds'), 1, 254):
        return 'Seconds must be a whole number from 1 to 254'

    return None


def invalid_user_indexes(users):
    return [
        index for index, user in enumerate(users)
        if not isinstance(user, dict)
        or not is_identifier(user.get('card'))
        or not is_identifier(user.get('pin'))
        or not is_door_list(user.get('doors'))
    ]

@app.route('/')
def home():
    print(f"[{get_local_time()}] Server received an empty request")
    with output_lock:
        with open('output.txt', 'a') as output:
            output.write(f"[{get_local_time()}] Server received an empty request" + "\n")
    return jsonify({
        "success": True
    })

@app.route('/ping/', methods = ['POST'])
def ping_host():
    if not controller_request_is_authorized():
        return jsonify({
            'success': False,
            'message': 'Unauthorized controller request',
        }), 401

    body = request_body()
    ip = body.get('ip')

    if not ip:
        return jsonify({
            'success': False,
            'message': 'The host IP is required',
        }), 422

    res = ping_host_endpoint(ip)
    print(f"[{get_local_time()}] Ping successful on host: {ip}")
    with output_lock:
        with open('output.txt', 'a') as output:
            output.write(f"[{get_local_time()}] Ping successful on host: {ip}" + "\n")
    return jsonify({
        "success": res,
    })

@app.route('/controller/users/set/', methods=['POST'])
def set_users():
    if not controller_request_is_authorized():
        return jsonify({
            'success': False,
            'message': 'Unauthorized controller request',
        }), 401

    body = request_body()
    ip = body.get('ip')
    users = body.get('users')

    if not ip or not isinstance(users, list):
        return jsonify({
            'success': False,
            'message': 'The controller IP and users array are required',
        }), 422

    invalid_users = invalid_user_indexes(users)

    if invalid_users:
        return jsonify({
            'success': False,
            'message': 'Each user requires card and pin, and doors must be a list of door numbers',
            'invalid_indexes': invalid_users,
        }), 422

    result = add_users(
        users=users,
        ip=ip,
        port=body.get('port', 4370),
        timeout=body.get('timeout', 4000),
        password=body.get('password', ''),
        model=body.get('model'),
        operation_id=body.get('operation_id'),
    )

    return jsonify(result), 200 if result.get('success') else 502

    
@app.route('/controller/users/remove/', methods=['POST'])
def remove_users():
    if not controller_request_is_authorized():
        return jsonify({
            'success': False,
            'message': 'Unauthorized controller request',
        }), 401

    body = request_body()
    ip = body.get('ip')
    users = body.get('users')

    if not ip or not isinstance(users, list):
        return jsonify({
            'success': False,
            'message': 'The controller IP and users array are required',
        }), 422

    invalid_users = invalid_user_indexes(users)

    if invalid_users:
        return jsonify({
            'success': False,
            'message': 'Each user requires card and pin, and doors must be a list of door numbers',
            'invalid_indexes': invalid_users,
        }), 422

    result = delete_users(
        users=users,
        ip=ip,
        port=body.get('port', 4370),
        timeout=body.get('timeout', 4000),
        password=body.get('password', ''),
        model=body.get('model'),
        operation_id=body.get('operation_id'),
    )

    return jsonify(result), 200 if result.get('success') else 502


@app.route('/controller/users/', methods = ['POST'])
def users():
    if not controller_request_is_authorized():
        return jsonify({
            'success': False,
            'message': 'Unauthorized controller request',
        }), 401

    body = request_body()
    ip = body.get('ip')

    if not ip:
        return jsonify({
            'success': False,
            'message': 'The controller IP is required',
        }), 422

    port = body.get('port')
    timeout = body.get('timeout')
    password = body.get('password')
    model = body.get('model')
    res = get_users(ip, port, timeout=timeout, password=password, model=model)
    print(f"[{get_local_time()}] Returned {len(res)} users from host: {ip}")
    with output_lock:
        with open('output.txt', 'a') as output:
            output.write(f"[{get_local_time()}] returned {len(res)} users from host: {ip}" + "\n")
    return jsonify({
        "users": res,
    })



@app.route('/controller/users/doors/', methods=['POST'])
def users_with_doors():
    if not controller_request_is_authorized():
        return jsonify({
            'success': False,
            'message': 'Unauthorized controller request',
        }), 401

    body = request_body()
    ip = body.get('ip')

    if not ip:
        return jsonify({
            'success': False,
            'message': 'The controller IP is required',
        }), 422

    res = get_users_with_doors(
        ip,
        body.get('port', 4370),
        timeout=body.get('timeout', 10000),
        password=body.get('password', ''),
        model=body.get('model'),
    )

    if res is None:
        return jsonify({
            'success': False,
            'message': 'Could not read users from the controller',
        }), 502

    return jsonify({
        'success': True,
        'users': res,
    })

@app.route('/controller/transactions/', methods=['POST'])
def transactions():
    if not controller_request_is_authorized():
        return jsonify({
            'success': False,
            'message': 'Unauthorized controller request',
        }), 401

    body = request_body()
    ip = body.get('ip')

    if not ip:
        return jsonify({
            'success': False,
            'message': 'The controller IP is required',
        }), 422

    pin = body.get('pin')
    card = body.get('card')

    try:
        limit = max(1, min(int(body.get('limit', 50)), 500))
    except (TypeError, ValueError):
        return jsonify({
            'success': False,
            'message': 'The limit must be a number',
        }), 422

    date_from = body.get('date_from')
    date_to = body.get('date_to')

    try:
        event_time_bound(date_from, end_of_day=False)
        event_time_bound(date_to, end_of_day=True)
    except ValueError:
        return jsonify({
            'success': False,
            'message': 'The dates must be YYYY-MM-DD or YYYY-MM-DD HH:MM:SS',
        }), 422

    event_codes = body.get('event_codes')

    if event_codes is not None:
        try:
            event_codes = [int(code) for code in event_codes]
        except (TypeError, ValueError):
            return jsonify({
                'success': False,
                'message': 'The event_codes must be a list of event numbers',
            }), 422

    res = get_transactions(
        ip,
        body.get('port', 4370),
        timeout=body.get('timeout', 10000),
        password=body.get('password', ''),
        model=body.get('model'),
        pin=pin,
        card=card,
        limit=limit,
        event_codes=event_codes,
        date_from=date_from,
        date_to=date_to,
    )

    if res is None:
        return jsonify({
            'success': False,
            'message': 'Could not read transactions from the controller',
        }), 502

    return jsonify({
        'success': True,
        'transactions': res,
    })


@app.route('/controller/restart/', methods=['POST'])
def restart_controller():
    if not controller_request_is_authorized():
        return jsonify({
            'success': False,
            'message': 'Unauthorized restart request',
        }), 401

    body = request_body()
    ip = body.get('ip')
    port = body.get('port', 4370)
    timeout = body.get('timeout', 10000)
    password = body.get('password', '')
    model = body.get('model')

    if not ip:
        return jsonify({
            'success': False,
            'message': 'The controller IP is required',
        }), 422

    result = restart_device(
        ip=ip,
        port=port,
        timeout=timeout,
        password=password,
        model=model,
    )

    return jsonify({
        'success': bool(result),
        'message': 'Restart command sent successfully' if result else 'Failed to restart controller',
    }), 200 if result else 502


@app.route('/controller/door/control/', methods=['POST'])
def door_control():
    if not controller_request_is_authorized():
        return jsonify({
            'success': False,
            'message': 'Unauthorized door control request',
        }), 401

    body = request_body()
    error = door_control_error(body)

    if error:
        return jsonify({
            'success': False,
            'message': error,
        }), 422

    action = body['action']
    result = control_door(
        ip=body['ip'],
        port=body.get('port', 4370),
        timeout=body.get('timeout', 10000),
        password=body.get('password', ''),
        model=body.get('model'),
        door=body['door'],
        action=action,
        seconds=body['seconds'] if action == 'open' else None,
    )

    return jsonify({
        'success': bool(result),
        'message': f"Door {body['door']} {action} command sent" if result else f"Failed to send door {body['door']} {action} command",
    }), 200 if result else 502


@app.route('/controller/health/', methods=['POST'])
def health_controller():
    if not controller_request_is_authorized():
        return jsonify({
            'success': False,
            'message': 'Unauthorized health request',
        }), 401

    body = request_body()
    ip = body.get('ip')
    port = body.get('port', 4370)
    timeout = body.get('timeout', 5000)
    password = body.get('password', '')
    model = body.get('model')

    if not ip:
        return jsonify({
            'success': False,
            'message': 'The controller IP is required',
        }), 422

    result = check_device(
        ip=ip,
        port=port,
        timeout=timeout,
        password=password,
        model=model,
    )

    return jsonify({
        'success': result['online'],
        'message': 'Device is reachable' if result['online'] else (result['error'] or 'Device health check failed'),
    }), 200

if __name__ == '__main__':
    app.run(debug=True)


# @app.route('/controller/disable/')
# def disable():
#     body = request.json
#     ip = body.get('ip')
#     port = body.get('port')
#     #TODO
#     return jsonify({
#         "success": res,
#     })

# @app.route('/controller/enable/')
# def enable():
#     body = request.json
#     ip = body.get('ip')
#     port = body.get('port')
#     #TODO
#     return jsonify({
#         "success": res,
#     })
