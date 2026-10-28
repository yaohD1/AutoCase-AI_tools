# -*- coding: utf-8 -*-
"""AI 定位实验 · 第三种形态：AI 只提候选，代码在真实页面上验证。

单条定位的准确率不够，但录制时页面是活的——
「count == 1 且命中的是目标」是可以确定性判定的。
所以关键问题变成：AI 给出的候选里，有没有一条是对的？

如果有多条候选能命中，这个功能就成立；一条都没有，功能就是空的。
"""
import json
import sys

sys.path.insert(0, 'D:/autoproject/Auto_case/backend')
sys.path.insert(0, 'D:/autoproject/Auto_case/.tmp-ai-locate')

from playwright.sync_api import sync_playwright  # noqa: E402

import compare  # noqa: E402
import experiment as ex  # noqa: E402
import harness  # noqa: E402

OUT = harness.OUT

SYSTEM_MULTI = '你是 Playwright 定位专家。你只输出 Playwright 表达式，每行一条，不解释、不加编号、不加代码块。'

USER_MULTI = """下面是一个正在运行的 Web 页面，录制器捕获了一次操作，需要定位到那个元素。

【目标元素描述】{desc}

【录制器采集到的目标快照】
{snap}

【目标周边 DOM（向上 2 层，已去掉 svg path）】
{dom}

【页面上所有可交互元素】
{inventory}

请给出**最多 5 个候选定位**，按你认为的可靠程度从高到低排列，每行一条 Playwright 表达式。
要求：
1. 每行形如 `page.getByRole('button', {{ name: '发送' }})` 或 `page.locator('css 选择器')`；
2. 优先语义定位（getByRole / getByLabel / getByPlaceholder / getByTestId / getByText）；
3. 不要用 class 里的哈希后缀（如 `_itemMenuIcon_fxrlp_45` 里的 `fxrlp_45`）；
4. 只输出表达式，每行一条，不要编号、不要解释、不要用 ``` 包裹。"""


def main():
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

        for target in compare.HARD_TARGETS:
            print('\n======== %s %s' % (target['id'], target['desc'][:34]))
            snap = page.evaluate(compare.MARK_HARD_JS, {'tid': target['id']})
            if not snap:
                print('  未找到')
                continue
            snippet = page.evaluate(harness.DOM_SNIPPET_JS, target['id'])

            prompt = USER_MULTI.format(
                desc=target['desc'],
                snap=json.dumps({k: v for k, v in snap.items() if k != 'css'},
                                ensure_ascii=False, indent=1),
                dom=snippet,
                inventory=json.dumps(inventory, ensure_ascii=False, indent=1),
            )
            resp = ex.ask(model, SYSTEM_MULTI, prompt, max_tokens=900)
            if 'error' in resp:
                print('  调用失败:', resp['error'])
                continue

            lines = [ln.strip().strip('`').strip() for ln in (resp['text'] or '').splitlines()]
            exprs = []
            for ln in lines:
                if not ln or ln.startswith('#') or not ln.startswith('page.'):
                    continue
                if ln not in exprs:
                    exprs.append(ln)
            exprs = exprs[:5]
            print('  AI 给了 %d 条候选' % len(exprs))

            results = []
            winner = None
            for expr in exprs:
                v = ex.verify(page, expr, target['id'])
                results.append(v)
                mark = ''
                if v['syntax'] == 'ok' and v['count'] == 1 and v['hit'] == 'yes':
                    mark = '  <== 命中'
                    if winner is None:
                        winner = expr
                print('    count=%-4s hit=%-4s %s%s' % (
                    v['count'], v['hit'], expr[:110], mark))

            print('  结论:', '第 %d 条就命中' % (exprs.index(winner) + 1) if winner else '5 条全没命中')

            report['targets'].append({
                'id': target['id'], 'desc': target['desc'],
                'system': 'css count=1' if target['id'] != 'H3' else 'css count=5',
                'candidates': results,
                'winner_index': (exprs.index(winner) + 1) if winner else None,
                'tokens': (resp.get('usage') or {}).get('total_tokens'),
            })
            page.evaluate("(t) => { const e = document.querySelector('[data-ai-probe=\"' + t + '\"]'); "
                          "if (e) e.removeAttribute('data-ai-probe'); }", target['id'])

        browser.close()

    json.dump(report, open(OUT + '/multi.json', 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('\n已写 multi.json')


if __name__ == '__main__':
    main()
