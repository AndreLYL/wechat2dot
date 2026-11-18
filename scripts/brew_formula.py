"""Generate the tap formula for exactly the sdist uploaded to the GitHub release."""

import hashlib
from pathlib import Path
import sys
from packaging.utils import parse_sdist_filename


def render(path):
    name, version = parse_sdist_filename(path.name)
    if name != "wechat2dot":
        raise ValueError("unexpected_distribution")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    return f'''class Wechat2dot < Formula
  desc "Mirror your primary DOT room into your own WeChat"
  homepage "https://github.com/AndreLYL/wechat2dot"
  url "https://github.com/AndreLYL/wechat2dot/releases/download/v{version}/{path.name}"
  sha256 "{sha}"
  license "MIT"
  depends_on "uv"
  depends_on "python@3.12"

  def install
    system "uv", "venv", "--python", Formula["python@3.12"].opt_bin/"python3.12", libexec
    system "uv", "pip", "install", "--python", libexec/"bin/python", "."
    bin.install_symlink libexec/"bin/wechat2dot"
  end

  test do
    assert_match "{version}", shell_output("#{{bin}}/wechat2dot --version")
  end
end
'''


if __name__ == "__main__":
    print(render(Path(sys.argv[1])), end="")
