# wechat2dot

Use your own WeChat conversation as a second client for your primary DOT room. A reply started on your phone, a follow-up on your Mac and a scheduled update written to that room all enter the same delivery queue. A message you send from WeChat appears in that room under your account.

## Install the 2.0.0a2 prerelease

Install with `curl -fsSL https://raw.githubusercontent.com/AndreLYL/wechat2dot/main/install.sh | sh` on macOS/Linux. The installer fetches the versioned wheel from GitHub Releases. From a checkout containing the native bridge, run `sh ./install.sh` on macOS/Linux or `.\install.ps1` in Windows PowerShell. With uv installed, `uv run --frozen wechat2dot setup` also works.

1. Scan the private QR with the WeChat account you want to bind.
2. Sign into the ChatGPT account containing your existing DOT via the device-code page. If required, enable device-code login under ChatGPT security settings. Select the DOT if several exist.
3. Send `/status` to the bound bot and check its response. Ask DOT in its primary room to reply with a unique phrase. Confirm one matching WeChat message, then reply in WeChat and check the ChatGPT room.

New messages are mirrored from link time. Supported content includes text, files and images up to 20 MiB, task milestones and simple approvals. A separate notification-only MCP process can enqueue scheduled messages. Desktop credentials use the system keyring. Container credentials and the queue live in an owner-only volume. Native mode uses an undocumented ChatGPT interface and a device authorization with broader permissions than the selected room.

## Reply formatting in WeChat

Native mode automatically formats ordinary replies sent from the primary DOT room to WeChat. Short plain-text replies stay as written. Headings, lists, compact tables and code blocks retain their structure. Wide tables become labeled entries, one per row, so each field is readable on a phone. Long replies split at paragraph or list boundaries where possible to keep links and code blocks intact.

On WeChat for iPhone, we tested the expanded table layout and verified that copying a code block preserves line breaks, indentation and literal characters.

This feature is available from the `main` source checkout. The installer uses a fixed GitHub Release. To try the source version, run `uv run --frozen wechat2dot setup` from the checkout.

## When delivery pauses

Run `wechat2dot status --json` or `wechat2dot doctor --live`. After a WeChat context expires, send `/ping` to reopen it. A catch-up summary precedes retained messages. Check uncertain deliveries at their destination before taking action. On macOS the optional Accessibility helper can refresh the window while the desktop is unlocked and idle.

[Native operations and migration (Chinese)](NATIVE.md) · [Docker](DOCKER.md) · [0.2 official Tunnel mode](GETTING_STARTED.md) · [License notices](../NOTICE.txt)
