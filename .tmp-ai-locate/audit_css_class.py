# -*- coding: utf-8 -*-
"""对照：改造前 vs 改造后的 cssPath，在真实页面上各有多少条不唯一。

只看「结构路径」这一个维度，因为它是这次改动的对象：
  old = 6 层封顶、从不验证（改造前 recorder.js）
  new = 逐层验证、爬到唯一为止（改造后 recorder.js）
同时统计 class 候选：有多少元素能从中剥出一个「页面上唯一」的名字候选，
这类元素以前只能靠结构路径。
"""
import sys

sys.path.insert(0, 'D:/autoproject/Auto_case/.tmp-ai-locate')
sys.path.insert(0, 'D:/autoproject/Auto_case/backend')

from playwright.sync_api import sync_playwright  # noqa: E402

import harness  # noqa: E402
from app.services.recording.locators import class_stems  # noqa: E402

JS = r"""
() => {
  const oldPath = (el) => {
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && parts.length < 6) {
      let part = cur.tagName.toLowerCase();
      if (cur === document.documentElement) { parts.unshift(part); break; }
      const sibs = cur.parentElement
        ? Array.from(cur.parentElement.children).filter(c => c.tagName === cur.tagName) : [];
      if (sibs.length > 1) part += ':nth-of-type(' + (sibs.indexOf(cur) + 1) + ')';
      parts.unshift(part);
      cur = cur.parentElement;
    }
    return parts.join(' > ');
  };
  const newPath = (el) => {
    const parts = [];
    let scope = document;
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
        if (hits === 1) return { path: path, depth: parts.length };
      }
      if (isRoot) break;
      cur = cur.parentElement;
    }
    return { path: parts.join(' > '), depth: parts.length };
  };
  const count = (sel) => { try { return document.querySelectorAll(sel).length; } catch (e) { return -1; } };
  const out = { total: 0, items: [] };
  const seen = new Set();
  const nodes = Array.from(document.querySelectorAll('*')).filter((el) => {
    const tag = el.tagName.toLowerCase();
    if (['a', 'button', 'input', 'textarea', 'select'].includes(tag)) return true;
    if (el.getAttribute('role')) return true;
    try { return getComputedStyle(el).cursor === 'pointer'; } catch (e) { return false; }
  });
  nodes.forEach((el) => {
    if (out.total >= 400) return;
    const r = el.getBoundingClientRect();
    if (r.width < 8 || r.height < 8) return;
    if (el.closest('#__autocase_hud')) return;
    const oldSel = oldPath(el);
    if (seen.has(oldSel)) return;
    seen.add(oldSel);
    const neu = newPath(el);
    out.total += 1;
    out.items.push({
      tag: el.tagName.toLowerCase(),
      cls: el.getAttribute('class') || '',
      old: oldSel, oldN: count(oldSel),
      neu: neu.path, neuN: count(neu.path), depth: neu.depth,
    });
  });
  return out;
}
"""


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(ignore_https_errors=True, viewport={'width': 1440, 'height': 900})
        page = ctx.new_page()
        harness.open_main(page)
        print('已进入主壳：', page.url)
        data = page.evaluate(JS)
        items = data['items']
        print('扫描到 %d 个可交互元素（按旧路径去重）' % data['total'])

        old_bad = [i for i in items if i['oldN'] != 1]
        neu_bad = [i for i in items if i['neuN'] != 1]
        print('\n=== 结构路径唯一性')
        print('  改造前（6 层封顶）不唯一：%d / %d' % (len(old_bad), len(items)))
        print('  改造后（爬到唯一）不唯一：%d / %d' % (len(neu_bad), len(items)))
        deeper = [i for i in items if i['depth'] > 6]
        print('  改造后需要爬到 7 层以上才唯一的：%d 条（其中旧路径不唯一的 %d 条）'
              % (len(deeper), len([i for i in deeper if i['oldN'] != 1])))
        print('  平均路径长度：旧 %.1f 段 → 新 %.1f 段'
              % (sum(i['old'].count(' > ') + 1 for i in items) / max(len(items), 1),
                 sum(i['depth'] for i in items) / max(len(items), 1)))

        print('\n  旧路径不唯一的样例：')
        for i in sorted(old_bad, key=lambda x: -x['oldN'])[:5]:
            print('    n=%-3d %s' % (i['oldN'], i['old'][:96]))
            print('      新: n=%d %s' % (i['neuN'], i['neu'][:96]))

        # class 候选：旧路径不唯一的那些元素里，有多少能靠 class 名字救回来
        rescued, clsmatch = 0, 0
        samples = []
        for i in old_bad:
            stems = class_stems(i['cls'])
            if not stems:
                continue
            for stem, exact in stems:
                sel = ('[class~="%s"]' % stem) if exact else ('[class*="%s"]' % stem)
                n = page.evaluate('(s) => { try { return document.querySelectorAll(s).length } catch (e) { return -1 } }', sel)
                if n == 1:
                    clsmatch += 1
                    if i['oldN'] != 1:
                        rescued += 1
                    samples.append((stem, sel, i['tag']))
                    break
        print('\n=== class 候选（旧路径不唯一的 %d 条里）' % len(old_bad))
        print('  能剥出唯一 class 定位的：%d' % rescued)
        for stem, sel, tag in samples[:6]:
            print('    <%s> %s' % (tag, sel))

        total_cls = sum(1 for i in items if class_stems(i['cls']))
        print('  全部元素里能从 class 剥出名字的：%d / %d' % (total_cls, len(items)))

        uniq_cls = 0
        for i in items:
            for stem, exact in class_stems(i['cls']):
                sel = ('[class~="%s"]' % stem) if exact else ('[class*="%s"]' % stem)
                n = page.evaluate('(s) => { try { return document.querySelectorAll(s).length } catch (e) { return -1 } }', sel)
                if n == 1:
                    uniq_cls += 1
                    break
        print('  其中 class 候选在页面上唯一的（可直接当主定位）：%d / %d' % (uniq_cls, len(items)))

        browser.close()


if __name__ == '__main__':
    main()
