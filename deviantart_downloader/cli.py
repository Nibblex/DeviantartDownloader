"""Argument parsing and the entry point."""

import argparse
import os
import sys
from pathlib import Path

from .api import UNREADABLE_PROFILE, ApiError, DeviantArtClient
from .auth import login
from .config import env_bool, env_choice, env_float, env_int, load_dotenv
from .constants import API_RATE, VERBOSE, CancelledByUser, say
from .listing import GalleryNotFoundError
from .naming import extract_username, profile_label
from .profile import print_profiles
from .sync import (ONLY_FILTERS, add_stats, discover_users, fetch_watching,
                   human_size, new_stats, parse_only, summary_lines,
                   sync_gallery, worth_repairing)
from .web import WebClient


def confirm(question: str) -> bool:
    """Ask a yes/no question at the terminal; assume yes when nobody can answer.

    A piped or redirected stdin means the run was scripted, so it goes ahead
    instead of blocking forever on a prompt no one will ever see.
    """
    try:
        if not sys.stdin.isatty():
            return True
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except (EOFError, ValueError):        # stdin closed mid-prompt
        return False


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
    parser.add_argument("--watching", action="store_true",
                        help="Work on every user your account watches instead of "
                             "a single profile: download their galleries, or "
                             "summarise them all with --info. Needs the session "
                             "--login saves")
    parser.add_argument("-q", "--quiet", action="store_true",
                        default=env_bool("DA_QUIET", False),
                        help="Report only results: the per-work and per-page "
                             "progress lines are dropped, while summaries, "
                             "warnings, errors and prompts still print "
                             "(default: DA_QUIET from .env or off)")
    parser.add_argument("-i", "--info", action="store_true",
                        help="Show the profile's info (URLs of the profile, avatar "
                             "and banner, bio, location, birthday, links, statistics, "
                             "galleries and their item counts) and exit without "
                             "downloading anything. Requires a profile, or "
                             "--watching to summarise everyone you watch")
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
    parser.add_argument("-w", "--web-workers", type=int,
                        default=env_int("DA_WEB_WORKERS", env_int("DA_WORKERS", 4)),
                        help="Simultaneous website downloads (default: DA_WEB_WORKERS "
                             "from .env or 4). The website route costs no API quota, so "
                             "this can be high (recommended not to exceed 8)")
    parser.add_argument("--api-workers", type=int, default=env_int("DA_API_WORKERS", 2),
                        help="Simultaneous file transfers on the API route "
                             "(default: DA_API_WORKERS from .env or 2). The request "
                             "rate is DA_API_RATE's job, shared by every worker, so "
                             "this is the knob for transfer concurrency, not for 429s")
    parser.add_argument("--api-rate", type=float, default=env_float("DA_API_RATE", API_RATE),
                        help="API requests per second, shared by every worker "
                             f"(default: DA_API_RATE from .env or {API_RATE}). The "
                             "limit DeviantArt enforces is per account and trips on "
                             "bursts, so pacing the whole run avoids the 429s that "
                             "stall it; 0 disables the pacing")
    parser.add_argument("--force-api", action="store_true",
                        default=env_bool("DA_FORCE_API", False),
                        help="Route every work through the API instead of reading "
                             "the public listing off the website (slower on the "
                             "API quota; use it if the website route breaks)")
    parser.add_argument("--unblur", action="store_true",
                        default=env_bool("DA_UNBLUR", False),
                        help="Strip the blur filter the API applies to mature-content "
                             "previews (default: keep the blur, or DA_UNBLUR from .env)")
    parser.add_argument("--literature-format", choices=["txt", "html"],
                        default=env_choice("DA_LITERATURE_FORMAT", "txt", ("txt", "html")),
                        help="File format for literature and journals, which have no "
                             "media file (default: DA_LITERATURE_FORMAT from .env or "
                             "'txt'). 'txt' saves the plain text; 'html' saves a "
                             "standalone HTML document that keeps the formatting")
    parser.add_argument("--only", nargs="+", metavar="WHAT",
                        default=[os.environ.get("DA_ONLY", "")],
                        help=f"Keep only the works matching all of {', '.join(ONLY_FILTERS)} "
                             "(default: everything). Repeat or comma-separate them: "
                             "'images' and 'literature' are the two kinds of work, so "
                             "naming both is the same as naming neither, while 'mature' "
                             "narrows whatever the kind left -- '--only literature mature' "
                             "is the mature literature. Also DA_ONLY in .env")
    parser.add_argument("--redownload-blurred", action="store_true",
                        help="Fetch again the mature works whose local copy may be "
                             "the blurred placeholder a logged-out run settled "
                             "for. Needs --login to do anything: without it the "
                             "API still only offers the blur, and each work is "
                             "left alone. Copies already unblurred are recognised "
                             "by their size and kept, and works still only served "
                             "blurred cost no request to dismiss. This is a repair "
                             "pass, not a sync: a user with nothing on the API "
                             "route is skipped whole")
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

    if args.quiet:
        VERBOSE.clear()

    only = parse_only(args.only)

    if args.web_workers < 1:
        sys.exit(f"The number of web workers must be at least 1 (got: {args.web_workers}).")
    if args.api_workers < 1:
        sys.exit(f"The number of API workers must be at least 1 (got: {args.api_workers}).")

    if args.watching and args.profile_url:
        sys.exit("--watching already picks the profiles to work on (every user "
                 "you watch); drop the profile argument or drop --watching.")
    if args.gallery:
        if args.watching:
            sys.exit("--gallery does not combine with --watching: folder names "
                     "differ from one profile to the next, so a single name "
                     "cannot be asked of everyone you watch. Pass one profile.")
        if not args.profile_url:
            sys.exit("--gallery needs a profile: pass the username or URL of the "
                     "gallery's owner.")
    if args.info and not (args.profile_url or args.watching):
        sys.exit("--info needs a profile: pass the username or URL to inspect, "
                 "or --watching to inspect every user you watch.")

    if not args.client_id or not args.client_secret:
        sys.exit(
            "Missing API credentials.\n"
            "Register at https://www.deviantart.com/developers/register and then:\n"
            "  export DA_CLIENT_ID='...'\n"
            "  export DA_CLIENT_SECRET='...'"
        )

    client = DeviantArtClient(args.client_id, args.client_secret,
                              api_rate=args.api_rate)

    if args.login:
        login(client)
        if not (args.profile_url or args.watching):
            return  # login-only invocation

    output_root = Path(args.output).expanduser()
    # One profile asked for by name fails loudly when it turns out to be gone;
    # every batch source skips the dead ones and carries on. Both loops below
    # read this rather than each re-deriving it from a different flag.
    single = bool(args.profile_url)
    if single:
        usernames = [extract_username(args.profile_url)]
    elif args.watching:
        say("Fetching the users your account watches...")
        usernames = fetch_watching(client)
        # Either way a whole watchlist is a long job, so it is worth seeing the
        # size of it before it starts. Interrupting later loses no progress.
        print(f"\nYou watch {len(usernames)} user(s).")
        action = (f"Show the profile of all {len(usernames)} of them" if args.info
                  else f"Download all {len(usernames)} galleries into {output_root}")
        if not confirm(f"{action}?"):
            print("Cancelled.")
            return
        print()
    else:
        # No profile: sync every user already downloaded to the output folder
        usernames = discover_users(output_root)
        say(
            f"No profile given: syncing {len(usernames)} previously "
            f"downloaded user(s) in {output_root}: {', '.join(usernames)}\n"
        )

    if args.redownload_blurred:
        if not client.user_mode:
            sys.exit(
                "--redownload-blurred needs your DeviantArt account: run --login "
                "first.\nWithout it the API only ever offers the blur, so every "
                "work would be left exactly as it is."
            )
        wanted = [name for name in usernames if worth_repairing(output_root, name)]
        skipped = len(usernames) - len(wanted)
        if skipped:
            print(f"Nothing downloaded through the API for {skipped} of "
                  f"{len(usernames)} user(s); they cannot hold a blur and are "
                  "skipped.")
        usernames = wanted
        if not usernames:
            sys.exit("No blurred copies to replace: nothing here came through "
                     "the API.")

    if client.user_mode:
        say("Using the saved user session (mature works come unblurred if "
            "your account allows them).")

    web = None if args.force_api else WebClient()
    if web is None:
        say("API-only mode: every work goes through the API.")

    if args.info:
        # A watchlist outlives the accounts on it, so one gone profile must not
        # take the other summaries down with it.
        print_profiles(client, web, usernames, skip_missing=not single,
                       workers=args.web_workers if web is not None
                       else args.api_workers)
        return

    totals = new_stats()
    per_user = []
    for username in usernames:
        try:
            counts = sync_gallery(
                client, username, output_root,
                web_workers=args.web_workers, api_workers=args.api_workers,
                redownload_missing=args.redownload_missing, unblur=args.unblur,
                redownload_blurred=args.redownload_blurred,
                full=args.full, web=web, gallery=args.gallery,
                text_format=args.literature_format, only=only,
            )
        except UNREADABLE_PROFILE as e:
            # A batch outlives the accounts in it. Whichever call found out this
            # profile cannot be read, it ends that user and not the run, and an
            # unreadable account has nothing to download -- same outcome as an
            # empty one.
            print(f"  {e}")
            counts = None
        if counts is None:
            if single:
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
            # The URL grows with the name it ends in, so padding the whole label
            # keeps the counts in one column just as padding the name alone did.
            rows = sorted(((profile_label(name), counts) for name, counts in per_user),
                          key=lambda row: row[1]["bytes"], reverse=True)
            width = max(len(label) for label, _ in rows)
            print("Per user:")
            for label, counts in rows:
                print(f"  {label:<{width}}  {counts['downloaded']} item(s) "
                      f"downloaded, {human_size(counts['bytes'])}")


def main():
    try:
        run()
    except (ApiError, GalleryNotFoundError) as e:
        sys.exit(f"\n{e}")
    except (KeyboardInterrupt, CancelledByUser):
        # Ctrl+C or 'q' outside the download loop (login, gallery listing, ...)
        print("\nInterrupted by the user.")
        sys.exit(130)
