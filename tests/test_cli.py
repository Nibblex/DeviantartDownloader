"""End-to-end runs through the command line."""

import io
import json

import pytest

from deviantart_downloader import api, cli, downloads, listing, overrides, sync
from deviantart_downloader.constants import CANCEL, CancelledByUser

from .conftest import (BASE_URI, DEV_ID, WEB_USER_ID, FakeClient, FakeWebClient,
                       blocked_web_item, fake_download, make_dev, make_user_dir,
                       set_argv, web_item)


class TestRun:
    def test_requires_credentials(self, clean_cli_env, monkeypatch):
        set_argv(monkeypatch, "someartist")
        with pytest.raises(SystemExit, match="Missing API credentials"):
            cli.run()

    def test_no_profile_and_no_output_dir_exits(self, clean_cli_env, monkeypatch):
        set_argv(monkeypatch, "--client-id", "x", "--client-secret", "y")
        with pytest.raises(SystemExit, match="does not exist"):
            cli.run()

    def test_rejects_zero_workers(self, clean_cli_env, monkeypatch):
        set_argv(monkeypatch, "someartist", "--client-id", "x",
                 "--client-secret", "y", "-w", "0")
        with pytest.raises(SystemExit, match="at least 1"):
            cli.run()

    def test_rejects_zero_api_workers(self, clean_cli_env, monkeypatch):
        set_argv(monkeypatch, "someartist", "--client-id", "x",
                 "--client-secret", "y", "--api-workers", "0")
        with pytest.raises(SystemExit, match="at least 1"):
            cli.run()

    def test_separate_pools_sized_per_route(self, clean_cli_env, monkeypatch):
        web = FakeWebClient(pages=[{"results": [web_item()], "hasMore": False}])
        monkeypatch.setattr(cli, "WebClient", lambda: web)
        monkeypatch.setattr(downloads, "download_file", fake_download)
        sizes = []
        real = sync.ThreadPoolExecutor
        monkeypatch.setattr(sync, "ThreadPoolExecutor",
                            lambda max_workers=None, **kw:
                            (sizes.append(max_workers),
                             real(max_workers=max_workers, **kw))[1])
        out = clean_cli_env / "out"
        set_argv(monkeypatch, "artist", "--web", "-o", str(out), "--client-id", "x",
                 "--client-secret", "y", "-w", "5", "--api-workers", "3")
        cli.run()
        # The website pool is created first, then the API pool.
        assert sizes == [5, 3]

    def test_empty_gallery_exits(self, clean_cli_env, monkeypatch):
        monkeypatch.setattr(listing, "fetch_gallery", lambda client, username, **kw: [])
        set_argv(monkeypatch, "someartist", "--client-id", "x",
                 "--client-secret", "y")
        with pytest.raises(SystemExit, match="empty"):
            cli.run()

    def test_info_without_profile_exits(self, clean_cli_env, monkeypatch):
        set_argv(monkeypatch, "--client-id", "x", "--client-secret", "y", "--info")
        with pytest.raises(SystemExit, match="--info needs a profile"):
            cli.run()

    def test_info_prints_and_downloads_nothing(self, clean_cli_env, monkeypatch,
                                               no_downloads):
        seen = {}
        monkeypatch.setattr(cli, "print_profiles",
                            lambda client, web, names, **kw: seen.update(
                                names=names, **kw))
        set_argv(monkeypatch, "artist", "--client-id", "x", "--client-secret", "y",
                 "--info")
        cli.run()
        assert seen["names"] == ["artist"]
        # A profile asked for by name must fail loudly if it is gone.
        assert seen["skip_missing"] is False

    def test_gallery_without_profile_exits(self, clean_cli_env, monkeypatch):
        set_argv(monkeypatch, "--client-id", "x", "--client-secret", "y",
                 "-g", "Sketches")
        with pytest.raises(SystemExit, match="--gallery needs a profile"):
            cli.run()

    def test_gallery_flows_through_to_the_listing(self, clean_cli_env,
                                                  monkeypatch, capsys):
        seen = {}

        def fake_fetch(client, username, **kw):
            seen.update(kw)
            return [make_dev()]

        monkeypatch.setattr(listing, "fetch_gallery", fake_fetch)
        monkeypatch.setattr(listing, "fetch_api_folders",
                            lambda client, username: [{"folderid": "UUID",
                                                       "name": "Sketches"}])
        monkeypatch.setattr(downloads, "download_file", fake_download)
        out = clean_cli_env / "out"
        set_argv(monkeypatch, "artist", "-o", str(out), "--client-id", "x",
                 "--client-secret", "y", "-g", "sketches")
        cli.run()
        assert seen["folder"] == "UUID"
        assert 'Gallery folder: "sketches"' in capsys.readouterr().out

    def test_unknown_gallery_exits_with_suggestions(self, clean_cli_env, monkeypatch):
        monkeypatch.setattr(listing, "fetch_api_folders",
                            lambda client, username: [{"folderid": "U",
                                                       "name": "Sketches"}])
        set_argv(monkeypatch, "artist", "--client-id", "x", "--client-secret", "y",
                 "-g", "Nope")
        with pytest.raises(listing.GalleryNotFoundError, match="Available folders"):
            cli.run()

    def test_deactivated_profile_exits_gracefully(self, clean_cli_env,
                                                  monkeypatch, capsys):
        def gone(client, username, **kw):
            raise api.UserNotFoundError('User "ghost" not found.')

        monkeypatch.setattr(listing, "fetch_gallery", gone)
        set_argv(monkeypatch, "ghost", "--client-id", "x", "--client-secret", "y")
        with pytest.raises(SystemExit, match="does not exist"):
            cli.run()
        assert 'User "ghost" not found.' in capsys.readouterr().out

    def test_end_to_end_download(self, clean_cli_env, monkeypatch, capsys):
        devs = [
            make_dev(),
            make_dev(deviationid="ffffeeee-0000", title="Journal", content=None),
        ]
        monkeypatch.setattr(listing, "fetch_gallery", lambda client, username, **kw: devs)

        monkeypatch.setattr(downloads, "download_file", fake_download)
        out = clean_cli_env / "out"
        set_argv(monkeypatch, "https://www.deviantart.com/someartist",
                 "-o", str(out), "--client-id", "x", "--client-secret", "y",
                 "-w", "2")
        cli.run()

        gallery = out / "someartist"
        assert (gallery / "api" / "My Art_abcd1234.png").is_file()
        assert json.loads((gallery / "_metadata.json").read_text(encoding="utf-8")) == devs
        assert json.loads((gallery / "_downloaded.json").read_text(encoding="utf-8")) == {
            "ABCD1234": "api/My Art_abcd1234.png"
        }
        stdout = capsys.readouterr().out
        assert "Downloaded: 1" in stdout
        assert "No file: 1" in stdout
        # A single-user run has no "Per user:" block, so the header is the only
        # place the profile URL can show up.
        assert "User: someartist — https://www.deviantart.com/someartist" in stdout

    def test_downloads_literature_as_text(self, clean_cli_env, monkeypatch, capsys):
        lit = web_item(
            deviationId=1260299235, title="My Poem", type="literature",
            url="https://www.deviantart.com/artist/art/My-Poem-1260299235",
            textContent={"excerpt": "short"})
        body = json.dumps({"document": {"content": [
            {"type": "paragraph", "content": [
                {"type": "text", "text": "The full poem"}]}]}})
        web = FakeWebClient(
            pages=[{"results": [lit], "hasMore": False}],
            texts={"1260299235": {"html": {"type": "tiptap", "markup": body}}})
        monkeypatch.setattr(cli, "WebClient", lambda: web)
        out = clean_cli_env / "out"
        set_argv(monkeypatch, "artist", "--web", "-o", str(out),
                 "--client-id", "x", "--client-secret", "y", "-w", "1")
        cli.run()

        dest = out / "artist" / "web" / "My Poem_1260299235.txt"
        assert dest.read_text(encoding="utf-8") == "The full poem\n"
        assert "Downloaded: 1" in capsys.readouterr().out

    def test_quit_during_listing_stops_before_downloading(self, clean_cli_env,
                                                           monkeypatch, capsys):
        def fetch_then_quit(client, username, **kw):
            CANCEL.set()                       # as if the user pressed 'q'
            return [make_dev()]

        monkeypatch.setattr(listing, "fetch_gallery", fetch_then_quit)
        called = []
        monkeypatch.setattr(downloads, "download_file",
                            lambda *a, **k: called.append(1) or True)
        set_argv(monkeypatch, "artist", "-o", str(clean_cli_env / "out"),
                 "--client-id", "x", "--client-secret", "y")
        with pytest.raises(SystemExit) as excinfo:
            cli.run()
        assert excinfo.value.code == 130
        assert "Stopped before downloading" in capsys.readouterr().out
        assert called == []                    # nothing was downloaded

    def test_quit_during_api_lookup_exits_cleanly(self, clean_cli_env,
                                                   monkeypatch, capsys):
        # A cancel during the mature-work API lookup surfaces as CancelledByUser;
        # it must exit 130 cleanly, not crash with a traceback.
        web = FakeWebClient(pages=[{"results": [blocked_web_item()], "hasMore": False}])
        monkeypatch.setattr(cli, "WebClient", lambda: web)

        def boom(*a, **k):
            raise CancelledByUser("Cancelled by the user")

        monkeypatch.setattr(sync, "resolve_via_api", boom)
        set_argv(monkeypatch, "artist", "--web", "-o", str(clean_cli_env / "out"),
                 "--client-id", "x", "--client-secret", "y")
        with pytest.raises(SystemExit) as excinfo:
            cli.run()
        assert excinfo.value.code == 130
        assert "Stopped before downloading" in capsys.readouterr().out

    def test_only_images_skips_literature(self, clean_cli_env, monkeypatch, capsys):
        img = web_item()                                   # an image work
        lit = web_item(deviationId=1260299235, title="My Poem", type="literature",
                       url="https://www.deviantart.com/artist/art/My-Poem-1260299235")
        web = FakeWebClient(pages=[{"results": [img, lit], "hasMore": False}])
        monkeypatch.setattr(cli, "WebClient", lambda: web)
        out = clean_cli_env / "out"
        set_argv(monkeypatch, "artist", "--web", "-o", str(out),
                 "--client-id", "x", "--client-secret", "y",
                 "-w", "1", "--only", "images")
        monkeypatch.setattr(downloads, "download_file", fake_download)
        cli.run()

        gallery = out / "artist"
        assert (gallery / "web" / "Web Art_1004952679.jpg").is_file()
        assert not list((gallery / "web").glob("*.txt"))
        stdout = capsys.readouterr().out
        assert "--only images): skipped 1 of 2 work(s)" in stdout

    def test_only_literature_skips_images(self, clean_cli_env, monkeypatch, capsys):
        img = web_item()
        lit = web_item(deviationId=1260299235, title="My Poem", type="literature",
                       url="https://www.deviantart.com/artist/art/My-Poem-1260299235")
        body = json.dumps({"document": {"content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "Poem"}]}]}})
        web = FakeWebClient(
            pages=[{"results": [img, lit], "hasMore": False}],
            texts={"1260299235": {"html": {"type": "tiptap", "markup": body}}})
        monkeypatch.setattr(cli, "WebClient", lambda: web)
        out = clean_cli_env / "out"
        set_argv(monkeypatch, "artist", "--web", "-o", str(out),
                 "--client-id", "x", "--client-secret", "y",
                 "-w", "1", "--only", "literature")
        cli.run()

        gallery = out / "artist"
        assert (gallery / "web" / "My Poem_1260299235.txt").is_file()
        assert not list((gallery / "web").glob("*.jpg"))
        assert "--only literature): skipped 1 of 2 work(s)" in capsys.readouterr().out

    def test_only_with_no_matches_is_not_fatal(self, clean_cli_env, monkeypatch, capsys):
        web = FakeWebClient(pages=[{"results": [web_item()], "hasMore": False}])
        monkeypatch.setattr(cli, "WebClient", lambda: web)
        out = clean_cli_env / "out"
        set_argv(monkeypatch, "artist", "--web", "-o", str(out),
                 "--client-id", "x", "--client-secret", "y",
                 "-w", "1", "--only", "literature")
        cli.run()                                          # no SystemExit
        assert "No literature to download" in capsys.readouterr().out
        assert not (out / "artist" / "web").exists()

    def test_literature_format_html_saves_a_document(self, clean_cli_env,
                                                     monkeypatch, capsys):
        lit = web_item(
            deviationId=1260299235, title="My Poem", type="literature",
            url="https://www.deviantart.com/artist/art/My-Poem-1260299235")
        body = json.dumps({"document": {"content": [
            {"type": "paragraph", "content": [
                {"type": "text", "text": "The full poem"}]}]}})
        web = FakeWebClient(
            pages=[{"results": [lit], "hasMore": False}],
            texts={"1260299235": {"html": {"type": "tiptap", "markup": body}}})
        monkeypatch.setattr(cli, "WebClient", lambda: web)
        out = clean_cli_env / "out"
        set_argv(monkeypatch, "artist", "--web", "-o", str(out),
                 "--client-id", "x", "--client-secret", "y",
                 "-w", "1", "--literature-format", "html")
        cli.run()

        dest = out / "artist" / "web" / "My Poem_1260299235.html"
        content = dest.read_text(encoding="utf-8")
        assert content.startswith("<!DOCTYPE html>")
        assert "<p>The full poem</p>" in content
        assert not (out / "artist" / "web" / "My Poem_1260299235.txt").exists()

    def test_routes_each_source_into_its_own_folder(self, clean_cli_env,
                                                    monkeypatch, both_routes,
                                                    capsys):
        web = both_routes
        fetched = []

        def recording_download(session, url, dest, fallback=None):
            fetched.append((session, url))
            dest.write_bytes(b"x")
            return True

        monkeypatch.setattr(downloads, "download_file", recording_download)
        out = clean_cli_env / "out"
        set_argv(monkeypatch, "artist", "--web", "-o", str(out),
                 "--client-id", "x", "--client-secret", "y",
                 "-w", "1")
        cli.run()

        gallery = out / "artist"
        assert (gallery / "web" / "Web Art_1004952679.jpg").is_file()
        assert (gallery / "api" / "Mature Art_222222222.png").is_file()
        assert json.loads((gallery / "_downloaded.json").read_text("utf-8")) == {
            "1004952679": "web/Web Art_1004952679.jpg",
            "222222222": "api/Mature Art_222222222.png",
        }
        # Each file was fetched with the session of its own route
        sessions = {url: session for session, url in fetched}
        assert sessions[f"{BASE_URI}?token=tok1"] is web.session
        assert "Route: 1 via the website (web/), 1 via the API (api/)" \
            in capsys.readouterr().out

    def test_web_route_does_not_call_the_download_endpoint(self, clean_cli_env,
                                                           monkeypatch):
        """A downloadable work still costs zero API calls on the web route."""
        web = FakeWebClient(pages=[
            {"results": [web_item(isDownloadable=True)], "hasMore": False},
        ])
        monkeypatch.setattr(cli, "WebClient", lambda: web)

        def unexpected(*args, **kwargs):
            raise AssertionError("the web route must not call the API")

        monkeypatch.setattr(listing, "fetch_gallery", unexpected)
        monkeypatch.setattr(api.DeviantArtClient, "api_get", unexpected)
        monkeypatch.setattr(downloads, "download_file",
                            lambda session, url, dest, fallback=None:
                            (dest.write_bytes(b"x"), True)[1])
        out = clean_cli_env / "out"
        set_argv(monkeypatch, "artist", "--web", "-o", str(out),
                 "--client-id", "x", "--client-secret", "y")
        cli.run()
        assert (out / "artist" / "web" / "Web Art_1004952679.jpg").is_file()

    def test_damaged_metadata_is_reported_and_regenerated(self, clean_cli_env,
                                                          monkeypatch, capsys):
        devs = [make_dev()]
        monkeypatch.setattr(listing, "fetch_gallery",
                            lambda client, username, **kw: devs)
        monkeypatch.setattr(downloads, "download_file", fake_download)
        out = clean_cli_env / "out"
        gallery = make_user_dir(out, "someartist")
        (gallery / "_metadata.json").write_text("[{truncated", encoding="utf-8")
        set_argv(monkeypatch, "someartist", "-o", str(out), "--client-id", "x",
                 "--client-secret", "y")
        cli.run()

        assert "WARNING: could not read _metadata.json" in capsys.readouterr().out
        assert json.loads(
            (gallery / "_metadata.json").read_text(encoding="utf-8")) == devs

    def test_metadata_merges_across_runs(self, clean_cli_env, monkeypatch, capsys):
        fetch_kwargs = []
        batches = [
            [make_dev()],
            # Second run: the early stop only returned the newest work
            [make_dev(deviationid="ffffeeee-0000", title="New Art")],
        ]

        def fake_fetch(client, username, **kw):
            fetch_kwargs.append(kw)
            return batches.pop(0)

        monkeypatch.setattr(listing, "fetch_gallery", fake_fetch)
        monkeypatch.setattr(downloads, "download_file", fake_download)
        out = clean_cli_env / "out"
        argv = ("someartist", "-o", str(out), "--client-id", "x",
                "--client-secret", "y")
        set_argv(monkeypatch, *argv)
        cli.run()
        set_argv(monkeypatch, *argv)
        cli.run()

        # No manifest before the first run; the second run can stop early
        assert fetch_kwargs[0]["manifest"] is None
        assert fetch_kwargs[1]["manifest"] is not None
        meta = json.loads(
            (out / "someartist" / "_metadata.json").read_text(encoding="utf-8"))
        assert [d["deviationid"] for d in meta] == ["ffffeeee-0000", DEV_ID]

    @pytest.mark.parametrize("flag", ["--full", "--redownload-missing"])
    def test_flags_force_the_full_listing(self, clean_cli_env, monkeypatch, flag):
        seen = {}

        def fake_fetch(client, username, **kw):
            seen.update(kw)
            return [make_dev()]

        monkeypatch.setattr(listing, "fetch_gallery", fake_fetch)
        monkeypatch.setattr(downloads, "download_file", fake_download)
        out = clean_cli_env / "out"
        make_user_dir(out, "someartist")
        set_argv(monkeypatch, "someartist", "-o", str(out), "--client-id", "x",
                 "--client-secret", "y", flag)
        cli.run()
        assert seen["full"] is True


class TestPerUserSettings:
    """The settings file, driving a real run one user at a time."""

    POEM_ID = 1260299235

    def gallery(self, monkeypatch):
        """A gallery of one image and one poem, over the website route."""
        lit = web_item(
            deviationId=self.POEM_ID, title="My Poem", type="literature",
            url=f"https://www.deviantart.com/artist/art/My-Poem-{self.POEM_ID}")
        body = json.dumps({"document": {"content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "Poem"}]}]}})
        web = FakeWebClient(
            pages=[{"results": [web_item(), lit], "hasMore": False}],
            texts={str(self.POEM_ID): {"html": {"type": "tiptap", "markup": body}}})
        monkeypatch.setattr(cli, "WebClient", lambda: web)
        monkeypatch.setattr(downloads, "download_file", fake_download)

    def settings(self, out, entries, name=None):
        out.mkdir(parents=True, exist_ok=True)
        path = out / (name or overrides.FILENAME)
        path.write_text(json.dumps(entries), encoding="utf-8")
        return path

    def run(self, monkeypatch, out, *extra):
        set_argv(monkeypatch, "artist", "--web", "-o", str(out), "--client-id", "x",
                 "--client-secret", "y", "-w", "1", *extra)
        cli.run()

    def test_the_file_narrows_one_user_where_the_run_asked_for_everything(
            self, clean_cli_env, monkeypatch, capsys):
        self.gallery(monkeypatch)
        out = clean_cli_env / "out"
        self.settings(out, {"artist": {"only": "literature"}})
        self.run(monkeypatch, out)

        web_dir = out / "artist" / "web"
        assert (web_dir / f"My Poem_{self.POEM_ID}.txt").is_file()
        assert not list(web_dir.glob("*.jpg"))
        assert "_users.json: --only literature" in capsys.readouterr().out

    def test_the_file_picks_the_literature_format(self, clean_cli_env, monkeypatch):
        self.gallery(monkeypatch)
        out = clean_cli_env / "out"
        self.settings(out, {"artist": {"literature-format": "html"}})
        self.run(monkeypatch, out)
        assert (out / "artist" / "web" / f"My Poem_{self.POEM_ID}.html").is_file()

    def test_a_file_elsewhere_is_read_when_named(self, clean_cli_env, monkeypatch):
        self.gallery(monkeypatch)
        out = clean_cli_env / "out"
        path = self.settings(clean_cli_env, {"artist": {"only": "literature"}},
                             name="mine.json")
        self.run(monkeypatch, out, "--user-config", str(path))
        assert not list((out / "artist" / "web").glob("*.jpg"))

    def test_a_user_the_file_leaves_alone_keeps_the_command_line(
            self, clean_cli_env, monkeypatch):
        self.gallery(monkeypatch)
        out = clean_cli_env / "out"
        self.settings(out, {"someone-else": {"only": "literature"}})
        self.run(monkeypatch, out, "--only", "images")
        # The run-wide --only still decides for everyone unnamed.
        assert (out / "artist" / "web" / "Web Art_1004952679.jpg").is_file()
        assert not list((out / "artist" / "web").glob("*.txt"))

    def test_the_flag_given_for_the_run_outranks_the_file(self, clean_cli_env,
                                                          monkeypatch, capsys):
        self.gallery(monkeypatch)
        out = clean_cli_env / "out"
        self.settings(out, {"artist": {"only": "literature"}})
        self.run(monkeypatch, out, "--only", "images")

        assert (out / "artist" / "web" / "Web Art_1004952679.jpg").is_file()
        assert not list((out / "artist" / "web").glob("*.txt"))
        assert "--only given on the command line" in capsys.readouterr().out

    def test_the_setting_the_run_left_out_still_comes_from_the_file(
            self, clean_cli_env, monkeypatch):
        self.gallery(monkeypatch)
        out = clean_cli_env / "out"
        self.settings(out, {"artist": {"only": "images",
                                       "literature-format": "html"}})
        # --only is settled here, so the file only gets to pick the format.
        self.run(monkeypatch, out, "--only", "literature")
        assert (out / "artist" / "web" / f"My Poem_{self.POEM_ID}.html").is_file()

    def test_the_env_default_does_not_outrank_the_file(self, clean_cli_env,
                                                      monkeypatch):
        self.gallery(monkeypatch)
        monkeypatch.setenv("DA_ONLY", "images")
        out = clean_cli_env / "out"
        self.settings(out, {"artist": {"only": "literature"}})
        self.run(monkeypatch, out)
        # DA_ONLY is a standing default, not this run's decision.
        assert (out / "artist" / "web" / f"My Poem_{self.POEM_ID}.txt").is_file()
        assert not list((out / "artist" / "web").glob("*.jpg"))

    def test_a_renamed_user_keeps_their_settings(self, clean_cli_env, monkeypatch,
                                                 capsys):
        self.gallery(monkeypatch)
        out = clean_cli_env / "out"
        # Written when the user still went by "oldname", id and all.
        path = self.settings(out, {"oldname": {"only": "literature",
                                              "ids": {"web": str(WEB_USER_ID)}}})
        self.run(monkeypatch, out)

        assert (out / "artist" / "web" / f"My Poem_{self.POEM_ID}.txt").is_file()
        assert not list((out / "artist" / "web").glob("*.jpg"))
        assert '"oldname" is now "artist"' in capsys.readouterr().out
        assert "artist" in json.loads(path.read_text(encoding="utf-8"))

    def test_the_id_is_recorded_so_a_later_rename_is_recognised(
            self, clean_cli_env, monkeypatch):
        self.gallery(monkeypatch)
        out = clean_cli_env / "out"
        path = self.settings(out, {"artist": {"only": "literature"}})
        self.run(monkeypatch, out)
        entries = json.loads(path.read_text(encoding="utf-8"))
        assert entries["artist"]["ids"] == {"web": str(WEB_USER_ID)}

    def test_a_broken_file_stops_the_run_before_anything_is_downloaded(
            self, clean_cli_env, monkeypatch):
        self.gallery(monkeypatch)
        out = clean_cli_env / "out"
        self.settings(out, {"artist": {"only": "sfw"}})
        with pytest.raises(SystemExit, match="asks --only for sfw"):
            self.run(monkeypatch, out)
        assert not (out / "artist").exists()


class TestDiscoverUsers:
    def test_finds_downloaded_users_sorted(self, tmp_path):
        make_user_dir(tmp_path, "zeta")
        make_user_dir(tmp_path, "alpha", marker="_metadata.json", content="[]")
        assert sync.discover_users(tmp_path) == ["alpha", "zeta"]

    def test_ignores_unrelated_entries(self, tmp_path):
        make_user_dir(tmp_path, "artist")
        (tmp_path / "random-folder").mkdir()          # no marker files
        (tmp_path / ".hidden").mkdir()
        (tmp_path / "_underscore").mkdir()
        (tmp_path / "loose-file.txt").write_bytes(b"x")
        assert sync.discover_users(tmp_path) == ["artist"]

    def test_missing_output_dir_exits(self, tmp_path):
        with pytest.raises(SystemExit, match="does not exist"):
            sync.discover_users(tmp_path / "nope")

    def test_no_users_exits(self, tmp_path):
        (tmp_path / "random-folder").mkdir()
        with pytest.raises(SystemExit, match="No previously downloaded users"):
            sync.discover_users(tmp_path)


@pytest.fixture
def both_routes(monkeypatch):
    """A gallery with one ordinary work and one the website only serves blurred,
    so the run exercises the website route and the API route at once."""
    web = FakeWebClient(pages=[
        {"results": [web_item(), blocked_web_item()], "hasMore": False},
    ])
    monkeypatch.setattr(cli, "WebClient", lambda: web)
    # The mature work is only resolvable through the API listing.
    monkeypatch.setattr(listing, "_api_page",
                        lambda client, endpoint, username, offset: {
                            "results": [make_dev(
                                url="https://www.deviantart.com/artist/art"
                                    "/Mature-Art-222222222",
                                title="Mature Art")],
                            "has_more": False})
    return web


@pytest.fixture
def galleries(monkeypatch):
    """Patch fetch_gallery/download_file; galleries dict drives the data."""
    galleries = {}
    monkeypatch.setattr(
        listing, "fetch_gallery",
        lambda client, username, **kw: galleries.get(username, []))

    monkeypatch.setattr(downloads, "download_file", fake_download)
    return galleries


class TestSyncAll:
    def test_syncs_every_downloaded_user(self, clean_cli_env, monkeypatch,
                                         galleries, capsys):
        out = clean_cli_env / "out"
        make_user_dir(out, "alice")
        make_user_dir(out, "bob")
        galleries["alice"] = [make_dev()]
        galleries["bob"] = [make_dev(deviationid="ffffeeee-0000", title="Bob Art")]

        set_argv(monkeypatch, "-o", str(out), "--client-id", "x",
                 "--client-secret", "y")
        cli.run()

        assert (out / "alice" / "api" / "My Art_abcd1234.png").is_file()
        assert (out / "bob" / "api" / "Bob Art_ffffeeee.png").is_file()
        stdout = capsys.readouterr().out
        assert "syncing 2 previously downloaded user(s)" in stdout
        assert "All users synced. Downloaded: 2" in stdout
        # The grand total breaks the downloads down by route and per user
        assert "via API:     2 item(s)" in stdout
        assert "Per user:" in stdout
        # Each name carries its profile URL, and the counts still line up: the
        # shorter label is padded out to the width of the longer one.
        assert ("alice — https://www.deviantart.com/alice  "
                "1 item(s) downloaded") in stdout
        assert ("bob — https://www.deviantart.com/bob      "
                "1 item(s) downloaded") in stdout

    def test_empty_gallery_is_skipped_not_fatal(self, clean_cli_env, monkeypatch,
                                                galleries, capsys):
        out = clean_cli_env / "out"
        make_user_dir(out, "ghost")     # deactivated account: empty gallery
        make_user_dir(out, "alice")
        galleries["alice"] = [make_dev()]

        set_argv(monkeypatch, "-o", str(out), "--client-id", "x",
                 "--client-secret", "y")
        cli.run()

        assert (out / "alice" / "api" / "My Art_abcd1234.png").is_file()
        stdout = capsys.readouterr().out
        assert "Skipping ghost" in stdout

    def test_deactivated_user_is_skipped_not_fatal(self, clean_cli_env,
                                                   monkeypatch, capsys):
        out = clean_cli_env / "out"
        make_user_dir(out, "ghost")     # profile deactivated since last sync
        make_user_dir(out, "alice")

        def fetch(client, username, **kw):
            if username == "ghost":
                raise api.UserNotFoundError('User "ghost" not found.')
            return [make_dev()]

        monkeypatch.setattr(listing, "fetch_gallery", fetch)
        monkeypatch.setattr(downloads, "download_file", fake_download)
        set_argv(monkeypatch, "-o", str(out), "--client-id", "x",
                 "--client-secret", "y")
        cli.run()

        assert (out / "alice" / "api" / "My Art_abcd1234.png").is_file()
        stdout = capsys.readouterr().out
        assert 'User "ghost" not found.' in stdout
        assert "Skipping ghost" in stdout

    def test_an_account_gone_after_the_listing_is_skipped_too(
            self, clean_cli_env, monkeypatch, capsys):
        """The listing can succeed and a later call still find the profile gone.

        The website listing comes off public pages; the API is what knows an
        account is deactivated, and it is only asked once there is mature
        content to resolve. That must end the user, not the run.
        """
        out = clean_cli_env / "out"
        make_user_dir(out, "alice")
        make_user_dir(out, "ghost")
        page = {"results": [web_item(), blocked_web_item()], "hasMore": False}
        monkeypatch.setattr(cli, "WebClient",
                            lambda: FakeWebClient(pages=[dict(page), dict(page)]))

        def api_page(client, endpoint, username, offset):
            if username == "ghost":
                raise api.UserNotFoundError('User "ghost" not found.')
            return {"results": [make_dev(
                url="https://www.deviantart.com/artist/art/Mature-Art-222222222",
                title="Mature Art")], "has_more": False}

        monkeypatch.setattr(listing, "_api_page", api_page)
        monkeypatch.setattr(downloads, "download_file", fake_download)
        set_argv(monkeypatch, "--web", "-o", str(out), "--client-id", "x",
                 "--client-secret", "y")
        cli.run()      # must not raise: one dead account is not the run's end

        stdout = capsys.readouterr().out
        assert 'User "ghost" not found.' in stdout
        assert "Skipping ghost" in stdout
        assert "All users synced" in stdout
        assert (out / "alice" / "api" / "Mature Art_222222222.png").is_file()

    def test_a_named_profile_gone_after_the_listing_still_exits(
            self, clean_cli_env, monkeypatch, both_routes):
        def gone(client, endpoint, username, offset):
            raise api.UserNotFoundError('User "ghost" not found.')

        monkeypatch.setattr(listing, "_api_page", gone)
        monkeypatch.setattr(downloads, "download_file", fake_download)
        set_argv(monkeypatch, "ghost", "--web", "-o", str(clean_cli_env / "out"),
                 "--client-id", "x", "--client-secret", "y")
        with pytest.raises(SystemExit, match="does not exist"):
            cli.run()

    def test_explicit_profile_with_empty_gallery_still_exits(
            self, clean_cli_env, monkeypatch, galleries):
        set_argv(monkeypatch, "someartist", "-o", str(clean_cli_env / "out"),
                 "--client-id", "x", "--client-secret", "y")
        with pytest.raises(SystemExit, match="empty"):
            cli.run()

    def test_sync_reuses_manifest_and_skips_existing(self, clean_cli_env,
                                                     monkeypatch, galleries,
                                                     capsys):
        out = clean_cli_env / "out"
        gallery_dir = make_user_dir(
            out, "alice", content=json.dumps({"ABCD1234": "My Art_abcd1234.png"}))
        (gallery_dir / "My Art_abcd1234.png").write_bytes(b"x")
        galleries["alice"] = [
            make_dev(),
            make_dev(deviationid="ffffeeee-0000", title="New Work"),
        ]

        set_argv(monkeypatch, "-o", str(out), "--client-id", "x",
                 "--client-secret", "y")
        cli.run()

        # The legacy flat file is still recognised; the new one lands in api/
        assert (gallery_dir / "api" / "New Work_ffffeeee.png").is_file()
        stdout = capsys.readouterr().out
        assert "Downloaded: 1" in stdout
        assert "Skipped (already existed): 1" in stdout


class TestConfirm:
    @pytest.fixture
    def at_a_terminal(self, monkeypatch):
        """Pretend stdin is a real terminal, so the prompt is actually asked."""
        monkeypatch.setattr(cli.sys, "stdin", io.StringIO())
        monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)

    @pytest.mark.parametrize("answer,expected", [
        ("y", True), ("Y", True), ("yes", True), (" YES ", True),
        ("n", False), ("", False), ("nope", False), ("maybe", False),
    ])
    def test_only_an_explicit_yes_confirms(self, at_a_terminal, monkeypatch,
                                           answer, expected):
        monkeypatch.setattr("builtins.input", lambda prompt="": answer)
        assert cli.confirm("Go?") is expected

    def test_a_scripted_run_is_never_asked(self, monkeypatch):
        def unreachable(prompt=""):
            raise AssertionError("a non-interactive run must not prompt")

        monkeypatch.setattr("builtins.input", unreachable)
        # pytest already replaces stdin with a non-tty stream.
        assert cli.confirm("Go?") is True

    def test_stdin_closing_mid_prompt_declines(self, at_a_terminal, monkeypatch):
        def closed(prompt=""):
            raise EOFError

        monkeypatch.setattr("builtins.input", closed)
        assert cli.confirm("Go?") is False


class TestFetchWatching:
    def watch_page(self, *names, has_more=False, next_offset=None):
        return {"results": [{"user": {"username": n}} for n in names],
                "has_more": has_more, "next_offset": next_offset}

    def test_walks_every_page_of_the_watchlist(self):
        client = FakeClient(user_mode=True, pages=[
            self.watch_page("alice", "bob", has_more=True, next_offset=50),
            self.watch_page("carol"),
        ])
        assert sync.fetch_watching(client) == ["alice", "bob", "carol"]
        endpoints = [endpoint for endpoint, _ in client.calls]
        assert endpoints == ["user/friends"] * 2
        # No username: the endpoint then answers for the account behind the token.
        assert "username" not in client.calls[0][1]
        assert [params["offset"] for _, params in client.calls] == [0, 50]

    def test_falls_back_to_a_computed_offset(self):
        client = FakeClient(user_mode=True, pages=[
            self.watch_page("alice", has_more=True),   # no next_offset
            self.watch_page("bob"),
        ])
        assert sync.fetch_watching(client) == ["alice", "bob"]
        assert client.calls[1][1]["offset"] == sync.WATCH_PAGE_LIMIT

    def test_entries_without_a_username_are_dropped(self):
        client = FakeClient(user_mode=True,
                            pages=[{"results": [{"user": {"username": "alice"}},
                                                {"user": None}, {}]}])
        assert sync.fetch_watching(client) == ["alice"]

    def test_without_a_saved_session_exits(self):
        client = FakeClient(user_mode=False)
        with pytest.raises(SystemExit, match="run --login first"):
            sync.fetch_watching(client)
        assert client.calls == []       # no quota spent on a doomed request

    def test_watching_nobody_exits(self):
        client = FakeClient(user_mode=True, pages=[self.watch_page()])
        with pytest.raises(SystemExit, match="not watching anybody"):
            sync.fetch_watching(client)


class TestWatchingRun:
    def test_syncs_every_watched_user(self, clean_cli_env, monkeypatch,
                                      galleries, capsys):
        out = clean_cli_env / "out"
        galleries["alice"] = [make_dev()]
        galleries["bob"] = [make_dev(deviationid="ffffeeee-0000", title="Bob Art")]
        monkeypatch.setattr(cli, "fetch_watching", lambda client: ["alice", "bob"])

        set_argv(monkeypatch, "--watching", "-o", str(out), "--client-id", "x",
                 "--client-secret", "y")
        cli.run()

        assert (out / "alice" / "api" / "My Art_abcd1234.png").is_file()
        assert (out / "bob" / "api" / "Bob Art_ffffeeee.png").is_file()
        stdout = capsys.readouterr().out
        assert "You watch 2 user(s)" in stdout
        assert "All users synced. Downloaded: 2" in stdout

    def test_a_gone_profile_is_skipped_not_fatal(self, clean_cli_env, monkeypatch,
                                                 galleries, capsys):
        out = clean_cli_env / "out"
        galleries["alice"] = [make_dev()]     # "ghost" has no gallery at all
        monkeypatch.setattr(cli, "fetch_watching", lambda client: ["ghost", "alice"])

        set_argv(monkeypatch, "--watching", "-o", str(out), "--client-id", "x",
                 "--client-secret", "y")
        cli.run()

        assert (out / "alice" / "api" / "My Art_abcd1234.png").is_file()
        assert "Skipping ghost" in capsys.readouterr().out

    def test_declining_the_prompt_downloads_nothing(self, clean_cli_env,
                                                    monkeypatch, galleries, capsys):
        out = clean_cli_env / "out"
        galleries["alice"] = [make_dev()]
        monkeypatch.setattr(cli, "fetch_watching", lambda client: ["alice"])
        monkeypatch.setattr(cli, "confirm", lambda question: False)

        set_argv(monkeypatch, "--watching", "-o", str(out), "--client-id", "x",
                 "--client-secret", "y")
        cli.run()      # a decline is not an error: it returns normally

        assert not out.exists()
        stdout = capsys.readouterr().out
        assert "You watch 1 user(s)." in stdout
        assert "Cancelled." in stdout

    def test_the_prompt_names_the_count_and_the_output_folder(
            self, clean_cli_env, monkeypatch, galleries):
        out = clean_cli_env / "out"
        asked = []
        monkeypatch.setattr(cli, "fetch_watching", lambda client: ["alice", "bob"])
        monkeypatch.setattr(cli, "confirm",
                            lambda question: asked.append(question) or False)

        set_argv(monkeypatch, "--watching", "-o", str(out), "--client-id", "x",
                 "--client-secret", "y")
        cli.run()

        assert asked == [f"Download all 2 galleries into {out}?"]

    def test_gallery_does_not_combine(self, clean_cli_env, monkeypatch):
        set_argv(monkeypatch, "--watching", "-g", "Sketches", "--client-id", "x",
                 "--client-secret", "y")
        with pytest.raises(SystemExit, match="does not combine with --watching"):
            cli.run()

    def test_info_summarises_everyone_and_downloads_nothing(
            self, clean_cli_env, monkeypatch, no_downloads):
        seen, asked = {}, []
        monkeypatch.setattr(cli, "fetch_watching", lambda client: ["alice", "bob"])
        monkeypatch.setattr(cli, "print_profiles",
                            lambda client, web, names, **kw: seen.update(
                                names=names, **kw))
        monkeypatch.setattr(cli, "confirm",
                            lambda question: asked.append(question) or True)
        set_argv(monkeypatch, "--watching", "--info", "--client-id", "x",
                 "--client-secret", "y")
        cli.run()

        assert seen["names"] == ["alice", "bob"]
        assert seen["skip_missing"] is True     # a watchlist outlives its accounts
        # The prompt reflects what actually happens: no download, no output folder.
        assert asked == ["Show the profile of all 2 of them?"]

    def test_it_combines_with_redownload_blurred(self, clean_cli_env, monkeypatch,
                                                 galleries, logged_in, capsys):
        """Replacing blurred copies across a whole watchlist is the likeliest
        way to need it: nobody logs in before their first mature download."""
        out = clean_cli_env / "out"
        asked = []
        # Both have a mature work on disk, so both are worth walking.
        for name in ("alice", "bob"):
            make_user_dir(out, name,
                          content=json.dumps({"ABCD1234": "api/My Art_abcd1234.png"}))
        monkeypatch.setattr(cli, "fetch_watching", lambda client: ["alice", "bob"])
        monkeypatch.setattr(cli, "confirm", lambda question: True)
        galleries["alice"] = galleries["bob"] = [make_dev()]
        real = sync.process_deviation
        monkeypatch.setattr(sync, "process_deviation",
                            lambda *a, **kw: (asked.append(kw.get("redownload_blurred")),
                                              real(*a, **kw))[1])
        set_argv(monkeypatch, "--watching", "--redownload-blurred", "-o", str(out),
                 "--client-id", "x", "--client-secret", "y")
        cli.run()

        assert asked == [True, True]        # every watched user, not just the first
        assert "All users synced" in capsys.readouterr().out

    def test_users_with_nothing_blurrable_are_skipped_before_any_request(
            self, clean_cli_env, monkeypatch, galleries, logged_in, capsys):
        """A repair pass over a long watchlist should only walk what it can fix."""
        out = clean_cli_env / "out"
        make_user_dir(out, "allages",
                      content=json.dumps({"1111": "web/Ordinary_1111.jpg"}))
        make_user_dir(out, "mature",
                      content=json.dumps({"ABCD1234": "api/My Art_abcd1234.png"}))
        make_user_dir(out, "untouched", content="{}")
        synced = []
        monkeypatch.setattr(cli, "sync_gallery",
                            lambda client, username, *a, **kw:
                            synced.append(username) or sync.new_stats())
        monkeypatch.setattr(cli, "fetch_watching",
                            lambda client: ["allages", "mature", "untouched"])
        monkeypatch.setattr(cli, "confirm", lambda question: True)
        set_argv(monkeypatch, "--watching", "--redownload-blurred", "-o", str(out),
                 "--client-id", "x", "--client-secret", "y")
        cli.run()

        # The other two never reach the sync, so they cost no request at all and
        # stay out of the per-user table.
        assert synced == ["mature"]
        stdout = capsys.readouterr().out
        assert "Nothing downloaded through the API for 2 of 3 user(s)" in stdout
        assert "allages" not in stdout and "untouched" not in stdout

    def test_with_a_profile_exits(self, clean_cli_env, monkeypatch):
        set_argv(monkeypatch, "--watching", "someartist", "--client-id", "x",
                 "--client-secret", "y")
        with pytest.raises(SystemExit, match="drop the profile argument"):
            cli.run()

    def test_login_then_watching_does_not_stop_at_the_login(
            self, clean_cli_env, monkeypatch, galleries, capsys):
        out = clean_cli_env / "out"
        galleries["alice"] = [make_dev()]
        monkeypatch.setattr(cli, "login", lambda client: None)
        monkeypatch.setattr(cli, "fetch_watching", lambda client: ["alice"])

        set_argv(monkeypatch, "--watching", "--login", "-o", str(out),
                 "--client-id", "x", "--client-secret", "y")
        cli.run()

        assert (out / "alice" / "api" / "My Art_abcd1234.png").is_file()


class TestMain:
    def test_api_error_exits_with_message(self, monkeypatch):
        def boom():
            raise api.ApiError("rate limited forever")

        monkeypatch.setattr(cli, "run", boom)
        with pytest.raises(SystemExit, match="rate limited forever"):
            cli.main()

    def test_gallery_not_found_exits_with_message(self, monkeypatch):
        def boom():
            raise listing.GalleryNotFoundError("artist", "Nope", ["Sketches"])

        monkeypatch.setattr(cli, "run", boom)
        with pytest.raises(SystemExit, match="no gallery folder named"):
            cli.main()

    def test_keyboard_interrupt_exits_130(self, monkeypatch, capsys):
        def interrupt():
            raise KeyboardInterrupt

        monkeypatch.setattr(cli, "run", interrupt)
        with pytest.raises(SystemExit) as excinfo:
            cli.main()
        assert excinfo.value.code == 130


class TestQuiet:
    """-q drops progress, keeps results, warnings, errors and prompts."""

    def run_gallery(self, monkeypatch, galleries, out, *extra):
        galleries["artist"] = [make_dev(),
                               make_dev(deviationid="ffffeeee-0000", title="Second")]
        set_argv(monkeypatch, "artist", "-o", str(out),
                 "--client-id", "x", "--client-secret", "y", *extra)
        cli.run()

    def test_progress_lines_are_dropped_but_the_summary_stays(
            self, clean_cli_env, monkeypatch, galleries, capsys):
        self.run_gallery(monkeypatch, galleries, clean_cli_env / "out", "-q")
        out = capsys.readouterr().out
        assert "[1/2]" not in out and "[2/2]" not in out   # per-work progress
        assert "Fetching gallery listing" not in out
        assert "Total works found" not in out
        assert "Downloaded: api/" not in out               # nor the ones that worked
        assert "Done. Downloaded: 2" in out                # the result survives
        assert "Files saved to:" in out

    def test_without_quiet_the_progress_is_there(self, clean_cli_env, monkeypatch,
                                                 galleries, capsys):
        self.run_gallery(monkeypatch, galleries, clean_cli_env / "out")
        out = capsys.readouterr().out
        assert "[1/2]" in out and "Total works found" in out

    def test_the_works_that_failed_are_still_named(self, clean_cli_env,
                                                    monkeypatch, capsys):
        """-q hides what succeeded, never what went wrong."""
        monkeypatch.setattr(listing, "fetch_gallery",
                            lambda client, username, **kw: [
                                make_dev(),
                                make_dev(deviationid="ffffeeee-0000",
                                         title="Journal", content=None)])
        monkeypatch.setattr(downloads, "download_file",
                            lambda session, url, dest, fallback=None: False)
        set_argv(monkeypatch, "artist", "-o", str(clean_cli_env / "out"), "-q",
                 "--client-id", "x", "--client-secret", "y")
        cli.run()
        out = capsys.readouterr().out
        assert "FAILED: api/My Art_abcd1234.png" in out      # named, not just counted
        assert "NO FILE (no text or media): Journal" in out
        assert "Failed: 1" in out and "No file: 1" in out

    def test_the_footer_names_the_route_of_each_work(self, clean_cli_env,
                                                     monkeypatch, both_routes):
        """Under -q the footer is the only progress, and the routes differ:
        the API one is metered and paced, the website one is free."""
        monkeypatch.setattr(downloads, "download_file", fake_download)
        shown = []
        monkeypatch.setattr(sync, "set_progress", shown.append)

        set_argv(monkeypatch, "artist", "--web", "-q", "-w", "1",
                 "-o", str(clean_cli_env / "out"),
                 "--client-id", "x", "--client-secret", "y")
        cli.run()

        assert "listing artist" in shown[0]
        works = [line for line in shown if "/2" in line]
        assert any("web" in line and "Web Art" in line for line in works)
        assert any("api" in line and "Mature Art" in line for line in works)

    def test_info_still_prints_the_summary(self, clean_cli_env, monkeypatch,
                                           capsys, no_downloads):
        monkeypatch.setattr(cli, "print_profiles",
                            lambda *a, **kw: print("Profile: artist"))
        set_argv(monkeypatch, "artist", "-q", "--info",
                 "--client-id", "x", "--client-secret", "y")
        cli.run()
        out = capsys.readouterr().out
        assert "Profile: artist" in out          # -q must not gut --info
        assert "Fetching profile info" not in out

    def test_the_watching_prompt_is_never_silenced(self, clean_cli_env,
                                                   monkeypatch, capsys):
        asked = []
        monkeypatch.setattr(cli, "fetch_watching", lambda client: ["alice"])
        monkeypatch.setattr(cli, "confirm",
                            lambda question: asked.append(question) or False)
        set_argv(monkeypatch, "--watching", "-q", "-o", str(clean_cli_env / "out"),
                 "--client-id", "x", "--client-secret", "y")
        cli.run()
        assert asked and "Download all 1 galleries" in asked[0]
        assert "You watch 1 user(s)." in capsys.readouterr().out

    def test_da_quiet_turns_it_on_without_the_flag(self, clean_cli_env,
                                                   monkeypatch, galleries, capsys):
        monkeypatch.setenv("DA_QUIET", "true")
        self.run_gallery(monkeypatch, galleries, clean_cli_env / "out")
        assert "[1/2]" not in capsys.readouterr().out
