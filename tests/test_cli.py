import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from workbuddy_checkin.cli import main


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name) / "checkin-rt.json"
        self.token = "a" * 100 + "." + "b" * 100 + "." + "c" * 100

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(list(args))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_import_token(self):
        code, out, err = self.invoke("login", "--token", self.token, "--state-file", str(self.state))
        self.assertEqual(code, 0, err)
        self.assertTrue(self.state.exists())
        self.assertNotIn(self.token, out)

    def test_reject_bad_token(self):
        code, out, err = self.invoke("login", "--token", "short", "--state-file", str(self.state))
        self.assertEqual(code, 2)
        self.assertIn("不像有效", err)
        self.assertFalse(self.state.exists())

    def test_status_without_credential_is_need_login(self):
        code, out, err = self.invoke("status", "--state-file", str(self.state), "--json")
        self.assertEqual(code, 2)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "need_login")

    def test_doctor_without_credential(self):
        code, out, err = self.invoke("doctor", "--state-file", str(self.state), "--json")
        self.assertEqual(code, 2)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "need_login")

    def test_logout(self):
        self.invoke("login", "--token", self.token, "--state-file", str(self.state))
        code, out, err = self.invoke("logout", "--state-file", str(self.state), "--json")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["action"], "logout")
        self.assertFalse(json.loads(self.state.read_text()) if self.state.exists() else {})


if __name__ == "__main__":
    unittest.main()
