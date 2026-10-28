from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from http.cookiejar import Cookie, CookieJar
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .credentials import atomic_write_json, default_home

PLUGIN_BASE = "https://copilot.tencent.com"
LOGIN_ORIGIN = "https://www.codebuddy.cn"
USER_AGENT = "CLI/2.63.2 CodeBuddy/2.63.2"


class OAuthError(RuntimeError):
    def __init__(self, message: str, code: Any = None, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class StateExpired(OAuthError):
    pass


@dataclass
class TokenBundle:
    access_token: str
    refresh_token: str
    expires_in: Optional[int] = None
    domain: Optional[str] = None
    account: Optional[Dict[str, Any]] = None


@dataclass
class OAuthSession:
    state: str
    auth_url: str
    created_at: int = field(default_factory=lambda: int(time.time()))
    cookies: List[Dict[str, Any]] = field(default_factory=list)


class OAuthSessionStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path).expanduser() if path else default_home() / "oauth-session.json"

    def save(self, session: OAuthSession) -> None:
        atomic_write_json(
            self.path,
            {
                "state": session.state,
                "auth_url": session.auth_url,
                "created_at": session.created_at,
                "cookies": session.cookies,
            },
        )

    def load(self) -> Optional[OAuthSession]:
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict) or not raw.get("state") or not raw.get("auth_url"):
            return None
        cookies = raw.get("cookies")
        return OAuthSession(
            state=str(raw["state"]),
            auth_url=str(raw["auth_url"]),
            created_at=int(raw.get("created_at") or 0),
            cookies=cookies if isinstance(cookies, list) else [],
        )

    def clear(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass


def _cookie_to_dict(cookie: Cookie) -> Dict[str, Any]:
    return {
        "version": cookie.version,
        "name": cookie.name,
        "value": cookie.value,
        "port": cookie.port,
        "port_specified": cookie.port_specified,
        "domain": cookie.domain,
        "domain_specified": cookie.domain_specified,
        "domain_initial_dot": cookie.domain_initial_dot,
        "path": cookie.path,
        "path_specified": cookie.path_specified,
        "secure": cookie.secure,
        "expires": cookie.expires,
        "discard": cookie.discard,
        "rest": cookie._rest,
    }


def _dict_to_cookie(raw: Dict[str, Any]) -> Cookie:
    return Cookie(
        version=int(raw.get("version") or 0),
        name=str(raw.get("name") or ""),
        value=str(raw.get("value") or ""),
        port=raw.get("port"),
        port_specified=bool(raw.get("port_specified")),
        domain=str(raw.get("domain") or ""),
        domain_specified=bool(raw.get("domain_specified")),
        domain_initial_dot=bool(raw.get("domain_initial_dot")),
        path=str(raw.get("path") or "/"),
        path_specified=bool(raw.get("path_specified", True)),
        secure=bool(raw.get("secure")),
        expires=raw.get("expires"),
        discard=bool(raw.get("discard")),
        comment=None,
        comment_url=None,
        rest=raw.get("rest") if isinstance(raw.get("rest"), dict) else {},
        rfc2109=False,
    )


class OAuthFlow:
    def __init__(
        self,
        base_url: str = PLUGIN_BASE,
        origin: str = LOGIN_ORIGIN,
        user_agent: str = USER_AGENT,
        timeout: float = 20.0,
        opener: Optional[urllib.request.OpenerDirector] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.origin = origin.rstrip("/")
        self.user_agent = user_agent
        self.timeout = timeout
        self._fixed_opener = opener

    def _headers(self) -> Dict[str, str]:
        return {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": self.origin,
            "Referer": self.origin + "/",
            "User-Agent": self.user_agent,
        }

    def _build_opener(self, jar: CookieJar) -> urllib.request.OpenerDirector:
        if self._fixed_opener is not None:
            return self._fixed_opener
        return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def _read_json(self, response: Any) -> Dict[str, Any]:
        raw = response.read().decode("utf-8", "replace")
        try:
            body = json.loads(raw)
        except ValueError as exc:
            raise OAuthError("OAuth endpoint returned non-JSON data") from exc
        if not isinstance(body, dict):
            raise OAuthError("OAuth endpoint returned an unexpected payload")
        return body

    def start(self) -> OAuthSession:
        jar = CookieJar()
        opener = self._build_opener(jar)
        request = urllib.request.Request(
            self.base_url + "/v2/plugin/auth/state?platform=CLI",
            data=b"{}",
            headers=self._headers(),
            method="POST",
        )
        try:
            with opener.open(request, timeout=self.timeout) as response:
                body = self._read_json(response)
        except urllib.error.HTTPError as exc:
            raise OAuthError("could not create OAuth state", status=exc.code) from exc
        except urllib.error.URLError as exc:
            raise OAuthError("network error while creating OAuth state: %s" % exc.reason) from exc
        if str(body.get("code")) != "0":
            raise OAuthError(
                "could not create OAuth state: %s" % (body.get("msg") or body.get("message") or body.get("code")),
                code=body.get("code"),
            )
        data = body.get("data")
        if not isinstance(data, dict) or not data.get("state") or not data.get("authUrl"):
            raise OAuthError("OAuth state response is missing state/authUrl")
        return OAuthSession(
            state=str(data["state"]),
            auth_url=str(data["authUrl"]),
            cookies=[_cookie_to_dict(cookie) for cookie in jar],
        )

    def poll(self, session: OAuthSession) -> Optional[TokenBundle]:
        jar = CookieJar()
        for raw in session.cookies:
            try:
                jar.set_cookie(_dict_to_cookie(raw))
            except (KeyError, TypeError, ValueError):
                continue
        opener = self._build_opener(jar)
        url = self.base_url + "/v2/plugin/auth/token?" + urllib.parse.urlencode({"state": session.state})
        request = urllib.request.Request(url, headers=self._headers(), method="GET")
        try:
            with opener.open(request, timeout=self.timeout) as response:
                body = self._read_json(response)
        except urllib.error.HTTPError as exc:
            try:
                body = self._read_json(exc)
            except Exception:
                body = {"code": exc.code, "msg": str(exc.reason)}
        except urllib.error.URLError as exc:
            raise OAuthError("network error while polling OAuth token: %s" % exc.reason) from exc
        code = body.get("code")
        if str(code) == "0":
            data = body.get("data")
            if not isinstance(data, dict) or not data.get("accessToken") or not data.get("refreshToken"):
                raise OAuthError("OAuth token response is missing tokens")
            return TokenBundle(
                access_token=str(data["accessToken"]),
                refresh_token=str(data["refreshToken"]),
                expires_in=data.get("expiresIn") if isinstance(data.get("expiresIn"), int) else None,
                domain=data.get("domain") if isinstance(data.get("domain"), str) else None,
            )
        if str(code) == "11217":
            return None
        if str(code) == "11218":
            raise StateExpired("OAuth state expired or invalid", code=code)
        raise OAuthError("OAuth polling failed: %s" % (body.get("msg") or body.get("message") or code), code=code)

    def wait(
        self,
        session: OAuthSession,
        timeout: float = 600.0,
        interval: float = 5.0,
        on_pending: Optional[Callable[[float], None]] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> TokenBundle:
        deadline = time.monotonic() + timeout
        last_notice = 0.0
        while time.monotonic() < deadline:
            token = self.poll(session)
            if token is not None:
                return token
            now = time.monotonic()
            if on_pending and now - last_notice >= 15:
                on_pending(max(0.0, deadline - now))
                last_notice = now
            sleep(min(interval, max(0.1, deadline - time.monotonic())))
        raise OAuthError("timed out waiting for OAuth login")
