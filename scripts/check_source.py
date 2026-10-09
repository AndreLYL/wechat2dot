"""Portable pre-publish check of selected Git files and local documentation links."""

import json
from pathlib import Path
import re
import subprocess

from scan_secrets import runtime_path, secret_matches


def check(root):
    process = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                             cwd=root, capture_output=True, check=True)
    findings = []
    for raw in process.stdout.split(b"\0"):
        if not raw:
            continue
        relative = raw.decode("utf-8")
        path = root / relative
        if runtime_path(relative):
            findings.append({"file": relative, "error": "private_path"})
        if path.is_symlink() or not path.is_file():
            findings.append({"file": relative, "error": "unsupported_file"})
            continue
        data = path.read_bytes()
        if b"\0" in data:
            continue
        text = data.decode("utf-8", errors="replace")
        for category, line in secret_matches(text):
            findings.append({"file": relative, "line": line, "error": category})
        if path.suffix == ".md":
            for target in re.findall(r"\]\(([^)]+)\)", text):
                target = target.split("#", 1)[0]
                if not target or "://" in target or target.startswith("mailto:"):
                    continue
                if not (path.parent / target).exists():
                    findings.append({"file": relative, "error": "broken_link", "target": target})
    return {"ok": not findings, "findings": findings}


if __name__ == "__main__":
    result = check(Path(__file__).resolve().parent.parent)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ok"] else 1)
