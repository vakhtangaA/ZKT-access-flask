"""Find out whether the controller reports its lock relay state.

Run it on the machine that has the PullSDK, against a controller whose relay is safe to switch:

    python probe_relay_state.py --ip 94.43.2.209 --model C3-200 --door 1

The guide (guide-v2.2, attached table 8) says GetRTLogExt returns a door/alarm status record with
a `relay=` field, one bit per door, but adds "(Both are currently 0)". GetRTLog's status record
(event 255) only has the door sensor. This script switches one door through hold_open, close and a
timed open, and prints the raw answer of both functions after each step, so we can see whether
`relay=` follows the commands on our firmware.

A status record only comes back once the controller's event cache is empty, so each step polls
several times. Output goes to the console and to probe_relay_state_<ip>_<time>.txt.

It sends door commands and nothing else. It never restarts the controller. It opens its own SDK
session outside the bridge's device lock, so run it when nothing else is syncing that controller.
"""
import argparse
import ctypes
import sys
import time
from datetime import datetime

from pyzkaccess import ZKAccess

from main import build_connstr, door_control_commands, resolve_device_model

BUFFER_SIZE = 4096
RT_LOG_FUNCTIONS = ('GetRTLogExt', 'GetRTLog')


class Probe:
    def __init__(self, zk, out_path):
        self.zk = zk
        self.out = open(out_path, 'w', encoding='utf-8')

    def log(self, message):
        line = f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {message}"
        print(line)
        self.out.write(line + '\n')
        self.out.flush()

    def read_raw(self, function_name):
        """Call one SDK realtime-log function and return its raw buffer, or the error."""
        function = getattr(self.zk.sdk.dll, function_name, None)

        if function is None:
            return f'<{function_name} is not exported by this plcommpro.dll>'

        buffer = ctypes.create_string_buffer(BUFFER_SIZE)
        result = function(self.zk.sdk.handle, buffer, BUFFER_SIZE)

        if result < 0:
            return f'<{function_name} error {result}>'

        return buffer.value.decode('utf-8', errors='replace')

    def poll(self, label, rounds, interval):
        self.log(f'--- {label} ---')

        for round_number in range(1, rounds + 1):
            for function_name in RT_LOG_FUNCTIONS:
                self.log(f'{round_number} {function_name}: {self.read_raw(function_name)!r}')

            time.sleep(interval)

    def send(self, door, action, seconds=None):
        self.log(f'>>> door {door} {action}' + (f' {seconds}s' if seconds else ''))

        for command in door_control_commands(door, action, seconds):
            self.zk.sdk.control_device(*command)


def main():
    parser = argparse.ArgumentParser(description='Check whether the controller reports its relay state.')
    parser.add_argument('--ip', required=True)
    parser.add_argument('--port', type=int, default=4370)
    parser.add_argument('--model', default='C3-200')
    parser.add_argument('--password', default='')
    parser.add_argument('--timeout', type=int, default=10000)
    parser.add_argument('--door', type=int, required=True, help='controller-local door (relay) number, 1-4')
    parser.add_argument('--rounds', type=int, default=5, help='polls per step')
    parser.add_argument('--interval', type=float, default=1.0, help='seconds between polls')
    parser.add_argument('--open-seconds', type=int, default=10, help='length of the timed open step')
    arguments = parser.parse_args()

    connstr = build_connstr(arguments.ip, arguments.port, arguments.timeout, arguments.password)
    out_path = f"probe_relay_state_{arguments.ip}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

    with ZKAccess(connstr=connstr, device_model=resolve_device_model(arguments.model)) as zk:
        probe = Probe(zk, out_path)
        probe.log(f'controller {arguments.ip}:{arguments.port} model {arguments.model} door {arguments.door}')

        try:
            probe.poll('baseline', arguments.rounds, arguments.interval)

            probe.send(arguments.door, 'hold_open')
            probe.poll('after hold_open', arguments.rounds, arguments.interval)

            probe.send(arguments.door, 'close')
            probe.poll('after close', arguments.rounds, arguments.interval)

            probe.send(arguments.door, 'open', arguments.open_seconds)
            probe.poll(f'during {arguments.open_seconds}s open', arguments.rounds, arguments.interval)

            remaining = arguments.open_seconds - arguments.rounds * arguments.interval + 2
            if remaining > 0:
                time.sleep(remaining)
            probe.poll('after timed open ended', arguments.rounds, arguments.interval)
        finally:
            # Leave the relay off whatever happened above.
            probe.send(arguments.door, 'close')
            probe.log(f'done, output in {out_path}')
            probe.out.close()

    return 0


if __name__ == '__main__':
    sys.exit(main())
