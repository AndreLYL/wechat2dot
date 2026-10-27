"""Guard checks are operator-only and never replace a live network-waiting process."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from wechat2dot.onboarding import new_file
from wechat2dot.platform import private_directory
from wechat2dot.runtime_guard import _invoke, ensure_running, load_settings, run


class RuntimeGuardTests(unittest.TestCase):
    def test_client_invocation_keeps_json_output_and_noninteractive_input(self):
        result = _invoke([sys.executable, '-c',
                          'import json,sys; print(json.dumps({"eof":sys.stdin.buffer.read(1)==b""}))'])
        self.assertEqual(result, {'eof':True})

    @unittest.skipUnless(os.name == 'nt', 'Windows console behavior')
    def test_client_and_its_console_child_have_no_visible_window(self):
        probe = '''import ctypes,json
def console_state():
    k=ctypes.windll.kernel32
    k.GetConsoleWindow.restype=ctypes.c_void_p
    u=ctypes.windll.user32
    u.IsWindowVisible.argtypes=[ctypes.c_void_p]
    h=k.GetConsoleWindow()
    return {"console":bool(h),"visible":bool(h and u.IsWindowVisible(h))}
'''
        # Both console programs use their normal executable and stdio. The
        # second launch intentionally has no hiding flags of its own, matching
        # the official managed-runtime launcher and MCP subprocess chain.
        child = probe + '\nprint(json.dumps(console_state()))'
        command = probe + f'''import subprocess,sys
r=subprocess.run([sys.executable,"-c",{child!r}],capture_output=True,check=True)
print(json.dumps({{"processes":[console_state(),json.loads(r.stdout)]}}))
'''
        result = _invoke([sys.executable, '-c', command])
        self.assertEqual(result, {'processes':[{'console':True,'visible':False},
                                             {'console':True,'visible':False}]})

    def settings(self, root):
        return {'alias':'fixture-runtime', 'profile_name':'fixture-runtime',
                'tunnel_client':str(root/'client.exe'), 'profile_dir':str(root/'profiles'),
                'runtime_key_file':str(root/'key'), 'tunnel_id':'tunnel_fixture',
                'bridge_config':str(root/'bridge.json'),
                'mcp_command':"python -m fixture --config 'path with spaces'"}

    def test_live_but_network_unready_runtime_is_never_restarted(self):
        settings=self.settings(Path.cwd())
        invoke=Mock(return_value={'process_running':True,'healthy':True,'ready':False})
        result=ensure_running(settings,invoke)
        self.assertEqual(result,{'action':'reused','running':True,'healthy':True,'ready':False})
        invoke.assert_called_once_with([settings['tunnel_client'],'runtimes','status','fixture-runtime','--json'])

    def test_stopped_runtime_starts_once_and_rechecks_health_with_key_reference_only(self):
        settings=self.settings(Path.cwd())
        invoke=Mock(side_effect=[{'process_running':False},{'started':True},
                                 {'process_running':True,'healthy':True,'ready':True}])
        result=ensure_running(settings,invoke)
        self.assertEqual(result,{'action':'started','running':True,'healthy':True,'ready':True})
        args=invoke.call_args_list[1].args[0]
        self.assertIn('file:'+settings['runtime_key_file'],args)
        self.assertEqual(args[args.index('--mcp-command')+1],settings['mcp_command'])
        self.assertEqual(invoke.call_count,3)

    def test_unknown_status_fails_closed_without_connect(self):
        invoke=Mock(return_value={'private':'fixture-private-detail'})
        with self.assertRaisesRegex(ValueError,'^runtime_response_invalid$'):
            ensure_running(self.settings(Path.cwd()),invoke)
        invoke.assert_called_once()

    def test_failed_runtime_check_replaces_stale_healthy_heartbeat(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = private_directory(Path(temporary)/'private')
            path = root/'guard.json'
            new_file(root/'last-runtime-check.json', json.dumps({'ready':True,'checked_at':1}))
            store = Mock()
            store.get_status.return_value = {'bound':True,'enabled':True,
                                            'diagnostics':{'healthy':True},'pending':2}
            with patch('wechat2dot.runtime_guard.load_settings', return_value=(path,self.settings(root))), \
                    patch('wechat2dot.runtime_guard.ensure_running', side_effect=ValueError('private-error')), \
                    patch('wechat2dot.runtime_guard.load_config', return_value=(root,)), \
                    patch('wechat_dot_bridge.store.Store', return_value=store):
                result = run(path)
            record = json.loads((root/'last-runtime-check.json').read_text())
            self.assertFalse(result['ready'])
            self.assertEqual(record['error'],'runtime_check_failed')
            self.assertGreater(record['checked_at'],1)
            self.assertNotIn('private-error', json.dumps(record))
            store.close.assert_called_once()

    def test_live_tunnel_does_not_hide_unbound_or_disabled_bridge(self):
        for bound, enabled in ((False,True),(True,False)):
            with self.subTest(bound=bound,enabled=enabled), tempfile.TemporaryDirectory() as temporary:
                root = private_directory(Path(temporary)/'private')
                store = Mock()
                store.get_status.return_value = {'bound':bound,'enabled':enabled,
                                                'diagnostics':{'healthy':True},'pending':0}
                with patch('wechat2dot.runtime_guard.load_settings', return_value=(root/'guard.json',self.settings(root))), \
                        patch('wechat2dot.runtime_guard.ensure_running', return_value={'ready':True}), \
                        patch('wechat2dot.runtime_guard.load_config', return_value=(root,)), \
                        patch('wechat_dot_bridge.store.Store', return_value=store):
                    result = run(root/'guard.json')
                self.assertFalse(result['ready'])
                self.assertFalse(result['bridge_healthy'])

    def test_settings_are_private_and_cannot_supply_a_literal_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=private_directory(Path(temporary)/'private')
            private_directory(root/'profiles')
            new_file(root/'key','fixture-private-key')
            new_file(root/'bridge.json','{}')
            (root/'client.exe').write_text('fixture-client')
            settings=self.settings(root)
            path=root/'guard.json'
            new_file(path,json.dumps(settings))
            self.assertEqual(load_settings(path)[1],settings)
            settings['runtime_api_key']='fixture-private-key'
            path.write_text(json.dumps(settings))
            with self.assertRaisesRegex(ValueError,'^runtime_settings_invalid$'):
                load_settings(path)
