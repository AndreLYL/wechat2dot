"""Verify built packages contain runtime resources and required notices."""

import base64
import csv
from email.parser import BytesParser
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile
import tomllib
import zipfile


def inspect(directory):
    root = directory.resolve().parent
    project = tomllib.loads((root / 'pyproject.toml').read_text())['project']
    version = project['version']
    if f'__version__ = "{version}"' not in (root / 'wechat2dot/__init__.py').read_text():
        raise ValueError('Runtime version differs from project version')
    locked = tomllib.loads((root / 'uv.lock').read_text())['package']
    if next(p['version'] for p in locked if p['name'] == project['name']) != version:
        raise ValueError('Lockfile version differs from project version')
    wheels, sources = list(directory.glob("*.whl")), list(directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        raise ValueError("Build exactly one wheel and one source archive first")
    with zipfile.ZipFile(wheels[0]) as package:
        wheel_names = package.namelist()
        metadata_path = f'wechat2dot-{version}.dist-info/METADATA'
        metadata = BytesParser().parsebytes(package.read(metadata_path))
        if (metadata['Name'] != project['name'] or metadata['Version'] != version
                or metadata['Requires-Python'] != project['requires-python']
                or metadata['License-Expression'] != project['license']):
            raise ValueError('Wheel metadata differs from project metadata')
        requirements = metadata.get_all('Requires-Dist', [])
        def compact(value):
            base, _, marker = value.replace(' ', '').replace("'", '"').partition(';')
            name, specs = re.fullmatch(r'([A-Za-z0-9_.-]+)(.*)', base).groups()
            return name.lower().replace('_', '-') + ','.join(sorted(specs.split(','))) + ';' + marker
        expected = list(project['dependencies']) + [
            f'{dep}; extra == "{extra}"'
            for extra, deps in project.get('optional-dependencies', {}).items() for dep in deps]
        if sorted(map(compact, requirements)) != sorted(map(compact, expected)):
            raise ValueError('Wheel dependencies differ from project dependencies')
        for folder in ('wechat2dot', 'wechat_dot_bridge'):
            for path in (root / folder).rglob('*'):
                if path.is_file() and 'node_modules' not in path.parts and path.suffix in {'.py', '.mjs', '.ts', '.json'}:
                    relative = path.relative_to(root).as_posix()
                    if package.read(relative) != path.read_bytes():
                        raise ValueError('Missing or stale runtime file: ' + relative)
        for path in [root / 'LICENSE', root / 'NOTICE.txt', *(root / 'licenses').glob('*.txt')]:
            name = next(n for n in wheel_names if n.endswith('/' + path.name))
            if package.read(name) != path.read_bytes():
                raise ValueError('License contents differ: ' + path.name)
        record_path = f'wechat2dot-{version}.dist-info/RECORD'
        records = list(csv.reader(io.StringIO(package.read(record_path).decode())))
        if {row[0] for row in records} != set(wheel_names):
            raise ValueError('Wheel RECORD does not cover all files')
        for name, digest, size in records:
            if name == record_path:
                continue
            data = package.read(name)
            expected_digest = 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('=')
            if digest != expected_digest or size != str(len(data)):
                raise ValueError('Wheel RECORD mismatch: ' + name)
        for name in ("wechat2dot/cli.py", "wechat2dot/demo.py", "wechat2dot/onboarding.py", "wechat2dot/platform.py",
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
        prefix = f'wechat2dot-{version}/'
        source_paths = [root / name for name in ('pyproject.toml', 'uv.lock', 'README.md', 'CHANGELOG.md',
                                                 'LICENSE', 'NOTICE.txt', 'MANIFEST.in')]
        source_paths += list((root / 'docs').glob('*.md')) + list((root / 'licenses').glob('*.txt'))
        for folder in ('wechat2dot', 'wechat_dot_bridge', 'tests', 'prototype_tests', 'scripts'):
            source_paths += [p for p in (root / folder).rglob('*') if p.is_file()
                             and 'node_modules' not in p.parts and p.suffix in {'.py', '.mjs', '.ts', '.json', '.sh'}]
        for path in source_paths:
            member = prefix + path.relative_to(root).as_posix()
            if package.extractfile(member).read() != path.read_bytes():
                raise ValueError('Missing or stale source resource: ' + member)
        for document in ("README.md", "docs/CONNECT_DOT.md", "docs/DOCKER.md", "docs/ARCHITECTURE.md", "uv.lock",
                         "Dockerfile", "compose.yaml", "tests/test_pipeline.py", "prototype_tests/test_onboarding.py"):
            if not any(name.endswith("/" + document) for name in source_names):
                raise ValueError("Missing source resource: " + document)
    forbidden = re.compile(r"(?:^|/)(?:node_modules|\.venv|\.git|runtime|state|secrets)(?:/|$)|\.(?:sqlite\w*|db|key|pem)$|(?:^|/)\.env(?:\.|$)")
    if any(forbidden.search(name) for name in wheel_names + source_names):
        raise ValueError("Private or development state in package")
    return {"package_resources": "passed", "private_files": "absent", "third_party_notices": "present"}


if __name__ == "__main__":
    print(json.dumps(inspect(Path("dist")), indent=2))
