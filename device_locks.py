from threading import Lock, RLock

_device_locks = {}
_device_locks_guard = Lock()
output_lock = Lock()


def _device_key(ip, port):
    normalized_ip = '' if ip is None else str(ip).strip()
    normalized_port = '' if port is None else str(port).strip()

    return f"{normalized_ip}:{normalized_port}"


def get_device_lock(ip, port):
    device_key = _device_key(ip, port)

    with _device_locks_guard:
        if device_key not in _device_locks:
            _device_locks[device_key] = RLock()

        return _device_locks[device_key]


def with_device_lock(ip, port, operation):
    with get_device_lock(ip, port):
        return operation()
