"""One operator-owned check of the official managed runtime; never replays work."""

import argparse
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time

from .platform import check_private_path
from wechat_dot_bridge.cli import acquire_lock, load_config, parser as engine_parser
from wechat_dot_bridge.errors import BridgeError

FIELDS = {'alias', 'tunnel_client', 'profile_dir', 'profile_name', 'tunnel_id', 'bridge_config',
          'runtime_key_file', 'mcp_command'}


def load_settings(path):
    path = Path(path).absolute()
    check_private_path(path)
    if path.stat().st_size > 16384:
        raise ValueError('runtime_settings_invalid')
    try:
        settings = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, UnicodeError):
        raise ValueError('runtime_settings_invalid') from None
    if (not isinstance(settings, dict) or set(settings) != FIELDS
            or any(not isinstance(v, str) or not v or '\x00' in v for v in settings.values())
            or not re.fullmatch(r'[a-z][a-z0-9-]{0,63}', settings['alias'])
            or not re.fullmatch(r'[a-z][a-z0-9-]{0,63}', settings['profile_name'])
            or not re.fullmatch(r'tunnel_[a-zA-Z0-9]{1,128}', settings['tunnel_id'])
            or len(settings['mcp_command']) > 8192):
        raise ValueError('runtime_settings_invalid')
    for key in ('tunnel_client', 'profile_dir', 'runtime_key_file', 'bridge_config'):
        if not Path(settings[key]).is_absolute():
            raise ValueError('runtime_settings_invalid')
    check_private_path(settings['runtime_key_file'])
    check_private_path(settings['bridge_config'])
    check_private_path(settings['profile_dir'], directory=True)
    if not Path(settings['tunnel_client']).is_file():
        raise ValueError('runtime_client_missing')
    return path, settings


def _invoke(command):
    options = {}
    if os.name == 'nt':
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        # The official client also starts console children. A hidden console
        # lets those children inherit it instead of allocating visible ones.
        options.update(startupinfo=startup, creationflags=subprocess.CREATE_NEW_CONSOLE)
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                                timeout=45, check=False, **options)
    except subprocess.TimeoutExpired:
        raise ValueError('runtime_command_timeout') from None
    except OSError:
        raise ValueError('runtime_command_failed') from None
    if result.returncode or len(result.stdout) > 1024 * 1024:
        raise ValueError('runtime_command_failed')
    try:
        value = json.loads(result.stdout)
    except (ValueError, UnicodeError):
        raise ValueError('runtime_response_invalid') from None
    if not isinstance(value, dict):
        raise ValueError('runtime_response_invalid')
    return value


def ensure_running(settings, invoke=_invoke):
    """Reuse a live runtime even during network failure; start only a stopped one.

    The official client owns process identity and its exclusive runtime lock.
    Never kill a PID, reset a subscription or retry an uncertain message here.
    """
    client, alias = settings['tunnel_client'], settings['alias']
    state = invoke([client, 'runtimes', 'status', alias, '--json'])
    if type(state.get('process_running')) is not bool:
        raise ValueError('runtime_response_invalid')
    started = not state['process_running']
    if started:
        invoke([client, 'runtimes', 'connect', '--alias', alias,
                '--tunnel-id', settings['tunnel_id'], '--profile', settings['profile_name'],
                '--profile-dir', settings['profile_dir'], '--runtime-api-key',
                'file:' + settings['runtime_key_file'], '--mcp-command', settings['mcp_command'], '--json'])
        # A successful launch command alone is not proof of a healthy runtime.
        state = invoke([client, 'runtimes', 'status', alias, '--json'])
    return {'action': 'started' if started else 'reused',
            'running': state.get('process_running') is True,
            'healthy': state.get('healthy') is True, 'ready': state.get('ready') is True}


def run(path):
    path, settings = load_settings(path)
    with acquire_lock(path.parent / 'runtime-guard.lock'):
        try:
            result = ensure_running(settings)
        except (ValueError, OSError):
            result = {'action':'check_failed', 'error':'runtime_check_failed',
                      'running':None, 'healthy':None, 'ready':False}
        from wechat_dot_bridge.store import Store
        args = engine_parser().parse_args(['--config', settings['bridge_config'], 'status'])
        store = None
        try:
            state, *_ = load_config(args)
            store = Store(state)
            status = store.get_status()
            result.update(bridge_healthy=bool(status['bound'] and status['enabled'] and status['diagnostics']['healthy']), pending=status['pending'])
            result['ready'] = result['ready'] and result['bridge_healthy']
        except (BridgeError, ValueError, OSError, sqlite3.Error):
            result.update(bridge_healthy=False, ready=False, error='bridge_status_unavailable')
        finally:
            if store is not None:
                store.close()
        record = path.parent / 'last-runtime-check.json'
        data = json.dumps({**result, 'checked_at': time.time()})
        if record.exists():
            check_private_path(record)
            record.write_text(data, encoding='utf-8')
        else:
            from .onboarding import new_file
            new_file(record, data)
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settings', required=True)
    args = parser.parse_args(argv)
    try:
        result = run(args.settings)
    except BlockingIOError:
        result = {'action': 'already_checking'}
    except (OSError, ValueError, BridgeError):
        # Official output may contain local routes or log tails. It stays private.
        result = {'action': 'check_failed', 'error': 'runtime_check_failed'}
    print(json.dumps(result))
    return 0 if result.get('ready') or result['action'] == 'already_checking' else 2


if __name__ == '__main__':
    raise SystemExit(main())
