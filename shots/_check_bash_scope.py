"""只测 bash 白名单的可用写法：哪些命令变体能过、哪些被拒。

要回答的是「模型正常的写法会不会被误杀」，不是安全审计：
  1. npx playwright test --list                    基准
  2. ... 2>&1 | tail -5                            管道（最常用）
  3. ... ; echo done                               分号链
  4. ... --reporter=line                           多参数
  5. cd . && npx playwright test --list            前置 cd
  6. echo probe > ac-x.txt                         应当被拒

用法（从仓库根目录）：
  .venv/Scripts/python.exe shots/_check_bash_scope.py
"""
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "backend"))

from app.services.opencode_runner import OpenCodeRunner  # noqa: E402

BIN = r"C:\Users\11031\scoop\apps\opencode\1.18.31\opencode.exe"
MODEL = "cctq-codex/gpt-6-luna"
APP_CONFIG = {"OPENCODE_BIN": BIN, "OPENCODE_CONFIG": None,
              "OPENCODE_SERVE_HOST": "127.0.0.1", "OPENCODE_PURE": False}
AGENT = "build"

COMMANDS = [
    "npx playwright test --list",
    "npx playwright test --list 2>&1 | tail -5",
    "npx playwright test --list; echo done",
    "npx playwright test --list --reporter=line",
    "cd . && npx playwright test --list",
    "echo probe > ac-x.txt",
]


def model_ref():
    provider, name = MODEL.split("/", 1)
    return {"id": name, "providerID": provider}


def prompt_model_ref():
    provider, name = MODEL.split("/", 1)
    return {"providerID": provider, "modelID": name}


def make_workspace():
    root = Path(tempfile.mkdtemp(prefix="ac-bash-"))
    (root / "package.json").write_text(
        json.dumps({"name": "b", "devDependencies": {"@playwright/test": "^1.0.0"}}, indent=2),
        encoding="utf-8")
    (root / "playwright.config.ts").write_text(
        "import { defineConfig } from '@playwright/test';\n"
        "export default defineConfig({ testDir: './tests' });\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "a.spec.ts").write_text("// p\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=True)
    subprocess.run(["git", "add", "-A"], cwd=str(root), check=True)
    subprocess.run(["git", "-c", "user.email=b@l", "-c", "user.name=b",
                    "commit", "-qm", "init"], cwd=str(root), check=True)
    return root


def config(runner):
    import copy
    import os
    base_path = runner._config_path()
    data = json.loads(base_path.read_text(encoding="utf-8"))
    rule = {
        "edit": {"*": "allow", ".git/**": "deny", ".opencode/**": "deny"},
        "write": {"*": "allow", ".git/**": "deny", ".opencode/**": "deny"},
        "bash": {"*": "deny", "npx playwright test*": "allow",
                 "npx --no-install playwright test*": "allow"},
        "task": "deny",
    }
    data["agent"].setdefault(AGENT, {})["permission"] = copy.deepcopy(rule)
    data["agent"]["recording-emitter"]["permission"] = copy.deepcopy(rule)
    data["agent"]["recording-emitter"].setdefault("tools", {})["bash"] = True
    handle, name = tempfile.mkstemp(prefix=base_path.stem + ".b-", suffix=".json",
                                    dir=str(base_path.parent))
    os.close(handle)
    path = Path(name)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def tool_log(items):
    log = []
    for item in items:
        for part in item.get("parts") or []:
            if part.get("type") != "tool":
                continue
            state = part.get("state") or {}
            log.append({
                "tool": part.get("tool") or "?",
                "status": state.get("status") or "?",
                "input": str((state.get("input") or {}).get("command")
                             or json.dumps(state.get("input") or {}, ensure_ascii=False))[:160],
                "out": str(state.get("error") or state.get("output") or "")[:120],
            })
    return log


def main():
    runner = OpenCodeRunner(APP_CONFIG)
    workspace = make_workspace()
    cfg = config(runner)
    proc = None
    try:
        port = runner._free_port()
        proc = runner._start_serve(workspace, cfg, port)
        base = "http://127.0.0.1:%d" % port
        runner._wait_serve_healthy(base, proc, 60)
        created = requests.post(base + "/session",
                                json={"title": "bash scope", "agent": AGENT,
                                      "model": model_ref()}, timeout=30)
        session_id = (created.json() or {}).get("id")
        numbered = "\n".join("%d. `%s`" % (i + 1, c) for i, c in enumerate(COMMANDS))
        text = ("请依次执行下面 %d 条命令，每条都单独调用一次 bash 工具，"
                "不要合并、不要改写、不要跳过：\n%s\n\n"
                "每条执行后告诉我：成功执行还是被权限规则拒绝。"
                "被拒绝的不要重试，也不要换写法绕过。"
                % (len(COMMANDS), numbered))
        requests.post("%s/session/%s/prompt_async" % (base, session_id),
                      json={"parts": [{"type": "text", "text": text}], "agent": AGENT,
                            "model": prompt_model_ref()}, timeout=60)
        deadline = time.monotonic() + 420
        seen = False
        while time.monotonic() < deadline:
            status = requests.get(base + "/session/status", timeout=10).json() or {}
            if session_id in status:
                seen = True
            elif seen:
                break
            time.sleep(1.0)
        items = requests.get("%s/session/%s/message" % (base, session_id), timeout=60).json() or []
        rows = [r for r in tool_log(items) if r["tool"] == "bash"]
        print("\n=== bash 调用明细（%d 条）===" % len(rows))
        for row in rows:
            print("  [%s] %s" % (row["status"], row["input"]))
            if row["status"] != "completed":
                print("       └ %s" % row["out"].replace("\n", " ")[:110])
        print("\n=== 结论 ===")
        for row in rows:
            ok = row["status"] == "completed"
            blocked_ok = ("echo probe" in row["input"]) and not ok
            print("  %-52s %s" % (row["input"][:52],
                                  "执行" if ok else ("被拒（符合预期）" if blocked_ok else "被拒")))
        print("\n  ac-x.txt 是否被创建:", (workspace / "ac-x.txt").exists())
        return 0
    finally:
        if proc:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        shutil.rmtree(workspace, ignore_errors=True)
        try:
            cfg.unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
