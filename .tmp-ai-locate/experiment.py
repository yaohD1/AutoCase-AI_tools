# -*- coding: utf-8 -*-
"""AI 定位实验：让 AI 在真实页面上给出定位，再用真实浏览器验证它对不对。

两组输入，对应两种可能的产品形态：
  A1 完整快照（含录制器算出的 css 路径）—— 产品里最容易实现的形态
  A2 只给语义属性 + DOM 片段，不给 css 路径 —— 逼 AI 自己合成语义定位

验证口径（每条表达式都在真实页面上跑）：
  syntax   表达式能不能被 Playwright 解析
  count    匹配到几个元素
  hit      命中的元素是不是我们标记的那个（data-ai-probe）
"""
import json
import re
import sys
import time

import requests
from playwright.sync_api import sync_playwright

sys.path.insert(0, 'D:/autoproject/Auto_case/.tmp-ai-locate')
import harness  # noqa: E402

OUT = harness.OUT
DB = 'D:/autoproject/Auto_case/storage/app.db'

# ---------- 模型 ----------


def load_model():
    import sqlite3
    con = sqlite3.connect(DB)
    row = con.execute(
        "select api_key, api_base, model from ai_configs "
        "where provider='volcengine' and is_active=1 and api_base != '' limit 1").fetchone()
    return {'key': row[0], 'base': row[1], 'model': row[2]}


def ask(model, system, user, max_tokens=800, retries=2):
    for attempt in range(retries + 1):
        try:
            resp = requests.post(
                model['base'],
                headers={'Authorization': 'Bearer ' + model['key'], 'Content-Type': 'application/json'},
                json={
                    'model': model['model'],
                    'messages': [{'role': 'system', 'content': system},
                                 {'role': 'user', 'content': user}],
                    'temperature': 0.1,
                    'max_tokens': max_tokens,
                },
                timeout=180,
            )
            if resp.status_code != 200:
                return {'error': 'HTTP %d %s' % (resp.status_code, resp.text[:120])}
            data = resp.json()
            return {'text': data['choices'][0]['message']['content'],
                    'usage': data.get('usage', {})}
        except Exception as exc:
            if attempt == retries:
                return {'error': str(exc)[:160]}
            time.sleep(2)


# ---------- prompt ----------

SYSTEM = (
    '你是 Playwright 定位专家。你只输出一行 Playwright 表达式，不解释、不加代码块标记。'
)

USER_TMPL = """下面是一个正在运行的 Web 页面。用户的录制器捕获了一次操作，需要你给出这条操作的定位。

【目标元素描述】{desc}

【录制器采集到的目标快照】
{snap}

【目标周边 DOM（向上 2 层，已去掉 svg path）】
{dom}

【页面上所有可交互元素】
{inventory}

请输出**一行** Playwright 表达式，能唯一且稳定地定位到该元素。要求：
1. 形如 `page.getByRole('button', {{ name: '发送' }})` 或 `page.locator('css 选择器')`；
2. 优先语义定位（getByRole / getByLabel / getByPlaceholder / getByTestId / getByText），其次才是 css；
3. 如果某个属性看起来是随机串（如 class 里的哈希后缀），不要用它；
4. 不要输出注释、不要输出原因、不要用 ``` 包裹。"""


def build_prompt(target, inventory, with_css):
    snap = dict(target['snap'])
    if not with_css:
        snap.pop('css', None)
    return USER_TMPL.format(
        desc=target['desc'],
        snap=json.dumps(snap, ensure_ascii=False, indent=1),
        dom=target['dom'],
        inventory=json.dumps(inventory, ensure_ascii=False, indent=1),
    )


# ---------- 表达式解析 ----------

def split_args(text):
    """按顶层逗号切参数。"""
    parts, depth, buf, quote = [], 0, '', None
    for ch in text:
        if quote:
            buf += ch
            if ch == quote:
                quote = None
            continue
        if ch in ('"', "'"):
            quote = ch
            buf += ch
        elif ch in '([{':
            depth += 1
            buf += ch
        elif ch in ')]}':
            depth -= 1
            buf += ch
        elif ch == ',' and depth == 0:
            parts.append(buf.strip())
            buf = ''
        else:
            buf += ch
    if buf.strip():
        parts.append(buf.strip())
    return parts


def parse_js_literal(text):
    text = text.strip()
    if not text:
        return None
    if text in ('true', 'false'):
        return text == 'true'
    if text == 'null':
        return None
    if re.fullmatch(r'-?\d+', text):
        return int(text)
    if re.fullmatch(r'-?\d+\.\d+', text):
        return float(text)
    if text[0] in ('"', "'") and text[-1] == text[0]:
        body = text[1:-1]
        body = body.replace('\\' + text[0], text[0]).replace('\\n', '\n')
        return body
    if text.startswith('{') and text.endswith('}'):
        out = {}
        for item in split_args(text[1:-1]):
            if ':' not in item:
                continue
            key, _, value = item.partition(':')
            key = key.strip().strip('"\'')
            out[key] = parse_js_literal(value)
        return out
    if text.startswith('/') and text.rfind('/') > 0:      # 正则
        return text
    return text


METHOD_MAP = {
    'getByRole': 'get_by_role',
    'getByText': 'get_by_text',
    'getByLabel': 'get_by_label',
    'getByPlaceholder': 'get_by_placeholder',
    'getByTestId': 'get_by_test_id',
    'getByAltText': 'get_by_alt_text',
    'getByTitle': 'get_by_title',
    'locator': 'locator',
}


def to_locator(page, expr):
    """把 AI 输出的一行表达式变成真实 Locator。失败抛 ValueError(原因)。"""
    code = (expr or '').strip().rstrip(';').strip()
    code = re.sub(r'^await\s+', '', code)
    if not code.startswith('page.'):
        raise ValueError('不是 page.* 调用: %s' % code[:60])
    code = code[5:]
    m = re.match(r'([A-Za-z]+)\(', code)
    if not m:
        raise ValueError('无法识别方法: %s' % code[:40])
    method = m.group(1)
    if method not in METHOD_MAP:
        raise ValueError('不支持的方法 %s' % method)
    start = m.end() - 1
    depth, end = 0, -1
    for i in range(start, len(code)):
        if code[i] == '(':
            depth += 1
        elif code[i] == ')':
            depth -= 1
            if depth == 0:
                end = i
                break
    if end < 0:
        raise ValueError('括号不匹配')
    args = [parse_js_literal(a) for a in split_args(code[start + 1:end])]
    kwargs = {}
    if args and isinstance(args[-1], dict):
        kwargs = args.pop()

    loc = getattr(page, METHOD_MAP[method])(*args, **kwargs)
    # .nth(n) / .first()
    tail = code[end + 1:]
    tail = re.sub(r'^\s*\.(first|last)\(\)', '.nth(0)', tail)
    pick = re.search(r'\.nth\((\d+)\)', tail)
    if pick:
        loc = loc.nth(int(pick.group(1)))
    elif '.first' in tail or '.last' in tail:
        loc = loc.first
    return loc


def verify(page, expr, tid):
    """在真实页面上验证一条表达式。"""
    result = {'expr': expr, 'syntax': 'ok', 'count': None, 'hit': None, 'error': None}
    try:
        loc = to_locator(page, expr)
    except Exception as exc:
        result['syntax'] = 'fail'
        result['error'] = str(exc)[:160]
        return result
    try:
        count = loc.count()
    except Exception as exc:
        result['syntax'] = 'fail'
        result['error'] = 'count 失败: ' + str(exc)[:120]
        return result
    result['count'] = count
    if count == 0:
        result['hit'] = 'none'
        return result
    # 逐个检查是否是我们标记的目标
    try:
        marked = loc.evaluate_all(
            "(els, tid) => els.map((e) => (e.getAttribute('data-ai-probe') === tid))", tid)
        result['hit'] = 'yes' if any(marked) else 'no'
        if count > 1:
            result['hits_marked_at'] = [i for i, v in enumerate(marked) if v]
    except Exception as exc:
        result['error'] = 'evaluate 失败: ' + str(exc)[:120]
    return result


def main():
    model = load_model()
    print('模型:', model['model'])

    report = {'model': model['model'], 'cases': []}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(ignore_https_errors=True, viewport={'width': 1440, 'height': 900})
        page = ctx.new_page()
        print('登录中...')
        url = harness.open_main(page)
        print('落到:', url)

        print('标记并采集目标...')
        data = harness.collect(page)
        print('采集到 %d 个目标，可交互元素 %d 个' % (len(data['targets']), len(data['inventory'])))
        json.dump(data, open(OUT + '/probes.json', 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)

        for tid, target in data['targets'].items():
            for variant, with_css in (('A1', True), ('A2', False)):
                label = '%s/%s' % (tid, variant)
                print('\n--- %s ---' % label)
                prompt = build_prompt(target, data['inventory'], with_css)
                print('prompt %d 字符' % len(prompt))
                resp = ask(model, SYSTEM, prompt)
                if 'error' in resp:
                    print('  调用失败:', resp['error'])
                    report['cases'].append({'case': label, 'desc': target['desc'],
                                            'error': resp['error']})
                    continue
                answer = (resp['text'] or '').strip()
                print('  AI:', answer.splitlines()[0][:160] if answer else '(空)')
                first = next((ln.strip() for ln in answer.splitlines()
                              if ln.strip() and not ln.strip().startswith('#')), '')
                first = first.strip('`').strip()
                verified = verify(page, first, tid) if first else {
                    'expr': '', 'syntax': 'fail', 'count': None, 'hit': None, 'error': 'AI 没给表达式'}
                verified.update({
                    'case': label,
                    'desc': target['desc'],
                    'variant': variant,
                    'true_css': target['snap']['css'],
                    'tokens': (resp.get('usage') or {}).get('total_tokens'),
                })
                print('  → syntax=%s count=%s hit=%s %s' % (
                    verified['syntax'], verified['count'], verified['hit'],
                    verified.get('error') or ''))
                report['cases'].append(verified)

        browser.close()

    json.dump(report, open(OUT + '/report.json', 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('\n===== 汇总 =====')
    for case in report['cases']:
        print('%-8s syntax=%-4s count=%-4s hit=%-4s %s' % (
            case.get('case'), case.get('syntax'), case.get('count'), case.get('hit'),
            (case.get('error') or '')))
    print('报告已写入 report.json')


if __name__ == '__main__':
    main()
