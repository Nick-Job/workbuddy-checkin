import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from workbuddy_checkin.oauth import OAuthError, OAuthFlow, StateExpired


class OAuthHandler(BaseHTTPRequestHandler):
    state = "state-1"
    token_payload = {
        "code": 0,
        "data": {
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
            "expiresIn": 3600,
            "domain": "www.codebuddy.cn",
        },
    }

    def log_message(self, fmt, *args):
        return

    def _send(self, status, payload, cookie=False):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if cookie:
            self.send_header("Set-Cookie", "tgw_l7_route=abc; Path=/; HttpOnly")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path.startswith("/v2/plugin/auth/state"):
            self._send(
                200,
                {
                    "code": 0,
                    "data": {
                        "state": self.state,
                        "authUrl": "https://example.invalid/login?state=" + self.state,
                    },
                },
                cookie=True,
            )
            return
        self._send(404, {"code": 404})

    def do_GET(self):
        if self.path.startswith("/v2/plugin/auth/token"):
            if "state=expired" in self.path:
                self._send(200, {"code": 11218, "msg": "state expired"})
                return
            cookie = self.headers.get("Cookie") or ""
            if "tgw_l7_route=abc" not in cookie:
                self._send(200, {"code": 11217, "msg": "login ing..."})
                return
            self._send(200, self.token_payload)
            return
        self._send(404, {"code": 404})


class OAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), OAuthHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:%d" % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def flow(self):
        return OAuthFlow(base_url=self.base, origin=self.base, timeout=2)

    def test_start_poll_restores_cookie(self):
        flow = self.flow()
        session = flow.start()
        self.assertEqual(session.state, "state-1")
        self.assertTrue(session.cookies)
        token = flow.poll(session)
        self.assertIsNotNone(token)
        self.assertEqual(token.access_token, "access-token")
        self.assertEqual(token.refresh_token, "refresh-token")
        self.assertEqual(token.domain, "www.codebuddy.cn")

    def test_poll_without_cookie_is_pending(self):
        flow = self.flow()
        session = flow.start()
        session.cookies = []
        self.assertIsNone(flow.poll(session))

    def test_expired_state(self):
        flow = self.flow()
        session = flow.start()
        session.state = "expired"
        with self.assertRaises(StateExpired):
            flow.poll(session)

    def test_wait_returns_token(self):
        flow = self.flow()
        session = flow.start()
        token = flow.wait(session, timeout=2, interval=0.01, sleep=lambda seconds: None)
        self.assertEqual(token.access_token, "access-token")

    def test_wait_timeout(self):
        flow = self.flow()
        session = flow.start()
        session.cookies = []
        with self.assertRaises(OAuthError):
            flow.wait(session, timeout=0.01, interval=0.01, sleep=lambda seconds: None)


if __name__ == "__main__":
    unittest.main()
