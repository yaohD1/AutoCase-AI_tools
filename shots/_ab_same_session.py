"""同一个 opencode session 里连续跑两轮，对比「跨 session + 全量文本」的效果。

    .venv/Scripts/python.exe shots/_ab_same_session.py --only login --img-mode ctx
    .venv/Scripts/python.exe shots/_ab_same_session.py --only login --img-mode attach

第一轮：图 + 直出 10 条（和线上 generate 同一套 prompt）
第二轮：同 session 里发「对照图补漏、去重、修预期、按区域命名 module」

--img-mode ctx    第二轮不重传图，靠会话上下文里的 image part
--img-mode attach 第二轮把图再挂一次（防止上下文里的图被裁掉）
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

from _ab_case_flow import (                                        # noqa: E402
    MODEL, PAGES, REVIEW_SYSTEM, TYPES, load, path_for, save)
from app.services import models_config                             # noqa: E402
from app.services.case_generator import (                          # noqa: E402
    IMAGE_RULES, SYSTEM_CASES, _normalise)
from app.services.oc_text import ASK_TIMEOUT, ASK_NO_TOOLS, TextWorker  # noqa: E402
from app.services.oc_text import _image_part                       # noqa: E402
from app.services.opencode_runner import OpenCodeRunner            # noqa: E402
from app.utils import PromptTemplates                              # noqa: E402


def post(base, sid, ref, system, text, images, timeout=600):
    parts = [{'type': 'text', 'text': text}]
    for path in images:
        parts.append(_image_part(path))
    body = {
        'parts': parts,
        'system': system,
        'tools': dict(ASK_NO_TOOLS),
        'format': {'type': 'text'},
        'model': {'providerID': ref['providerID'], 'modelID': ref['id']},
    }
    started = time.monotonic()
    reply = requests.post('%s/session/%s/message' % (base, sid), json=body,
                          timeout=timeout)
    if reply.status_code != 200:
        raise RuntimeError('模型调用失败（%s）：%s' % (reply.status_code, reply.text[:200]))
    payload = reply.json() or {}
    error = (payload.get('info') or {}).get('error')
    if error:
        raise RuntimeError('模型返回错误（%.0fs）：%s'
                           % (time.monotonic() - started, str(error)[:200]))
    return ''.join(p.get('text', '') for p in payload.get('parts', [])
                   if p.get('type') == 'text').strip()


def run(page, img_mode, count=10, style='owner'):
    from app.services.oc_text import parse_json

    worker = TextWorker.shared({})
    ref = OpenCodeRunner._split_model(MODEL)
    base = worker._ensure_serve()
    created = requests.post('%s/session' % base, json={'title': 'ab-same-session'},
                            timeout=15)
    sid = (created.json() or {}).get('id')
    if not sid:
        raise RuntimeError('建会话失败：%s' % created.text[:150])
    print('  会话 %s' % sid)

    started = time.time()
    first_prompt = (PromptTemplates.get_combined_prompt(TYPES, count) + IMAGE_RULES
                    + '\n\n## 额外功能介绍/业务说明\n' + page['desc'])
    try:
        draft_text = post(base, sid, ref, SYSTEM_CASES, first_prompt, [page['path']])
        draft_secs = time.time() - started
        draft = _normalise(parse_json(draft_text))
        print('  第一轮 %.1fs  %d 条' % (draft_secs, len(draft)))

        if style == 'external':
            # 和跨 session 那组完全相同的措辞：把上一轮结果当成「外部材料」审。
            # 想隔离的变量只有一个——同 session / 不同 session。
            second_prompt = ('## 已有用例（共 %d 条）\n```json\n%s\n```\n\n'
                             '设计图上的每个控件、状态和跳转都要有用例覆盖，缺的补上。'
                             '请按要求和设计图核对后输出最终版本。'
                             % (len(draft), json.dumps(draft, ensure_ascii=False)))
        else:
            second_prompt = ('上面你生成的 %d 条用例，请对照同一张设计图复核：'
                             '漏掉的控件、状态、跳转补上；重复的合并；'
                             '不可验证的预期改成能判定的；步骤写清点哪里填什么。'
                             '直接输出最终 JSON 数组。' % len(draft))
        images = [page['path']] if img_mode == 'attach' else []
        started = time.time()
        final_text = post(base, sid, ref, REVIEW_SYSTEM, second_prompt, images)
        final_secs = time.time() - started
        cases = _normalise(parse_json(final_text))
        print('  第二轮 %.1fs  %d 条' % (final_secs, len(cases)))
    finally:
        try:
            requests.delete('%s/session/%s' % (base, sid), timeout=10)
        except requests.RequestException:
            pass

    return {
        'variant': 'E_same_' + img_mode,
        'model': MODEL,
        'draft_count': len(draft),
        'draft_secs': round(draft_secs, 1),
        'secs': round(final_secs, 1),
        'total_secs': round(draft_secs + final_secs, 1),
        'cases': cases,
        'draft': draft,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--only', default='')
    parser.add_argument('--img-mode', default='ctx', choices=['ctx', 'attach'])
    parser.add_argument('--style', default='owner', choices=['owner', 'external'])
    parser.add_argument('--count', type=int, default=10)
    args = parser.parse_args()

    resolved = models_config.resolve(MODEL)
    print('模型 %s  vision=%s' % (resolved.ref, resolved.supports_vision))

    for page in PAGES:
        if args.only and page['key'] != args.only:
            continue
        print('\n=== %s ===' % page['key'])
        name = 'E_same_' + args.img_mode
        if args.style == 'external':
            name += '_ext'
        if os.path.exists(path_for(page['key'], name)):
            print('  已有结果，跳过')
            continue
        save(page['key'], name, run(page, args.img_mode, args.count, args.style))


if __name__ == '__main__':
    main()
