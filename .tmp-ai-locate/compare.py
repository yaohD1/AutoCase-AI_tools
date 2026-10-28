# -*- coding: utf-8 -*-
"""AI 定位实验 · 关键对比：真正难定位的元素，现有系统 vs AI。

每个目标跑两条链路，都在真实页面上验证：
  1) 现有系统：recorder 同源快照 → build_specs → 真实 count → pick_for / best_effort
  2) AI：同一份快照 + DOM 片段 + 页面清单 → AI 返回表达式 → 真实 count / 命中

判定「命中」靠页面上打的 data-ai-probe 标记。
"""
import json
import sys

sys.path.insert(0, 'D:/autoproject/Auto_case/backend')
sys.path.insert(0, 'D:/autoproject/Auto_case/.tmp-ai-locate')

from playwright.sync_api import sync_playwright  # noqa: E402

import harness  # noqa: E402
from app.services.recording.locators import build_locator, build_specs, pick_for, best_effort  # noqa: E402

OUT = harness.OUT

# 从 scan_hard 的结果里挑的代表性目标
HARD_TARGETS = [
    {'id': 'H1', 'desc': '左侧会话列表里，「日常问候开场」这一条会话右侧出现的操作图标（更多/菜单）'},
    {'id': 'H2', 'desc': '左侧「会话分组」标题右边的新增按钮（加号图标）'},
    {'id': 'H3', 'desc': '左上角用户头像右侧的侧边栏折叠/展开箭头'},
    {'id': 'H4', 'desc': '左侧导航「知识库」菜单项左边的图标'},
]

MARK_HARD_JS = harness.HELPERS + """
(args) => {
  const tid = args.tid;
  const box = (el) => { const r = el.getBoundingClientRect();
    return [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]; };
  const seen = (el) => el.getBoundingClientRect().width >= 6 && el.getBoundingClientRect().height >= 6;
  let hit = null;
  const all = [...document.querySelectorAll('*')].filter(seen);

  if (tid === 'H1') {
    // 会话项：文本为「日常问候开场」的元素，取它的最近可点祖先里的图标
    const label = all.find((el) => norm(el.innerText) === '日常问候开场');
    if (label) {
      const item = label.closest('div[class*="item"], li, div');
      hit = (item || label).querySelector('span[class*="ItemMenu"], svg');
    }
  } else if (tid === 'H2') {
    const head = all.find((el) => norm(el.innerText).includes('会话分组')
      && (el.getAttribute('class') || '').includes('sidebarListHeader'));
    hit = head ? head.querySelector('div[class*="HeaderAdd"] span, div[class*="HeaderAdd"]') : null;
  } else if (tid === 'H3') {
    hit = all.find((el) => (el.getAttribute('class') || '').includes('sidebarHeaderExpandable'));
  } else if (tid === 'H4') {
    const link = all.find((el) => el.tagName.toLowerCase() === 'a' && norm(el.innerText) === '知识库');
    hit = link ? link.querySelector('div > div, span, svg') : null;
  }
  if (!hit) return null;
  hit.setAttribute('data-ai-probe', tid);
  const snap = snapshot(hit);
  const r = hit.getBoundingClientRect();
  snap.box = box(hit);
  const chain = [];
  let cur = hit.parentElement;
  for (let i = 0; i < 4 && cur; i++) {
    chain.push({
      tag: cur.tagName.toLowerCase(),
      cls: (cur.getAttribute('class') || '').slice(0, 60),
      text: norm(cur.innerText).slice(0, 40),
    });
    cur = cur.parentElement;
  }
  snap.chain = chain;
  return snap;
}"""


def system_locator(page, snap, tid):
    """现有系统在这份快照上会产出什么。"""
    specs = build_specs(snap)
    counted = []
    for spec in specs:
        try:
            loc = build_locator(page, spec)
            n = loc.count()
        except Exception:
            n = -1
        hit = False
        if n > 0:
            try:
                hit = loc.evaluate_all(
                    "(els, t) => els.some((e) => e.getAttribute('data-ai-probe') === t)", tid)
            except Exception:
                hit = False
        counted.append({
            'kind': spec['kind'], 'desc': spec['desc'], 'count': n, 'spec': spec,
            'relation': 'self' if hit else ('other' if n > 0 else 'gone'),
        })
    picked = pick_for('playwright-ts', counted)
    best = best_effort('playwright-ts', counted)
    return {
        'specs': [{'kind': c['kind'], 'desc': c['desc'], 'count': c['count'],
                   'relation': c['relation']} for c in counted],
        'picked': {'kind': picked['kind'], 'desc': picked['desc']} if picked else None,
        'best': {'kind': best['kind'], 'desc': best['desc'], 'count': best['count']} if best else None,
    }


def main():
    import experiment as ex

    model = ex.load_model()
    print('模型:', model['model'])
    report = {'model': model['model'], 'targets': []}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(ignore_https_errors=True, viewport={'width': 1440, 'height': 900})
        page = ctx.new_page()
        print('登录...')
        harness.open_main(page)
        inventory = page.evaluate(harness.INVENTORY_JS)
        print('可交互元素:', len(inventory))

        for target in HARD_TARGETS:
            print('\n======== %s %s' % (target['id'], target['desc'][:40]))
            snap = page.evaluate(MARK_HARD_JS, {'tid': target['id']})
            if not snap:
                print('  未找到')
                continue
            snippet = page.evaluate(harness.DOM_SNIPPET_JS, target['id'])
            print('  元素: %s cls=%s box=%s' % (snap['tag'], snap['cls'][:40], snap['box']))
            print('  父链:', ' / '.join('%s[%s]' % (c['tag'], c['text'][:12]) for c in snap['chain']))

            sysinfo = system_locator(page, snap, target['id'])
            print('  现有系统:')
            for s in sysinfo['specs']:
                print('    %-10s count=%-4s %s %s' % (s['kind'], s['count'], s['relation'], s['desc'][:70]))
            print('    pick_for ->', (sysinfo['picked'] or {}).get('desc', 'None'))
            print('    best_effort ->', (sysinfo['best'] or {}).get('desc', 'None'))

            prompt = ex.build_prompt({'desc': target['desc'], 'snap': snap, 'dom': snippet},
                                     inventory, True)
            print('  prompt %d 字符' % len(prompt))
            resp = ex.ask(model, ex.SYSTEM, prompt)
            ai_expr, verified = None, None
            if 'error' in resp:
                print('  AI 调用失败:', resp['error'])
            else:
                answer = (resp['text'] or '').strip()
                ai_expr = next((ln.strip().strip('`') for ln in answer.splitlines()
                                if ln.strip() and not ln.strip().startswith('#')), '')
                print('  AI:', ai_expr[:160])
                verified = ex.verify(page, ai_expr, target['id'])
                print('    → syntax=%s count=%s hit=%s %s' % (
                    verified['syntax'], verified['count'], verified['hit'],
                    verified.get('error') or ''))

            report['targets'].append({
                'id': target['id'], 'desc': target['desc'],
                'snap': {k: snap.get(k) for k in
                         ('tag', 'role', 'name', 'text', 'aria_label', 'placeholder', 'testid',
                          'id', 'cls', 'css', 'box')},
                'chain': snap['chain'],
                'system': sysinfo,
                'ai': {'expr': ai_expr, 'verify': verified,
                       'error': resp.get('error'),
                       'tokens': (resp.get('usage') or {}).get('total_tokens')},
            })
            page.evaluate("(t) => { const e = document.querySelector('[data-ai-probe=\"' + t + '\"]'); "
                          "if (e) e.removeAttribute('data-ai-probe'); }", target['id'])

        browser.close()

    json.dump(report, open(OUT + '/compare.json', 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('\n已写 compare.json')


if __name__ == '__main__':
    main()
