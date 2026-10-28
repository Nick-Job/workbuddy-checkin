import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from workbuddy_checkin.credentials import Credential, CredentialStore, looks_like_refresh_token


class CredentialStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "checkin-rt.json"
        self.store = CredentialStore(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_round_trip_and_private_permissions(self):
        self.store.save(Credential(refresh_token="rt-123", source="test", domain="www.codebuddy.cn"))
        loaded = self.store.load()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.refresh_token, "rt-123")
        self.assertEqual(loaded.source, "test")
        self.assertEqual(loaded.domain, "www.codebuddy.cn")
        mode = stat.S_IMODE(self.path.stat().st_mode)
        self.assertEqual(mode & 0o077, 0)

    def test_reads_legacy_rt_key(self):
        self.path.write_text(json.dumps({"rt": "legacy-token"}), encoding="utf-8")
        loaded = self.store.load()
        self.assertEqual(loaded.refresh_token, "legacy-token")

    def test_clear_keeps_private_file(self):
        self.store.save(Credential(refresh_token="rt-123"))
        self.assertTrue(self.store.clear())
        self.assertIsNone(self.store.load())
        self.assertTrue(self.path.exists())
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode) & 0o077, 0)

    def test_info_never_exposes_token(self):
        token = "a" * 100 + "." + "b" * 100 + "." + "c" * 100
        self.store.save(Credential(refresh_token=token))
        info = self.store.info()
        self.assertTrue(info["configured"])
        self.assertNotIn(token, json.dumps(info))
        self.assertIn("...", info["token_masked"])

    def test_token_shape(self):
        self.assertTrue(looks_like_refresh_token("a" * 100 + "." + "b" * 100 + "." + "c" * 100))
        self.assertFalse(looks_like_refresh_token("short"))
        self.assertFalse(looks_like_refresh_token("a" * 100 + "." + "b" * 100))


if __name__ == "__main__":
    unittest.main()
