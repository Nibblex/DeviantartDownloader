"""What the API answered about the works the website only serves blurred.

Those works are the whole reason the API route exists, and reaching them costs
the one thing this tool has to ration: the API is keyed by a UUID the website
listing does not carry, so finding a work means walking pages of the API's own
listing until it turns up. A run that revisits the same works -- a repair pass,
a retried failure, a download interrupted halfway -- would pay again for an
answer that has not changed since.

So the answers are kept. What is worth keeping is the deviation exactly as the
API returned it: the UUID it is keyed by and the URL to fetch, whose CDN token
carries no expiry, which is what makes the answer outlive the run that paid for
it. A cached answer that turns out not to work is dropped rather than retried
forever, so the next run pays for a fresh one.
"""

from pathlib import Path

from .naming import content_src, is_blurred
from .storage import read_json, write_json

FILENAME = "_resolved.json"


class ResolvedCache:
    """The API entries already paid for, by the key both routes agree on."""

    def __init__(self, out_dir: Path):
        self.path = out_dir / FILENAME
        self._entries = {str(k): v for k, v in read_json(self.path, {}).items()
                         if isinstance(v, dict)}

    def get(self, key: str, *, user_mode: bool) -> dict | None:
        """The cached answer for a work, when it is still as good as a fresh one.

        A logged-out run cached whatever the API offered it, which for a mature
        work is the blurred placeholder. Once a session could do better, that
        answer is not worth reusing -- the same judgement --redownload-blurred
        makes about the file already on disk. The other direction is fine: an
        unblurred URL a logged-in run cached stays valid for a logged-out one,
        because it is the CDN token that authorises the fetch, not the session.
        """
        entry = self._entries.get(key)
        if entry is None:
            return None
        if user_mode and is_blurred(content_src(entry)):
            return None
        return entry

    def remember(self, answers: dict[str, dict]) -> None:
        """Record what a lookup just paid for, keyed by work, in one write."""
        # A work with no key could never be looked up again, and "" would
        # collide with the next one.
        added = {key: entry for key, entry in answers.items() if key}
        if not added:
            return
        self._entries.update(added)
        self._save()

    def forget(self, key: str) -> bool:
        """Drop an answer that did not work. True when there was one to drop.

        A download can fail for reasons that have nothing to do with the URL, so
        this throws away answers that were probably fine. That costs one page of
        the API listing next time; keeping a URL the CDN has stopped serving
        would cost the work, every run, forever.

        Whether to say so is the caller's: it is the one that knows a failure
        has just been reported for this work, and the news only makes sense
        against it.
        """
        if self._entries.pop(key, None) is None:
            return False
        self._save()
        return True

    def _save(self) -> None:
        """Write the cache back, without taking the run down if that fails.

        Nothing downloaded depends on it: an unwritable cache costs the requests
        it would have saved a later run, which is what not having one costs too.
        Said out loud all the same -- every other file this tool keeps reports
        the trouble, and silence here would leave a folder nothing can be cached
        in looking exactly like one where the cache is working.
        """
        try:
            write_json(self.path, self._entries)
        except OSError as e:
            print(f"  WARNING: could not update {self.path.name} ({e}); the "
                  "requests it would have saved a later run will be spent again.")
