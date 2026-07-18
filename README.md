# DeviantArt Downloader

[![CI](https://github.com/Nibblex/DeviantartDownloader/actions/workflows/ci.yml/badge.svg)](https://github.com/Nibblex/DeviantartDownloader/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/Nibblex/DeviantartDownloader/branch/main/graph/badge.svg)](https://codecov.io/gh/Nibblex/DeviantartDownloader)
[![PyPI](https://img.shields.io/pypi/v/deviantart-gallery-downloader)](https://pypi.org/project/deviantart-gallery-downloader/)
[![Python versions](https://img.shields.io/pypi/pyversions/deviantart-gallery-downloader)](https://pypi.org/project/deviantart-gallery-downloader/)
[![Downloads](https://img.shields.io/pypi/dm/deviantart-gallery-downloader)](https://pypistats.org/packages/deviantart-gallery-downloader)
[![License: MIT](https://img.shields.io/pypi/l/deviantart-gallery-downloader)](LICENSE)

Download the full gallery of any DeviantArt profile using the [official public API](https://www.deviantart.com/developers/).

- Downloads the original file when the author allows it, or the highest publicly available resolution image.
- Downloads mature content unblurred when you log in with your account (`--login`, see below). Without login, `--unblur`/`DA_UNBLUR=true` strips the blur where possible: works uploaded since ~mid-2021 have their URL token pinned to the blurred version, so for those the blurred preview is downloaded instead.
- Parallel downloads with retries and API rate-limit handling.
- Detects duplicates across runs (even if the artwork's title has changed), so it is safe to re-run to sync new works.
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
# Optional: simultaneous downloads (default: 4, recommended not to exceed 8)
DA_WORKERS=4
# Optional: strip the blur filter the API applies to mature-content previews
# (default: false, images are kept as the API serves them)
DA_UNBLUR=false
# Optional: output folder, absolute or relative ("~" is expanded)
DA_OUTPUT=~/Pictures/deviantart
```

## Usage

```bash
deviantart-downloader https://www.deviantart.com/username
deviantart-downloader username

# Passing the credentials as arguments:
deviantart-downloader username --client-id XXX --client-secret YYY

# Useful options:
deviantart-downloader username -o my_folder   # output folder (default: DA_OUTPUT or downloads)
deviantart-downloader username -w 8           # simultaneous downloads
deviantart-downloader username --delay 1.0    # pause after each download, per thread
deviantart-downloader username --redownload-missing  # restore manually deleted files
deviantart-downloader username --unblur       # strip the blur on mature-content previews
```

Files are saved to `<output>/<username>/`.

## Unblurred mature content (`--login`)

Without a logged-in user, the API serves mature works as an anonymous visitor would see them: blurred, and with the image URL cryptographically pinned to the blurred version for works uploaded since ~mid-2021 (`--unblur` cannot help there). To get the real images, log in with your DeviantArt account:

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

## License

MIT
