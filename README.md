# DeviantArt Downloader

Download the full gallery of any DeviantArt profile using the [official public API](https://www.deviantart.com/developers/).

- Downloads the original file when the author allows it, or the highest publicly available resolution image.
- Parallel downloads with retries and API rate-limit handling.
- Detects duplicates across runs (even if the artwork's title has changed), so it is safe to re-run to sync new works.
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
```

Files are saved to `<output>/<username>/`.

## License

MIT
