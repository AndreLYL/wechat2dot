# Changelog

## Unreleased

## 0.2.0a2 — 2026-10-31

- Replaced the overview-only README with a complete first-run guide: prerequisites, credentials, QR binding, private plugin setup, callback verification, event subscription and visible WeChat acceptance.
- Added copyable managed-runtime setup, Windows Task Scheduler settings, Linux systemd and macOS LaunchAgent examples, and maintenance/recovery instructions.
- Updated the current validation status: one Windows deployment has passed text, image, CSV and WeChat-transcription-to-text checks. Raw-audio understanding, generated voice replies and live Linux/macOS validation remain unverified.
- Includes the previously merged transient network recovery, waiting notice, runtime guard and hidden Windows console fixes. These changes were absent from 0.2.0a1 and are included in this release.

## 0.2.0a1

- Native Windows, Linux and macOS runtime with a shared OS boundary.
- Windows owner-only ACLs and handle checks; native process locks across platforms.
- Real engine demo on every platform; native stdio/EOF, login-state, media/backup and configuration tests.
- Product-focused documentation and optional Docker deployment.
- At this release, live WeChat / dot validation was still pending.

## 0.1.0a1

- Introduced iLink messaging, durable queues, media and MCP Events.
- Added wechat2dot onboarding CLI, hidden runtime-key entry, QR login, explicit callback approval and offline diagnostics.
- Added clearly labelled protocol preview and production-engine offline demo.
- Added container deployment, cross-platform onboarding checks and Linux engine CI.
- At this release, live validation of this project's deployment was still pending.
