"""Native ChatGPT dot messaging: login, dot discovery, room reads/writes and live updates.

The endpoints are undocumented. Every response shape is checked; an unexpected shape
raises DotProtocolError so the bridge stops instead of guessing.
"""
