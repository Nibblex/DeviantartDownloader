"""Reading configuration out of the environment and .env files."""

import os
import sys
from pathlib import Path


def load_dotenv(path: Path | None = None):
    """Load variables from a .env file without overwriting already-defined ones.

    Looks first in the current working directory and, if not found, next to
    the package (useful when running straight from a clone of the repo).
    """
    repo_root = Path(__file__).resolve().parent.parent
    candidates = [path] if path else [Path.cwd() / ".env", repo_root / ".env"]
    env_file = next((p for p in candidates if p.is_file()), None)
    if env_file is None:
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        os.environ.setdefault(key, value)


def env_int(name: str, default: int) -> int:
    """Read an integer from an environment variable, with a default value."""
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        sys.exit(f"The value of {name} must be an integer, not: {value!r}")


def env_bool(name: str, default: bool) -> bool:
    """Read a boolean from an environment variable, with a default value."""
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    sys.exit(f"The value of {name} must be a boolean (true/false), not: {value!r}")
