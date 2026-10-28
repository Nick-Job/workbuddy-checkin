from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

from .oauth import TokenBundle, USER_AGENT

BILLING_BASE = "https://www.codebuddy.cn/v2"
TRAVEL_BASE = "https://www.workbuddy.cn"
PLUGIN_BASE = "https://copilot.tencent.com"


class ApiNetworkError(RuntimeError):
    pass


def _decode(raw: bytes) -> Any:
    text = raw.decode("utf-8", "replace")
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except ValueError:
        return {"raw": text}


class WorkBuddyAPI:
    def __init__(
        self,
        access_token: str,
        billing_base: str = BILLING_BASE,
        travel_base: str = TRAVEL_BASE,
        plugin_base: str = PLUGIN_BASE,
        timeout: float = 15.0,
        opener: Optional[urllib.request.OpenerDirector] = None,
    ) -> None:
        self.access_token = access_token
        self.billing_base = billing_base.rstrip("/")
        self.travel_base = travel_base.rstrip("/")
        self.plugin_base = plugin_base.rstrip("/")
        self.timeout = timeout
        self.opener = opener or urllib.request.build_opener()

    def _request(
        self,
        method: str,
        url: str,
        payload: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        auth: bool = True,
    ) -> Tuple[int, Any]:
        body = json.dumps(payload if payload is not None else {}).encode("utf-8") if method != "GET" else None
        request_headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }
        if auth:
            request_headers["Authorization"] = "Bearer " + self.access_token
        if headers:
            request_headers.update(headers)
        request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return response.status, _decode(response.read())
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, _decode(exc.read())
            except Exception:
                return exc.code, {"msg": str(exc.reason)}
        except urllib.error.URLError as exc:
            raise ApiNetworkError("network error: %s" % exc.reason) from exc

    def account(self, state: str) -> Tuple[int, Any]:
        url = self.plugin_base + "/v2/plugin/login/account?" + urllib.parse.urlencode({"state": state})
        return self._request("GET", url)

    def checkin_status(self) -> Tuple[int, Any]:
        return self._request("POST", self.billing_base + "/billing/meter/checkin-activity-status")

    def checkin(self) -> Tuple[int, Any]:
        return self._request("POST", self.billing_base + "/billing/meter/daily-checkin")

    def travel_status(self) -> Tuple[int, Any]:
        return self._request("GET", self.travel_base + "/activity/growth/buddy/travel/status")

    def travel_config(self) -> Tuple[int, Any]:
        return self._request("GET", self.travel_base + "/activity/growth/buddy/travel/config")

    def travel_claim(self) -> Tuple[int, Any]:
        return self._request("POST", self.travel_base + "/activity/growth/buddy/travel/claim")

    def travel_depart(self, location_id: int) -> Tuple[int, Any]:
        return self._request(
            "POST",
            self.travel_base + "/activity/growth/buddy/travel/depart",
            payload={"location_id": location_id},
        )


def refresh_tokens(
    refresh_token: str,
    plugin_base: str = PLUGIN_BASE,
    timeout: float = 15.0,
    opener: Optional[urllib.request.OpenerDirector] = None,
) -> Tuple[int, Any]:
    request = urllib.request.Request(
        plugin_base.rstrip("/") + "/v2/plugin/auth/token/refresh",
        data=b"{}",
        method="POST",
        headers={
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "X-Refresh-Token": refresh_token,
            "X-Auth-Refresh-Source": "plugin",
            "X-Domain": "copilot.tencent.com",
            "User-Agent": USER_AGENT,
        },
    )
    active_opener = opener or urllib.request.build_opener()
    try:
        with active_opener.open(request, timeout=timeout) as response:
            return response.status, _decode(response.read())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, _decode(exc.read())
        except Exception:
            return exc.code, {"msg": str(exc.reason)}
    except urllib.error.URLError as exc:
        raise ApiNetworkError("network error: %s" % exc.reason) from exc


def parse_refresh_response(status: int, body: Any) -> TokenBundle:
    if not isinstance(body, dict):
        raise ValueError("refresh response is not an object")
    if str(body.get("code")) != "0":
        message = body.get("msg") or body.get("message") or ("HTTP %s" % status)
        raise ValueError(str(message))
    data = body.get("data")
    if not isinstance(data, dict) or not data.get("accessToken") or not data.get("refreshToken"):
        raise ValueError("refresh response is missing tokens")
    return TokenBundle(
        access_token=str(data["accessToken"]),
        refresh_token=str(data["refreshToken"]),
        expires_in=data.get("expiresIn") if isinstance(data.get("expiresIn"), int) else None,
        domain=data.get("domain") if isinstance(data.get("domain"), str) else None,
    )
