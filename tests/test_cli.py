"""End-to-end runs through the command line."""

import json

import pytest

from deviantart_downloader import api, cli, downloads, listing, sync

from .conftest import (BASE_URI, DEV_ID, FakeWebClient, blocked_web_item,
                       fake_download, make_dev, make_user_dir, set_argv,
                       web_item)


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
                 "--client-secret", "y", "--delay", "0", "-w", "5", "--api-workers", "3")
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

    def test_info_prints_and_downloads_nothing(self, clean_cli_env, monkeypatch):
        seen = []
        monkeypatch.setattr(cli, "print_profile",
                            lambda client, web, username: seen.append(username))

        def no_download(*a, **k):
            raise AssertionError("--info must not download anything")

        monkeypatch.setattr(downloads, "download_file", no_download)
        monkeypatch.setattr(listing, "fetch_gallery", no_download)
        set_argv(monkeypatch, "artist", "--client-id", "x", "--client-secret", "y",
                 "--info")
        cli.run()
        assert seen == ["artist"]

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
                 "--client-secret", "y", "--delay", "0", "-g", "sketches")
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
                 "--delay", "0", "-w", "2")
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

    def test_routes_each_source_into_its_own_folder(self, clean_cli_env,
                                                    monkeypatch, capsys):
        web = FakeWebClient(pages=[
            {"results": [web_item(), blocked_web_item()], "hasMore": False},
        ])
        monkeypatch.setattr(cli, "WebClient", lambda: web)
        # The mature work is only resolvable through the API listing
        api_entry = make_dev(
            url="https://www.deviantart.com/artist/art/Mature-Art-222222222",
            title="Mature Art")
        monkeypatch.setattr(listing, "_api_page",
                            lambda client, endpoint, username, offset: {
                                "results": [api_entry], "has_more": False})
        fetched = []

        def recording_download(session, url, dest, fallback=None):
            fetched.append((session, url))
            dest.write_bytes(b"x")
            return True

        monkeypatch.setattr(downloads, "download_file", recording_download)
        out = clean_cli_env / "out"
        set_argv(monkeypatch, "artist", "--web", "-o", str(out),
                 "--client-id", "x", "--client-secret", "y", "--delay", "0",
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
                 "--client-id", "x", "--client-secret", "y", "--delay", "0")
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
                 "--client-secret", "y", "--delay", "0")
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
                "--client-secret", "y", "--delay", "0")
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
                 "--client-secret", "y", "--delay", "0", flag)
        cli.run()
        assert seen["full"] is True


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


class TestSyncAll:
    @pytest.fixture
    def galleries(self, monkeypatch):
        """Patch fetch_gallery/download_file; galleries dict drives the data."""
        galleries = {}
        monkeypatch.setattr(
            listing, "fetch_gallery",
            lambda client, username, **kw: galleries.get(username, []))

        monkeypatch.setattr(downloads, "download_file", fake_download)
        return galleries

    def test_syncs_every_downloaded_user(self, clean_cli_env, monkeypatch,
                                         galleries, capsys):
        out = clean_cli_env / "out"
        make_user_dir(out, "alice")
        make_user_dir(out, "bob")
        galleries["alice"] = [make_dev()]
        galleries["bob"] = [make_dev(deviationid="ffffeeee-0000", title="Bob Art")]

        set_argv(monkeypatch, "-o", str(out), "--client-id", "x",
                 "--client-secret", "y", "--delay", "0")
        cli.run()

        assert (out / "alice" / "api" / "My Art_abcd1234.png").is_file()
        assert (out / "bob" / "api" / "Bob Art_ffffeeee.png").is_file()
        stdout = capsys.readouterr().out
        assert "syncing 2 previously downloaded user(s)" in stdout
        assert "All users synced. Downloaded: 2" in stdout
        # The grand total breaks the downloads down by route and per user
        assert "via API:     2 item(s)" in stdout
        assert "Per user:" in stdout
        assert "alice  1 item(s) downloaded" in stdout
        assert "bob    1 item(s) downloaded" in stdout

    def test_empty_gallery_is_skipped_not_fatal(self, clean_cli_env, monkeypatch,
                                                galleries, capsys):
        out = clean_cli_env / "out"
        make_user_dir(out, "ghost")     # deactivated account: empty gallery
        make_user_dir(out, "alice")
        galleries["alice"] = [make_dev()]

        set_argv(monkeypatch, "-o", str(out), "--client-id", "x",
                 "--client-secret", "y", "--delay", "0")
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
                 "--client-secret", "y", "--delay", "0")
        cli.run()

        assert (out / "alice" / "api" / "My Art_abcd1234.png").is_file()
        stdout = capsys.readouterr().out
        assert 'User "ghost" not found.' in stdout
        assert "Skipping ghost" in stdout

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
                 "--client-secret", "y", "--delay", "0")
        cli.run()

        # The legacy flat file is still recognised; the new one lands in api/
        assert (gallery_dir / "api" / "New Work_ffffeeee.png").is_file()
        stdout = capsys.readouterr().out
        assert "Downloaded: 1" in stdout
        assert "Skipped (already existed): 1" in stdout


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
