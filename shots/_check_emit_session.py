"""阶段 0 spike：验证「同一 opencode session 跨 serve 续用」+「收窄配置是否生效」。

一次性脚本，验证完即删。检查项：
  1. 杀掉 serve1 后，新 serve2 的 GET /session/{id} 是否 200
  2. 两个 serve 上 GET /session/{id}/message 的条数是否一致
  3. 同一 session 第二轮换 agent=recording-emitter，能否复述第一轮读到的 token
  4. serve2 的收窄配置是否被加载（GET /agent 对比 planner/emitter 的 edit 规则）
  5. 收窄配置下能写出允许的那一个文件
  6. 同一会话里换 build agent 去写另一个文件，是否被运行时拒绝

用法（从仓库根目录）：
  .venv/Scripts/python.exe backend/_spike_emit_session.py
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

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from app.services.opencode_runner import OpenCodeRunner  # noqa: E402

BIN = r"C:\Users\11031\scoop\apps\opencode\1.18.31\opencode.exe"
MODEL = "cctq-codex/gpt-6-luna"
TOKEN = "ACSPIKE-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
APP_CONFIG = {
    "OPENCODE_BIN": BIN,
    "OPENCODE_CONFIG": None,
    "OPENCODE_SERVE_HOST": "127.0.0.1",
    "OPENCODE_PURE": False,
}
PLANNER = "recording-planner"
EMITTER = "recording-emitter"
ALLOWED = "tests/only.spec.ts"
FORBIDDEN = "tests/forbidden.spec.ts"


def model_ref():
    """POST /session 用 {id, providerID}；prompt 用 {providerID, modelID}，两者不同。"""
    provider, name = MODEL.split("/", 1)
    return {"id": name, "providerID": provider}


def prompt_model_ref():
    provider, name = MODEL.split("/", 1)
    return {"providerID": provider, "modelID": name}


def make_workspace():
    root = Path(tempfile.mkdtemp(prefix="ac-spike-"))
    (root / "package.json").write_text(
        json.dumps({"name": "spike", "devDependencies": {"@playwright/test": "^1.0.0"}}, indent=2),
        encoding="utf-8",
    )
    (root / "README.md").write_text(
        "# demo\n\nThis project is used for a capability spike.\n\naccess token: %s\n" % TOKEN,
        encoding="utf-8",
    )
    (root / "tests").mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=True)
    subprocess.run(["git", "add", "-A"], cwd=str(root), check=True)
    subprocess.run(
        ["git", "-c", "user.email=spike@local", "-c", "user.name=spike", "commit", "-qm", "init"],
        cwd=str(root),
        check=True,
    )
    return root


def kill(proc):
    if proc is None or proc.poll() is not None:
        return
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    try:
        proc.wait(timeout=10)
    except Exception:
        pass


def start(runner, workspace, config_path, tag):
    port = runner._free_port()
    proc = runner._start_serve(Path(workspace), config_path, port)
    base = "http://127.0.0.1:%d" % port
    runner._wait_serve_healthy(base, proc, 60)
    print("[%s] pid=%s port=%s config=%s" % (tag, proc.pid, port, config_path))
    return proc, base


def new_session(base, agent, title="AutoCase spike"):
    response = requests.post(base + "/session",
                             json={"title": title, "agent": agent, "model": model_ref()}, timeout=30)
    print("POST /session ->", response.status_code, (response.text or "")[:120])
    response.raise_for_status()
    return (response.json() or {}).get("id")


def wait_turn(base, session_id, timeout):
    deadline = time.monotonic() + timeout
    busy_deadline = time.monotonic() + 60
    seen_busy = False
    while True:
        status = requests.get(base + "/session/status", timeout=10).json() or {}
        if session_id in status:
            seen_busy = True
        elif seen_busy:
            return True
        elif time.monotonic() > busy_deadline:
            raise RuntimeError("派发后 60 秒都没有进入 busy")
        if time.monotonic() > deadline:
            raise RuntimeError("单轮超过 %ss" % timeout)
        time.sleep(1)


def prompt(base, session_id, agent, text, timeout=420):
    body = {"parts": [{"type": "text", "text": text}], "agent": agent, "model": prompt_model_ref()}
    response = requests.post(base + "/session/%s/prompt_async" % session_id, json=body, timeout=60)
    print("prompt_async(%s) -> %s %s" % (agent, response.status_code, (response.text or "")[:100]))
    if response.status_code not in (200, 202, 204):
        raise RuntimeError("派发失败：%s %s" % (response.status_code, response.text[:300]))
    wait_turn(base, session_id, timeout)


def messages(base, session_id):
    response = requests.get(base + "/session/%s/message" % session_id, timeout=60)
    if response.status_code != 200:
        return None
    return response.json() or []


def last_assistant_text(items):
    for message in reversed(items or []):
        if (message.get("info") or {}).get("role") != "assistant":
            continue
        texts = [p.get("text") or "" for p in (message.get("parts") or []) if p.get("type") == "text"]
        joined = "\n".join(t for t in texts if t).strip()
        if joined:
            return joined
    return ""


def tool_errors(items):
    found = []
    for message in items or []:
        for part in message.get("parts") or []:
            if part.get("type") != "tool":
                continue
            state = part.get("state") or {}
            error = state.get("error") or ""
            if state.get("status") in ("error", "failed") or error:
                found.append({"tool": part.get("tool"), "status": state.get("status"),
                              "error": str(error)[:400]})
    return found


def edit_rules(base, agent):
    items = requests.get(base + "/agent", timeout=20).json() or []
    for item in items:
        if item.get("name") != agent:
            continue
        rules = []
        for rule in item.get("permission") or []:
            if rule.get("permission") not in ("edit", "write"):
                continue
            rules.append("%s:%s=%s" % (rule.get("permission"), rule.get("pattern"), rule.get("action")))
        return rules
    return None


def dispatch(path):
    return json.dumps(
        {
            "framework": "playwright-ts",
            "target_path": path,
            "title": "spike flow",
            "steps": [
                {"no": 1, "type": "goto", "url": "https://example.com"},
                {"no": 2, "type": "click", "text": "更多信息",
                 "target": {"locator": "getByRole('link', { name: 'More information...' })",
                            "match_count": 1, "status": "unique"}},
            ],
        },
        ensure_ascii=False,
        indent=2,
    )


def main():
    print("binary:", BIN, "exists:", Path(BIN).is_file())
    try:
        version = subprocess.run([BIN, "--version"], capture_output=True, text=True, timeout=30)
        print("version:", (version.stdout or version.stderr or "").strip())
    except Exception as exc:
        print("version check failed:", exc)
    print("model:", MODEL, "token:", TOKEN)

    runner = OpenCodeRunner(APP_CONFIG)
    workspace = make_workspace()
    print("workspace:", workspace)
    print("config base:", runner._config_path())

    proc1 = proc2 = None
    drop1 = drop2 = None
    results = {}
    try:
        config1, drop1 = runner._emit_config(None, workspace)
        proc1, base1 = start(runner, workspace, config1, "serve1")
        session_id = new_session(base1, PLANNER)
        print("session:", session_id)
        print("serve1 emitter edit rules:", edit_rules(base1, EMITTER))

        prompt(base1, session_id, PLANNER,
               "Read the file README.md in this repository. Then reply with ONLY this JSON: "
               '{"token": "<the access token you found>"}',
               timeout=300)
        items1 = messages(base1, session_id) or []
        text1 = last_assistant_text(items1)
        print("\n--- round1 messages:", len(items1), "---")
        print(text1[:400])
        results["round1_token_read"] = TOKEN in text1
        results["round1_message_count"] = len(items1)

        kill(proc1)
        proc1 = None
        time.sleep(2)

        config2, drop2 = runner._emit_config(ALLOWED, workspace)
        # 额外把 build 的写权限也收窄，用来在同一个会话里逼出「运行时拒绝」。
        data = json.loads(Path(config2).read_text(encoding="utf-8"))
        data.setdefault("agent", {})["build"] = {
            "permission": {
                "edit": {"*": "deny", ALLOWED: "allow"},
                "write": {"*": "deny", ALLOWED: "allow"},
                "bash": {"*": "deny"},
            }
        }
        Path(config2).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        proc2, base2 = start(runner, workspace, config2, "serve2")
        probe = requests.get(base2 + "/session/%s" % session_id, timeout=30)
        print("\nGET /session/{id} on serve2 ->", probe.status_code)
        results["resume_status"] = probe.status_code
        print("serve2 emitter edit rules:", edit_rules(base2, EMITTER))
        print("serve2 build edit rules:", edit_rules(base2, "build"))

        items2 = messages(base2, session_id)
        before = len(items2 or [])
        print("messages on serve2 before round2:", before, "(round1 was %s)" % results["round1_message_count"])
        results["message_count_preserved"] = before >= results["round1_message_count"]

        if probe.status_code != 200:
            raise RuntimeError("serve2 看不到会话，后续无法验证")

        prompt(base2, session_id, EMITTER,
               "这是同一个会话的第二轮。先原样复述上一轮你在 README.md 里读到的那串 access token，"
               "然后按下面的 dispatch 写出测试文件（写成最小可编译的 Playwright 测试即可）。\n\n"
               "```json\n%s\n```" % dispatch(ALLOWED))
        items3 = messages(base2, session_id) or []
        text2 = last_assistant_text(items3)
        print("\n--- round2 (allowed) messages:", len(items3), "---")
        print(text2[:500])
        results["round2_recalled_token"] = TOKEN in text2
        results["write_allowed_ok"] = (workspace / ALLOWED).is_file()
        print("allowed file exists:", results["write_allowed_ok"])

        prompt(base2, session_id, "build",
               "请用 write 工具创建 %s，内容是 `export const forbidden = 2;`。"
               "直接执行，不要解释，不要改用其他路径。" % FORBIDDEN)
        items4 = messages(base2, session_id) or []
        text3 = last_assistant_text(items4)
        errors = tool_errors(items4)
        print("\n--- round3 (build, forbidden) messages:", len(items4), "---")
        print(text3[:500])
        for item in errors:
            print("tool error:", json.dumps(item, ensure_ascii=False))
        results["forbidden_blocked"] = not (workspace / FORBIDDEN).exists()
        results["perm_denied_seen"] = any(
            any(w in (item["error"] or "").lower()
                for w in ("permission", "denied", "deny", "reject", "not allowed"))
            for item in errors
        )
        print("forbidden file exists:", (workspace / FORBIDDEN).exists())
        print("permission error in tool result:", results["perm_denied_seen"])
    finally:
        kill(proc2)
        kill(proc1)
        for drop in (drop1, drop2):
            try:
                if drop:
                    drop()
            except Exception:
                pass

    checks = {
        "1. serve2 sees the session (200)": results.get("resume_status") == 200,
        "2. message count preserved": bool(results.get("message_count_preserved")),
        "3. second turn recalls round1 token": bool(results.get("round2_recalled_token")),
        "4. allowed path written under narrowed config": bool(results.get("write_allowed_ok")),
        "5. forbidden path blocked at runtime": bool(results.get("forbidden_blocked")),
        "6. permission denial visible in tool result": bool(results.get("perm_denied_seen")),
    }
    print("\n=== SPIKE RESULT ===")
    for name, ok in checks.items():
        print(("PASS  " if ok else "FAIL  ") + name)
    print("raw:", json.dumps(results, ensure_ascii=False))

    core = list(checks.values())[:5]
    if all(core):
        shutil.rmtree(workspace, ignore_errors=True)
        print("\nworkspace cleaned:", workspace)
        return 0
    print("\nworkspace kept for inspection:", workspace)
    return 1


if __name__ == "__main__":
    sys.exit(main())
