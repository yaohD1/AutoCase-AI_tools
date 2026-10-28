# -*- coding: utf-8 -*-
"""代价测量：新的 cssPath（逐层验证）比旧版（6 层封顶、不验证）慢多少。

录制时每次点击会算两次快照（pointerdown 预计数 + click），所以这个开销会直接落在
「点一下到出现步骤」的延迟上，必须量出来。
"""
import sys
sys.path.insert(0, 'D:/autoproject/Auto_case/.tmp-ai-locate')
from playwright.sync_api import sync_playwright
import harness

JS = r"""
() => {
  const oldPath = (el) => {
    const parts = []; let cur = el;
    while (cur && cur.nodeType === 1 && parts.length < 6) {
      let part = cur.tagName.toLowerCase();
      if (cur === document.documentElement) { parts.unshift(part); break; }
      const sibs = cur.parentElement
        ? Array.from(cur.parentElement.children).filter(c => c.tagName === cur.tagName) : [];
      if (sibs.length > 1) part += ':nth-of-type(' + (sibs.indexOf(cur) + 1) + ')';
      parts.unshift(part); cur = cur.parentElement;
    }
    return parts.join(' > ');
  };
  const newPath = (el) => {
    const parts = []; let scope = document;
    try { const r = el.getRootNode ? el.getRootNode() : null; if (r && r.querySelectorAll) scope = r; } catch (e) {}
    let cur = el;
    while (cur && cur.nodeType === 1 && parts.length < 12) {
      let part = cur.tagName.toLowerCase();
      const isRoot = cur === document.documentElement || !cur.parentElement;
      if (!isRoot) {
        const sibs = Array.from(cur.parentElement.children).filter(c => c.tagName === cur.tagName);
        if (sibs.length > 1) part += ':nth-of-type(' + (sibs.indexOf(cur) + 1) + ')';
      }
      parts.unshift(part);
      const path = parts.join(' > ');
      if (parts.length >= 2 || isRoot) {
        let hits = -1;
        try { hits = scope.querySelectorAll(path).length; } catch (e) { hits = -1; }
        if (hits === 1) return path;
      }
      if (isRoot) break;
      cur = cur.parentElement;
    }
    return parts.join(' > ');
  };
  const pick = () => document.querySelectorAll('button,a,div[role],li,span')[0];
  const els = [];
  document.querySelectorAll('*').forEach((el) => {
    if (els.length >= 60) return;
    const r = el.getBoundingClientRect();
    if (r.width < 8 || r.height < 8) return;
    els.push(el);
  });
  const bench = (fn) => {
    for (let i = 0; i < 5; i++) els.forEach(fn);   // 预热
    const t0 = performance.now();
    for (let i = 0; i < 10; i++) els.forEach(fn);
    return (performance.now() - t0) / (10 * els.length);
  };
  const oldMs = bench(oldPath);
  const newMs = bench(newPath);
  return {n: els.length, oldMs: oldMs, newMs: newMs, nodes: document.querySelectorAll('*').length};
}
"""

with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True)
    ctx = b.new_context(ignore_https_errors=True, viewport={'width': 1440, 'height': 900})
    page = ctx.new_page()
    harness.open_main(page)
    r = page.evaluate(JS)
    print('页面元素总数 %d，抽样 %d 个' % (r['nodes'], r['n']))
    print('旧 cssPath 平均 %.3f ms / 次' % r['oldMs'])
    print('新 cssPath 平均 %.3f ms / 次（含逐层唯一性验证）' % r['newMs'])
    print('每次点击多付 %.3f ms（两次快照合计）' % ((r['newMs'] - r['oldMs']) * 2))
    b.close()
