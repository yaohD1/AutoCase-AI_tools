# -*- coding: utf-8 -*-
"""真机端到端：真实录制 → 生成两版脚本 → 对比「有没有封装思想」。

流程用 https://app.corekg.com/login 的真实页面：切到账号密码登录、填错误凭据、
点登录、断言失败提示。选错误凭据是有意的——它不依赖真实账号，回放能稳定复现，
而且「登录失败」本身就是一条正经用例。

产出：
  tests/login-page/_recorded-baseline.spec.ts   原始 6 步，不封装
  tests/login-page/_recorded-wrapped.spec.ts    登录 4 步封装成动作，并调用两次

用法（仓库根目录）：
  .venv/Scripts/python.exe shots/_live_verify_actions.py
"""
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.opencode_runner import OpenCodeRunner  # noqa: E402
from app.services.recording import actions as actions_mod  # noqa: E402
from app.services.recording import emit, ir  # noqa: E402
from app.services.recording.session import RecordingSession  # noqa: E402

BIN = os.environ.get("OPENCODE_BIN") or r"C:\Users\11031\scoop\apps\opencode\1.18.31\opencode.exe"
MODEL = "4rount-hyper/gpt-6-luna"
CONFIG = {"OPENCODE_BIN": BIN, "OPENCODE_CONFIG": None,
          "OPENCODE_SERVE_HOST": "127.0.0.1", "OPENCODE_PURE": False}
WORKSPACE = Path(r"D:\autoproject\midscene-ui-test")
BASE = "https://app.corekg.com/login"
ACCOUNT = "13800138000"
PASSWORD = "WrongPass123"
TARGET_BASE = "tests/login-page/_recorded-baseline.spec.ts"
TARGET_WRAP = "tests/login-page/_recorded-wrapped.spec.ts"


def log(*args):
    print(*args, flush=True)


def record():
    """真实录一遍。操作必须在录制线程里做，且每步之后要 pump 一次收件箱。"""
    done = threading.Event()
    problems = []

    def scenario(session, page):
        def pump(ms=350):
            page.wait_for_timeout(ms)
            session._process_inbox()

        try:
            page.wait_for_selector("text=账号密码登录", timeout=30000)
            pump(700)
            page.get_by_text("账号密码登录").first.click()
            pump(700)
            page.get_by_placeholder("请输入手机号或邮箱").fill(ACCOUNT)
            pump(400)
            page.get_by_placeholder("请输入密码").fill(PASSWORD)
            pump(400)
            page.locator("button[type=submit]").first.click()
            pump(1500)
        except Exception as exc:  # noqa: BLE001
            problems.append("%s: %s" % (type(exc).__name__, exc))
        finally:
            done.set()

    RecordingSession._current = None
    session = RecordingSession.start_new(BASE, headless=True, ask_daemon=False, driver=scenario)
    if not done.wait(180):
        RecordingSession.dispose_current()
        raise RuntimeError("录制驱动 180 秒未结束")
    time.sleep(0.6)
    # 断言在 stop 之前加：标签页跟着最后一步走，stop 之后再加会落到错的页面。
    session.add_intent_step(
        {"type": "page_text_contains", "target": None, "value": "用户或密码错误", "timeout": 5},
        text="登录失败时提示「用户或密码错误」",
    )
    session.stop()
    steps = session.ir()
    RecordingSession.dispose_current()
    return steps, problems


def wrapped_steps(steps):
    """把「切 tab → 填账号 → 填密码 → 点登录」这四步封装成动作，再插一次不同实参的调用。

    步骤号必须从录制结果里取，不能写死：真实录制多一步 goto，第 2 步是点击 tab，
    写死 2/3 会把「点击」当成要参数化的输入值，group 直接报「没有 value 可以替换」。
    """
    clicks = [s["no"] for s in steps if s["type"] == "click"]
    if len(clicks) < 2:
        raise RuntimeError("录制结果里没有足够的点击步骤：%s" % clicks)
    start, end = clicks[0], clicks[-1]
    fills = [s for s in steps if s["type"] in ("fill", "select")]
    if len(fills) < 2:
        raise RuntimeError("录制结果里没有两个输入步骤")
    packed, action = actions_mod.group(
        steps, list(range(start, end + 1)), "账号密码登录",
        desc="切到账号密码登录，填凭据并提交",
        params=[
            {"step": fills[0]["no"], "key": "account", "label": "手机号或邮箱", "field": "value"},
            {"step": fills[1]["no"], "key": "password", "label": "密码", "field": "value"},
        ],
    )
    again = ir.make_step("call", 0, action=action["id"],
                         args={"account": "13900139000", "password": "AnotherWrong"},
                         label="账号密码登录")
    packed = packed[:2] + [again] + packed[2:]
    for i, step in enumerate(packed, 1):
        step["no"] = i
    return packed, {action["id"]: action}


def run_one(tag, steps, actions, target_path, title):
    runner = OpenCodeRunner(CONFIG)
    log("\n" + "=" * 60)
    log("[%s] 步数=%s 动作=%s target=%s" % (tag, len(steps), len(actions or {}), target_path))
    started = time.time()
    planned = emit.plan(runner, str(WORKSPACE), MODEL, steps, "playwright-ts",
                        target_path, title, actions=actions)
    log("[%s] 任务书 %.1fs 路径=%s 步数=%s 复用项=%s"
        % (tag, time.time() - started, planned["target_path"], planned["steps"],
           len(planned["plan"].get("reusable") or [])))
    started = time.time()
    result = emit.generate(runner, str(WORKSPACE), MODEL, steps, "playwright-ts",
                           target_path, title, plan=planned["plan"], overwrite=True,
                           actions=actions, write_scope="workspace")
    log("[%s] 写出 %.1fs changed=%s validated=%s outside=%s"
        % (tag, time.time() - started, result.get("changed"), result.get("validated"),
           result.get("outside")))
    path = WORKSPACE / target_path
    code = path.read_text(encoding="utf-8") if path.is_file() else ""
    log("[%s] 文件 %d 行" % (tag, len(code.splitlines())))
    return code, planned, result


def check_baseline(code):
    return {
        "有 @autocase 标记": "@autocase" in code,
        "用了录制定位": "getByPlaceholder" in code or "getByRole" in code,
        "有断言": "expect(" in code,
        "没有 {{ 占位符": "{{" not in code,
    }


def check_wrapped(code):
    """函数名可能是中文（「账号密码登录」），正则不能绑死英文 login。"""
    import re
    found = re.search(r"async\s+function\s+(\S+?)\s*\(([^)]*)\)", code)
    name = found.group(1) if found else ""
    signature = found.group(2) if found else ""
    calls = len(re.findall(r"await\s+%s\s*\(" % re.escape(name), code)) if name else 0
    locator_calls = len(re.findall(r"getBy(?:Label|Placeholder|Role|Text)\(", code))
    return {
        "有 @autocase 标记": "@autocase" in code,
        "抽出了函数": bool(found),
        "函数名非空": bool(name.strip()),
        "两次调用": calls >= 2,
        "参数进了签名": "account" in signature and "password" in signature,
        "没有 {{ 占位符": "{{" not in code,
        # 函数体里每个定位只出现一次：出现两次说明把动作复制了一遍，而不是复用。
        "没有复制两段": locator_calls <= 4,
        "函数体只有一份定位": code.count("getByLabel('手机号或邮箱'") <= 1,
    }


def main():
    if os.environ.get("CHECK_ONLY") == "1":
        base = (WORKSPACE / TARGET_BASE).read_text(encoding="utf-8")
        wrap = (WORKSPACE / TARGET_WRAP).read_text(encoding="utf-8")
        log("--- baseline 检查 ---")
        for name, ok in check_baseline(base).items():
            log(("PASS " if ok else "FAIL ") + name)
        log("--- wrapped 检查 ---")
        for name, ok in check_wrapped(wrap).items():
            log(("PASS " if ok else "FAIL ") + name)
        return 0
    if not WORKSPACE.is_dir():
        log("工作区不存在", WORKSPACE)
        return 2
    log("模型", MODEL)
    steps, problems = record()
    if problems:
        log("录制期间报错:", problems)
    log("\n录制结果 %d 步：" % len(steps))
    for step in steps:
        alias = ((step.get("target") or {}).get("alias") or "")
        log("  %2s %-10s %-20s %s" % (step["no"], step["type"], alias,
                                      (step.get("value") or "")[:24]))

    if os.environ.get("ONLY_RECORD") == "1":
        log("\n[ONLY_RECORD] 只录制，不进模型")
        return 0

    baseline_path = WORKSPACE / TARGET_BASE
    if os.environ.get("SKIP_BASELINE") == "1" and baseline_path.is_file():
        baseline_code = baseline_path.read_text(encoding="utf-8")
        log("复用已生成的 baseline：%s" % TARGET_BASE)
    else:
        baseline_code, _, _ = run_one("baseline", steps, None, TARGET_BASE, "账号密码登录失败提示")
    packed, actions = wrapped_steps(steps)
    log("\n封装后：")
    for step in packed:
        extra = ""
        if step["type"] == "call":
            extra = "%s %s" % (step.get("label"), step.get("args"))
        log("  %2s %-10s %s" % (step["no"], step["type"], extra))
    wrapped_code, _, _ = run_one("wrapped", packed, actions, TARGET_WRAP, "账号密码登录失败提示（封装版）")

    log("\n" + "=" * 60)
    log("--- baseline 检查 ---")
    for name, ok in check_baseline(baseline_code).items():
        log(("PASS " if ok else "FAIL ") + name)
    log("--- wrapped 检查 ---")
    for name, ok in check_wrapped(wrapped_code).items():
        log(("PASS " if ok else "FAIL ") + name)

    log("\n--- baseline 正文 ---")
    log(baseline_code)
    log("\n--- wrapped 正文 ---")
    log(wrapped_code)

    ok = all(check_baseline(baseline_code).values()) and all(check_wrapped(wrapped_code).values())
    log("\n结果:", "OK" if ok else "有问题")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
