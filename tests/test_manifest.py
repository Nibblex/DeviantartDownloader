"""The download record, including migration from API-only manifests."""

import json

from deviantart_downloader import manifest as manifest_mod

from .conftest import DEV_ID, WEB_ID, WEB_URL, make_dev


class TestDownloadManifest:
    def test_add_has_and_filename_for(self, tmp_path):
        manifest = manifest_mod.DownloadManifest(tmp_path)
        assert not manifest.has(DEV_ID)
        manifest.add(DEV_ID, "art.png")
        assert manifest.has(DEV_ID)
        assert manifest.filename_for(DEV_ID) == "art.png"
        # The key is the first 8 chars, case-insensitive
        assert manifest.has(DEV_ID.upper())

    def test_persists_across_instances(self, tmp_path):
        manifest_mod.DownloadManifest(tmp_path).add(DEV_ID, "art.png")
        reloaded = manifest_mod.DownloadManifest(tmp_path)
        assert reloaded.filename_for(DEV_ID) == "art.png"
        data = json.loads((tmp_path / "_downloaded.json").read_text(encoding="utf-8"))
        assert data == {"ABCD1234": "art.png"}

    def test_corrupt_manifest_warns_and_starts_empty(self, tmp_path, capsys):
        (tmp_path / "_downloaded.json").write_text("{not json", encoding="utf-8")
        manifest = manifest_mod.DownloadManifest(tmp_path)
        assert "WARNING" in capsys.readouterr().out
        assert not manifest.has(DEV_ID)

    def test_seeds_from_existing_files(self, tmp_path):
        (tmp_path / "Some Art_ABCD1234.png").write_bytes(b"x")
        (tmp_path / "lowercase_ffff0000.jpg").write_bytes(b"x")
        (tmp_path / "no-id-suffix.png").write_bytes(b"x")
        (tmp_path / "_metadata.json").write_text("[]", encoding="utf-8")
        (tmp_path / "partial_12345678.png.part").write_bytes(b"x")
        manifest = manifest_mod.DownloadManifest(tmp_path)
        assert manifest.filename_for(DEV_ID) == "Some Art_ABCD1234.png"
        assert manifest.has("ffff0000-aaaa")
        assert not manifest.has("12345678")


class TestManifestMigration:
    def test_reheys_uuid_entries_to_the_shared_key(self, tmp_path):
        manifest = manifest_mod.DownloadManifest(tmp_path)
        manifest.add(DEV_ID, "My Art_abcd1234.png")
        metadata = [make_dev(url=WEB_URL)]
        assert manifest.adopt_web_keys(metadata) == 1
        # The website route now recognises the work, and the file is untouched
        assert manifest.has(str(WEB_ID))
        assert manifest.filename_for(str(WEB_ID)) == "My Art_abcd1234.png"

    def test_is_idempotent(self, tmp_path):
        manifest = manifest_mod.DownloadManifest(tmp_path)
        manifest.add(DEV_ID, "My Art_abcd1234.png")
        metadata = [make_dev(url=WEB_URL)]
        manifest.adopt_web_keys(metadata)
        assert manifest.adopt_web_keys(metadata) == 0

    def test_leaves_entries_without_a_numeric_id_alone(self, tmp_path):
        manifest = manifest_mod.DownloadManifest(tmp_path)
        manifest.add(DEV_ID, "My Art_abcd1234.png")
        assert manifest.adopt_web_keys([make_dev()]) == 0
        assert manifest.has(DEV_ID)

    def test_seeds_from_route_subfolders(self, tmp_path):
        (tmp_path / "web").mkdir()
        (tmp_path / "web" / "Web Art_ABCD1234.jpg").write_bytes(b"x")
        manifest = manifest_mod.DownloadManifest(tmp_path)
        assert manifest.filename_for(DEV_ID) == "web/Web Art_ABCD1234.jpg"
