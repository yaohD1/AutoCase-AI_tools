"""用生产类 EmitSession 打真实 opencode：验证「规划轮 → 写轮」同会话链路。

和 `_check_emit_session.py` 的区别：那个手写 HTTP，验的是 opencode 的能力；
这个走 `emit_session.EmitSession`，验的是我们自己的实现（请求体形状、
busy 轮询、begin_write 的收窄与续用判定、关闭时机）。

用法（仓库根目录）：.venv/Scripts/python.exe shots/_check_emit_session_live.py
"""

import json
import random
import shutil
import string
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from app.services.opencode_runner import OpenCodeRunner  # noqa: E402
from app.services.recording import emit_session  # noqa: E402

BIN = r"C:\Users\11031\scoop\apps\opencode\1.18.31\opencode.exe"
MODEL = "cctq-codex/gpt-6-luna"
TOKEN = "LIVE-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
APP_CONFIG = {"OPENCODE_BIN": BIN, "OPENCODE_CONFIG": None,
              "OPENCODE_SERVE_HOST": "127.0.0.1", "OPENCODE_PURE": False}


def make_workspace():
    root = Path(tempfile.mkdtemp(prefix="ac-live-"))
    (root / "package.json").write_text(
        json.dumps({"name": "live", "devDependencies": {"@playwright/test": "^1.0.0"}}, indent=2),
        encoding="utf-8")
    (root / "README.md").write_text(
        "# demo\n\naccess token: %s\n" % TOKEN, encoding="utf-8")
    (root / "tests").mkdir()
    # 真实工作区必须是 git 仓库根（validate_workspace 强制）。opencode 的写权限
    # 按 worktree 算相对路径，非 git 目录算出来的路径一条白名单都命中不了。
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=True)
    subprocess.run(["git", "add", "-A"], cwd=str(root), check=True)
    subprocess.run(["git", "-c", "user.email=live@local", "-c", "user.name=live",
                    "commit", "-qm", "init"], cwd=str(root), check=True)
    return root


def main():
    print("binary:", BIN, "exists:", Path(BIN).is_file())
    print("token:", TOKEN)
    workspace = make_workspace()
    runner = OpenCodeRunner(APP_CONFIG)
    session = emit_session.EmitSession('live-check', workspace, 'playwright-ts', MODEL, runner)
    results = {}
    try:
        text1 = session.plan_turn(
            "Read README.md in this repository, then reply with ONLY this JSON: "
            '{"token": "<the access token you found>"}', timeout=300)
        print("\n--- plan_turn ---\n", text1[:300])
        results["plan_read_token"] = TOKEN in text1
        results["session_id"] = bool(session._session_id)

        scope = session.begin_write("tests/a.spec.ts")
        print("\nbegin_write ->", scope)
        results["scoped"] = bool(scope.get("scoped"))

        text2 = session.emitter_turn(
            "这是同一个会话的第二轮。先原样复述上一轮你在 README.md 里读到的那串 token，"
            "然后按下面的 dispatch 写一个最小可编译的 Playwright 测试。\n\n"
            "```json\n%s\n```" % json.dumps({
                "framework": "playwright-ts",
                "target_path": "tests/a.spec.ts",
                "title": "live flow",
                "steps": [
                    {"no": 1, "type": "goto", "url": "https://example.com"},
                    {"no": 2, "type": "click", "text": "更多信息",
                     "target": {"locator": "getByRole('link', { name: 'More information...' })",
                                "match_count": 1, "status": "unique"}},
                ],
            }, ensure_ascii=False, indent=2),
            timeout=420)
        print("\n--- emitter_turn ---\n", text2[:400])
        results["round2_recalled_token"] = TOKEN in text2
        results["wrote_file"] = (workspace / "tests/a.spec.ts").is_file()
        results["turns"] = len(session._turns)
        results["snapshot"] = session.snapshot()
        # 失败时把工具错误原样打出来：权限拒绝的规则原文在里面
        items = session._messages(session._active) or []
        for message in items:
            for part in message.get("parts") or []:
                state = part.get("state") or {}
                if part.get("type") == "tool" and (state.get("error") or state.get("status") in ("error", "failed")):
                    print("[tool error]", part.get("tool"), str(state.get("error"))[:2000])
        try:
            import requests as _rq
            base = session._active.base_url
            agents = _rq.get(base + "/agent", timeout=20).json() or []
            for item in agents:
                if item.get("name") == "recording-emitter":
                    print("[rules]", [r for r in item.get("permission") or []
                                      if r.get("permission") in ("edit", "write")])
            sess = _rq.get("%s/session/%s" % (base, session._session_id), timeout=20).json() or {}
            print("[session directory]", sess.get("directory"))
        except Exception as exc:
            print("[diag failed]", exc)
        print("[file exists]", (workspace / "tests/a.spec.ts").is_file(),
              "| dir exists:", (workspace / "tests").is_dir())
    finally:
        session.close('check')
        print("\nclosed; registry empty:", emit_session.REGISTRY.get('live-check') is None)

    checks = {
        "1. plan_turn reads the project and creates a session": results.get("plan_read_token") and results.get("session_id"),
        "2. begin_write switches to the narrowed serve": results.get("scoped"),
        "3. second turn recalls the first turn's token": results.get("round2_recalled_token"),
        "4. second turn writes the target file": results.get("wrote_file"),
        "5. two turns recorded": results.get("turns") == 2,
    }
    print("\n=== LIVE RESULT ===")
    for name, ok in checks.items():
        print(("PASS  " if ok else "FAIL  ") + name)
    print("raw:", json.dumps({k: v for k, v in results.items() if k != 'snapshot'},
                             ensure_ascii=False))
    print("snapshot:", json.dumps(results.get("snapshot"), ensure_ascii=False))

    if all(checks.values()):
        shutil.rmtree(workspace, ignore_errors=True)
        return 0
    print("workspace kept:", workspace)
    return 1


if __name__ == "__main__":
    sys.exit(main())
