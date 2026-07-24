"""
DeviantArt gallery downloader.

Works are fetched through two routes to keep the API quota for what only the
API can serve:

  * the website's own JSON listing (no OAuth, no rate limit to speak of)
    resolves the file URL of every ordinary work straight from the listing,
    with no extra request per work;
  * the official API handles the works the website hides from logged-out
    visitors (mature content), which the listing only returns blurred.

Each route saves to its own subfolder, `web/` and `api/`.

Requirements:
  1. Have a DeviantArt account.
  2. Register an application at https://www.deviantart.com/developers/register
     ("confidential" type) to obtain a client_id and client_secret.
  3. pip install requests

Usage:
  cp .env.example .env   # and fill in DA_CLIENT_ID / DA_CLIENT_SECRET
  deviantart-downloader https://www.deviantart.com/username
  deviantart-downloader username

  # with no profile, re-sync every user already present in the output folder:
  deviantart-downloader

  # or passing the credentials as arguments:
  deviantart-downloader <profile_url> --client-id XXX --client-secret YYY

  # log in with your account so mature works are served unblurred
  # (requires whitelisting http://127.0.0.1:8721/callback in your app):
  deviantart-downloader --login

  # route everything through the API, as older versions did:
  deviantart-downloader username --api-only

The modules are layered bottom-up: constants and config depend on nothing,
naming, manifest, literature and controls on those, api and web wrap one route
each, listing and downloads use both, and sync and cli sit on top.
"""

import sys
from importlib.util import find_spec

if find_spec("requests") is None:
    sys.exit("Missing the 'requests' library. Install it with: pip install requests")

from .cli import main, run

__all__ = ["main", "run"]
