"""Per-user native service definitions; no administrator privileges or secrets."""

import os
from pathlib import Path
import plistlib
import subprocess
import sys
from xml.etree import ElementTree as ET


LABEL = "com.wechat2dot.native"
UNIT = "wechat2dot-native.service"
TASK = "wechat2dot-native"


def args(root):
    python = Path(sys.executable)
    # Homebrew replaces its versioned Cellar directory on upgrade.
    for parent in python.parents:
        if parent.name == "Cellar":
            stable = parent.parent / "opt/wechat2dot/libexec/bin" / python.name
            if stable.is_file() and stable.samefile(python):
                python = stable
            break
    return [str(python), "-m", "wechat2dot", "--home", str(root), "run"]


def unit_arg(s):
    return (
        '"'
        + s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "$$")
        .replace("%", "%%")
        .replace("\n", "\\n")
        + '"'
    )


def definition(root, platform=sys.platform):
    cmd = args(root)
    if platform == "darwin":
        return (
            Path.home() / "Library/LaunchAgents" / (LABEL + ".plist"),
            plistlib.dumps(
                dict(
                    Label=LABEL,
                    ProgramArguments=cmd,
                    RunAtLoad=True,
                    KeepAlive=True,
                    ThrottleInterval=10,
                    ProcessType="Background",
                )
            ),
        )
    if platform == "win32":
        ns = "http://schemas.microsoft.com/windows/2004/02/mit/task"
        task = ET.Element("Task", version="1.2", xmlns=ns)
        user = subprocess.getoutput("whoami") if os.name == "nt" else "interactive-user"
        trigger = ET.SubElement(ET.SubElement(task, "Triggers"), "LogonTrigger")
        ET.SubElement(trigger, "UserId").text = user
        principal = ET.SubElement(
            ET.SubElement(task, "Principals"), "Principal", id="Owner"
        )
        ET.SubElement(principal, "UserId").text = user
        ET.SubElement(principal, "LogonType").text = "InteractiveToken"
        settings = ET.SubElement(task, "Settings")
        for k, v in [
            ("MultipleInstancesPolicy", "IgnoreNew"),
            ("DisallowStartIfOnBatteries", "false"),
            ("StopIfGoingOnBatteries", "false"),
            ("ExecutionTimeLimit", "PT0S"),
        ]:
            ET.SubElement(settings, k).text = v
        restart = ET.SubElement(settings, "RestartOnFailure")
        ET.SubElement(restart, "Interval").text = "PT1M"
        ET.SubElement(restart, "Count").text = "999"
        exe = ET.SubElement(ET.SubElement(task, "Actions", Context="Owner"), "Exec")
        ET.SubElement(exe, "Command").text = cmd[0]
        ET.SubElement(exe, "Arguments").text = subprocess.list2cmdline(cmd[1:])
        return root / "native-task.xml", ET.tostring(
            task, encoding="utf-16", xml_declaration=True
        )
    body = (
        "[Unit]\nDescription=WeChat to DOT native bridge\n[Service]\nType=simple\nUMask=0077\nExecStart="
        + " ".join(map(unit_arg, cmd))
        + "\nRestart=always\nRestartSec=10\n[Install]\nWantedBy=default.target\n"
    )
    return Path.home() / ".config/systemd/user" / UNIT, body.encode()


def manage(root, action):
    path, content = definition(root)

    def call(*cmd, required=True):
        result = subprocess.run(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        if required and result.returncode:
            raise ValueError("service_" + action + "_failed")
        return result.returncode == 0

    if action == "install":
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.is_symlink() or (path.exists() and path.read_bytes() != content):
            raise ValueError("conflicting_service_definition")
        if not path.exists():
            from .platform import safe_open

            fd = safe_open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(content)
    if sys.platform == "darwin":
        domain = "gui/" + str(os.getuid())
        loaded = call("launchctl", "print", domain + "/" + LABEL, required=False)
        if action in ("install", "start") and not loaded:
            call("launchctl", "bootstrap", domain, str(path))
        if action in ("stop", "uninstall") and loaded:
            call("launchctl", "bootout", domain + "/" + LABEL)
        if action == "status":
            return loaded
    elif os.name == "nt":
        if action == "install":
            call("schtasks", "/Create", "/TN", TASK, "/XML", str(path), "/F")
        if action in ("install", "start"):
            call("schtasks", "/Run", "/TN", TASK)
        if action == "stop":
            call("schtasks", "/End", "/TN", TASK)
        if action == "uninstall":
            call("schtasks", "/End", "/TN", TASK, required=False)
            call("schtasks", "/Delete", "/TN", TASK, "/F")
        if action == "status":
            r = subprocess.run(
                ["schtasks", "/Query", "/TN", TASK, "/XML"], capture_output=True
            )
            if r.returncode:
                return False
            # Task registration is separate from a fresh worker heartbeat.
            import json, time

            try:
                return (
                    json.loads((root / "status.json").read_text()).get("online")
                    and time.time() - (root / "status.json").stat().st_mtime < 90
                )
            except (OSError, ValueError):
                return False
    else:
        if action == "install":
            call("systemctl", "--user", "daemon-reload")
            call("systemctl", "--user", "enable", "--now", UNIT)
        elif action == "uninstall" and path.exists():
            call("systemctl", "--user", "disable", "--now", UNIT)
        elif action == "status":
            return call(
                "systemctl", "--user", "is-active", "--quiet", UNIT, required=False
            )
        elif action in ("start", "stop"):
            call("systemctl", "--user", action, UNIT)
    if action == "uninstall" and path.exists():
        if path.is_symlink() or path.read_bytes() != content:
            raise ValueError("conflicting_service_definition")
        path.unlink()
        if sys.platform not in ("darwin", "win32"):
            call("systemctl", "--user", "daemon-reload")
    return True
