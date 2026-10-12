import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import updater


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        (self.base / "versions" / "0.7-old").mkdir(parents=True)
        (self.base / "versions" / "0.7-old" / "version.txt").write_text("0.7", encoding="ascii")
        (self.base / "versions" / "0.7-old" / "app.py").write_text("# old", encoding="ascii")
        (self.base / "current.txt").write_text("0.7-old", encoding="ascii")
        (self.base / "data").mkdir()
        (self.base / "data" / "private.sqlite").write_bytes(b"private sentinel")

    def tearDown(self):
        self.temp.cleanup()

    def test_manifest_rejects_untrusted_package_url(self):
        raw = json.dumps({"schema": 1, "version": "0.8", "file": "crypto-desk-0.8.zip",
                          "url": "https://example.invalid/payload.zip", "sha256": "a" * 64}).encode()
        with self.assertRaises(ValueError):
            updater._manifest(raw)

    def test_archive_rejects_zip_slip_and_preserves_current_and_data(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name in updater.REQUIRED:
                archive.writestr("../outside" if name == "app.py" else name,
                                 "0.8" if name == "version.txt" else "# test")
        with self.assertRaises(ValueError):
            updater._install_archive(self.base, buffer.getvalue(), "0.8", "b" * 64)
        self.assertEqual((self.base / "current.txt").read_text(), "0.7-old")
        self.assertEqual((self.base / "data" / "private.sqlite").read_bytes(), b"private sentinel")
        self.assertFalse((self.base / "outside").exists())

    def test_bad_hash_leaves_current_and_data_unchanged(self):
        raw = json.dumps({"schema": 1, "version": "0.8", "file": "crypto-desk-0.8.zip",
                          "url": updater.PACKAGE_PREFIX + "crypto-desk-0.8.zip", "sha256": "a" * 64}).encode()
        with patch.object(updater, "_get", side_effect=[raw, b"wrong package"]):
            name, message, changed = updater.check_and_update(self.base)
        self.assertEqual(name, "0.7-old")
        self.assertFalse(changed)
        self.assertIn("uso la versione 0.7", message)
        self.assertEqual((self.base / "current.txt").read_text(), "0.7-old")
        self.assertEqual((self.base / "data" / "private.sqlite").read_bytes(), b"private sentinel")

    def test_current_release_skips_package_and_keeps_data(self):
        raw = json.dumps({"schema": 1, "version": "0.7", "file": "crypto-desk-0.7.zip",
                          "url": updater.PACKAGE_PREFIX + "crypto-desk-0.7.zip", "sha256": "a" * 64}).encode()
        with patch.object(updater, "_get", return_value=raw) as get:
            name, message, changed = updater.check_and_update(self.base)
        self.assertEqual(name, "0.7-old")
        self.assertFalse(changed)
        self.assertEqual(get.call_count, 1)
        self.assertEqual((self.base / "data" / "private.sqlite").read_bytes(), b"private sentinel")


if __name__ == "__main__":
    unittest.main()

