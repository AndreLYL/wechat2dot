"""Guard checks are operator-only and never replace a live network-waiting process."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from wechat2dot.onboarding import new_file
from wechat2dot.platform import private_directory
from wechat2dot.runtime_guard import ensure_running, load_settings


class RuntimeGuardTests(unittest.TestCase):
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
