import contextlib
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

from wechat2dot.cli import main
from wechat2dot.demo import preview
from wechat2dot.onboarding import diagnose, prepare


class OnboardingTests(unittest.TestCase):
    def test_init_preserves_existing_config_and_uses_no_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "runtime"
            result = prepare(root)
            config = root / "bridge.json"
            before = config.read_bytes()
            self.assertFalse(result["credentials_created"])
            self.assertFalse(result["started"])
            self.assertEqual(json.loads(before)["callback_hosts"], [])
            with self.assertRaisesRegex(ValueError, "installation_exists"):
                prepare(root)
            self.assertEqual(config.read_bytes(), before)
            if os.name == "posix":
                self.assertEqual(root.stat().st_mode & 0o777, 0o700)
                self.assertEqual(config.stat().st_mode & 0o777, 0o600)

    def test_invalid_tunnel_is_rejected_before_files_are_created(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "runtime"
            with self.assertRaisesRegex(ValueError, "invalid_tunnel_id"):
                prepare(root, "anything", "/some/client")
            self.assertFalse(root.exists())
            with self.assertRaisesRegex(ValueError, "required_together"):
                prepare(root, "tunnel_" + "1" * 32)
            self.assertFalse(root.exists())

    def test_doctor_never_claims_live_connection_or_exposes_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(prepare(Path(temporary) / "runtime")["config"])
            data = json.loads(config.read_text())
            data["callback_hosts"] = ["callback.example.com"]
            data["secret"] = "DO_NOT_PRINT_THIS"
            config.write_text(json.dumps(data))
            result = diagnose(config)
            self.assertEqual(result["network_requests"], 0)
            self.assertFalse(result["live_ready"])
            self.assertEqual(result["layers"]["callback"], "configured_unverified")
            self.assertEqual(result["layers"]["wechat_binding"], "not_verified")
            self.assertNotIn("DO_NOT_PRINT_THIS", json.dumps(result))
            self.assertNotIn(temporary, json.dumps(result))

    def test_preview_does_not_report_fixture_as_executed(self):
        result = preview()
        self.assertEqual(result["mode"], "protocol_preview")
        self.assertEqual(result["engine_e2e"], "not_run")
        self.assertEqual(len(result["tools"]), 11)
        self.assertFalse(result["real_dot_connected"])

    def test_live_engine_requires_config_on_every_platform(self):
        with patch.object(sys, "platform", "win32"), contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(main(["serve"]), 2)
        self.assertIn("config_required", error.getvalue())

    def test_cli_preview_is_parseable_json(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["demo", "--preview"]), 0)
        self.assertEqual(json.loads(output.getvalue())["mode"], "protocol_preview")

    def test_command_help_reaches_onboarding_options_without_creating_files(self):
        with contextlib.redirect_stdout(io.StringIO()) as output, self.assertRaises(SystemExit) as caught:
            main(["init", "--help"])
        self.assertEqual(caught.exception.code, 0)
        self.assertIn("--directory", output.getvalue())
        self.assertIn("--tunnel-id", output.getvalue())

    @unittest.skipUnless(os.name == "posix", "POSIX symlink test")
    def test_symlink_destination_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "target").mkdir()
            (root / "link").symlink_to(root / "target", target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlink_not_allowed"):
                prepare(root / "link" / "runtime")
            self.assertFalse((root / "target" / "runtime").exists())

    def test_namespaced_tunnel_profile_preserves_id_and_key_reference(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / "tunnel-client"
            binary.write_text("synthetic binary")
            binary.chmod(0o700)
            identifier = "tunnel_ab12_" + "z" * 32
            result = prepare(root / "runtime", identifier, binary)
            profile = Path(result["profile"]).read_text()
            self.assertIn(identifier, profile)
            self.assertIn("file:", profile)
            self.assertIn("-m wechat2dot", profile)
            self.assertFalse((root / "runtime" / "tunnel-runtime-key").exists())

    def test_callback_approval_requires_stopped_worker_and_never_claims_subscription(self):
        from wechat2dot.onboarding import approve_callback
        from wechat_dot_bridge.cli import acquire_lock
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(prepare(Path(temporary) / "runtime")["config"])
            state = Path(json.loads(config.read_text())["state_dir"])
            with acquire_lock(state / "worker.lock"):
                with self.assertRaises(BlockingIOError):
                    approve_callback(config, "callback.example.com")
            result = approve_callback(config, "callback.example.com")
            self.assertFalse(result["subscription_verified"])
            self.assertEqual(json.loads(config.read_text())["callback_hosts"], ["callback.example.com"])
            for host in ("https://example.com", "*.example.com", "127.0.0.1", "example.com:443"):
                with self.assertRaises(ValueError):
                    approve_callback(config, host)

    def test_demo_runs_production_engine_without_network(self):
        from wechat2dot.demo import run_demo
        actual_socket = socket.socket

        class LocalSocketOnly(actual_socket):
            def connect(self, address):
                if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1"}:
                    raise AssertionError("Internet connection forbidden in demo")
                return super().connect(address)

            def connect_ex(self, address):
                if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1"}:
                    raise AssertionError("Internet connection forbidden in demo")
                return super().connect_ex(address)

        with patch("socket.socket", LocalSocketOnly), patch("socket.getaddrinfo", side_effect=AssertionError("DNS forbidden in demo")):
            result = run_demo()
        self.assertEqual(result["engine_e2e"], "passed")
        self.assertEqual(len(result["simulated_wechat"]), 3)
        self.assertFalse(result["real_wechat_connected"])


if __name__ == "__main__":
    unittest.main()
