# wechat2dot

Send tasks to your existing OpenAI dot from WeChat and receive progress and results.

**Alpha:** this project's live WeChat ↔ dot validation is still pending. Your account needs private plugin, Secure MCP Tunnel and MCP Events access. Runtime: Linux, including Linux containers. Windows and macOS support the offline protocol preview.

```sh
git clone https://github.com/AndreLYL/wechat2dot.git
cd wechat2dot
uv sync --frozen --extra setup
uv run wechat2dot doctor
uv run wechat2dot demo
```

On Linux the demo exercises the production inbox, signed callbacks, MCP reads, interim/final replies, deduplication and restart recovery with synthetic network boundaries. No real accounts or model calls are used. Elsewhere it prints a clearly labelled protocol preview.

The bridge exposes 11 tools for owner-only messaging, progress, attachment reads/staging/import/replies and recovery. Your existing dot keeps its own authorized tools. It does not provide native approvals, full history sync, arbitrary unsolicited messages or voice calls. Artifact export requires text/bytes or a server-accessible HTTPS URL; internal sandbox paths cannot be fetched.

[Connect dot (Chinese)](CONNECT_DOT.md) · [Docker (Chinese)](DOCKER.md) · [Capabilities](CAPABILITIES.md) · [Contributing](../CONTRIBUTING.md)

Messaging core and original tests are derived from [cd233ljx/wechat-dot-bridge](https://github.com/cd233ljx/wechat-dot-bridge), MIT, revision `67c726e15ae2de8b60d293d6f5e81107bd0b6ebc`. We preserve the internal module names and notices. New work adds onboarding, diagnostics, demos, packaging and deployment. See [NOTICE](../NOTICE.txt). This is an independent community project.
