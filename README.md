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
| `api/` | Mature content, which the website only serves blurred to logged-out visitors | 1 listing walk + the download endpoint |

Each route saves to its own subfolder inside the gallery folder. `--api-only` restores the old behaviour of routing everything through the [official API](https://www.deviantart.com/developers/).

- Downloads the original file when the author allows it, or the highest publicly available resolution image.
- Downloads mature content unblurred when you log in with your account (`--login`, see below). Without login, `--unblur`/`DA_UNBLUR=true` strips the blur where possible: works uploaded since ~mid-2021 have their URL token pinned to the blurred version, so for those the blurred preview is downloaded instead.
- Parallel downloads with retries and API rate-limit handling. The website route needs no OAuth call at all, so a re-sync of an all-ages gallery costs zero API requests.
- Detects duplicates across runs (even if the artwork's title has changed), so it is safe to re-run to sync new works.
- Run it with no arguments to re-sync every user already present in the output folder with their latest works.
- Re-syncs are incremental: the gallery listing stops as soon as it reaches a page of already-downloaded works (`--full` forces a complete walk).
- Files you delete manually stay deleted: the download record (`_downloaded.json`) is authoritative, so deleted works are not downloaded again unless you pass `--redownload-missing`.
- Saves the full metadata of every work to `_metadata.json`.

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
# Optional: pause in seconds after each API download, per thread (default: 0.5);
# the website route costs no quota and is never delayed
DA_DELAY=0.5
# Optional: strip the blur filter the API applies to mature-content previews
# (default: false, images are kept as the API serves them)
DA_UNBLUR=false
# Optional: output folder, absolute or relative ("~" is expanded)
DA_OUTPUT=~/Pictures/deviantart
# Optional: route every work through the API instead of the website listing
DA_API_ONLY=false
```

## Usage

```bash
deviantart-downloader https://www.deviantart.com/username
deviantart-downloader username

# Passing the credentials as arguments:
deviantart-downloader username --client-id XXX --client-secret YYY

# Useful options:
deviantart-downloader username -o my_folder   # output folder (default: DA_OUTPUT or downloads)
deviantart-downloader username -w 8           # simultaneous website downloads
deviantart-downloader username --api-workers 3  # simultaneous API downloads (default: 2)
deviantart-downloader username --delay 1.0    # pause after each API download, per thread
deviantart-downloader username --redownload-missing  # restore manually deleted files
deviantart-downloader username --unblur       # strip the blur on mature-content previews
deviantart-downloader username --full         # walk the entire gallery listing
deviantart-downloader username --api-only     # route everything through the API
```

Files are saved to `<output>/<username>/web/` or `<output>/<username>/api/`, depending on the route each work took. The download record and the metadata live in `<output>/<username>/`, shared by both routes: a work is never downloaded twice, whichever route lists it.

Galleries downloaded by earlier versions keep their existing flat layout; those files are recognised and left where they are, and only new works land in the route subfolders.

When re-syncing a user, the gallery listing (newest first) stops at the first page whose works were all downloaded before, so frequent re-runs stay cheap even on huge galleries. Pass `--full` occasionally to walk the whole listing and pick up older works that became visible later (for example mature content after `--login`); `--redownload-missing` implies it.

### Sync every downloaded user

With no profile argument, the tool scans the output folder for the users you already downloaded (their subdirectories) and fetches whatever they published since:

```bash
deviantart-downloader                # sync everyone under DA_OUTPUT (or ./downloads)
deviantart-downloader -o my_folder   # sync everyone under my_folder
```

Only subdirectories created by a previous run are considered (they are recognised by the `_downloaded.json` / `_metadata.json` files inside), so unrelated folders in the output directory are ignored. Users whose gallery comes back empty (deactivated accounts) are skipped with a notice instead of aborting the run.

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
| `api.py` | The OAuth2 client: tokens, retries, rate limits |
| `web.py` | The website's JSON endpoints and their media URLs |
| `auth.py` | The interactive `--login` flow |
| `listing.py` | Walking a gallery over either route, and pairing the two up |
| `downloads.py` | Resolving a work to a file URL and writing it to disk |
| `sync.py` | Orchestration: list, route, download |
| `cli.py` | Argument parsing and the entry point |

The test suite mirrors that layout (`tests/test_<module>.py`), with the fakes and factories in `tests/conftest.py`.

## License

GNU General Public License v3.0 or later — see [LICENSE](LICENSE).

Releases up to and including 1.6.1 were published under the MIT license and stay
available under those terms; 1.7.0 onwards are GPLv3+.
