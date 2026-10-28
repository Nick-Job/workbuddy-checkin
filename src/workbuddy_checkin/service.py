from __future__ import annotations

import random
import time
from typing import Any, Dict, List, Optional, Tuple

from .api import ApiNetworkError, WorkBuddyAPI, parse_refresh_response, refresh_tokens
from .credentials import Credential, CredentialStore


BALANCE_KEYS = (
    "total_credits",
    "total_credit",
    "total_credit_balance",
    "credits",
    "total_points",
    "points_balance",
    "credit_balance",
    "balance",
    "remain_credit",
    "remain_credits",
    "remain",
    "score",
    "integral",
    "totalCredits",
    "totalCredit",
    "pointsBalance",
    "balanceCredit",
)

TRAVEL_STATE_TEXT = {
    "idle": "空闲（可派遣）",
    "traveling": "旅行中",
    "arrived": "已到达，待领取",
}


def _code(body: Any) -> str:
    if isinstance(body, dict):
        value = body.get("code")
        return "" if value is None else str(value)
    return ""


def _data(body: Any) -> Dict[str, Any]:
    if isinstance(body, dict) and isinstance(body.get("data"), dict):
        return body["data"]
    return {}


def _message(body: Any, fallback: str = "") -> str:
    if isinstance(body, dict):
        return str(body.get("msg") or body.get("message") or fallback)
    return fallback


def extract_balance(body: Any) -> Optional[int]:
    if not isinstance(body, dict):
        return None
    containers = [body]
    for key in ("data", "result"):
        value = body.get(key)
        if isinstance(value, dict):
            containers.append(value)
            nested = value.get("result")
            if isinstance(nested, dict):
                containers.append(nested)
    for container in containers:
        for key in BALANCE_KEYS:
            value = container.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                return int(value)
            if isinstance(value, str) and value.strip().isdigit():
                return int(value.strip())
    return None


def _is_checked_in(body: Any) -> bool:
    if not isinstance(body, dict):
        return False
    if body.get("today_checked_in") is True:
        return True
    data = body.get("data")
    if isinstance(data, dict) and data.get("today_checked_in") is True:
        return True
    return _code(body) == "10001"


def _already_checked_in_message(body: Any) -> bool:
    text = _message(body)
    return _code(body) == "10001" or "已签到" in text or "今天已签到" in text


def _first_int(*values: Any) -> Optional[int]:
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
    return None


def _duration_text(seconds: Optional[int]) -> Optional[str]:
    if seconds is None:
        return None
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours:
        return "%d 小时 %d 分" % (hours, minutes)
    if minutes:
        return "%d 分钟" % minutes
    return "%d 秒" % seconds


def parse_travel(status: Any, config: Any = None) -> Dict[str, Any]:
    if not isinstance(status, dict):
        return {"available": False}
    state = status.get("state")
    remaining = None
    if state == "traveling":
        arrive = status.get("arrive_at")
        now = status.get("server_now")
        if isinstance(arrive, int) and isinstance(now, int):
            remaining = max(0, arrive - now)
    location = status.get("location")
    locations: List[Dict[str, Any]] = []
    if isinstance(config, dict) and isinstance(config.get("locations"), list):
        for item in config["locations"]:
            if isinstance(item, dict):
                locations.append({"id": item.get("id"), "name": item.get("name") or "未命名地点"})
    return {
        "available": True,
        "state": state,
        "state_text": TRAVEL_STATE_TEXT.get(state, state),
        "location": location.get("name") if isinstance(location, dict) else None,
        "reward_credit": status.get("reward_credit"),
        "remaining_seconds": remaining,
        "remaining_text": _duration_text(remaining),
        "daily_limit_reached": status.get("daily_limit_reached"),
        "locations": locations,
    }


class CheckinRunner:
    def __init__(
        self,
        store: Optional[CredentialStore] = None,
        api_factory: Any = None,
        refresh_fn: Any = None,
        sleep: Any = time.sleep,
    ) -> None:
        self.store = store or CredentialStore()
        self.api_factory = api_factory or (lambda token: WorkBuddyAPI(token))
        self.refresh_fn = refresh_fn or refresh_tokens
        self.sleep = sleep

    def _result(self) -> Dict[str, Any]:
        return {
            "ok": False,
            "status": "unknown",
            "action": None,
            "message": "",
            "points": None,
            "balance": None,
            "streak_days": None,
            "checkin": {},
            "travel": None,
            "credential": {},
            "warnings": [],
            "timestamp": int(time.time()),
        }

    def _refresh(self, credential: Credential, result: Dict[str, Any]) -> Optional[str]:
        try:
            http_status, body = self.refresh_fn(credential.refresh_token)
        except ApiNetworkError as exc:
            result.update(status="error", action="refresh_network_error", message=str(exc))
            return None
        except Exception as exc:
            result.update(status="error", action="refresh_error", message=str(exc)[:300])
            return None
        try:
            tokens = parse_refresh_response(http_status, body)
        except ValueError as exc:
            result.update(
                status="need_login",
                action="refresh_failed",
                message="长效令牌刷新失败：%s" % str(exc)[:300],
            )
            return None
        rotated = tokens.refresh_token != credential.refresh_token
        if rotated:
            try:
                self.store.save(
                    Credential(
                        refresh_token=tokens.refresh_token,
                        source="refresh",
                        domain=tokens.domain or credential.domain,
                    )
                )
            except OSError as exc:
                result["warnings"].append("刷新令牌已轮换但写入失败：%s" % str(exc)[:200])
        result["credential"] = {
            "configured": True,
            "source": "refresh" if rotated else credential.source,
            "masked": tokens.refresh_token[:6] + "..." + tokens.refresh_token[-4:],
            "rotated": rotated,
            "path": str(self.store.path),
        }
        return tokens.access_token

    def _checkin(self, api: WorkBuddyAPI, result: Dict[str, Any], check_only: bool) -> None:
        status_http, status_body = api.checkin_status()
        result["checkin"]["status_http"] = status_http
        result["checkin"]["status_code"] = _code(status_body)
        balance = extract_balance(status_body)
        if balance is not None:
            result["balance"] = balance
        data = _data(status_body)
        streak = _first_int(status_body.get("streak_days") if isinstance(status_body, dict) else None, data.get("streak_days"))
        if streak is not None:
            result["streak_days"] = streak

        status_ok = 200 <= status_http < 300 and _code(status_body) in ("", "0", "10001")
        if not status_ok:
            result["warnings"].append("签到状态查询失败：HTTP %s %s" % (status_http, _message(status_body)))
        if check_only:
            result.update(
                ok=status_ok,
                status="ok" if status_ok else "error",
                action="status",
                message="状态查询成功" if status_ok else "状态查询失败",
            )
            result["checkin"]["today_checked_in"] = _is_checked_in(status_body)
            return

        if _is_checked_in(status_body):
            result.update(
                ok=True,
                status="ok",
                action="skip_already_signed",
                message="今日已签到，无需重复领取",
            )
            result["checkin"]["today_checked_in"] = True
            return

        checkin_http, checkin_body = api.checkin()
        result["checkin"]["http"] = checkin_http
        result["checkin"]["code"] = _code(checkin_body)
        if _already_checked_in_message(checkin_body):
            result.update(
                ok=True,
                status="ok",
                action="skip_already_signed",
                message="今日已签到（接口返回幂等状态）",
            )
            result["checkin"]["today_checked_in"] = True
            return
        if 200 <= checkin_http < 300 and _code(checkin_body) in ("", "0", "200"):
            cdata = _data(checkin_body)
            credit = _first_int(
                checkin_body.get("credit") if isinstance(checkin_body, dict) else None,
                cdata.get("credit"),
                cdata.get("daily_credit"),
                cdata.get("today_credit"),
            )
            streak = _first_int(
                checkin_body.get("streak_days") if isinstance(checkin_body, dict) else None,
                cdata.get("streak_days"),
            )
            new_balance = extract_balance(checkin_body)
            result.update(
                ok=True,
                status="ok",
                action="clicked",
                points=credit,
                message="签到成功",
            )
            if new_balance is not None:
                result["balance"] = new_balance
            if streak is not None:
                result["streak_days"] = streak
            result["checkin"]["today_checked_in"] = True
            return
        result.update(
            ok=False,
            status="error",
            action="checkin_failed",
            message=_message(checkin_body, "HTTP %s（业务码 %s）" % (checkin_http, _code(checkin_body))),
        )

    def _travel_get(self, api: WorkBuddyAPI, kind: str) -> Tuple[bool, Any, str]:
        try:
            if kind == "status":
                http_status, body = api.travel_status()
            elif kind == "config":
                http_status, body = api.travel_config()
            else:
                raise ValueError("unknown travel request")
        except ApiNetworkError as exc:
            return False, None, str(exc)
        if http_status != 200 or not isinstance(body, dict) or _code(body) != "0":
            return False, None, "HTTP %s %s" % (http_status, _message(body))
        return True, body.get("data"), ""

    def _travel(self, api: WorkBuddyAPI, mode: str) -> Optional[Dict[str, Any]]:
        if mode == "off":
            return None
        available, status_data, error = self._travel_get(api, "status")
        if not available:
            return {"mode": mode, "available": False, "error": error}
        _, config_data, _ = self._travel_get(api, "config")
        travel = parse_travel(status_data, config_data)
        travel["mode"] = mode
        if mode == "readonly":
            return travel

        log: List[str] = []
        if travel.get("state") == "arrived":
            try:
                claim_http, claim_body = api.travel_claim()
                if claim_http == 200 and _code(claim_body) == "0":
                    reward = _data(claim_body).get("reward_credit")
                    log.append("已领取旅行奖励 +%s 积分" % (reward if reward is not None else "?"))
                else:
                    log.append("领取旅行奖励失败：%s" % _message(claim_body, "HTTP %s" % claim_http))
            except ApiNetworkError as exc:
                log.append("领取旅行奖励网络错误：%s" % str(exc))
            available, fresh, error = self._travel_get(api, "status")
            if available:
                status_data = fresh
                travel = parse_travel(status_data, config_data)
                travel["mode"] = mode

        if travel.get("daily_limit_reached"):
            log.append("今日派遣次数已用完，跳过派遣")
        elif travel.get("state") in (None, "", "idle"):
            locations = travel.get("locations") or []
            ids = [item.get("id") for item in locations if item.get("id") is not None]
            if not ids:
                log.append("未获取到可选地点，跳过派遣")
            else:
                location_id = random.choice(ids)
                try:
                    depart_http, depart_body = api.travel_depart(location_id)
                    if depart_http == 200 and _code(depart_body) == "0":
                        location = _data(depart_body).get("location") or {}
                        log.append("已派出 Buddy 前往【%s】" % (location.get("name") or location_id))
                    else:
                        log.append("派遣失败：%s" % _message(depart_body, "HTTP %s" % depart_http))
                except ApiNetworkError as exc:
                    log.append("派遣网络错误：%s" % str(exc))
        else:
            log.append("Buddy 正在旅行中，无需派遣")

        available, final, error = self._travel_get(api, "status")
        if available:
            travel = parse_travel(final, config_data)
            travel["mode"] = mode
        travel["log"] = log
        return travel

    def run(
        self,
        travel: str = "off",
        check_only: bool = False,
        no_checkin: bool = False,
    ) -> Dict[str, Any]:
        result = self._result()
        credential = self.store.load()
        if not credential:
            result.update(
                status="need_login",
                action="need_login",
                message="尚未配置长效令牌，请先运行 `wb-checkin login`",
            )
            result["credential"] = self.store.info()
            return result

        access_token = self._refresh(credential, result)
        if not access_token:
            result["credential"] = self.store.info()
            return result

        api = self.api_factory(access_token)
        if not no_checkin:
            try:
                self._checkin(api, result, check_only=check_only)
            except ApiNetworkError as exc:
                result.update(ok=False, status="error", action="network_error", message=str(exc))
        if travel != "off":
            try:
                result["travel"] = self._travel(api, travel)
            except ApiNetworkError as exc:
                result["travel"] = {"mode": travel, "available": False, "error": str(exc)}
        if not result.get("message"):
            if result.get("action") == "status":
                result["message"] = "状态查询成功"
            elif result.get("travel") and not no_checkin:
                result["message"] = "已完成"
        if no_checkin:
            travel_result = result.get("travel") or {}
            result["status"] = "ok" if travel_result.get("available") else "error"
            result["ok"] = bool(travel_result.get("available"))
            if result["status"] == "ok" and not result.get("message"):
                result["message"] = "旅行状态处理完成"
        else:
            result["ok"] = result.get("status") == "ok"
        return result
