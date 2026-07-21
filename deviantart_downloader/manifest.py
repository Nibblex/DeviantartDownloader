"""The per-gallery record of what has already been downloaded."""

import json
import re
import threading
from pathlib import Path

from .constants import API_SUBDIR, WEB_SUBDIR
from .naming import deviation_key


class DownloadManifest:
    """Persistent record of already-downloaded deviations (by deviation key).

    Allows detecting duplicates across runs even if the artwork's title
    (and therefore the file name) has changed. File names are stored relative
    to the gallery folder, so a work downloaded into web/ or api/ is found
    again whichever route lists it next time.
    """

    def __init__(self, out_dir: Path):
        self.path = out_dir / "_downloaded.json"
        self._lock = threading.Lock()
        self._entries: dict[str, str] = {}
        if self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._entries = {str(k): str(v) for k, v in data.items()}
            except (json.JSONDecodeError, OSError):
                print(f"  WARNING: could not read {self.path.name}, it will be regenerated.")
        self._seed_from_existing_files(out_dir)

    def _seed_from_existing_files(self, out_dir: Path):
        """Register files downloaded by previous versions of the script
        (name suffixed with _<first 8 chars of the deviationid>)."""
        pattern = re.compile(r"_([0-9A-Fa-f]{8})$")
        folders = [out_dir] + [
            d for name in (WEB_SUBDIR, API_SUBDIR)
            if (d := out_dir / name).is_dir()
        ]
        for folder in folders:
            for f in folder.iterdir():
                if not f.is_file() or f.name.startswith("_") or f.suffix == ".part":
                    continue
                m = pattern.search(f.stem)
                if m:
                    rel = f.relative_to(out_dir).as_posix()
                    self._entries.setdefault(m.group(1).upper(), rel)

    def _key(self, dev_id: str) -> str:
        # Numeric website ids are used whole; UUIDs keep the historical
        # 8-character prefix so manifests written by older versions still match.
        return dev_id if dev_id.isdigit() else dev_id[:8].upper()

    def adopt_web_keys(self, metadata: list[dict]) -> int:
        """Re-key entries recorded by API-only versions to the shared key.

        Older manifests are keyed by UUID prefix, which the website route
        cannot produce. The saved metadata holds both the UUID and the URL the
        numeric id comes from, so it can bridge the two and spare the user a
        full re-download. Returns how many entries were migrated.
        """
        migrated = 0
        with self._lock:
            for dev in metadata:
                if not isinstance(dev, dict):
                    continue
                new_key = self._key(deviation_key(dev))
                old_key = self._key(dev.get("deviationid") or "")
                if (not new_key.isdigit() or new_key == old_key
                        or new_key in self._entries or old_key not in self._entries):
                    continue
                self._entries[new_key] = self._entries.pop(old_key)
                migrated += 1
            if migrated:
                self._save_locked()
        return migrated

    def has(self, dev_id: str) -> bool:
        with self._lock:
            return self._key(dev_id) in self._entries

    def filename_for(self, dev_id: str) -> str | None:
        with self._lock:
            return self._entries.get(self._key(dev_id))

    def add(self, dev_id: str, filename: str):
        with self._lock:
            self._entries[self._key(dev_id)] = filename
            self._save_locked()

    def _save_locked(self):
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self._entries, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.path)
