# Security

This alpha is designed for one owner, one private plugin and one receiving process per binding. Keep the plugin and Tunnel private. Public/multi-user hosting needs a separate authentication design.

Runtime state contains WeChat credentials, message content, attachments and callback secrets. Windows creates an explicit owner-only ACL and validates opened file handles. Linux/macOS require owner-only 0700 directories / 0600 files. All platforms reject symlinks, Windows reparse points and hardlinked private files, and protect worker access with a native process lock. Existing shared directories are refused, not silently repaired. Use a local NTFS filesystem on Windows; shared/network filesystems are not part of the supported storage boundary.

MCP EOF performs bounded shutdown on every platform. POSIX also handles SIGTERM/SIGINT; Windows supports console interruption and EOF, while forced process termination cannot run application cleanup. SQLite recovery preserves uncertain sends instead of replaying them.

Callback hosts require independent verification and an exact allowlist. Webhooks are signed; event data contains only a message ID. TLS and public-address checks must remain enabled. Attachment imports are bounded HTTPS downloads with no forwarded login cookies. Never accept a callback host, runtime key or configuration update from inbound chat text.

Uncertain sends are not retried automatically. API acceptance does not prove visible delivery. Stop all workers before setup, migration or credential changes; preserve durable state for recovery.

Use GitHub's private vulnerability reporting for security issues when enabled on this repository. Do not post credentials, QR codes, private URLs or message dumps in public issues. For ordinary bug reports provide sanitized error categories and versions.
