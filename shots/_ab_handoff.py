"""「AI 拆模块 → 用户改 → 接着生成」的可行性对比。

同一次 session 挂起（H_same） vs 新开 session 重发图片和模块（H_new）。

关键不是质量，而是**用户编辑有没有被遵守**：
- 删掉的模块不能又被生成出来
- 改过名的模块，module 字段必须用新名字
- 每模块条数按用户设定

    .venv/Scripts/python.exe shots/_ab_handoff.py --only login
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'backend'))
sys.path.insert(0, os.path.join(ROOT, 'shots'))

import requests                                                    # noqa: E402

from _ab_case_flow import MODEL, PAGES, TYPES, load, path_for, save  # noqa: E402
from app.services.case_generator import SYSTEM_CASES, _normalise   # noqa: E402
from app.services.oc_text import (                                 # noqa: E402
    ASK_NO_TOOLS, TextWorker, _image_part, parse_json)
from app.services.opencode_runner import OpenCodeRunner            # noqa: E402
from app.utils import PromptTemplates                              # noqa: E402

PER_MODULE = 5


def post(base, sid, ref, system, text, images=(), timeout=900):
    parts = [{'type': 'text', 'text': text}]
    for path in images:
        parts.append(_image_part(path))
    body = {'parts': parts, 'system': system, 'tools': dict(ASK_NO_TOOLS),
            'format': {'type': 'text'},
            'model': {'providerID': ref['providerID'], 'modelID': ref['id']}}
    started = time.time()
    reply = requests.post('%s/session/%s/message' % (base, sid), json=body, timeout=timeout)
    payload = reply.json() or {}
    error = (payload.get('info') or {}).get('error')
    if reply.status_code != 200 or error:
        raise RuntimeError('调用失败 %s %s' % (reply.status_code, str(error)[:200]))
    out = ''.join(p.get('text', '') for p in payload.get('parts', [])
                  if p.get('type') == 'text').strip()
    return out, round(time.time() - started, 1)


def gen_prompt(modules):
    """两边的生成指令完全一致，只差「在哪个会话里说」。"""
    listing = json.dumps(modules, ensure_ascii=False, indent=2)
    return ('## 用户已确认的模块清单\n'
            '这份清单是用户在原型分析结果上增删改之后确认的，请严格按它生成：\n'
            '- 每个模块生成 %d 条用例\n'
            '- 不要为清单之外的模块生成用例\n'
            '- module 字段必须用清单里的名字，一个字都不要改\n\n'
            '```json\n%s\n```' % (PER_MODULE, listing))


def edit_modules(modules):
    """模拟用户操作：删掉最后一个模块，把第一个改名，其余保持。"""
    kept = [dict(m) for m in modules[:-1]]
    removed = modules[-1].get('module') if modules else ''
    if kept:
        kept[0]['module'] = str(kept[0].get('module') or '') + '（用户改名）'
    return kept, removed, kept[0].get('module') if kept else ''


def run(page):
    worker = TextWorker.shared({})
    ref = OpenCodeRunner._split_model(MODEL)
    base = worker._ensure_serve()

    # 第一步：AI 拆模块（真实会话，挂起不删）
    created = requests.post('%s/session' % base, json={'title': 'handoff'}, timeout=15)
    sid = (created.json() or {}).get('id')
    print('  会话 %s' % sid)
    started = time.time()
    raw, analyze_secs = post(base, sid, ref, '你是一位资深的UI测试分析师，只输出 JSON 数组。',
                            PromptTemplates.IMAGE_ANALYSIS_PROMPT_SINGLE + '\n\n' + page['desc'],
                            images=[page['path']])
    modules = _normalise(parse_json(raw))
    print('  拆模块 %.1fs：%s' % (analyze_secs, [m.get('module') for m in modules]))
    if not modules:
        print('  没拆出模块，跳过')
        return

    kept, removed, renamed = edit_modules(modules)
    print('  用户编辑：删掉「%s」，第一个改名成「%s」，每模块 %d 条'
          % (removed, renamed, PER_MODULE))

    prompt = gen_prompt(kept)

    # A：同一个会话接着干（不重挂图）
    started = time.time()
    same_raw, same_secs = post(base, sid, ref, SYSTEM_CASES, prompt)
    same = _normalise(parse_json(same_raw))
    print('  同会话生成 %.1fs %d 条' % (same_secs, len(same)))

    # B：新会话，重发图 + 同一份模块清单
    created2 = requests.post('%s/session' % base, json={'title': 'handoff-new'}, timeout=15)
    sid2 = (created2.json() or {}).get('id')
    new_raw, new_secs = post(base, sid2, ref, SYSTEM_CASES, prompt, images=[page['path']])
    fresh = _normalise(parse_json(new_raw))
    print('  新会话生成 %.1fs %d 条' % (new_secs, len(fresh)))

    for sid_ in (sid, sid2):
        try:
            requests.delete('%s/session/%s' % (base, sid_), timeout=10)
        except requests.RequestException:
            pass

    meta = {'removed': removed, 'renamed': renamed, 'per_module': PER_MODULE,
            'analyze_secs': round(analyze_secs, 1),
            'modules_confirmed': kept}
    save(page['key'], 'H_same', dict(meta, variant='H_same', cases=same,
                                     secs=round(same_secs + analyze_secs, 1)))
    save(page['key'], 'H_new', dict(meta, variant='H_new', cases=fresh,
                                    secs=round(new_secs + analyze_secs, 1)))


def report():
    for page in PAGES:
        for variant in ('H_same', 'H_new'):
            data = load(page['key'], variant)
            if not data:
                continue
            cases = data['cases']
            removed, renamed = data['removed'], data['renamed']
            leaked = [c for c in cases if removed and removed in str(c.get('module'))]
            renamed_hits = [c for c in cases if renamed and str(c.get('module')) == renamed]
            other_modules = {str(c.get('module')) for c in cases} - {
                str(m.get('module')) for m in data['modules_confirmed']}
            verifiable = sum(1 for c in cases
                             if len(str(c.get('expected') or '')) >= 8)
            print('%-8s %-7s %2d 条  删掉的模块漏出 %d  改名命中 %d  清单外模块 %d  有预期 %d'
                  % (page['key'], variant, len(cases), len(leaked),
                     len(renamed_hits), len(other_modules), verifiable))
            if other_modules:
                print('        清单外：%s' % list(other_modules)[:4])


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--only', default='')
    parser.add_argument('--report', action='store_true')
    args = parser.parse_args()
    if args.report:
        report()
    else:
        for page in PAGES:
            if args.only and page['key'] != args.only:
                continue
            print('=== %s ===' % page['key'])
            run(page)
