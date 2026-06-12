#!/usr/bin/env python3
"""
Alluvium — Health Check
Verifies dependencies, config, and folder structure are in place.
Exits 0 if all checks pass, 1 if any fail.
"""

import shutil
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent

REQUIRED_FOLDERS = [
    "00 Journal",
    "01 Inbox",
    "1 Projects",
    "2 Areas",
    "3 Resources",
    "4 Archive",
    "Authors",
    "People",
    "Day Summaries",
]

failures = []

# 1. Check Python dependencies
try:
    import yaml
except ImportError:
    yaml = None
    failures.append("Missing dependency: PyYAML  (pip install pyyaml)")

# 2. Validate config.yaml
provider = None
dayone_enabled = True
config_path = BASE_DIR / "config.yaml"
if not config_path.exists():
    failures.append("config.yaml not found")
elif yaml is not None:
    try:
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
        provider = config.get("provider")
        dayone_enabled = bool(config.get("dayone_enabled", True))
    except Exception as e:
        failures.append(f"config.yaml parse error: {e}")

# 3. Check the CLI tools the pipeline actually shells out to
if provider == "claude-code" and not shutil.which("claude"):
    failures.append("claude CLI not found on PATH (required by provider: claude-code)")
if dayone_enabled and not shutil.which("dayone"):
    failures.append("dayone CLI not found on PATH (set dayone_enabled: false in config.yaml if you don't use Day One)")

# 4. Check expected folder structure
for folder in REQUIRED_FOLDERS:
    if not (BASE_DIR / folder).is_dir():
        failures.append(f"Missing folder: {folder}")

# Report
if failures:
    print("Alluvium healthcheck FAILED:")
    for msg in failures:
        print(f"  ✗ {msg}")
    sys.exit(1)
else:
    print("Alluvium healthcheck passed — all checks OK.")
    sys.exit(0)
