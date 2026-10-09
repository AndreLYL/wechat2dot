"""Verify the built derivative packages contain runtime resources and notices."""

import json
from pathlib import Path
import re
import tarfile
import zipfile


def inspect(directory):
    wheels, sources = list(directory.glob("*.whl")), list(directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        raise ValueError("Build exactly one wheel and one source archive first")
    with zipfile.ZipFile(wheels[0]) as package:
        wheel_names = package.namelist()
        for name in ("wechat2dot/cli.py", "wechat2dot/demo.py", "wechat2dot/onboarding.py",
                     "wechat_dot_bridge/sdk_adapter.py", "wechat_dot_bridge/migrations.py",
                     "wechat_dot_bridge/codecs/audio.mjs", "wechat_dot_bridge/codecs/markdown-filter.ts"):
            if name not in wheel_names:
                raise ValueError("Missing runtime resource: " + name)
        for notice in ("LICENSE", "NOTICE.txt", "upstream-bridge-MIT.txt",
                       "Tencent-openclaw-weixin-MIT.txt", "silk-wasm-MIT.txt"):
            if not any(name.endswith("/" + notice) for name in wheel_names):
                raise ValueError("Missing notice: " + notice)
    with tarfile.open(sources[0]) as package:
        source_names = package.getnames()
        for document in ("README.md", "docs/CONNECT_DOT.md", "docs/DOCKER.md", "uv.lock",
                         "Dockerfile", "compose.yaml", "tests/test_pipeline.py", "prototype_tests/test_onboarding.py"):
            if not any(name.endswith("/" + document) for name in source_names):
                raise ValueError("Missing source resource: " + document)
    forbidden = re.compile(r"(?:^|/)(?:node_modules|\.venv|\.git|runtime|state|secrets)(?:/|$)|\.(?:sqlite\w*|db|key|pem)$|(?:^|/)\.env(?:\.|$)")
    if any(forbidden.search(name) for name in wheel_names + source_names):
        raise ValueError("Private or development state in package")
    return {"package_resources": "passed", "private_files": "absent", "third_party_notices": "present"}


if __name__ == "__main__":
    print(json.dumps(inspect(Path("dist")), indent=2))
