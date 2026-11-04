# -*- coding: utf-8 -*-
"""定位质量实测复核（只读，不改产品代码）。

录制一遍真实流程拿到 IR 里的候选定位，然后**另开一个干净的浏览器**，
在每一步「真正执行前的那个页面状态」下把候选逐个交给 Playwright 实跑，
对比录制时记下的 count / status / primary，回答三个问题：

  1. 选中的 primary 在真实页面上是不是唯一命中、命中的是不是目标元素？
  2. 录制时记的 count 和实测差多少（差得多说明校验时机不对，界面在误报）？
  3. 定位会不会串到别的页面（把 A 页的定位拿到 B 页跑，应该 0 命中）？

用法：.venv/Scripts/python.exe shots/_locate_quality.py
"""
import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "shots"))

from playwright.sync_api import sync_playwright  # noqa: E402
from _deep_verify import record, ACCOUNT, PASSWORD, ORG, KB, BASE  # noqa: E402

REPORT = ROOT / "shots" / "_locate_quality.json"


def to_locator(page, spec):
    """把录制 spec 结构还原成真正的 Playwright locator。"""
    method = spec.get("method")
    args = spec.get("args") or []
    kwargs = spec.get("kwargs") or {}
    fn = getattr(page, method, None)
    if fn is None:
        return None
    return fn(*args, **kwargs)


def probe(page, candidates):
    """在当前页面上把每条候选实跑一遍。"""
    out = []
    for cand in candidates:
        spec = cand.get("spec") or {}
        desc = cand.get("desc") or ""
        row = {"kind": cand.get("kind"), "desc": desc,
               "recorded": cand.get("count"), "relation": cand.get("relation")}
        loc = to_locator(page, spec)
        if loc is None:
            row["actual"] = "NO_METHOD:%s" % spec.get("method")
            out.append(row)
            continue
        try:
            n = loc.count()
            row["actual"] = n
            items = []
            for i in range(min(n, 3)):
                el = loc.nth(i)
                info = el.evaluate(
                    "e => ({tag: e.tagName.toLowerCase(), "
                    "text: (e.innerText||'').trim().slice(0,60), "
                    "role: e.getAttribute('role')||'', "
                    "ph: e.getAttribute('placeholder')||''})")
                box = el.bounding_box()
                info["box"] = [round(box[k]) for k in ("x", "y", "width", "height")] if box else None
                items.append(info)
            row["items"] = items
        except Exception as exc:  # noqa: BLE001
            row["actual"] = "ERR:%s" % str(exc)[:100]
        out.append(row)
    return out


def main():
    print("阶段 1：真实录制，取 IR 候选定位 ...", flush=True)
    started = time.time()
    steps, problems = record()
    print("录制完成 %.1fs，%d 步，问题=%s" % (time.time() - started, len(steps), problems), flush=True)

    targets = {s["no"]: s.get("target") for s in steps if s.get("target")}
    report = {"steps": [], "cross_page": [], "problems": problems}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        def snap(label, no, stage):
            t = targets.get(no)
            if not t:
                return
            cands = t.get("candidates") or []
            rows = probe(page, cands)
            primary = t.get("primary") or {}
            p_desc = primary.get("desc")
            p_row = next((r for r in rows if r["desc"] == p_desc), None)
            entry = {
                "no": no, "stage": stage, "label": label,
                "alias": t.get("alias"), "status": t.get("status"),
                "vanished_after": t.get("vanished_after"),
                "primary": p_desc, "primary_recorded": primary.get("count"),
                "primary_actual": p_row["actual"] if p_row else ("NO_PRIMARY" if p_desc is None else "?"),
                "candidates": rows,
                "recorded_matches": (t.get("matches") or {}).get("count"),
            }
            report["steps"].append(entry)
            print("\n[步骤 %s / %s] %s  status=%s  primary=%s"
                  % (no, stage, label, t.get("status"), (p_desc or "无")[:70]))
            print("    primary 录制count=%s → 实测count=%s"
                  % (entry["primary_recorded"], entry["primary_actual"]))
            for r in rows:
                mark = "OK " if r["actual"] == r["recorded"] else "DIFF"
                print("    %s %-10s 录=%-4s 实=%-4s %s"
                      % (mark, r["kind"], r["recorded"], r["actual"], r["desc"][:78]))

        # --- 页面 A：登录页（切 tab 前）---
        page.goto(BASE, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_selector("text=账号密码登录", timeout=40000)
        snap("切到账号密码登录", 2, "A-登录页")

        # --- 页面 A'：切 tab 后，表单可见 ---
        page.get_by_text("账号密码登录").first.click()
        page.wait_for_timeout(600)
        snap("填账号", 3, "A'-登录表单")
        snap("填密码", 4, "A'-登录表单")
        snap("点登录", 5, "A'-登录表单")

        # 跨页面：把「知识库」「知识库卡片」「组织卡片」的定位拿到登录页跑
        for no, label in ((6, "组织卡片"), (7, "知识库入口"), (8, "知识库卡片")):
            t = targets.get(no)
            if not t:
                continue
            rows = probe(page, t.get("candidates") or [])
            hit = [r for r in rows if isinstance(r["actual"], int) and r["actual"] > 0]
            report["cross_page"].append({"no": no, "wrong_page": "A-登录页", "label": label,
                                         "hits": [(r["kind"], r["actual"]) for r in hit]})
            print("    [跨页面] 登录页上跑「%s」定位 → 命中 %s" % (label, [(r["kind"], r["actual"]) for r in hit]))

        page.get_by_placeholder("请输入手机号或邮箱").fill(ACCOUNT)
        page.get_by_placeholder("请输入密码").fill(PASSWORD)
        page.locator("button[type=submit]").first.click()
        page.wait_for_selector("text=请选择登录身份", timeout=40000)
        page.wait_for_timeout(800)

        # --- 页面 B：身份选择 ---
        snap("选组织", 6, "B-身份选择页")

        # 跨页面：登录按钮 / 账号输入框 在身份页不该命中
        for no, label in ((3, "账号输入框"), (5, "登录按钮"), (7, "知识库入口")):
            t = targets.get(no)
            if not t:
                continue
            rows = probe(page, t.get("candidates") or [])
            hit = [r for r in rows if isinstance(r["actual"], int) and r["actual"] > 0]
            report["cross_page"].append({"no": no, "wrong_page": "B-身份选择页", "label": label,
                                         "hits": [(r["kind"], r["actual"]) for r in hit]})
            print("    [跨页面] 身份页上跑「%s」定位 → 命中 %s" % (label, [(r["kind"], r["actual"]) for r in hit]))

        page.get_by_text(ORG, exact=True).first.click()
        page.wait_for_selector("text=知识库", timeout=40000)
        page.wait_for_timeout(1000)

        # --- 页面 C：主界面 ---
        snap("进知识库", 7, "C-主界面")

        page.get_by_text("知识库", exact=True).first.click()
        page.wait_for_selector("text=新建知识库", timeout=40000)
        page.wait_for_timeout(900)

        # --- 页面 D：知识库列表 ---
        snap("开知识库", 8, "D-知识库列表页")

        # 跨页面：列表页上还残留登录页/身份页的定位吗
        for no, label in ((2, "账号密码登录tab"), (5, "登录按钮"), (6, "组织卡片")):
            t = targets.get(no)
            if not t:
                continue
            rows = probe(page, t.get("candidates") or [])
            hit = [r for r in rows if isinstance(r["actual"], int) and r["actual"] > 0]
            report["cross_page"].append({"no": no, "wrong_page": "D-知识库列表页", "label": label,
                                         "hits": [(r["kind"], r["actual"]) for r in hit]})
            print("    [跨页面] 列表页上跑「%s」定位 → 命中 %s" % (label, [(r["kind"], r["actual"]) for r in hit]))

        page.get_by_text(KB, exact=True).first.click()
        page.wait_for_selector("text=资源已就绪", timeout=40000)
        page.wait_for_timeout(600)

        # --- 页面 E：知识库详情（录制时第 8 步 count=2 的那个页面）---
        t8 = targets.get(8)
        if t8:
            rows = probe(page, t8.get("candidates") or [])
            report["cross_page"].append({"no": 8, "wrong_page": "E-知识库详情页", "label": "知识库卡片",
                                         "hits": [(r["kind"], r["actual"]) for r in rows
                                                  if isinstance(r["actual"], int) and r["actual"] > 0]})
            print("\n[对照] 详情页上跑第 8 步的候选：")
            for r in rows:
                print("    %-10s 实=%-4s %s" % (r["kind"], r["actual"], r["desc"][:70]))

        browser.close()

    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n报告已写 %s" % REPORT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
