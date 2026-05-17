import hmac
import os
from flask import Flask, request, jsonify
from device_locks import output_lock
from main import ping_host_endpoint
from observability import initialize_sentry
from queue_manager import add_user, delete_user, get_users
import sys
from datetime import datetime
import pytz

initialize_sentry()
app = Flask(__name__)


def get_shared_secret():
    return os.getenv('ZKT_SHARED_SECRET')


def authorize_request():
    configured_secret = get_shared_secret()

    if not configured_secret:
        return jsonify({
            "success": False,
            "message": "Shared secret is not configured",
        }), 503

    authorization_header = request.headers.get('Authorization', '')
    expected_header = f'Bearer {configured_secret}'

    if not hmac.compare_digest(authorization_header, expected_header):
        return jsonify({
            "success": False,
            "message": "Unauthorized",
        }), 401

    return None

def get_local_time():
    tz = pytz.timezone('Asia/Tbilisi')
    return datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')

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
    authorization_failure = authorize_request()
    if authorization_failure is not None:
        return authorization_failure

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
    authorization_failure = authorize_request()
    if authorization_failure is not None:
        return authorization_failure

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
    authorization_failure = authorize_request()
    if authorization_failure is not None:
        return authorization_failure

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
    authorization_failure = authorize_request()
    if authorization_failure is not None:
        return authorization_failure

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
    
    
# @app.route('/controller/restart/')
# def restart():
#     body = request.json
#     ip = body.get('ip')
#     #TODO
#     return jsonify({
#         "success": res,
#     })
