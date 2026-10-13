"""The same real process, storage and media behavior runs on all three OSes."""

import base64
import contextlib
import io
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
import wave

from wechat2dot.cli import main
from wechat2dot.onboarding import prepare, diagnose, new_file
from wechat2dot.platform import check_private_path, safe_open, check_private_fd
from wechat_dot_bridge.errors import BridgeError
from wechat_dot_bridge.events import Events
from wechat_dot_bridge.login import _read_state, _write_state, _login_lock
from wechat_dot_bridge.mcp import MCPServer
from wechat_dot_bridge.store import Store

ROOT = Path(__file__).resolve().parent.parent


class NativeRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="wechat2dot-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "runtime"
        self.config = Path(prepare(self.root)["config"])
        self.state = self.root / "state"

    def test_native_private_storage_is_validated(self):
        check_private_path(self.root, directory=True)
        check_private_path(self.config)
        result = diagnose(self.config)
        self.assertTrue(result["environment"]["live_runtime_supported"])
        self.assertIn(result["layers"]["private_storage"], {"windows_acl_ok", "posix_permissions_ok"})
        self.assertFalse(result["live_ready"])

    def test_existing_shared_directory_is_refused_without_repair(self):
        shared = Path(self.temporary.name) / "shared"
        shared.mkdir(mode=0o755)
        if os.name != "nt":
            shared.chmod(0o755)
        with self.assertRaises(ValueError):
            prepare(shared)
        self.assertFalse((shared / "bridge.json").exists())

    def test_file_handles_are_private_and_no_overwrite(self):
        path = self.root / "fixture-key"
        new_file(path, "synthetic-private-content")
        fd = safe_open(path, os.O_RDONLY)
        try:
            check_private_fd(fd)
            self.assertEqual(os.read(fd, 100), b"synthetic-private-content")
        finally:
            os.close(fd)
        with self.assertRaises(FileExistsError):
            new_file(path, "replace")

    def test_redirected_directory_is_refused_without_touching_target(self):
        target = self.root / "target"
        target.mkdir()
        link = self.root / "redirected"
        if os.name == "nt":
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            link.symlink_to(target, target_is_directory=True)
        try:
            with self.assertRaisesRegex(ValueError, "symlink_not_allowed"):
                prepare(link / "runtime")
            self.assertEqual(list(target.iterdir()), [])
        finally:
            if os.name == "nt":
                link.rmdir()
            else:
                link.unlink()

    def test_hardlinked_database_is_refused(self):
        original = self.root / "original"
        new_file(original, "synthetic")
        os.link(original, self.state / "bridge.sqlite3")
        with self.assertRaisesRegex(BridgeError, "unsafe_database_file"):
            Store(self.state)

    def test_login_state_atomic_replace_and_lock(self):
        self.assertIsNone(_read_state(self.state))
        with _login_lock(self.state):
            with self.assertRaisesRegex(BridgeError, "login_busy"):
                with _login_lock(self.state):
                    self.fail("Second login lock acquired")
            _write_state(self.state, {"version": 1, "status": "wait", "qrcode": "synthetic"})
            self.assertEqual(_read_state(self.state)["status"], "wait")
            _write_state(self.state, {"version": 1, "status": "expired"})
            self.assertEqual(_read_state(self.state)["status"], "expired")
        check_private_path(self.state / "weixin-login.json")

    def test_cli_status_executes_on_native_os(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["--config", str(self.config), "status"]), 0)
        self.assertFalse(json.loads(output.getvalue())["bound"])

    def test_attachment_and_backup_roundtrip_survive_restart(self):
        from wechat_dot_bridge.operations import backup, restore
        from wechat2dot.platform import private_directory
        store = Store(self.state)
        try:
            store.import_binding({"id": "fixture", "owner": "owner", "token": "synthetic-token",
                                  "base_url": "https://ilinkai.weixin.qq.com"})
            store.set_enabled(True)
            store.ingest("fixture", [{"source_id": "1", "peer": "owner", "text": "file test",
                         "context_token": "synthetic-context", "created_at": 1}], "cursor")
            server = MCPServer(store, Events(store, allowed_hosts=[]))
            result = server._call({"name": "stage_weixin_attachment", "arguments": {
                    "kind": "file", "name": "测试.txt", "text_content": "跨平台附件"}})
            self.assertFalse(result["isError"])
            identifier = result["structuredContent"]["attachment_id"]
            backup(store, self.root / "backup.sqlite3")
        finally:
            store.close()
        restored = private_directory(self.root / "restored")
        restore(self.root / "backup.sqlite3", restored)
        with contextlib.closing(Store(restored)) as recovered:
            self.assertFalse(recovered.get_binding()["enabled"])
            result = MCPServer(recovered, Events(recovered, allowed_hosts=[]))._call({"name": "read_weixin_attachment", "arguments": {"attachment_id": identifier}})
            self.assertFalse(result["isError"])
            # Match raw bytes, not only metadata.
            content = recovered.db.execute("SELECT data FROM attachments WHERE id=?", (identifier,)).fetchone()[0]
            self.assertEqual(content, "跨平台附件".encode())

    def test_native_audio_codec_roundtrip(self):
        from wechat_dot_bridge import audio
        if not audio.available():
            if os.environ.get("WECHAT2DOT_REQUIRE_CODECS") == "1":
                self.fail("CI must install native Node, SILK and FFmpeg")
            self.skipTest("Optional audio codecs not installed")
        source = io.BytesIO()
        with wave.open(source, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes(b"\x00\x00\x80\x01" * 12000)
        encoded = audio.to_silk(source.getvalue())
        self.assertTrue(audio.is_silk(encoded))
        with wave.open(io.BytesIO(audio.to_wav(encoded))) as wav:
            self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()), (1, 2, 24000))
            self.assertGreater(wav.getnframes(), 23000)
        with self.assertRaisesRegex(BridgeError, "media_audio_conversion_failed"):
            audio.to_silk(b"invalid audio")

    def test_actual_stdio_process_and_eof_release_single_consumer_lock(self):
        process = subprocess.Popen([sys.executable, "-m", "wechat2dot", "--config", str(self.config), "serve"],
                                  cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env={**os.environ, "PYTHONUTF8": "1"})
        responses = queue.Queue()
        reader = threading.Thread(target=lambda: [responses.put(line) for line in process.stdout], daemon=True)
        reader.start()

        def request(value):
            process.stdin.write(json.dumps(value).encode() + b"\n")
            process.stdin.flush()
            return json.loads(responses.get(timeout=15))

        try:
            discovery = request({"jsonrpc": "2.0", "id": 1, "method": "server/discover"})
            self.assertEqual(discovery["result"]["supportedVersions"], ["2026-07-28"])
            second = subprocess.run([sys.executable, "-m", "wechat2dot", "--config", str(self.config), "serve"],
                                    input=b"", cwd=ROOT, capture_output=True, timeout=15)
            self.assertEqual(second.returncode, 2, second.stderr)
            self.assertEqual(json.loads(second.stderr)["error"], "already_running")
            process.stdin.write(b"invalid-json\n")
            process.stdin.flush()
            self.assertEqual(json.loads(responses.get(timeout=15))["error"]["code"], -32700)
            listed = request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            self.assertEqual(len(listed["result"]["tools"]), 11)
            process.stdin.close()
            process.wait(timeout=15)
            self.assertEqual(process.returncode, 0, process.stderr.read())
            from wechat_dot_bridge.cli import acquire_lock
            with acquire_lock(self.state / "worker.lock"):
                pass
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            reader.join(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()


if __name__ == "__main__":
    unittest.main()
