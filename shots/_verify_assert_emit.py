# -*- coding: utf-8 -*-
"""验证「智能生成断言」：自然语言描述 → 模型解析成 intent → 插进录制 → 生成脚本，
再看写出来的断言代码是否符合框架规则与项目习惯。

只读产品代码，不修改；生成的 spec 落在 midscene-ui-test 的这个测试目录里。

用法（仓库根目录）：
  .venv/Scripts/python.exe shots/_verify_assert_emit.py
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

from app.services import models_config                     # noqa: E402
from app.services.oc_text import TextWorker, TextError      # noqa: E402
from app.services.opencode_runner import OpenCodeRunner     # noqa: E402
from app.services.recording import emit, intent             # noqa: E402
from app.services.recording.session import RecordingSession  # noqa: E402

BIN = os.environ.get("OPENCODE_BIN") or r"C:\Users\11031\scoop\apps\opencode\1.18.31\opencode.exe"
MODEL = "4rount-hyper/gpt-6-luna"
CONFIG = {"OPENCODE_BIN": BIN, "OPENCODE_CONFIG": None,
          "OPENCODE_SERVE_HOST": "127.0.0.1", "OPENCODE_PURE": False}
WORKSPACE = Path(r"D:\autoproject\midscene-ui-test")
BASE = "https://app.corekg.com/login"
ACCOUNT = "13800138000"
PASSWORD = "WrongPass123"
TARGET = "tests/login-page/_assert-probe.spec.ts"
TITLE = "账号密码登录失败（断言探针）"

# 刻意覆盖 6 种断言类型，其中 3 条带易错点：
#   - 「页面上出现…」必须走 page_text_contains，不能硬选元素
#   - 「没有被禁用」是 enabled，不是 visible
#   - 带「最多等 10 秒」要变成 wait + timeout:10
PROBES = [
    ("页面上出现「用户或密码错误」的提示文字", "page_text_contains，无 target"),
    ("手机号输入框里的内容是 13800138000", "value_equals + 输入框 target"),
    ("密码输入框依然可见", "visible + 密码框 target"),
    ("登录按钮没有被禁用", "enabled + 登录按钮 target"),
    ("当前地址里包含 login", "url_contains，无 target"),
    ("「账号密码登录」这段文字还在页面上", "text_contains + 元素 target"),
    ("最多等 10 秒，直到页面上出现「用户或密码错误」", "page_text_contains + timeout=10 → wait"),
]


def log(*args):
    print(*args, flush=True)


def record():
    """真机录一遍，停在「点击登录」之后。"""
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
    return session, problems


def resolve_all(session):
    """用真实模型逐条解析，走的是产品里那条 parse_intent。"""
    worker = TextWorker.shared({})

    def ask_model(system, prompt):
        return worker.ask(MODEL, system, prompt)

    aliases = session.aliases()
    log("\n已录元素别名：%s" % aliases)
    results = []
    for text, expectation in PROBES:
        started = time.time()
        try:
            parsed = intent.parse_intent(text, aliases, ask_model)
            results.append({"text": text, "expect": expectation, "ok": True,
                            "intent": parsed, "secs": round(time.time() - started, 1)})
            log("  [OK] %-28s → %s  (%.1fs)" % (text[:26], json.dumps(parsed, ensure_ascii=False),
                                                 time.time() - started))
        except (intent.IntentError, TextError) as exc:
            results.append({"text": text, "expect": expectation, "ok": False,
                            "error": str(exc)[:200], "secs": round(time.time() - started, 1)})
            log("  [FAIL] %-26s → %s" % (text[:26], str(exc)[:110]))
        time.sleep(0.2)
    return results


def insert_all(session, results):
    inserted = []
    for item in results:
        if not item["ok"]:
            continue
        try:
            step = session.add_intent_step(item["intent"], text=item["text"])
            inserted.append({"text": item["text"], "no": step.get("no"),
                             "type": step.get("type"), "intent": item["intent"]})
        except Exception as exc:  # noqa: BLE001
            item["insert_error"] = str(exc)[:160]
            log("  插入失败 %s：%s" % (item["text"][:20], exc))
    return inserted


def run_emit(steps):
    runner = OpenCodeRunner(CONFIG)
    log("\n" + "=" * 64)
    log("生成 %s（步数 %d）" % (TARGET, len(steps)))
    started = time.time()
    planned = emit.plan(runner, str(WORKSPACE), MODEL, steps, "playwright-ts",
                        TARGET, TITLE)
    log("任务书 %.1fs → %s（规划步数 %s）"
        % (time.time() - started, planned["target_path"], planned["steps"]))
    started = time.time()
    result = emit.generate(runner, str(WORKSPACE), MODEL, steps, "playwright-ts",
                           TARGET, TITLE, plan=planned["plan"], overwrite=True,
                           write_scope="workspace")
    log("写出 %.1fs changed=%s validated=%s"
        % (time.time() - started, result.get("changed"), result.get("validated")))
    path = WORKSPACE / TARGET
    return (path.read_text(encoding="utf-8") if path.is_file() else ""), planned, result


def check(code):
    """按框架规则逐条核对断言写法。返回 [(项, 结论, 证据)]。"""
    import re

    out = []

    def add(name, ok, evidence=""):
        out.append((name, "PASS" if ok else "FAIL", evidence))

    add("文件写出", bool(code.strip()), "%d 行" % len(code.splitlines()))
    add("没有 {{ 占位符", "{{" not in code, "")

    # 1. 整页文字断言：必须 page.locator('body') + toContainText，不能编造元素定位
    body = [m for m in re.findall(r"expect\([^\n]*\)[^\n]*", code) if "toContainText" in m]
    add("page_text_contains → body.toContainText",
        any("body" in m for m in body), " / ".join(body[:3])[:160])

    # 2. 输入框值断言
    add("value_equals → toHaveValue",
        bool(re.search(r"toHaveValue\(\s*['\"]?13800138000", code)),
        "; ".join(re.findall(r"expect\([^\n]*toHaveValue\([^\n]*", code))[:160])

    # 3. 可见性
    add("visible → toBeVisible", "toBeVisible" in code, "")

    # 4. 可用性：必须 toBeEnabled，不能退化成 toBeVisible
    add("enabled → toBeEnabled", "toBeEnabled" in code, "")

    # 5. 地址断言
    urls = re.findall(r"expect\(page\)\.toHaveURL\([^\n]*", code)
    add("url_contains → expect(page).toHaveURL", bool(urls), " / ".join(urls)[:160])

    # 6. 带超时的断言：10 秒要写成 10_000 或 10000
    timeouts = re.findall(r"timeout:\s*([\d_]+)", code)
    add("timeout 10s → 10000ms", any(t.replace("_", "") == "10000" for t in timeouts),
        "代码里的 timeout：%s" % (timeouts or "无"))

    # 7. click / fill 步骤不许自己加断言
    actions = re.findall(r"await page[^\n]*\.(?:click|fill)\([^\n]*", code)
    adhoc = [line for line in actions if "expect(" in line]
    add("click/fill 没有附带断言", not adhoc, " / ".join(adhoc)[:120])

    # 8. 断言条数：7 条探针里成功的都应该落进代码
    add("断言条数够", len(re.findall(r"expect\(", code)) >= 5,
        "expect() 出现 %d 次" % len(re.findall(r"expect\(", code)))

    return out


def main():
    log("模型 %s" % MODEL)
    log("工作区 %s" % WORKSPACE)
    if not WORKSPACE.is_dir():
        log("工作区不存在")
        return 2

    cached = ROOT / "shots" / "_assert_steps.json"
    if os.environ.get("SKIP_RECORD") == "1" and cached.is_file():
        steps = json.loads(cached.read_text(encoding="utf-8"))
        results, inserted, problems = [], [], []
        log("跳过录制，复用 %d 步" % len(steps))
    else:
        session, problems = record()
        log("录制完成，问题：%s" % (problems or "无"))
        results = resolve_all(session)
        inserted = insert_all(session, results)
        session.stop()
        steps = session.ir()
        RecordingSession.dispose_current()
        cached.write_text(json.dumps(steps, ensure_ascii=False, indent=2), encoding="utf-8")
        log("\n最终步骤 %d 步：" % len(steps))
        for s in steps:
            kind = s.get("type")
            extra = ""
            if s.get("intent"):
                extra = " %s" % json.dumps(s["intent"], ensure_ascii=False)
            log("  %2d %-8s %s%s" % (s["no"], kind, (s.get("label") or s.get("text") or "")[:24], extra))

    code, planned, result = run_emit(steps)
    if code:
        log("\n--- 生成的代码 ---\n%s" % code)

    log("\n--- 断言写法核对 ---")
    for name, verdict, evidence in check(code):
        log("%-4s %-38s %s" % (verdict, name, evidence))

    dump = {"model": MODEL, "record_problems": problems, "aliases": [],
            "parsed": results, "inserted": inserted, "target": TARGET,
            "emit": {"changed": result.get("changed"), "validated": result.get("validated")},
            "code": code}
    out = ROOT / "shots" / "_assert_probe.json"
    out.write_text(json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8")
    log("\n明细写入 %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
