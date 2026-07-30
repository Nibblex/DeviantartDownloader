"""Per-user answers to --only and --literature-format, read from one file.

Both flags are run-wide, which is the wrong shape for a folder full of
galleries that want different things: one artist worth only their images,
another followed for the literature. A settings file gives a user their own
answer and leaves the command line as the default for everyone it does not name.

A flag typed for this run outranks the file, which was written before it: the
settings the command line gives explicitly are locked and no entry may touch
them, whoever it names. What is left of the precedence is the usual one -- the
file, then DA_* in .env, then the built-in default.

Usernames are not stable -- DeviantArt lets people rename -- so an entry filed
under a name nobody answers to any more would quietly stop applying, and the
run would fall back to those defaults without saying so. Every entry the tool
touches therefore records the id the route that listed the user reports for
them, and a run that meets that id under a different name moves the entry
across instead of losing it.
"""

import json
import sys
from pathlib import Path

from .constants import TEXT_FORMATS, say
from .naming import user_ids
from .storage import write_json
from .sync import ONLY_FILTERS, parse_selectors

# Looked for in the output folder, beside the galleries it has something to say
# about, unless --user-config names another file.
FILENAME = "_users.json"

# The settings an entry may carry, spelled as the flags they stand for; SETTINGS
# below pairs each with the reader that validates its value.
ONLY = "only"
FORMAT = "literature-format"
# Written by the tool rather than by hand: what a rename is recognised by.
IDS = "ids"


def load_overrides(path: str | None, output_root: Path,
                   locked: frozenset[str] = frozenset()) -> "UserOverrides":
    """The settings file to read: the one named, or the one the output folder holds.

    A file asked for by name and not there is a typo worth stopping for; the
    default location simply has nothing to say when nobody put a file in it.
    """
    if not path:
        return UserOverrides(output_root / FILENAME, locked)
    chosen = Path(path).expanduser()
    if not chosen.is_file():
        sys.exit(f"No per-user settings file at {chosen}.")
    return UserOverrides(chosen, locked)


class UserOverrides:
    """What a per-user settings file says, one user at a time.

    Loading validates every entry, not only the ones this run will reach: a
    typo is worth hearing about the first time anything runs rather than weeks
    later, when the turn of the user it was written for finally comes. That
    holds for a setting `locked` by the command line too -- it is wrong today
    and would bite the first run that leaves the flag off.
    """

    def __init__(self, path: Path, locked: frozenset[str] = frozenset()):
        self.path = path
        self.locked = frozenset(locked)
        self._entries = _read(path)
        for username, entry in self._entries.items():
            _settings(entry, username, path)

    def for_user(self, username: str, deviations: list[dict],
                 only: frozenset[str] | None,
                 text_format: str) -> tuple[frozenset[str] | None, str]:
        """The --only and --literature-format to sync this user with.

        Returns what was passed in -- everyone's values -- unless the file names
        this user, in which case their entry replaces them one setting at a
        time, bar the ones the command line locked.

        `deviations` is the listing just fetched: it carries the author id a
        renamed user is recognised by, and that id is recorded on the way, so
        the rename after this one is recognised too. That happens even when
        every setting is locked: the rename still has to be followed, or the
        first run that leaves the flag off would find the entry stale.
        """
        ids = user_ids(deviations)
        key, moved = self._key_for(username, ids)
        if key is None:
            return only, text_format
        if self._learn(key, ids) or moved:
            self._save()
        settings = {name: value
                    for name, value in _settings(self._entries[key], key,
                                                 self.path).items()
                    if name not in self.locked}
        if settings:
            say(f"  {self.path.name}: {_describe(settings)}")
        return settings.get(ONLY, only), settings.get(FORMAT, text_format)

    def shadowed(self) -> frozenset[str]:
        """The settings the file has an answer for that the command line locked.

        Worth saying out loud once a run: a file that looks ignored is otherwise
        a puzzle, since nothing else printed would mention it.
        """
        return frozenset(
            name for username, entry in self._entries.items()
            for name in _settings(entry, username, self.path)
            if name in self.locked)

    def _key_for(self, username: str, ids: dict[str, str]) -> tuple[str | None, bool]:
        """This user's entry key, and whether following a rename moved it there.

        A key spelling the name wins, whatever its case: DeviantArt names are
        unique whichever way they are capitalised, and if someone has taken the
        old name over, the entry filed under the name asked for is the answer
        rather than a stranger's settings. Only when no key spells it does the
        recorded id get a say, which is exactly the case a rename leaves behind;
        the entry is then re-keyed to the new name, so the file stays readable
        to whoever wrote it. (None, False) when the file does not know this user.
        """
        wanted = username.casefold()
        if named := next((k for k in self._entries if k.casefold() == wanted), None):
            return named, False
        # An entry no run has touched yet has no recorded id, so it is never
        # mistaken for anyone.
        old = next((key for key, entry in self._entries.items()
                    if any((entry.get(IDS) or {}).get(route) == value
                           for route, value in ids.items())), None)
        if old is None:
            return None, False
        print(f'  {self.path.name}: "{old}" is now "{username}"; moving those '
              "settings over.")
        self._entries[username] = self._entries.pop(old)
        return username, True

    def _learn(self, key: str, ids: dict[str, str]) -> bool:
        """Record the ids this run saw. True when any of them was news."""
        known = self._entries[key].get(IDS) or {}
        if not (learned := {r: v for r, v in ids.items() if known.get(r) != v}):
            return False
        self._entries[key][IDS] = {**known, **learned}
        return True

    def _save(self) -> None:
        """Write the file back, without taking the run down if that fails.

        Nothing downloaded depends on this: what is being written is the id
        that would let a *later* rename be followed, so a read-only file costs
        that convenience rather than the gallery being synced.
        """
        try:
            write_json(self.path, self._entries)
        except OSError as e:
            print(f"  WARNING: could not update {self.path.name} ({e}); a later "
                  "rename may have to be followed by hand.")


def _read(path: Path) -> dict[str, dict]:
    """The file's entries, or none at all when there is no file.

    Unlike the download record, this file is written by hand and cannot be
    regenerated, so anything wrong with it ends the run: ignoring it and
    carrying on would sync every user it names with the wrong settings and say
    nothing about it.
    """
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        sys.exit(f"Could not read the per-user settings in {path}: {e}")
    if not isinstance(data, dict):
        sys.exit(f"{path} must hold one group of settings per username.")
    for username, entry in data.items():
        if not isinstance(entry, dict):
            sys.exit(f'{path}: "{username}" must name a group of settings, not '
                     f"a {type(entry).__name__}.")
    return data


def _settings(entry: dict, username: str, path: Path) -> dict:
    """One entry's settings, parsed and validated.

    Keys are read as the flags they stand for, give or take the dash or
    underscore. Anything unusable ends the run naming both the user and the
    problem: a typo that was skipped instead would download the wrong thing and
    only be noticed once the wrong files were on disk.
    """
    out = {}
    for written, value in entry.items():
        key = str(written).strip().lower().replace("_", "-")
        if key == IDS:
            continue
        if key not in SETTINGS:
            sys.exit(f'{path}: "{username}" sets "{written}", which is not a '
                     f"per-user setting. Accepted: {', '.join(SETTINGS)}.")
        out[key] = SETTINGS[key](value, username, path)
    return out


def _only(value, username: str, path: Path) -> frozenset[str]:
    """An entry's `only`, in any shape --only itself accepts.

    One string of words, a list of them, commas or spaces between: the same
    reading as on the command line. An empty value selects everything, which is
    how a user opts out of a run-wide --only.
    """
    chosen, unknown = parse_selectors(value if isinstance(value, list) else [value])
    if unknown:
        sys.exit(f'{path}: "{username}" asks --only for {", ".join(unknown)}, '
                 f"which is not among {', '.join(ONLY_FILTERS)}.")
    return chosen


def _format(value, username: str, path: Path) -> str:
    """An entry's literature format, which is one of the two --literature-format takes."""
    chosen = str(value).strip().lower()
    if chosen not in TEXT_FORMATS:
        sys.exit(f'{path}: "{username}" asks --literature-format for {value!r}, '
                 f"which must be one of {', '.join(TEXT_FORMATS)}.")
    return chosen


# What an entry may set, and how each value is read. A setting is a row here
# and a reader above, rather than another branch in _settings.
SETTINGS = {ONLY: _only, FORMAT: _format}


def _describe(settings: dict) -> str:
    """The settings as the flags they stand for, for the line that reports them."""
    shown = []
    if ONLY in settings:
        chosen = " ".join(sorted(settings[ONLY])) or "(everything)"
        shown.append(f"--only {chosen}")
    if FORMAT in settings:
        shown.append(f"--literature-format {settings[FORMAT]}")
    return ", ".join(shown)
