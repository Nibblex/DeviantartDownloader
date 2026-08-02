"""Argument parsing and the entry point."""

import argparse
import os
import sys
from pathlib import Path

from .api import UNREADABLE_PROFILE, ApiError, DeviantArtClient
from .auth import login
from .config import env_bool, env_choice, env_float, env_int, load_dotenv
from .constants import API_RATE, TEXT_FORMATS, VERBOSE, CancelledByUser, say
from .listing import GalleryNotFoundError
from .naming import extract_username, profile_label
from .overrides import FILENAME, FORMAT, ONLY, load_overrides
from .profile import print_profiles
from .sync import (ONLY_FILTERS, add_stats, discover_users, fetch_watching,
                   human_size, new_stats, parse_date, parse_only,
                   summary_lines, sync_gallery, verify_users, worth_repairing)
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


def without_skipped(usernames: list[str], overrides) -> list[str]:
    """Drop the users the settings file marks skip, and say how many.

    Only ever applied to a batch. Naming one profile on the command line is as
    explicit as a request gets, and the file does not overrule it -- the same
    precedence every other setting in it follows.
    """
    wanted = [name for name in usernames if not overrides.skips(name)]
    left_out = len(usernames) - len(wanted)
    if left_out:
        print(f"{overrides.path.name}: {left_out} of {len(usernames)} user(s) "
              f"marked skip, left out.")
    if not wanted:
        sys.exit(f"Every user is marked skip in {overrides.path.name}, so there "
                 "is nothing to do.")
    return wanted


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
                             "--watching to summarise everyone you watch. Given "
                             "--since/--until it also reports how many works fall "
                             "in the range, which is the one figure that costs a "
                             "walk of the gallery listing to answer")
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
    # Both of these are left without a default so that "given on the command
    # line" stays distinguishable from "not given": what the flag decides is
    # everyone's, and outranks the per-user file, while the .env fallback below
    # is only a standing default and does not.
    parser.add_argument("--literature-format", choices=list(TEXT_FORMATS),
                        help="File format for literature and journals, which have no "
                             "media file (default: DA_LITERATURE_FORMAT from .env or "
                             "'txt'). 'txt' saves the plain text; 'html' saves a "
                             "standalone HTML document that keeps the formatting")
    parser.add_argument("--only", nargs="+", metavar="WHAT",
                        help=f"Keep only the works matching all of {', '.join(ONLY_FILTERS)} "
                             "(default: everything). Repeat or comma-separate them: "
                             "'images' and 'literature' are the two kinds of work, so "
                             "naming both is the same as naming neither, while 'mature' "
                             "narrows whatever the kind left -- '--only literature mature' "
                             "is the mature literature. 'ai'/'no-ai' and "
                             "'upscaled'/'no-upscaled' are two more axes, the website's own "
                             "AI declarations (DreamUp counts as AI-made, and an upscale is "
                             "not an AI-made work); only the website listing carries them, "
                             "so the plain value takes the works known to be that way while "
                             "the 'no-' one keeps everything not known to be. Also DA_ONLY "
                             "in .env")
    parser.add_argument("--user-config", metavar="PATH",
                        default=os.environ.get("DA_USER_CONFIG", "").strip() or None,
                        help=f"Per-user settings file: a JSON object naming a "
                             f"username and the --only, --literature-format and "
                             f'"skip" to sync them with, for that user alone '
                             f"(default: {FILENAME} in the output folder, if it "
                             "exists; also "
                             "DA_USER_CONFIG in .env). Either flag given above "
                             "outranks it and settles that setting for everyone, so "
                             "the file has the say on whatever this run leaves out. "
                             "Renames are followed: each entry records the id the "
                             "route reports for that user, so the settings move to "
                             "the new name instead of being lost")
    parser.add_argument("--verify", action="store_true",
                        help="Check what is already downloaded against its own "
                             "record and report: works recorded but missing from "
                             "disk, empty files, and .part files an interrupted "
                             "run left behind. Repairs nothing and downloads "
                             "nothing, needs no credentials, and exits non-zero "
                             "when it finds something. With a profile it checks "
                             "that user, otherwise every user in the output folder")
    parser.add_argument("--dry-run", action="store_true",
                        help="List and route the works, report what a real run "
                             "would fetch, and stop before fetching any of it. "
                             "Nothing is written: not the gallery folder, not the "
                             "metadata file, not the per-user settings file. The "
                             "mature works are counted rather than looked up, "
                             "since that lookup is the part that costs API quota. "
                             "Combines with the --redownload-* flags, and the "
                             "counts move with them")
    parser.add_argument("--since", metavar="DATE",
                        help="Keep only the works published on or after this date "
                             "(YYYY-MM-DD, or a full ISO 8601 timestamp; no offset "
                             "means UTC). Both routes list newest-first, so this "
                             "also stops the listing walk once a page is entirely "
                             "older -- on the API route, every page not asked for "
                             "is a request not spent. With --info it reports how "
                             "many works fall in the range instead of downloading "
                             "them")
    parser.add_argument("--until", metavar="DATE",
                        help="Keep only the works published on or before this date. "
                             "A bare date means the whole day, so --until 2024-12-31 "
                             "includes that day. Older works still have to be walked "
                             "past to reach, so unlike --since this narrows what is "
                             "downloaded rather than what is listed")
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

    # A flag given for this run settles its setting for every user: the per-user
    # file may not touch it. Not passing it leaves .env, and then the built-in
    # default, as a fallback the file is free to override.
    locked = frozenset(name for name, given in ((ONLY, args.only),
                                                (FORMAT, args.literature_format))
                       if given is not None)
    only = parse_only(args.only if args.only is not None
                      else [os.environ.get("DA_ONLY", "")])
    text_format = (args.literature_format if args.literature_format is not None
                   else env_choice("DA_LITERATURE_FORMAT", "txt", TEXT_FORMATS))

    since = parse_date(args.since, "--since") if args.since else None
    until = parse_date(args.until, "--until", end_of_day=True) if args.until else None
    if since and until and since > until:
        sys.exit(f"--since {since:%Y-%m-%d} is after --until {until:%Y-%m-%d}, "
                 "so no work could fall between them.")

    if args.web_workers < 1:
        sys.exit(f"The number of web workers must be at least 1 (got: {args.web_workers}).")
    if args.api_workers < 1:
        sys.exit(f"The number of API workers must be at least 1 (got: {args.api_workers}).")

    # Ahead of the checks below, so that a --verify asked for alongside one of
    # them hears about that rather than about a rule it was never subject to:
    # --verify wants no profile and no gallery, so "--info needs a profile" is
    # the wrong thing to answer somebody who typed both.
    if args.verify:
        # Every one of these would otherwise be silently ignored, --login most
        # sharply: this branch exits above the point where it would happen, so
        # asking for both would log you in never and say nothing about it.
        clashes = [name for name, given in (("--watching", args.watching),
                                            ("--info", args.info),
                                            ("--login", args.login),
                                            ("--dry-run", args.dry_run),
                                            ("--gallery", args.gallery)) if given]
        if clashes:
            sys.exit(f"--verify does not combine with {', '.join(clashes)}: it "
                     "reads the output folder and does nothing else, so there "
                     "is nothing for those to change. Run it on its own, with a "
                     "profile or without one.")
        # Ahead of the credentials check on purpose: this reads the disk and
        # nothing else, and a copy can be verified by someone who never
        # registered an application.
        root = Path(args.output).expanduser()
        names = ([extract_username(args.profile_url)] if args.profile_url
                 else discover_users(root))
        sys.exit(0 if verify_users(root, names) else 1)

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
    # Read before anything is fetched, so a typo in it is heard about before a
    # watchlist is walked or a single work downloaded.
    overrides = load_overrides(args.user_config, output_root, locked,
                               read_only=args.dry_run)
    if shadowed := overrides.shadowed():
        flags = ", ".join(f"--{name}" for name in sorted(shadowed))
        say(f"{flags} given on the command line, which settles it for every user: "
            f"{overrides.path.name} does not get a say in that.")
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
        # Before the question, so the number it quotes is the one it will do.
        usernames = without_skipped(usernames, overrides)
        # A dry run still walks every listing, which is what makes a watchlist
        # long, so it is still worth asking -- but asking whether to download
        # would be the one place it promised something it will not do.
        if args.info:
            action = f"Show the profile of all {len(usernames)} of them"
        elif args.dry_run:
            action = (f"Work out what a sync of all {len(usernames)} galleries "
                      "would fetch")
        else:
            action = f"Download all {len(usernames)} galleries into {output_root}"
        if not confirm(f"{action}?"):
            print("Cancelled.")
            return
        print()
    else:
        # No profile: sync every user already downloaded to the output folder
        usernames = without_skipped(discover_users(output_root), overrides)
        say(
            f"No profile given: {'checking' if args.dry_run else 'syncing'} "
            f"{len(usernames)} previously downloaded user(s) in "
            f"{output_root}: {', '.join(usernames)}\n"
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
                       else args.api_workers,
                       since=since, until=until)
        return

    totals = new_stats()
    per_user = []
    # The run's own count, rather than the sum of the galleries': a user skipped
    # whole returns no stats to add up, but the requests spent finding that out
    # were still spent.
    spent_before = client.limiter.requests
    for username in usernames:
        try:
            counts = sync_gallery(
                client, username, output_root,
                web_workers=args.web_workers, api_workers=args.api_workers,
                redownload_missing=args.redownload_missing, unblur=args.unblur,
                redownload_blurred=args.redownload_blurred,
                full=args.full, web=web, gallery=args.gallery,
                text_format=text_format, only=only,
                overrides=overrides, since=since, until=until,
                dry_run=args.dry_run,
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

    # A dry run has already said what it would do, per user. Folding zeroes into
    # a line that begins "All users synced" would only misreport it.
    if len(usernames) > 1 and not args.dry_run:
        totals["requests"] = client.limiter.requests - spent_before
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
