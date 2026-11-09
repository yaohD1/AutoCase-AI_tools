# -*- coding: utf-8 -*-
"""断言里最容易写错的三种：带超时的等待、轮询刷新、元素消失。

复用上一轮录好的步骤，只在末尾追加三条 wait/assert，单独生成一份，
看写手有没有按 FRAMEWORK_RULES 里的写法来（尤其是 reload 那套 expect.poll）。

    .venv/Scripts/python.exe shots/_verify_assert_poll.py
"""
import json
import os
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.opencode_runner import OpenCodeRunner     # noqa: E402
from app.services.recording import emit                     # noqa: E402

BIN = os.environ.get("OPENCODE_BIN") or r"C:\Users\11031\scoop\apps\opencode\1.18.31\opencode.exe"
MODEL = "4rount-hyper/gpt-6-luna"
CONFIG = {"OPENCODE_BIN": BIN, "OPENCODE_CONFIG": None,
          "OPENCODE_SERVE_HOST": "127.0.0.1", "OPENCODE_PURE": False}
WORKSPACE = Path(r"D:\autoproject\midscene-ui-test")
STEPS_FILE = ROOT / "shots" / "_assert_steps.json"
TARGET = "tests/login-page/_assert-poll3.spec.ts"
TITLE = "断言探针：等待与轮询"

# 三条，都由「智能解析」产出过的形状（timeout / reload 用结构化的形式给出）
EXTRA = [
    {"type": "wait", "no": 0, "text": "等到密码输入框出现（最多 30 秒）",
     "label": "等到密码输入框出现（最多 30 秒）",
     "intent": {"type": "visible", "target": "密码", "value": None, "seconds": None,
                "timeout": 30.0, "interval": None, "reload": False}},
    {"type": "wait", "no": 0, "text": "最多等 30 秒，每 5 秒刷新一次，直到页面上出现「用户或密码错误」",
     "label": "最多等 30 秒，每 5 秒刷新一次，直到页面上出现「用户或密码错误」",
     "intent": {"type": "page_text_contains", "target": None, "value": "用户或密码错误",
                "seconds": None, "timeout": 30.0, "interval": 5.0, "reload": True}},
    {"type": "assert", "no": 0, "text": "密码输入框不可见",
     "label": "密码输入框不可见",
     "intent": {"type": "hidden", "target": "密码", "value": None, "seconds": None,
                "timeout": None, "interval": None, "reload": False}},
]


def log(*args):
    print(*args, flush=True)


def attach_targets(steps):
    """追加的步骤要带上 target：直接从已有步骤里借同一个元素的定位，别自己编。"""
    known = {}
    for step in steps:
        target = step.get("target")
        if target and target.get("alias"):
            known.setdefault(target["alias"], target)
    out = []
    for item in EXTRA:
        step = dict(item)
        alias = (step.get("intent") or {}).get("target")
        if alias:
            if alias not in known:
                raise RuntimeError("步骤里没有可借用的元素：%s" % alias)
            step["target"] = dict(known[alias])
        out.append(step)
    return out


def check(code):
    import re
    out = []

    def add(name, ok, evidence=""):
        out.append((name, "PASS" if ok else "FAIL", evidence))

    add("带超时的等待 → { timeout: 30000 }",
        bool(re.search(r"timeout:\s*30_?000", code)),
        " / ".join(re.findall(r"timeout:\s*[\d_]+", code))[:120])
    add("轮询刷新 → expect.poll", "expect.poll" in code, "")
    add("轮询里真的 reload", "page.reload()" in code, "")
    add("轮询即时判断不吃 matcher",
        not re.search(r"expect\.poll\([\s\S]{0,400}?toContainText", code),
        "poll 块内若用 matcher 会把间隔吃掉")
    add("没有 while(true) 手写循环", "while (true)" not in code and "while(true)" not in code, "")
    add("元素消失 → toBeHidden", "toBeHidden" in code, "")
    return out


def main():
    steps = json.loads(STEPS_FILE.read_text(encoding="utf-8"))
    extra = attach_targets(steps)
    merged = steps + extra
    for i, step in enumerate(merged, 1):
        step["no"] = i

    log("模型 %s，共 %d 步（原 %d + 追加 %d）" % (MODEL, len(merged), len(steps), len(extra)))
    runner = OpenCodeRunner(CONFIG)
    started = time.time()
    planned = emit.plan(runner, str(WORKSPACE), MODEL, merged, "playwright-ts", TARGET, TITLE)
    log("任务书 %.1fs" % (time.time() - started))
    # 任务书里对轮询那一步写了什么，直接决定写手会不会重放操作
    for item in (planned.get("plan") or {}).get("steps") or []:
        if item.get("no") in (13, 14, 15):
            log("  plan[%s] %s" % (item.get("no"), item.get("note")))
    started = time.time()
    result = emit.generate(runner, str(WORKSPACE), MODEL, merged, "playwright-ts",
                           TARGET, TITLE, plan=planned["plan"], overwrite=True,
                           write_scope="workspace")
    log("写出 %.1fs changed=%s validated=%s"
        % (time.time() - started, result.get("changed"), result.get("validated")))

    path = WORKSPACE / TARGET
    code = path.read_text(encoding="utf-8") if path.is_file() else ""
    log("\n--- 生成的代码 ---\n%s" % code)
    log("\n--- 核对 ---")
    for name, verdict, evidence in check(code):
        log("%-4s %-40s %s" % (verdict, name, evidence))

    (ROOT / "shots" / "_assert_poll.json").write_text(
        json.dumps({"code": code}, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
