"""Argument parsing and the entry point."""

import argparse
import os
import sys
from pathlib import Path

from .api import ApiError, DeviantArtClient
from .auth import login
from .config import env_bool, env_float, env_int, load_dotenv
from .listing import GalleryNotFoundError
from .naming import extract_username
from .profile import print_profile
from .sync import (add_stats, discover_users, human_size, new_stats,
                   summary_lines, sync_gallery)
from .web import WebClient


def run():
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Download the full gallery of a DeviantArt profile using the official API."
    )
    parser.add_argument(
        "profile_url",
        metavar="profile",
        nargs="?",
        help="Profile URL (https://www.deviantart.com/username) or just the "
             "username. If omitted, every user already downloaded to the "
             "output folder is synced with their latest works",
    )
    parser.add_argument("-i", "--info", action="store_true",
                        help="Show the profile's info (bio, location, birthday, "
                             "links, statistics, galleries and their item counts) "
                             "and exit without downloading anything. Requires a profile")
    parser.add_argument("-g", "--gallery", metavar="NAME",
                        help="Download only the gallery folder with this name "
                             "(case-insensitive) instead of the whole gallery. "
                             "Requires a profile")
    parser.add_argument("--login", action="store_true",
                        help="Log in with your DeviantArt account (OAuth) and save the "
                             "session. Mature works are then downloaded unblurred if "
                             "your account has mature content enabled")
    parser.add_argument("-o", "--output",
                        default=os.environ.get("DA_OUTPUT", "").strip() or "downloads",
                        help="Output folder, absolute or relative (default: DA_OUTPUT "
                             "from .env or 'downloads')")
    parser.add_argument("--client-id", default=os.environ.get("DA_CLIENT_ID"))
    parser.add_argument("--client-secret", default=os.environ.get("DA_CLIENT_SECRET"))
    parser.add_argument("--delay", type=float, default=env_float("DA_DELAY", 0.5),
                        help="Pause in seconds after each API download, per thread "
                             "(default: DA_DELAY from .env or 0.5). Website downloads "
                             "cost no API quota and are never delayed")
    parser.add_argument("-w", "--web-workers", type=int,
                        default=env_int("DA_WEB_WORKERS", env_int("DA_WORKERS", 4)),
                        help="Simultaneous website downloads (default: DA_WEB_WORKERS "
                             "from .env or 4). The website route costs no API quota, so "
                             "this can be high (recommended not to exceed 8)")
    parser.add_argument("--api-workers", type=int, default=env_int("DA_API_WORKERS", 2),
                        help="Simultaneous API downloads (default: DA_API_WORKERS from "
                             ".env or 2). Kept low on purpose: the API is rate-limited, "
                             "so fewer parallel requests avoid 429s")
    parser.add_argument("--api-only", action="store_true",
                        default=env_bool("DA_API_ONLY", False),
                        help="Route every work through the API instead of reading "
                             "the public listing off the website (slower on the "
                             "API quota; use it if the website route breaks)")
    parser.add_argument("--unblur", action="store_true",
                        default=env_bool("DA_UNBLUR", False),
                        help="Strip the blur filter the API applies to mature-content "
                             "previews (default: keep the blur, or DA_UNBLUR from .env)")
    parser.add_argument("--redownload-missing", action="store_true",
                        help="Download again works recorded in the manifest whose local "
                             "file is missing (by default, manually deleted files are "
                             "not downloaded again)")
    parser.add_argument("--full", action="store_true",
                        help="Walk the entire gallery listing. By default it stops at "
                             "the first page whose works were all downloaded in "
                             "previous runs; use --full occasionally to pick up older "
                             "works that became visible later (e.g. mature content "
                             "after --login)")
    args = parser.parse_args()

    if args.web_workers < 1:
        sys.exit(f"The number of web workers must be at least 1 (got: {args.web_workers}).")
    if args.api_workers < 1:
        sys.exit(f"The number of API workers must be at least 1 (got: {args.api_workers}).")

    if args.gallery and not args.profile_url:
        sys.exit("--gallery needs a profile: pass the username or URL of the "
                 "gallery's owner.")
    if args.info and not args.profile_url:
        sys.exit("--info needs a profile: pass the username or URL to inspect.")

    if not args.client_id or not args.client_secret:
        sys.exit(
            "Missing API credentials.\n"
            "Register at https://www.deviantart.com/developers/register and then:\n"
            "  export DA_CLIENT_ID='...'\n"
            "  export DA_CLIENT_SECRET='...'"
        )

    client = DeviantArtClient(args.client_id, args.client_secret)

    if args.login:
        login(client)
        if not args.profile_url:
            return  # login-only invocation

    output_root = Path(args.output).expanduser()
    if args.profile_url:
        usernames = [extract_username(args.profile_url)]
    else:
        # No profile: sync every user already downloaded to the output folder
        usernames = discover_users(output_root)
        print(
            f"No profile given: syncing {len(usernames)} previously "
            f"downloaded user(s) in {output_root}: {', '.join(usernames)}\n"
        )

    if client.user_mode:
        print("Using the saved user session (mature works come unblurred if "
              "your account allows them).")

    web = None if args.api_only else WebClient()
    if web is None:
        print("API-only mode: every work goes through the API.")

    if args.info:
        print_profile(client, web, usernames[0])
        return

    totals = new_stats()
    per_user = []
    for username in usernames:
        counts = sync_gallery(
            client, username, output_root,
            delay=args.delay, web_workers=args.web_workers, api_workers=args.api_workers,
            redownload_missing=args.redownload_missing, unblur=args.unblur,
            full=args.full, web=web, gallery=args.gallery,
        )
        if counts is None:
            if args.profile_url:
                empty = f'The gallery "{args.gallery}"' if args.gallery else "The gallery"
                sys.exit(f"{empty} is empty or the user does not exist.")
            print(f"Skipping {username}: the gallery is empty or the user no longer exists.\n")
            continue
        add_stats(totals, counts)
        per_user.append((username, counts))
        print()

    if len(usernames) > 1:
        lines = summary_lines(totals, users=len(per_user))
        print(f"All users synced. {lines[0]}")
        for line in lines[1:]:
            print(line)
        if per_user:
            width = max(len(name) for name, _ in per_user)
            print("Per user:")
            for name, counts in sorted(per_user, key=lambda uc: uc[1]["bytes"],
                                       reverse=True):
                print(f"  {name:<{width}}  {counts['downloaded']} item(s) "
                      f"downloaded, {human_size(counts['bytes'])}")


def main():
    try:
        run()
    except (ApiError, GalleryNotFoundError) as e:
        sys.exit(f"\n{e}")
    except KeyboardInterrupt:
        # Ctrl+C outside the download loop (login, gallery listing, ...)
        print("\nInterrupted by the user.")
        sys.exit(130)
