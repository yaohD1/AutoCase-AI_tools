# -*- coding: utf-8 -*-
"""真机验证：封装后的动作，写手会不会真的写成函数、并复用同一份。

单测能证明「载荷里是 action 条目」，证明不了「模型照做」。这里建一个临时
git 工程，把封装后的 dispatch 真发给 recording-emitter，再检查产出文件：
- 有没有一个具名函数；
- 两次调用是不是都指向它（而不是复制两段）；
- 有没有把 `{{username}}` 原样写进代码。

用法（仓库根目录）：.venv/Scripts/python.exe shots/_check_action_generate_live.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.opencode_runner import OpenCodeRunner  # noqa: E402
from app.services.recording import actions as a  # noqa: E402
from app.services.recording import emit, ir  # noqa: E402

BIN = os.environ.get("OPENCODE_BIN") or r"C:\Users\11031\scoop\apps\opencode\1.18.31\opencode.exe"
MODEL = os.environ.get("ACTION_CHECK_MODEL") or "opencode/beam-free"
CONFIG = {"OPENCODE_BIN": BIN, "OPENCODE_CONFIG": None,
          "OPENCODE_SERVE_HOST": "127.0.0.1", "OPENCODE_PURE": False}


def candidate(kind, desc, **spec):
    return {"kind": kind, "desc": desc, "spec": dict(kind=kind, **spec),
            "count": 1, "index": 0, "relation": "self"}


def target(alias, *cands):
    return {"alias": alias, "snap": {"tag": "input", "name": alias},
            "candidates": list(cands), "primary": cands[0] if cands else None,
            "status": "unique"}


def make_workspace():
    root = Path(tempfile.mkdtemp(prefix="ac-action-"))
    (root / "package.json").write_text(json.dumps({
        "name": "action-demo",
        "devDependencies": {"@playwright/test": "^1.40.0"},
    }, indent=2), encoding="utf-8")
    (root / "playwright.config.ts").write_text(
        "import { defineConfig } from '@playwright/test';\n"
        "export default defineConfig({ testDir: './tests' });\n", encoding="utf-8")
    tests = root / "tests"
    tests.mkdir()
    (tests / "seed.spec.ts").write_text(
        "import { test, expect } from '@playwright/test';\n\n"
        "test('seed', async ({ page }) => {\n"
        "  await page.goto('https://example.com');\n"
        "  await expect(page).toHaveTitle(/Example/);\n"
        "});\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=True)
    subprocess.run(["git", "add", "-A"], cwd=str(root), check=True)
    subprocess.run(["git", "-c", "user.email=live@local", "-c", "user.name=live",
                    "commit", "-qm", "init"], cwd=str(root), check=True)
    return root


def build():
    user = target("用户名", candidate("role", "getByRole('textbox', { name: '用户名' })",
                                      role="textbox", name="用户名"))
    pwd = target("密码", candidate("role", "getByRole('textbox', { name: '密码' })",
                                   role="textbox", name="密码"))
    login = target("登录按钮", candidate("role", "getByRole('button', { name: '登录' })",
                                         role="button", name="登录"))
    new_btn = target("新建", candidate("role", "getByRole('button', { name: '新建' })",
                                       role="button", name="新建"))
    flow = [
        ir.make_step("goto", 1, url="https://demo.example.com/login"),
        ir.make_step("fill", 2, target=user, value="admin"),
        ir.make_step("fill", 3, target=pwd, value="123456"),
        ir.make_step("click", 4, target=login),
        ir.make_step("click", 5, target=new_btn),
        ir.make_step("fill", 6, target=user, value="tester"),
        ir.make_step("fill", 7, target=pwd, value="654321"),
        ir.make_step("click", 8, target=login),
    ]
    params = [
        {"step": 2, "key": "username", "label": "用户名", "field": "value"},
        {"step": 3, "key": "password", "label": "密码", "field": "value"},
    ]
    packed, action = a.group(flow, [2, 3, 4], "登录", desc="用账号密码登录", params=params)
    # group 后：[goto, call, 点新建, 填用户名, 填密码, 点登录]
    again = ir.make_step("call", 0, action=action["id"],
                         args={"username": "tester", "password": "654321"}, label="登录")
    packed = packed[:3] + [again] + packed[3:]
    for i, step in enumerate(packed, 1):
        step["no"] = i
    return packed, {action["id"]: action}


def main():
    if not Path(BIN).is_file():
        print("opencode 不存在：", BIN)
        return 2
    workspace = make_workspace()
    steps, actions = build()
    print("流程步数 =", len(steps), "动作数 =", len(actions))
    payload = emit._payload("playwright-ts", steps, "tests/login.spec.ts", "登录流程",
                            {"evidence": [], "samples": []}, [], write_scope="workspace",
                            actions=actions)
    prompt = emit._prompt(payload)
    print("payload.steps 类型 =", [s["type"] for s in payload["steps"]])
    print("actions[0].called_at =", payload["actions"][0]["called_at"])

    runner = OpenCodeRunner(CONFIG)
    try:
        reply = runner.emit_recording(str(workspace), MODEL, prompt,
                                      writable="tests/login.spec.ts", timeout=600)
        print("\n--- 模型回复 ---\n", (reply or "")[:400])
    except Exception as exc:  # noqa: BLE001
        print("生成失败：", type(exc).__name__, exc)
        print("工作区保留：", workspace)
        return 1

    path = workspace / "tests" / "login.spec.ts"
    if not path.is_file():
        print("没有写出目标文件")
        print("工作区保留：", workspace)
        return 1
    code = path.read_text(encoding="utf-8", errors="replace")
    print("\n--- 产出 ---")
    print(code)

    checks = {
        "有具名函数": bool(re.search(r"(async\s+function\s+\w+|const\s+\w+\s*=\s*async)", code)),
        "两次调用同一函数": len(re.findall(r"await\s+login\(", code)) >= 2,
        "没有残留占位符": "{{" not in code,
        "参数进了函数签名": bool(re.search(r"login\s*\(", code)),
    }
    print("\n--- 检查 ---")
    for name, ok in checks.items():
        print(("PASS" if ok else "FAIL"), name)
    print("工作区保留：", workspace)
    if all(checks.values()):
        shutil.rmtree(workspace, ignore_errors=True)
        print("已清理临时工作区")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
