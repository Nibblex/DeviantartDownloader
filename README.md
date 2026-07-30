# DeviantArt Downloader

[![CI](https://github.com/Nibblex/DeviantartDownloader/actions/workflows/ci.yml/badge.svg)](https://github.com/Nibblex/DeviantartDownloader/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/Nibblex/DeviantartDownloader/branch/main/graph/badge.svg)](https://codecov.io/gh/Nibblex/DeviantartDownloader)
[![PyPI](https://img.shields.io/pypi/v/deviantart-gallery-downloader)](https://pypi.org/project/deviantart-gallery-downloader/)
[![Python versions](https://img.shields.io/pypi/pyversions/deviantart-gallery-downloader)](https://pypi.org/project/deviantart-gallery-downloader/)
[![Downloads](https://img.shields.io/pypi/dm/deviantart-gallery-downloader)](https://pypistats.org/packages/deviantart-gallery-downloader)
[![License](https://img.shields.io/pypi/l/deviantart-gallery-downloader)](LICENSE)

Download the full gallery of any DeviantArt profile.

Works are fetched through two routes, so the API quota is spent only on what the API alone can serve:

| Route | What it fetches | API quota |
| --- | --- | --- |
| `web/` | Every ordinary work, resolved straight from the website's public listing | none |
| `api/` | Mature content, which the website only serves blurred to logged-out visitors | only the listing pages that hold mature works + the download endpoint |

Each route saves to its own subfolder inside the gallery folder. `--force-api` restores the old behaviour of routing everything through the [official API](https://www.deviantart.com/developers/).

- Downloads the original file when the author allows it, or the highest publicly available resolution image.
- Downloads literature and journals too: text works have no media file, so their full body is saved next to the images as plain text (`.txt`) or a standalone HTML document (`.html`), your choice with `--literature-format`. The body is fetched from the website for no API quota, falling back to the listing excerpt when it is unavailable. Restrict a run with `--only`, which takes `images`, `literature`, `mature`, `ai` and `no-ai`, repeated or comma-separated.
- Downloads mature content unblurred when you log in with your account (`--login`, see below). Without login, `--unblur`/`DA_UNBLUR=true` strips the blur where possible: works uploaded since ~mid-2021 have their URL token pinned to the blurred version, so for those the blurred preview is downloaded instead.
- Parallel downloads with retries and API rate-limit handling: every worker draws from one shared budget (`DA_API_RATE`, 3 requests/second by default), and a 429 holds the whole pool back instead of each thread backing off on its own. The website route needs no OAuth call at all, so a re-sync of an all-ages gallery costs zero API requests.
- Detects duplicates across runs (even if the artwork's title has changed), so it is safe to re-run to sync new works.
- Run it with no arguments to re-sync every user already present in the output folder with their latest works, or with `--watching` to download every user your account watches.
- Re-syncs are incremental: the gallery listing stops as soon as it reaches a page of already-downloaded works (`--full` forces a complete walk).
- Files you delete manually stay deleted: the download record (`_downloaded.json`) is authoritative, so deleted works are not downloaded again unless you pass `--redownload-missing`.
- Saves the full metadata of every work to `_metadata.json`, including what the website knows about AI involvement: `is_ai_generated` (the flag behind the site's own "Suppress AI" filter, true for DreamUp works too), `is_upscaled` and `is_ai_use_disallowed`. Only the website listing carries these, so a work listed through the API records them as `null` — not known, rather than not AI.
- Ends every run with a summary broken down by route and size (items and MB downloaded via the website vs. the API), plus a per-user breakdown when syncing several profiles.

## Installation

```bash
pip install deviantart-gallery-downloader
```

## Credentials

1. Create a DeviantArt account.
2. Register an application (*confidential* type) at <https://www.deviantart.com/developers/register>.
3. Copy the `client_id` and `client_secret` from <https://www.deviantart.com/developers/apps>.

Export them as environment variables or put them in a `.env` file in the directory you run the command from:

```bash
DA_CLIENT_ID=your_client_id
DA_CLIENT_SECRET=your_client_secret
# Optional: simultaneous website downloads (default: 4, recommended not to exceed 8)
DA_WEB_WORKERS=4
# Optional: simultaneous API downloads (default: 2); kept low so parallel API
# requests don't trip the rate limit
DA_API_WORKERS=2
# Optional: API requests per second, shared by every worker (default: 3);
# 0 disables the pacing
DA_API_RATE=3
# Optional: report only results, dropping the per-work progress lines
DA_QUIET=false
# Optional: strip the blur filter the API applies to mature-content previews
# (default: false, images are kept as the API serves them)
DA_UNBLUR=false
# Optional: file format for literature and journals — "txt" (plain text) or
# "html" (a standalone document that keeps the formatting) (default: txt)
DA_LITERATURE_FORMAT=txt
# Optional: keep only the works matching all of images, literature, mature,
# ai, no-ai (default: unset, which keeps everything); comma-separate to combine
DA_ONLY=
# Optional: output folder, absolute or relative ("~" is expanded)
DA_OUTPUT=~/Pictures/deviantart
# Optional: route every work through the API instead of the website listing
DA_FORCE_API=false
```

## Usage

```bash
deviantart-downloader https://www.deviantart.com/username
deviantart-downloader username

# Passing the credentials as arguments:
deviantart-downloader username --client-id XXX --client-secret YYY

# Useful options:
deviantart-downloader username --info         # show profile info + galleries, download nothing
deviantart-downloader username -g "Sketches"  # only the named gallery folder (case-insensitive)
deviantart-downloader username -o my_folder   # output folder (default: DA_OUTPUT or downloads)
deviantart-downloader username -w 8           # simultaneous website downloads
deviantart-downloader username --api-workers 3  # simultaneous API downloads (default: 2)
deviantart-downloader username --api-rate 2   # API requests per second, all workers (default: 3)
deviantart-downloader username --redownload-missing  # restore manually deleted files
deviantart-downloader username --redownload-blurred  # replace copies saved blurred before --login
deviantart-downloader username --unblur       # strip the blur on mature-content previews
deviantart-downloader username --literature-format html  # save literature/journals as .html (default: txt)
deviantart-downloader username --only images       # download only images (skip literature/journals)
deviantart-downloader username --only mature      # download only the mature works
deviantart-downloader username --only literature mature  # ...and only the mature literature
deviantart-downloader username -q             # only results: no per-work progress lines
deviantart-downloader username --full         # walk the entire gallery listing
deviantart-downloader username --force-api    # route everything through the API
deviantart-downloader --watching              # download every user you watch (needs --login)
deviantart-downloader --watching --info       # just summarise them, download nothing
```

### Try it on a demo profile

[`test`](https://www.deviantart.com/test) is a small, long-standing public profile that makes a good first run: one gallery, 18 all-ages works (~4.5 MB), nothing mature, so no `--login` is needed.

```bash
deviantart-downloader test --info     # inspect it first: profile + gallery counts
deviantart-downloader test -o demo     # download all 18 works into ./demo/test/
```

`--only` narrows a run to the works you actually want. Its selectors sit on three axes and combine the way filters usually do — a union within an axis, an intersection across them:

| Command | Keeps |
| --- | --- |
| `--only images` | Images, no literature or journals |
| `--only mature` | Mature works of either kind |
| `--only literature mature` | The mature literature only |
| `--only=literature,mature` | The same; repeat the words or comma-separate them |
| `--only images literature` | Everything — the two kinds are one axis, so naming both restricts nothing |
| `--only no-ai` | What the site's own "Suppress AI" filter would leave |
| `--only ai images` | The AI-made images only |

`mature` reads the flag the listing carries. Note that a handful of works are served blurred without carrying it, and those are not selected by it.

`ai` / `no-ai` read the same declaration the website's "Suppress AI" setting filters on, DreamUp works included. Only the website listing carries it, so the two are not mirror images of each other: `ai` keeps the works *known* to be AI-made, while `no-ai` keeps everything not known to be, rather than dropping a work over a fact the listing never reported. When the API ends up doing the listing (`--api-only`, or the website route being unavailable) nothing at all is known, and the run says so instead of quietly selecting everything or nothing.

Pass `-q/--quiet` (or `DA_QUIET=true`) when the progress is more noise than signal — a long sync prints one line per work, and `--watching` multiplies that by every user you follow. It drops the lines for works that *succeeded*; summaries, the works that failed, warnings, errors and the `--watching` confirmation still print, so a quiet run still tells you what happened and what went wrong. On the demo profile below that is 34 lines of output against 8. It layers on anything else: `--watching -q`, `--info -q`, `--only images -q`.

Progress does not disappear, it moves: in a terminal it goes to the status line pinned at the bottom, next to the keyboard controls, so a quiet run still shows what it is working on.

```
[running]  12/900  api  Crystal ID  keys: [p] pause  [r] resume  [q] quit
```

It names the route each work took, since the two behave nothing alike: `web` costs no quota and runs at `-w` workers, `api` is metered and paced by `--api-rate`. The line is trimmed to the terminal width, and a rate-limit wait adds a second line above it that counts down:

```
[rate limit]  resuming in 28s                                    429s so far: 3
[running]  12/900  api  Crystal ID                keys: [p] pause  [r] resume  [q] quit
```

That replaces the line each 429 used to print — several workers, several attempts, per user — with one place that also shows the run is waiting rather than hung. Piped or redirected there is no status line, so a stall announces itself once instead of counting down, and `-q` simply prints less.

Every run ends with a summary broken down by route, size and (when syncing several users) per user:

```
Done. Downloaded: 18 | Skipped (already existed): 0 | No file: 0 | Failed: 0
  · via website: 18 item(s), 4.5 MB
  · via API:     0 item(s), 0 B
  · Total downloaded: 4.5 MB in 3.7s (1.2 MB/s), avg 257.5 KB/file
Files saved to: /.../demo/test
```

Pass `-i/--info` to print a profile summary — profile URL, avatar and banner URLs, bio, location, birthday, "deviant for X years", links, statistics and every gallery folder with its item count — and exit without downloading anything. Combined with `--watching` it summarises every user you watch instead of one profile. DeviantArt does not expose pronouns through its endpoints, so those are not shown.

The whole summary comes off the website, so `--info` costs **no API quota at all** — which is what makes `--watching --info` viable over a long watchlist. The website does not publish the real name or a readable artist specialty; add `--force-api` to fetch the profile through the API and get those two back, at one request per user. (The bio is not a reason to: the API's bio field comes back empty on current profiles, while the website carries the full text.)

Pass `-g/--gallery "NAME"` to download only one gallery folder instead of the whole gallery (the name is matched case-insensitively; if it doesn't exist the tool lists the folders that do). Files land in the same `<output>/<username>/` folder as a full sync, so works are never downloaded twice across runs.

Files are saved to `<output>/<username>/web/` or `<output>/<username>/api/`, depending on the route each work took. Literature and journals land in the same subfolders as a `.txt` or `.html` file (see `--literature-format`). The download record and the metadata live in `<output>/<username>/`, shared by both routes: a work is never downloaded twice, whichever route lists it.

Galleries downloaded by earlier versions keep their existing flat layout; those files are recognised and left where they are, and only new works land in the route subfolders.

While it is fetching the listing or downloading, and when run in a terminal, you can steer it from the keyboard: **`p`** pauses, **`r`** resumes, and **`q`** quits (like `Ctrl+C`: it stops and cleans up, and re-running resumes where it left off). A status line pinned to the bottom of the terminal shows the available keys and the current state, and the output scrolls above it. When the output is piped or redirected, these controls are simply inactive.

### Replacing copies you saved blurred (`--redownload-blurred`)

Mature works downloaded before you ran `--login` are the blurred placeholder the API serves an anonymous visitor. `--redownload-blurred` fetches those again now that your account can see the real image:

```bash
deviantart-downloader --login              # once, if you have not already
deviantart-downloader username --redownload-blurred
```

Nothing on disk records whether a given file is the blurred one, so the tool works it out from what is on offer now and what you already have:

| Situation | What happens |
| --- | --- |
| The API now offers the work unblurred and your copy is a different size | Downloaded again, replacing the blurred one |
| Your copy already matches the size on offer | Kept — it was already the good one |
| The work is *still* only offered blurred | Kept — refetching it would change nothing, and it is decided off the listing, so no API request is spent to find out |

The summary counts the replacements apart from ordinary downloads, so what the pass achieved is visible rather than buried among works that were simply new:

```
Done. Downloaded: 1 | Skipped (already existed): 137 | No file: 0 | Failed: 0 | Replaced (were blurred): 42
```

So it is safe to re-run: the second pass replaces nothing. It requires `--login` and says so rather than walking every listing to conclude nothing, since without a session the API only ever offers the blur.

**It is a repair pass, not a sync.** Because it can only ever replace files you already have, a user whose download record holds nothing from the API route has no blur to replace — and is skipped whole, before a single request. That is read off `_downloaded.json` locally, so it costs nothing:

```bash
deviantart-downloader --watching --redownload-blurred
```

On a 148-user watchlist where 13 had ever downloaded mature content, that walks 13 galleries instead of 148. The other 135 are dismissed in milliseconds. Galleries downloaded by versions old enough to predate the `web/` and `api/` subfolders are always walked, since their record does not say which route a work took and assuming all-ages would skip works that do need repair.

Note that being a repair pass cuts both ways: a skipped user's *new* works are not picked up either. Run it without the flag for that.

When re-syncing a user, the gallery listing (newest first) stops at the first page whose works were all downloaded before, so frequent re-runs stay cheap even on huge galleries. Pass `--full` occasionally to walk the whole listing and pick up older works that became visible later (for example mature content after `--login`); `--redownload-missing` implies it.

### Sync every downloaded user

With no profile argument, the tool scans the output folder for the users you already downloaded (their subdirectories) and fetches whatever they published since:

```bash
deviantart-downloader                # sync everyone under DA_OUTPUT (or ./downloads)
deviantart-downloader -o my_folder   # sync everyone under my_folder
```

Only subdirectories created by a previous run are considered (they are recognised by the `_downloaded.json` / `_metadata.json` files inside), so unrelated folders in the output directory are ignored. Users whose gallery comes back empty (deactivated accounts) are skipped with a notice instead of aborting the run.

### Sync everyone you watch (`--watching`)

`--watching` reads the watchlist of the account you logged in with and downloads all of those galleries into the output folder, one subfolder per user:

```bash
deviantart-downloader --login        # once, to save the session
deviantart-downloader --watching     # then, whenever you want to catch up
```

Since a watchlist can be long, it tells you how many users it found and asks before starting:

```
Fetching the users your account watches...
  Page at offset 0: 50 watched user(s) (total: 50)
  Page at offset 50: 48 watched user(s) (total: 98)

You watch 98 user(s).
Download all 98 galleries into downloads? [y/N]
```

Anything but `y`/`yes` cancels. When stdin is piped or redirected the question is skipped and the run goes ahead, so `--watching` still works unattended from a cron job or a script — which also means a redirected stdin starts the whole download with no confirmation.

It needs that saved session: without it the token belongs to the application, which watches nobody. The list is fetched fresh on each run, so people you started or stopped watching are picked up automatically.

Most options apply per user as usual, so `--watching --only images` or `--watching --full` do what you would expect:

| Option | With `--watching` |
| --- | --- |
| `--only`, `--full`, `--redownload-missing`, `--redownload-blurred`, `--unblur`, `--literature-format`, `--api-rate`, `-q`, `-w`, `--api-workers`, `-o`, `--force-api` | Applied to every watched user |
| `-i/--info` | Summarises every watched profile instead of downloading |
| `-g/--gallery` | Rejected: folder names differ from one profile to the next, so one name cannot be asked of everyone you watch |

Two of those are worth thinking about before you use them on a long watchlist. `--redownload-missing` and `--redownload-blurred` both imply `--full`, so they walk every listing of every watched user from end to end instead of stopping at the first page already downloaded — unavoidably, since the works they look for are exactly the ones an incremental sync skips. `--watching --redownload-blurred` is the likeliest way you will want the latter, though: it replaces every blurred copy across everyone you follow in one pass.

`--unblur` is close to pointless here: `--watching` already requires the logged-in session, and with it mature works arrive unblurred anyway (`--unblur` exists for runs without `--login`).

Re-runs are incremental like any other sync, and a watched user whose account has since been deactivated is skipped with a notice. This is not a setting you want in `.env`: it is an action, and every run downloads every gallery you watch.

## Staying under the API rate limit

DeviantArt answers an overrun with `user_api_threshold`. That limit is **per account, not per endpoint**, and it reacts to short bursts rather than to a running total — so spreading the work over different endpoints buys nothing, and the only thing that helps is not going too fast.

Two mechanisms keep a run under it:

- **One shared budget.** Every API request, whichever endpoint it is for and whichever worker makes it, is paced through a single limiter at `DA_API_RATE` requests per second (3 by default, `--api-rate` to override, `0` to disable). Workers queue for the next slot instead of racing each other, so the request rate stays flat no matter how you set `--api-workers`.
- **One shared cool-down.** When a 429 does arrive, the first worker to see it backs off *the whole pool*, and the others wait that out rather than each starting their own ladder. Since a 429 means the account is going too fast, that is true of every worker at once; letting the rest keep firing only earns more 429s. The wait doubles from 4 s up to a 5-minute ceiling and resets after any request gets through.

Note that DeviantArt sends no `Retry-After` header and no rate-limit headers of any kind, so the wait is a blind ladder. The header is honoured if it ever appears.

The default rate was picked by measuring it. Thirty back-to-back API calls, run twice against the same account:

| | Calls completed | HTTP requests | 429s | Wall time |
| --- | --- | --- | --- | --- |
| Unpaced (`--api-rate 0`) | 30/30 | 45 | 15 | 222 s |
| Paced (`--api-rate 3`) | 30/30 | 30 | 0 | 13 s |

Unpaced, a third of the requests were rejected and had to be retried, so 30 calls cost 45 requests and most of the time went into backoff waits. Paced, the same work cost exactly 30 requests and never tripped the limit. Going slower on purpose finished **17× faster**.

Your mileage will differ: the threshold appears to run over a long window, so a run that starts with quota already spent — by an earlier run, or by the same account downloading somewhere else — will see 429s at any rate. If that happens, lower `DA_API_RATE`; if you never see one, raise it.

The website route is not affected by any of this: it needs no OAuth and costs no quota, which is why a re-sync of an all-ages gallery makes zero API requests.

## Unblurred mature content (`--login`)

Mature works are the reason the API route exists: the website lists them to logged-out visitors as a blurred placeholder whose URL token is pinned to that blurred transformation, so scraping cannot recover them. Without a logged-in user, the API serves mature works as an anonymous visitor would see them: blurred, and with the image URL cryptographically pinned to the blurred version for works uploaded since ~mid-2021 (`--unblur` cannot help there). To get the real images, log in with your DeviantArt account:

1. In your account settings, enable **mature content**.
2. In <https://www.deviantart.com/developers/apps>, edit your application and add `http://127.0.0.1:8721/callback` to the **OAuth2 Redirect URI Whitelist**.
3. Run:

```bash
deviantart-downloader --login            # one-time browser authorization
deviantart-downloader username           # subsequent runs use the saved session
```

The browser opens once to authorize the app; the session is stored in `~/.config/deviantart-downloader/token.json` and renewed automatically. If it ever expires (about 3 months without use), run `--login` again. While a session is saved, mature works are downloaded unblurred and `--unblur` is unnecessary.

## Development

```bash
git clone https://github.com/Nibblex/DeviantartDownloader
cd DeviantartDownloader
python -m venv .venv && source .venv/bin/activate
pip install -e .[dev]
pytest   # runs the test suite with a coverage report
```

The package is layered bottom-up, each module depending only on the ones above it:

| Module | Responsibility |
| --- | --- |
| `constants.py` | Endpoints, limits and the shared cancellation flag |
| `config.py` | `.env` files and `DA_*` environment variables |
| `naming.py` | Usernames, file names, and the deviation key shared by both routes |
| `manifest.py` | `_downloaded.json`: what has been fetched, and where it landed |
| `literature.py` | Rendering a text work's body (tiptap/HTML) to plain text |
| `api.py` | The OAuth2 client: tokens, retries, rate limits |
| `web.py` | The website's JSON endpoints and their media URLs |
| `auth.py` | The interactive `--login` flow |
| `controls.py` | Live keyboard controls (pause / resume / quit) during a run |
| `listing.py` | Walking a gallery over either route, and pairing the two up |
| `downloads.py` | Resolving a work to a file (or text) and writing it to disk |
| `sync.py` | Orchestration: list, route, download |
| `cli.py` | Argument parsing and the entry point |

The test suite mirrors that layout (`tests/test_<module>.py`), with the fakes and factories in `tests/conftest.py`.

## License

GNU General Public License v3.0 or later — see [LICENSE](LICENSE).

Releases up to and including 1.6.1 were published under the MIT license and stay
available under those terms; 1.7.0 onwards are GPLv3+.
