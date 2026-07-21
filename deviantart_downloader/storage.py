"""Reading and writing the JSON files a gallery folder keeps.

Both of them (`_downloaded.json` and `_metadata.json`) are rewritten while
downloads are in flight, so they are written through a temporary file: a run
killed mid-write leaves the previous version intact instead of a truncated one.
"""

import json
from pathlib import Path


def read_json(path: Path, default):
    """Parse a JSON file, falling back to default if it cannot be used.

    A damaged file is reported rather than silently discarded: it is about to
    be regenerated, and losing what it held is worth a line of output. The
    fallback also decides the expected shape, so a file holding the wrong kind
    of data is treated as damaged.
    """
    if not path.is_file():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"  WARNING: could not read {path.name} ({e}); it will be regenerated.")
        return default
    if not isinstance(data, type(default)):
        print(f"  WARNING: {path.name} holds unexpected data; it will be regenerated.")
        return default
    return data


def write_json(path: Path, data):
    """Write a JSON file atomically, so an interrupted run cannot truncate it."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
