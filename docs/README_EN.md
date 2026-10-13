# wechat2dot

Send tasks to your existing OpenAI dot from WeChat and receive progress and results.

**Windows, Linux and macOS — native runtime, no Docker or WSL required.**

**Alpha:** live WeChat ↔ dot validation is still pending. Your account needs private plugin, Secure MCP Tunnel and MCP Events access.

```sh
git clone https://github.com/AndreLYL/wechat2dot.git
cd wechat2dot
uv sync --frozen --extra setup
uv run wechat2dot doctor
uv run wechat2dot demo
```

On every supported OS the demo exercises the production inbox, signed callbacks, MCP reads, interim/final replies, deduplication and restart recovery with synthetic network boundaries. No real accounts or model calls are used. `demo --preview` only prints the protocol structure.

The bridge exposes 11 tools for owner-only messaging, progress, attachment reads/staging/import/replies and recovery. Your existing dot keeps its own authorized tools. It does not provide native approvals, full history sync, arbitrary unsolicited messages or voice calls. Artifact export requires text/bytes or a server-accessible HTTPS URL; internal sandbox paths cannot be fetched.

[Connect dot (Chinese)](CONNECT_DOT.md) · [Docker (Chinese)](DOCKER.md) · [Capabilities](CAPABILITIES.md) · [Contributing](../CONTRIBUTING.md)

The design separates the durable task core, WeChat channel, dot connector and native OS boundary. See [architecture](ARCHITECTURE.md). This is an independent community project.
