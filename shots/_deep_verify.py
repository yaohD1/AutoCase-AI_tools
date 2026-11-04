# -*- coding: utf-8 -*-
"""深层真机验证：真实账号、真实业务、真实生成、真实执行。

流程（app.corekg.com）：
    登录（切 tab → 填账号密码 → 提交）→ 选组织 → 进知识库 → 打开某个知识库 → 断言文档列表

选这条流程是因为它覆盖了录制工具最容易被问到的地方：
  - 跨页面跳转（登录页 → 身份选择 → SPA 主界面），中间有真实网络等待
  - 异步渲染的列表（组织卡片、知识库卡片）——定位容易退化成 nth
  - 一条对每条用例都成立的可封装前置（登录 + 进组织）

用法（仓库根目录）：
    .venv/Scripts/python.exe shots/_deep_verify.py            # 全流程
    ONLY_RECORD=1 .venv/Scripts/python.exe shots/_deep_verify.py   # 只录制
    SKIP_BASELINE=1 ...                                            # 复用已生成的 baseline
"""
import json
import os
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
ACCOUNT = "13609266230"
PASSWORD = "123456789"
ORG = "涤烦的社会主义共和国"
KB = "多模态文件速度测试"
TARGET_BASE = "tests/knowledge/_deep-baseline.spec.ts"
TARGET_WRAP = "tests/knowledge/_deep-wrapped.spec.ts"


def log(*args):
    print(*args, flush=True)


def record():
    """真实录一遍。所有操作必须在录制线程里做，且每步之后 pump 一次收件箱。"""
    done = threading.Event()
    problems = []

    def scenario(session, page):
        def pump(ms=350):
            page.wait_for_timeout(ms)
            session._process_inbox()

        def step(label, fn, settle=0):
            """走一步，记下失败点——真实站点偶发慢，要知道是哪一步卡的。"""
            try:
                fn()
                pump(settle or 600)
            except Exception as exc:  # noqa: BLE001
                problems.append("%s -> %s: %s" % (label, type(exc).__name__, str(exc)[:160]))

        try:
            page.wait_for_selector("text=账号密码登录", timeout=40000)
            pump(800)
            step("切账号密码tab", lambda: page.get_by_text("账号密码登录").first.click())
            step("填账号", lambda: page.get_by_placeholder("请输入手机号或邮箱").fill(ACCOUNT), 400)
            step("填密码", lambda: page.get_by_placeholder("请输入密码").fill(PASSWORD), 400)
            step("提交", lambda: page.locator("button[type=submit]").first.click(), 1200)

            page.wait_for_selector("text=请选择登录身份", timeout=40000)
            pump(1500)
            step("选组织", lambda: page.get_by_text(ORG, exact=True).first.click(), 1200)

            page.wait_for_selector("text=知识库", timeout=40000)
            pump(1500)
            step("进知识库", lambda: page.get_by_text("知识库", exact=True).first.click(), 1500)

            page.wait_for_selector("text=新建知识库", timeout=40000)
            pump(1000)
            step("开知识库", lambda: page.get_by_text(KB, exact=True).first.click(), 2000)

            page.wait_for_selector("text=资源已就绪", timeout=40000)
            pump(800)
        except Exception as exc:  # noqa: BLE001
            problems.append("流程中断 %s: %s" % (type(exc).__name__, str(exc)[:200]))
        finally:
            done.set()

    RecordingSession._current = None
    session = RecordingSession.start_new(BASE, headless=True, ask_daemon=False, driver=scenario)
    if not done.wait(240):
        RecordingSession.dispose_current()
        raise RuntimeError("录制驱动 240 秒未结束")
    time.sleep(0.8)
    # 断言必须在 stop 之前加：标签页跟着最后一步走。
    session.add_intent_step(
        {"type": "page_text_contains", "target": None, "value": "共 5 条", "timeout": 8},
        text="知识库文档列表加载完成，底部显示「共 5 条」",
    )
    session.stop()
    steps = session.ir()
    RecordingSession.dispose_current()
    return steps, problems


def show(steps, title):
    log("\n" + title)
    for step in steps:
        target = step.get("target") or {}
        alias = target.get("alias") or ""
        loc = ""
        cands = target.get("candidates") or []
        if cands:
            loc = cands[0].get("locator") or ""
        log("  %2s %-9s %-22s %-30s %s"
            % (step["no"], step["type"], alias[:22], str(step.get("value") or "")[:30], loc[:70]))


def wrap_login(steps):
    """把「切 tab → 填账号 → 填密码 → 提交 → 选组织」封装成动作，替换成一次调用。

    这是真实场景里最有价值的封装对象：登录+进组织是每条用例的公共前置。
    """
    fills = [s for s in steps if s["type"] in ("fill", "select")]
    if len(fills) < 2:
        raise RuntimeError("没有两个输入步骤：%s" % [(s["no"], s["type"]) for s in steps])
    # 组织卡片是 button，命中文本在 alias 里、value 为 None，所以只能写死不能当参数。
    org_at = next(s["no"] for s in steps
                  if s["type"] == "click" and ORG in ((s.get("target") or {}).get("alias") or ""))
    start = min(s["no"] for s in steps if s["type"] == "click")
    packed, action = actions_mod.group(
        steps, list(range(start, org_at + 1)), "登录并进入组织",
        desc="切到账号密码登录，提交凭据，选中指定组织进入工作台",
        params=[
            {"step": fills[0]["no"], "key": "account", "label": "手机号或邮箱", "field": "value"},
            {"step": fills[1]["no"], "key": "password", "label": "密码", "field": "value"},
        ],
    )
    return packed, {action["id"]: action}


def run_one(tag, steps, actions, target_path, title):
    runner = OpenCodeRunner(CONFIG)
    log("\n" + "=" * 62)
    log("[%s] 步数=%s 动作=%s target=%s" % (tag, len(steps), len(actions or {}), target_path))
    started = time.time()
    planned = emit.plan(runner, str(WORKSPACE), MODEL, steps, "playwright-ts",
                        target_path, title, actions=actions)
    log("[%s] 任务书 %.1fs 路径=%s 复用项=%s"
        % (tag, time.time() - started, planned["target_path"],
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


def main():
    if not WORKSPACE.is_dir():
        log("工作区不存在", WORKSPACE)
        return 2
    log("模型", MODEL, "| 账号", ACCOUNT)
    steps, problems = record()
    if problems:
        log("\n录制期间问题:")
        for item in problems:
            log("  -", item)
    show(steps, "录制结果 %d 步：" % len(steps))

    if os.environ.get("ONLY_RECORD") == "1":
        log("\n[ONLY_RECORD] 只录制")
        return 0

    baseline_path = WORKSPACE / TARGET_BASE
    if os.environ.get("SKIP_BASELINE") == "1" and baseline_path.is_file():
        baseline_code = baseline_path.read_text(encoding="utf-8")
        log("\n复用已生成的 baseline")
    else:
        baseline_code, _, _ = run_one("baseline", steps, None, TARGET_BASE,
                                      "知识库文档列表可正常打开")

    packed, actions = wrap_login(steps)
    show(packed, "封装后 %d 步（登录 5 步 → 1 次调用）：" % len(packed))
    wrapped_code, _, _ = run_one("wrapped", packed, actions, TARGET_WRAP,
                                 "知识库文档列表可正常打开（封装版）")

    log("\n" + "=" * 62)
    log("--- baseline 正文 ---")
    log(baseline_code)
    log("\n--- wrapped 正文 ---")
    log(wrapped_code)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
