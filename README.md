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
- Downloads literature and journals too: text works have no media file, so their full body is saved next to the images as plain text (`.txt`) or a standalone HTML document (`.html`), your choice with `--literature-format`. The body is fetched from the website for no API quota, falling back to the listing excerpt when it is unavailable. Restrict a run with `--only`, which takes `images`, `literature`, `mature`, `ai`, `no-ai`, `upscaled` and `no-upscaled`, repeated or comma-separated, and set either flag per user in a `_users.json` that survives a rename (see below).
- Downloads mature content unblurred when you log in with your account (`--login`, see below). Without login, `--unblur`/`DA_UNBLUR=true` strips the blur where possible: works uploaded since ~mid-2021 have their URL token pinned to the blurred version, so for those the blurred preview is downloaded instead.
- Parallel downloads with retries and API rate-limit handling: every worker draws from one shared budget (`DA_API_RATE`, 3 requests/second by default), and a 429 holds the whole pool back instead of each thread backing off on its own. The website route needs no OAuth call at all, so a re-sync of an all-ages gallery costs zero API requests.
- Detects duplicates across runs (even if the artwork's title has changed), so it is safe to re-run to sync new works.
- Run it with no arguments to re-sync every user already present in the output folder with their latest works, or with `--watching` to download every user your account watches.
- Re-syncs are incremental: the gallery listing stops as soon as it reaches a page of already-downloaded works (`--full` forces a complete walk).
- Restrict a run to a date range with `--since` / `--until`. Because both routes list newest-first, `--since` also stops the listing walk once a page is entirely older than the bound, so on the API route it is a saving in requests and not only a filter (see below).
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
# ai, no-ai, upscaled, no-upscaled (default: unset, keeps everything)
DA_ONLY=
# Optional: per-user --only / --literature-format file (default: _users.json in
# the output folder, if it exists)
DA_USER_CONFIG=
# Optional: output folder, absolute or relative ("~" is expanded)
DA_OUTPUT=~/Pictures/deviantart
# Optional: route every work through the API instead of the website listing
DA_FORCE_API=false
```

`DA_WORKERS` is what `DA_WEB_WORKERS` was called before the API route got a cap of its own, and is still read when `DA_WEB_WORKERS` is unset, so an `.env` written for an older version keeps working.

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
deviantart-downloader username --since 2024-01-01  # only works published since that date
deviantart-downloader username --until 2023-12-31  # only works published up to that date (inclusive)
deviantart-downloader username --dry-run      # say what would be fetched, fetch nothing
deviantart-downloader username --verify       # check what is on disk against its own record
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

`--only` narrows a run to the works you actually want. Its selectors sit on four axes and combine the way filters usually do — a union within an axis, an intersection across them:

| Axis | Selectors |
| --- | --- |
| What kind of work it is | `images`, `literature` |
| Maturity | `mature` |
| The author's AI declaration | `ai`, `no-ai` |
| Upscaled with AI | `upscaled`, `no-upscaled` |

| Command | Keeps |
| --- | --- |
| `--only images` | Images, no literature or journals |
| `--only mature` | Mature works of either kind |
| `--only literature mature` | The mature literature only |
| `--only=literature,mature` | The same; repeat the words or comma-separate them |
| `--only images literature` | Everything — the two kinds are one axis, so naming both restricts nothing |
| `--only no-ai` | What the site's own "Suppress AI" filter would leave |
| `--only ai images` | The AI-made images only |
| `--only no-ai no-upscaled` | Neither AI-made nor AI-upscaled |

`mature` reads the flag the listing carries. Note that a handful of works are served blurred without carrying it, and those are not selected by it.

The last two axes are separate declarations, not degrees of one: `ai` / `no-ai` read what the website's "Suppress AI" setting filters on (DreamUp works included), while `upscaled` / `no-upscaled` read whether the author ran the work through an AI upscaler. A hand-drawn piece upscaled that way is *not* AI-made, so `--only no-ai` keeps it and only `--only no-upscaled` turns it away.

Only the website listing carries either one, which is what makes each pair lopsided rather than mirrored: the plain value keeps the works *known* to be that way, while the `no-` one keeps everything not known to be, rather than dropping a work over a fact the listing never reported. When the API ends up doing the listing (`--force-api`, or the website route being unavailable) nothing at all is known, and the run says so, one line per axis, instead of quietly selecting everything or nothing:

```
WARNING: the API listing does not report whether a work was upscaled with AI, so --only upscaled has nothing to select on and matches no work here.
```

### Different settings per user

`--only` and `--literature-format` apply to the whole run, which is the wrong shape for a folder full of galleries that want different things: one artist worth only their images, another followed for the literature. A `_users.json` in the output folder gives a user their own answer:

```json
{
  "someartist": { "only": "images, no-ai" },
  "aWriter":    { "only": "literature", "literature-format": "html" },
  "everything": { "only": "" },
  "onHold":     { "skip": true }
}
```

Selectors read exactly as they do on the command line — one string or a list, spaces or commas — and an empty `only` means everything for that user. Usernames are matched whatever their case, and `literature_format` is accepted alongside `literature-format`.

**`"skip": true` leaves a user out of a batch** — a re-sync of the output folder, or `--watching` — without deleting anything you already have from them. There is no `--skip` flag to go with it, because leaving one user out is a standing decision about that user and never about the run as a whole. Naming them on the command line still works: typing `deviantart-downloader onHold` is as explicit as a request gets, and the file does not overrule it.

The decision is made from the name alone, before anything is fetched, which is the point — a user dropped after the listing would already have cost the listing. That is also why a rename slips past it: the id that recognises one only arrives with the listing this is refusing to spend, so a renamed user goes on being synced until some run meets them under the new name and re-keys the entry.

**A flag you type outranks the file**, which was written before this run. Highest wins:

| | Decides | Scope |
| --- | --- | --- |
| 1 | `--only` / `--literature-format` on the command line | every user in the run |
| 2 | that user's entry in `_users.json` | one user |
| 3 | `DA_ONLY` / `DA_LITERATURE_FORMAT` in `.env` | every user, as a standing default |
| 4 | the built-in default (everything, `txt`) | every user |

So `--only images` with the file above downloads only images from `aWriter` too, and says which setting the file lost:

```
--only given on the command line, which settles it for every user: _users.json does not get a say in that.
```

One flag settles one setting: pass `--only images` and the file still picks each user's `literature-format`. The `.env` variables sit *below* the file rather than above it, because they are a standing default rather than a decision about this run — `DA_ONLY=images` with the file above still gets `aWriter`'s literature.

**A run says, in green, that it found the file and understood it**, before it fetches anything:

```
_users.json: read, settings for 4 user(s).
```

Worth a line of its own because silence is ambiguous: a file saved in the wrong folder, or under a name with a typo in it, is indistinguishable from one that simply has nothing to say about the users being synced, and without that line neither would print anything at all. It is progress rather than a result, so `-q` drops it.

The file is read before the first user is synced, so a typo in it is heard about there instead of halfway through a batch — including in a setting this run overrides, which is wrong today and would bite the first run that leaves the flag off. **Anything wrong with it is an orange warning**, and because the file is written by hand and cannot be regenerated, the run does not decide on its own to go on without it:

```
WARNING: _users.json: "someartist" asks --only for sfw, which is not among images, literature, mature, ai, no-ai, upscaled, no-upscaled.
_users.json cannot be applied as written, so no user would get their own settings: every gallery would be synced with the flags this run was given.
Carry on without it? [y/N]
```

Answering yes sets the file aside whole — nobody gets their own settings for that run, and the file itself is left exactly as it is for you to go and fix, since a run rescued this way must not write over it. Anything else stops before a single work is fetched.

**With nobody there to answer** — a pipe, a cron job, CI — the question is not asked and the run stops with the message above. That is the only safe default: assuming yes would hand the decision to whoever reads the log afterwards, by which time the wrong files are already on disk, under the right names, with nothing saying so.

Colour is dropped whenever the output is not going to a terminal, where the escapes would be noise sitting in a log file rather than colour, and `NO_COLOR=1` turns it off everywhere ([no-color.org](https://no-color.org)). Nothing is lost either way: every coloured line says in words what it means.

Point somewhere else with `--user-config PATH` (or `DA_USER_CONFIG`); a file named that way and not found is an error — a typo on the command line, not a damaged file, so there is nothing to ask about — while the default location simply has nothing to say when there is no file in it.

**A rename does not invalidate it.** DeviantArt lets people change their username, and an entry filed under a name nobody answers to any more would quietly stop applying. So each entry records the id the route reports for that user — the one thing a rename does not change — and a run that meets that id under a new name moves the entry across:

```
_users.json: "oldname" is now "newname"; moving those settings over.
```

The ids are written by the tool, not by hand, and appear the first time a user is synced. Note the two routes disagree about user ids (the website reports a numeric one, the API a UUID, and for the same user they differ), so each is kept under the name of the route that reported it and neither is ever read as the other:

```json
{ "newname": { "only": "literature", "ids": { "web": "233267" } } }
```

The gallery folder is still named after the username, so a renamed user's older files stay in the folder under the old name; only the settings follow the rename.

Pass `-q/--quiet` (or `DA_QUIET=true`) when the progress is more noise than signal — a long sync prints one line per work, and `--watching` multiplies that by every user you follow. It drops the lines for works that *succeeded*, and for the ones that had nothing to save (nothing went wrong there either — the summary still counts them); summaries, the works that failed, warnings, errors and the `--watching` confirmation still print, so a quiet run still tells you what happened and what went wrong. On the demo profile below that is 34 lines of output against 8. It layers on anything else: `--watching -q`, `--info -q`, `--only images -q`.

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

**A pause holds no connection open.** The files being transferred when you press `p` are let go of rather than left half-read with the socket waiting, and what is already on disk is continued with a range request when you resume — so pausing for an hour costs the same as pausing for a second, and the bytes stop arriving straight away. A connection lost mid-transfer is picked up the same way, twice, before the work counts as a failure. (Before this, a long pause ended those transfers as failures: whatever closed the idle connection first — the CDN, a laptop going to sleep — took every byte down with it.)

### Narrowing a run to a date range (`--since` / `--until`)

Both flags take a plain `YYYY-MM-DD`, or a full ISO 8601 timestamp when you need the precision. A value with no offset is read as UTC.

```bash
deviantart-downloader username --since 2024-01-01
deviantart-downloader username --since 2024-01-01 --until 2024-06-30
deviantart-downloader username --until 2019-12-31        # only the early work
```

A bare date given to `--until` means the **whole** day, so `--until 2024-06-30` includes everything published on the 30th. Read the other way the flag would silently drop almost all of the day it names.

`--since` is more than a filter. Both routes list a gallery newest-first, so once a listing page is entirely older than the bound there is nothing below it worth asking for, and the walk stops there — on the API route, every page not fetched is a request not spent. `--until` cannot do the same: the works above the bound have to be walked past to reach the ones under it, so it narrows what is downloaded rather than what is listed.

Two details worth knowing:

- **`--full` does not defeat `--since`.** `--full` exists to override the *incremental* stop (the one that halts at the first page of already-downloaded works). A date bound is something you asked for in as many words, so it still applies; `--full --since 2024-01-01` means "walk everything back to 2024, ignoring what is already downloaded".
- **A work whose publication date the listing does not carry is kept, not dropped.** This is the same lopsidedness `--only no-ai` has: a bound narrows the run by what the listing actually said, rather than discarding a work over a fact that was never reported. The same page also keeps the walk going, since an unknown date could be on either side of the bound.

Neither flag has a `DA_*` variable on purpose. A date range is something you ask of one run, not a standing preference — left in `.env` it would quietly truncate every later sync, and the works it skipped would look like works that do not exist.

#### With `--info`: who has been posting?

Both flags combine with `--info`, which then adds one line to each summary:

```bash
deviantart-downloader username --info --since 2024-01-01
deviantart-downloader --watching --info --since 2024-01-01   # ...for everyone you watch
```

```
Galleries: 3 folder(s), 1,413 items
  - Featured — 1,373 items
  - Sketches — 40 items
Published within --since 2024-01-01: 42 work(s)
```

That last line is the one figure a summary cannot read off the profile: the folder counts both routes publish are totals, with no breakdown by date, so answering it means walking the gallery listing. `--since` bounds that walk, which is what makes it affordable — a recent window costs a page or two, while `--until` on its own has to walk back to the bound.

So a plain `--info` still costs the two website round trips per user it always has, and only asking for a range makes it read the listing. On the website route that is still free; under `--force-api` it is quota, and `--watching --info --since` multiplies it by the size of your watchlist.

### Seeing what a run would do (`--dry-run`)

Lists the gallery, applies every filter, works out which route each work would take — and stops there.

```
Total works found: 160

Dry run: nothing is downloaded, and nothing is written.
  160 work(s) selected: 148 via the website (web/), 12 via the API (api/).
  131 would be skipped, so 29 would be fetched.
  Reaching those 12 would need a listing lookup on the API first, which this did not spend.
```

Nothing is written: not the gallery folder, not `_metadata.json`, and not `_users.json` either — an ordinary run records the id that keeps a rename followable, and a dry run gives that up rather than write anything at all. The last line is the point of the exercise: turning a mature work into something downloadable costs a page of the API's own listing, so a dry run counts those works instead of resolving them. Spending quota to answer a question about spending quota would defeat it.

The count reads the download record *and* the disk, because that is what a real run consults — being on record is not enough on its own, since `--redownload-missing` fetches back exactly the works whose file has gone, and `--redownload-blurred` revisits ones that are still there. Pass those flags to the dry run and the numbers move with them. It is still what would be *attempted* rather than what would land: a work can turn out to have no file to offer, or fail once asked for.

Worth reaching for before `--watching`, where the alternative is starting the thing and watching what happens.

### Checking what you already have (`--verify`)

Reads the download record and compares it with the disk. It repairs nothing, downloads nothing, and needs no credentials — a copy can be checked by someone who never registered an application.

```
User: artist — https://www.deviantart.com/artist
  1373 recorded work(s).
  2 on record but not on disk:
    - web/Some Art_1004952679.jpg
    - api/Mature Art_222222222.jpg
    Pass --redownload-missing to fetch these again; left alone, the record keeps them deleted.
  1 empty file(s):
    - web/Half Written_333.txt
    Delete them and pass --redownload-missing; while they exist the run counts them as already downloaded.
```

With a profile it checks that user; without one, every user in the output folder. It **exits non-zero when it finds something**, so a script can ask "is my copy intact?" without reading the output back.

Three things it looks for: works the record claims that are not on disk, files of zero bytes (what an interrupted write leaves, and what every other check would call present), and `.part` files an interrupted run left behind. That last kind is harmless — a transfer only ever continues one within the call that made it — but knowing they are there is the difference between tidying up and wondering.

Nothing is repaired, deliberately: the two answers a repair could give — fetch it again, or accept that it is gone — are yours to choose between, and both already have flags.

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

The cheapest request is the one never made, so two of them are avoided outright:

- **The original file is often what you already have.** The API charges a request per work to hand out the original, and for most works it hands back exactly what `content.src` was already serving — the fullview is only re-encoded when the original is too large for it. The listing already says how many bytes the original has (`download_filesize`) and the CDN says how many the fullview has, so the two are compared first, over a head request that costs no quota. Measured over 23 mature downloadable works: **17 needed no request at all, 73%**. Every uncertainty — an unknown size, a CDN that will not say, a blur — spends the request instead, because being wrong the other way would save the fullview as if it were the original.
- **An answer already paid for is not bought twice.** Reaching a mature work means finding the UUID the API is keyed by, which costs a page of the API's own listing. Those answers are kept in `_resolved.json` beside the download record, and the CDN URLs they hold carry no expiry, so a repair pass or a retried failure spends nothing on works it has already resolved. A cached answer that fails to download is dropped rather than retried forever, so the next run buys a fresh one. One exception keeps `--redownload-blurred` honest: an answer cached by a logged-out run holds the blurred placeholder, and once `--login` could do better it is looked up again instead of reused.

Then two mechanisms keep what is left under the limit:

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
| `__version__.py` | The version, written once; `pyproject.toml` reads it from here |
| `constants.py` | Endpoints, limits and the shared cancellation flag |
| `config.py` | `.env` files and `DA_*` environment variables |
| `naming.py` | Usernames, file names, and the work and user ids each route reports |
| `manifest.py` | `_downloaded.json`: what has been fetched, and where it landed |
| `resolved.py` | `_resolved.json`: the API answers already paid for, so runs stop re-buying them |
| `literature.py` | Rendering a text work's body (tiptap/HTML) to plain text |
| `api.py` | The OAuth2 client: tokens, retries, rate limits |
| `web.py` | The website's JSON endpoints and their media URLs |
| `auth.py` | The interactive `--login` flow |
| `controls.py` | Live keyboard controls (pause / resume / quit) during a run |
| `listing.py` | Walking a gallery over either route, and pairing the two up |
| `downloads.py` | Resolving a work to a file (or text) and writing it to disk |
| `sync.py` | Orchestration: list, route, download; the `--only` and date filters |
| `profile.py` | `--info`: a profile's facts, stats and galleries, over either route |
| `overrides.py` | `_users.json`: per-user `--only` / `--literature-format`, rename-proof |
| `cli.py` | Argument parsing and the entry point |

The test suite mirrors that layout (`tests/test_<module>.py`), with the fakes and factories in `tests/conftest.py`.

## License

GNU General Public License v3.0 or later — see [LICENSE](LICENSE).

Releases up to and including 1.6.1 were published under the MIT license and stay
available under those terms; 1.7.0 onwards are GPLv3+.
