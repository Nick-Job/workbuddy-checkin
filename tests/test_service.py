import tempfile
import unittest
from pathlib import Path

from workbuddy_checkin.credentials import Credential, CredentialStore
from workbuddy_checkin.service import CheckinRunner


class FakeAPI:
    def __init__(self):
        self.status_calls = 0
        self.claim_calls = 0
        self.depart_calls = 0
        self.departed_with = None
        self.checkin_calls = 0

    def checkin_status(self):
        return 200, {
            "code": 0,
            "data": {"today_checked_in": False, "streak_days": 4, "total_credits": 1200},
        }

    def checkin(self):
        self.checkin_calls += 1
        return 200, {"code": 0, "credit": 100, "streak_days": 5, "data": {"total_credits": 1300}}

    def travel_status(self):
        self.status_calls += 1
        if self.status_calls == 1:
            return 200, {"code": 0, "data": {"state": "arrived", "reward_credit": 8}}
        if self.status_calls == 2:
            return 200, {"code": 0, "data": {"state": "idle", "daily_limit_reached": False}}
        return 200, {
            "code": 0,
            "data": {
                "state": "traveling",
                "location": {"name": "咖啡馆"},
                "arrive_at": 1100,
                "server_now": 1000,
                "daily_limit_reached": False,
            },
        }

    def travel_config(self):
        return 200, {"code": 0, "data": {"locations": [{"id": 1, "name": "咖啡馆"}]}}

    def travel_claim(self):
        self.claim_calls += 1
        return 200, {"code": 0, "data": {"reward_credit": 8}}

    def travel_depart(self, location_id):
        self.depart_calls += 1
        self.departed_with = location_id
        return 200, {"code": 0, "data": {"location": {"id": 1, "name": "咖啡馆"}}}


class AlreadyCheckedAPI(FakeAPI):
    def checkin_status(self):
        return 200, {"code": 0, "data": {"today_checked_in": True, "streak_days": 7, "total_credits": 500}}


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "checkin-rt.json"
        self.store = CredentialStore(self.path)
        self.store.save(Credential(refresh_token="rt-old", source="test"))
        self.api = FakeAPI()

    def tearDown(self):
        self.temp.cleanup()

    def refresh_ok(self, token):
        return 200, {
            "code": 0,
            "data": {
                "accessToken": "access-new",
                "refreshToken": "rt-new",
                "expiresIn": 100,
                "domain": "www.codebuddy.cn",
            },
        }

    def runner(self):
        return CheckinRunner(
            store=self.store,
            api_factory=lambda token: self.api,
            refresh_fn=self.refresh_ok,
        )

    def test_checkin_and_rotate_token(self):
        result = self.runner().run(travel="off")
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "clicked")
        self.assertEqual(result["points"], 100)
        self.assertEqual(result["balance"], 1300)
        self.assertEqual(result["streak_days"], 5)
        self.assertEqual(self.store.load().refresh_token, "rt-new")
        self.assertEqual(self.api.checkin_calls, 1)

    def test_already_checked_in_skips_write(self):
        self.api = AlreadyCheckedAPI()
        result = self.runner().run(travel="off")
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "skip_already_signed")
        self.assertEqual(self.api.checkin_calls, 0)
        self.assertEqual(result["balance"], 500)

    def test_check_only(self):
        result = self.runner().run(travel="off", check_only=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "status")
        self.assertEqual(self.api.checkin_calls, 0)

    def test_travel_auto_claim_then_depart(self):
        result = self.runner().run(travel="auto")
        self.assertTrue(result["ok"])
        self.assertEqual(self.api.claim_calls, 1)
        self.assertEqual(self.api.depart_calls, 1)
        self.assertEqual(self.api.departed_with, 1)
        self.assertEqual(result["travel"]["state"], "traveling")
        self.assertEqual(result["travel"]["location"], "咖啡馆")
        self.assertEqual(result["travel"]["remaining_text"], "1 分钟")
        self.assertTrue(result["travel"]["log"])

    def test_travel_readonly_never_writes(self):
        result = self.runner().run(travel="readonly", check_only=True)
        self.assertEqual(self.api.claim_calls, 0)
        self.assertEqual(self.api.depart_calls, 0)
        self.assertEqual(result["travel"]["state"], "arrived")

    def test_travel_only(self):
        result = self.runner().run(travel="readonly", no_checkin=True)
        self.assertTrue(result["ok"])
        self.assertEqual(self.api.checkin_calls, 0)
        self.assertEqual(result["travel"]["state"], "arrived")

    def test_missing_credential(self):
        self.store.clear()
        result = self.runner().run(travel="off")
        self.assertEqual(result["status"], "need_login")
        self.assertFalse(result["ok"])

    def test_refresh_failure(self):
        runner = CheckinRunner(
            store=self.store,
            api_factory=lambda token: self.api,
            refresh_fn=lambda token: (401, {"code": 401, "msg": "invalid token"}),
        )
        result = runner.run(travel="off")
        self.assertEqual(result["status"], "need_login")
        self.assertIn("刷新失败", result["message"])


if __name__ == "__main__":
    unittest.main()
