from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from .credentials import Credential, CredentialStore, looks_like_refresh_token, mask_token
from .oauth import OAuthError, OAuthFlow, OAuthSessionStore, StateExpired
from .service import CheckinRunner

CRON_MARKER = "# workbuddy-checkin"


def _print_json(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _state_file(value: Optional[str]) -> Optional[Path]:
    return Path(value).expanduser() if value else None


def _stores(args: argparse.Namespace) -> tuple:
    path = _state_file(getattr(args, "state_file", None))
    store = CredentialStore(path)
    session_path = store.path.with_name("oauth-session.json")
    return store, OAuthSessionStore(session_path)


def _human_result(result: Dict[str, Any]) -> None:
    status = result.get("status")
    if status == "need_login":
        print("需要登录：%s" % result.get("message", ""))
        return
    if result.get("ok"):
        print("✓ %s" % (result.get("message") or "完成"))
        if result.get("points") is not None:
            print("  本次积分：+%s" % result["points"])
        if result.get("balance") is not None:
            print("  当前余额：%s" % result["balance"])
        if result.get("streak_days") is not None:
            print("  连续签到：%s 天" % result["streak_days"])
    else:
        print("✗ %s" % (result.get("message") or "执行失败"))
    travel = result.get("travel")
    if isinstance(travel, dict):
        if travel.get("available"):
            parts = ["派猫猫旅行：%s" % (travel.get("state_text") or travel.get("state") or "未知")]
            if travel.get("location"):
                parts.append(str(travel["location"]))
            if travel.get("remaining_text"):
                parts.append("还需 %s" % travel["remaining_text"])
            print("  " + "，".join(parts))
            for line in travel.get("log") or []:
                print("  - %s" % line)
        elif travel.get("error"):
            print("  派猫猫旅行：不可用（%s）" % travel["error"])
    for warning in result.get("warnings") or []:
        print("  ! %s" % warning)


def cmd_login(args: argparse.Namespace) -> int:
    store, sessions = _stores(args)
    if args.no_wait and args.poll:
        print("--no-wait 与 --poll 不能同时使用", file=sys.stderr)
        return 2

    if args.token or args.token_file or args.token_stdin:
        sources = [bool(args.token), bool(args.token_file), bool(args.token_stdin)]
        if sum(sources) > 1:
            print("--token / --token-file / --token-stdin 只能选一个", file=sys.stderr)
            return 2
        try:
            if args.token_file:
                token = Path(args.token_file).expanduser().read_text(encoding="utf-8").strip()
            elif args.token_stdin:
                token = sys.stdin.read().strip()
            else:
                token = args.token.strip()
        except OSError as exc:
            print("读取令牌失败：%s" % exc, file=sys.stderr)
            return 2
        if not looks_like_refresh_token(token):
            print("这不像有效的长效令牌（应为较长的三段式 JWT）", file=sys.stderr)
            return 2
        store.save(Credential(refresh_token=token, source="manual"))
        result = {
            "status": "ok",
            "action": "token_saved",
            "message": "长效令牌已保存到 %s" % store.path,
            "credential": store.info(),
        }
        if args.json:
            _print_json(result)
        else:
            print(result["message"])
            print("令牌：%s" % mask_token(token))
        return 0

    session = sessions.load() if args.poll else None
    if args.poll and not session:
        print("没有待完成的授权会话，请先运行 wb-checkin login", file=sys.stderr)
        return 2

    if session is None:
        try:
            session = OAuthFlow().start()
        except OAuthError as exc:
            print("无法创建授权链接：%s" % exc, file=sys.stderr)
            return 1
        sessions.save(session)
        payload = {
            "status": "pending",
            "action": "authorize",
            "auth_url": session.auth_url,
            "state": session.state,
            "message": "请在浏览器中打开上面的链接完成登录，然后等待自动完成。",
            "expires_in": 600,
        }
        if args.json and args.no_wait:
            _print_json(payload)
        elif args.json:
            print(session.auth_url, file=sys.stderr)
        else:
            print(session.auth_url)
            print("请在浏览器中打开上面的链接完成登录；链接约 10 分钟内有效。", file=sys.stderr)
        if args.no_wait:
            return 0

    def pending(seconds_left: float) -> None:
        if not args.json:
            print("等待登录完成…（剩余约 %d 秒）" % int(seconds_left), file=sys.stderr)

    try:
        tokens = OAuthFlow().wait(
            session,
            timeout=args.timeout,
            interval=args.interval,
            on_pending=pending,
        )
    except StateExpired as exc:
        print("授权链接已过期，请重新运行 wb-checkin login", file=sys.stderr)
        return 1
    except OAuthError as exc:
        print("等待授权失败：%s" % exc, file=sys.stderr)
        return 1

    store.save(
        Credential(
            refresh_token=tokens.refresh_token,
            source="oauth",
            domain=tokens.domain,
        )
    )
    sessions.clear()
    result = {
        "status": "ok",
        "action": "login_complete",
        "message": "授权成功，长效令牌已保存到 %s" % store.path,
        "credential": store.info(),
    }
    if args.json:
        _print_json(result)
    else:
        print(result["message"])
        print("令牌：%s" % mask_token(tokens.refresh_token))
    return 0


def cmd_run(args: argparse.Namespace, check_only: bool = False) -> int:
    store, _ = _stores(args)
    runner = CheckinRunner(store=store)
    result = runner.run(
        travel=args.travel,
        check_only=check_only,
        no_checkin=args.no_checkin,
    )
    if args.json:
        _print_json(result)
    else:
        _human_result(result)
    if result.get("status") == "need_login":
        return 2
    return 0 if result.get("ok") else 1


def cmd_logout(args: argparse.Namespace) -> int:
    store, sessions = _stores(args)
    removed = store.clear()
    sessions.clear()
    result = {
        "status": "ok",
        "action": "logout",
        "message": "已清除本机凭据" if removed else "本机没有已保存的凭据",
    }
    if args.json:
        _print_json(result)
    else:
        print(result["message"])
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    store, _ = _stores(args)
    info = store.info()
    checks: List[Dict[str, Any]] = []
    checks.append({"name": "python", "ok": sys.version_info >= (3, 9), "detail": sys.version.split()[0]})
    checks.append({"name": "credential_configured", "ok": bool(info.get("configured")), "detail": info.get("path")})
    if info.get("exists"):
        checks.append({"name": "credential_private", "ok": bool(info.get("private")), "detail": info.get("mode")})
    result: Dict[str, Any] = {
        "status": "ok" if info.get("configured") else "need_login",
        "checks": checks,
        "credential": info,
    }
    if args.network and info.get("configured"):
        runner = CheckinRunner(store=store)
        result["network"] = runner.run(travel="readonly", check_only=True)
        if not result["network"].get("ok"):
            result["status"] = "error"
    if args.json:
        _print_json(result)
    else:
        for check in checks:
            print("%s %s：%s" % ("✓" if check["ok"] else "✗", check["name"], check.get("detail") or ""))
        if result.get("network"):
            print("网络检查：%s" % (result["network"].get("message") or result["network"].get("status")))
    return 0 if result["status"] == "ok" else 2


def _cron_lines() -> List[str]:
    try:
        proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    except FileNotFoundError:
        raise RuntimeError("系统没有 crontab 命令")
    if proc.returncode != 0:
        if "no crontab" in (proc.stderr or "").lower():
            return []
        raise RuntimeError(proc.stderr.strip() or "读取 crontab 失败")
    return proc.stdout.splitlines()


def _cron_write(lines: List[str]) -> None:
    content = "\n".join(lines).rstrip() + "\n"
    proc = subprocess.run(["crontab", "-"], input=content, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "写入 crontab 失败")


def _cron_command(args: argparse.Namespace, store: CredentialStore) -> str:
    src_dir = Path(__file__).resolve().parents[1]
    env_prefix = ""
    if src_dir.name == "src" and (src_dir / "workbuddy_checkin").is_dir():
        env_prefix = "PYTHONPATH=%s " % shlex.quote(str(src_dir))
    parts = [
        env_prefix + shlex.quote(sys.executable),
        "-m",
        "workbuddy_checkin",
        "run",
        "--travel",
        args.travel,
        "--json",
    ]
    if args.state_file:
        parts.extend(["--state-file", shlex.quote(str(store.path))])
    command = " ".join(parts)
    log_path = store.path.parent / "checkin.log"
    return "%s >> %s 2>&1 %s" % (command, shlex.quote(str(log_path)), CRON_MARKER)


def _parse_time(value: str) -> tuple:
    try:
        hour_text, minute_text = value.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (ValueError, AttributeError):
        raise ValueError("时间格式应为 HH:MM")
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("时间超出范围")
    return hour, minute


def cmd_cron(args: argparse.Namespace) -> int:
    store, _ = _stores(args)
    if args.action == "show":
        try:
            lines = _cron_lines()
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        matches = [line for line in lines if CRON_MARKER in line]
        if args.json:
            _print_json({"status": "ok", "lines": matches})
        elif matches:
            print("\n".join(matches))
        else:
            print("没有安装 workbuddy-checkin 定时任务")
        return 0

    if os.name == "nt":
        print("Windows 请使用任务计划程序（Task Scheduler）创建每日任务。", file=sys.stderr)
        print("命令：%s" % _cron_command(args, store), file=sys.stderr)
        return 2

    try:
        lines = [line for line in _cron_lines() if CRON_MARKER not in line]
        if args.action == "install":
            hour, minute = _parse_time(args.time)
            lines.append("%d %d * * * %s" % (minute, hour, _cron_command(args, store)))
        _cron_write(lines)
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    result = {
        "status": "ok",
        "action": "cron_%s" % args.action,
        "message": "定时任务已安装（每天 %s）" % args.time if args.action == "install" else "定时任务已移除",
        "command": _cron_command(args, store) if args.action == "install" else None,
    }
    if args.json:
        _print_json(result)
    else:
        print(result["message"])
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--state-file", help="凭据文件路径（默认 ~/.workbuddy/checkin-rt.json）")

    parser = argparse.ArgumentParser(
        prog="wb-checkin",
        description="WorkBuddy/CodeBuddy 每日签到与派猫猫旅行（独立实现）",
    )
    parser.add_argument("--version", action="version", version="workbuddy-checkin " + __version__)
    sub = parser.add_subparsers(dest="command")

    login = sub.add_parser("login", parents=[common], help="OAuth 登录或导入长效令牌")
    login.add_argument("--token", help="直接导入已有的 refresh token（会进入 shell 历史，建议用 --token-file）")
    login.add_argument("--token-file", help="从文件读取 refresh token")
    login.add_argument("--token-stdin", action="store_true", help="从标准输入读取 refresh token")
    login.add_argument("--no-wait", action="store_true", help="只输出授权链接，不等待")
    login.add_argument("--poll", action="store_true", help="继续等待已创建的授权会话")
    login.add_argument("--timeout", type=float, default=600.0, help="等待授权秒数（默认 600）")
    login.add_argument("--interval", type=float, default=5.0, help="轮询间隔秒数（默认 5）")
    login.add_argument("--json", action="store_true")
    login.set_defaults(func=cmd_login)

    run = sub.add_parser("run", parents=[common], help="执行签到（默认不派猫猫）")
    run.add_argument("--travel", choices=["off", "readonly", "auto"], default="off")
    run.add_argument("--check-only", action="store_true", help="只查询状态，不领取")
    run.add_argument("--no-checkin", action="store_true", help="跳过签到，只处理旅行")
    run.add_argument("--json", action="store_true")
    run.set_defaults(func=lambda args: cmd_run(args, check_only=args.check_only))

    status = sub.add_parser("status", parents=[common], help="查询签到与旅行状态")
    status.add_argument("--travel", choices=["off", "readonly", "auto"], default="readonly")
    status.add_argument("--no-checkin", action="store_true", help="跳过签到，只处理旅行")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=lambda args: cmd_run(args, check_only=True))

    logout = sub.add_parser("logout", parents=[common], help="清除本机凭据")
    logout.add_argument("--json", action="store_true")
    logout.set_defaults(func=cmd_logout)

    doctor = sub.add_parser("doctor", parents=[common], help="检查本机环境与凭据")
    doctor.add_argument("--network", action="store_true", help="联网验证令牌")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(func=cmd_doctor)

    cron = sub.add_parser("cron", parents=[common], help="安装/移除每日定时任务")
    cron.add_argument("action", choices=["install", "remove", "show"])
    cron.add_argument("--time", default="09:00", help="每天执行时间 HH:MM")
    cron.add_argument("--travel", choices=["off", "readonly", "auto"], default="auto")
    cron.add_argument("--json", action="store_true")
    cron.set_defaults(func=cmd_cron)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("已取消", file=sys.stderr)
        return 130
