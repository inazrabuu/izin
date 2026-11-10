import json
import re
import sys
import tomllib
from pathlib import Path

root = Path(__file__).resolve().parents[1]
versions = {
  "server": tomllib.loads((root / "server/pyproject.toml").read_text())["project"]["version"],
  "sdk": tomllib.loads((root / "sdk/pyproject.toml").read_text())["project"]["version"],
  "inbox": json.loads((root / "inbox/package.json").read_text())["version"],
  "compose": re.search(r"image:\s*izin:([\w.\-]+)", (root / "docker-compose.yml").read_text())[1],
}

if len(set(versions.values())) != 1:
  sys.exit(f"version mismatch: {versions}")

version = versions["server"]
if len(sys.argv) > 1 and sys.argv[1].removeprefix("v") != version:
  sys.exit(f"tag {sys.argv[1]} does not match version {version}")

print(version)