from flask import Flask, request, jsonify
import hmac
import os
from device_locks import output_lock
from main import ping_host_endpoint
from observability import initialize_sentry
from queue_manager import add_user, check_device, delete_user, get_users, restart_device
import sys
from datetime import datetime
import pytz

initialize_sentry()
app = Flask(__name__)

def get_local_time():
    tz = pytz.timezone('Asia/Tbilisi')
    return datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')


def get_shared_secret():
    return (os.environ.get('ZKTECO_SHARED_SECRET') or os.environ.get('ZKT_SHARED_SECRET', '')).strip()


def restart_request_is_authorized():
    secret = get_shared_secret()
    authorization = request.headers.get('Authorization', '')

    if not secret or not authorization.startswith('Bearer '):
        return False

    return hmac.compare_digest(authorization[7:].strip(), secret)

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
    body = request.json
    ip = body.get('ip')
    res = ping_host_endpoint(ip)
    print(f"[{get_local_time()}] Ping successful on host: {ip}")
    with output_lock:
        with open('output.txt', 'a') as output:
            output.write(f"[{get_local_time()}] Ping successful on host: {ip}" + "\n")
    return jsonify({
        "success": res,
    })

@app.route('/controller/user/set/', methods = ['POST'])
def set_user():
    body = request.json
    card = body.get('card')
    pin = body.get('pin')
    ip = body.get('ip')
    port = body.get('port')
    doors = body.get('doors')
    timeout = body.get('timeout')
    password = body.get('password')
    model = body.get('model')
    print(f"[{get_local_time()}] Recieved request to add user with card: {card} and pin: {pin}")
    with output_lock:
        with open('output.txt', 'a') as output:
            output.write(f"[{get_local_time()}] Recieved request to add user with card: {card} and pin: {pin}" + "\n")
    res = add_user(card=card, pin=pin, ip=ip, port=port, doors=doors, timeout=timeout, password=password, model=model)
    
    return jsonify({
        "success": res,
        "message": "Added user successfully" if res else "Failed to add user",
    })

    
@app.route('/controller/user/remove/', methods = ['POST'])
def remove_user():
    body = request.json
    card = body.get('card')
    pin = body.get('pin')
    ip = body.get('ip')
    port = body.get('port')
    timeout = body.get('timeout')
    password = body.get('password')
    model = body.get('model')
    print(f"[{get_local_time()}] Recieved request to remove user with card: {card} and pin: {pin}")
    with output_lock:
        with open('output.txt', 'a') as output:
            output.write(f"[{get_local_time()}] Recieved request to remove user with card: {card} and pin: {pin}" + "\n")
    res = delete_user(card=card, pin=pin, ip=ip, port=port, timeout=timeout, password=password, model=model)
    
    return jsonify({
        "success": res,
        "message": "Removed user successfully" if res else "Failed to remove user",
    })
    
@app.route('/controller/users/', methods = ['POST'])
def users():
    body = request.json
    ip = body.get('ip')
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


@app.route('/controller/restart/', methods=['POST'])
def restart_controller():
    if not restart_request_is_authorized():
        return jsonify({
            'success': False,
            'message': 'Unauthorized restart request',
        }), 401

    body = request.get_json(silent=True) or {}
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


@app.route('/controller/health/', methods=['POST'])
def health_controller():
    if not restart_request_is_authorized():
        return jsonify({
            'success': False,
            'message': 'Unauthorized health request',
        }), 401

    body = request.get_json(silent=True) or {}
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

    result = check_device(
        ip=ip,
        port=port,
        timeout=timeout,
        password=password,
        model=model,
    )

    return jsonify({
        'success': bool(result),
        'message': 'Device is reachable' if result else 'Device health check failed',
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
