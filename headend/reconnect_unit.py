"""Reconnect an existing unit over USB after a headend move, preserving its ID."""
import argparse
import hashlib
import ipaddress
import os
from pathlib import Path
import secrets
from urllib.parse import urlsplit

from .device import DeviceBridge
from .state import State


def reconnect(state, bridge, unit_id, url):
    address = urlsplit(url)
    try:
        ip = ipaddress.IPv4Address(address.hostname)
        private = any(ip in ipaddress.IPv4Network(network) for network in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
        valid = private and url == f'http://{ip}:8765'
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('Use the Pi LAN IPv4 address in the form http://<Pi-IP>:8765')
    with state.db() as db:
        unit = db.execute('SELECT id,name FROM units WHERE id=?', (unit_id,)).fetchone()
    if unit is None:
        raise ValueError('Unit not found in the migrated database')
    token = secrets.token_urlsafe(32)
    bridge.run('provision', dict(url=url, id=unit_id, token=token))
    with state.db() as db:
        db.execute('UPDATE units SET token=?,seen=0 WHERE id=?',
                   (hashlib.sha256(token.encode()).hexdigest(), unit_id))
    return dict(id=unit_id, name=unit['name'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=Path(os.environ.get('ADAM_DATA_DIR', Path(__file__).resolve().parent/'data')))
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--unit')
    parser.add_argument('--url')
    parser.add_argument('--port', default=os.environ.get('VOICE_USB_PORT', 'COM6' if os.name=='nt' else '/dev/ttyACM0'))
    args = parser.parse_args()
    if not (args.data/'fleet.sqlite3').is_file():
        parser.error('Restore the existing headend database first')
    state = State(args.data)
    if args.list:
        for unit in state.units():
            print(unit['id'], unit['name'])
        return
    if not args.unit or not args.url:
        parser.error('Use --list, or supply --unit and --url')
    unit = reconnect(state, DeviceBridge(args.port), args.unit, args.url)
    print(f"Reconnected {unit['name']} ({unit['id']}). Check for its next heartbeat in ADAM.")


if __name__ == '__main__':
    main()
