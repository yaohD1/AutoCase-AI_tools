"""探针：同一个 opencode session 挂起几分钟后，图和上文还在不在。

这决定「AI 拆完模块 → 用户改 → 接着同一轮对话生成」能不能成立。

    .venv/Scripts/python.exe shots/_probe_session.py --wait 120
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'backend'))

import requests                                                    # noqa: E402

from app.services.oc_text import ASK_NO_TOOLS, TextWorker, _image_part  # noqa: E402
from app.services.opencode_runner import OpenCodeRunner            # noqa: E402

MODEL = os.environ.get('AB_MODEL', '4rount-hyper/gpt-6-luna')
IMAGE = os.path.join(ROOT, 'shots', '_case_src_login.png')
EXPECT = ['手机号或邮箱', '密码', '忘记密码', '服务协议', '隐私政策']


def post(base, sid, ref, text, images=(), system='你是一个严谨的助手。', timeout=600):
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
        raise RuntimeError('失败 %s %s' % (reply.status_code, str(error)[:200]))
    text_out = ''.join(p.get('text', '') for p in payload.get('parts', [])
                       if p.get('type') == 'text').strip()
    return text_out, time.time() - started


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--wait', type=int, default=120)
    args = parser.parse_args()

    worker = TextWorker.shared({})
    ref = OpenCodeRunner._split_model(MODEL)
    base = worker._ensure_serve()
    print('serve %s' % base)

    created = requests.post('%s/session' % base, json={'title': 'probe'}, timeout=15)
    sid = (created.json() or {}).get('id')
    print('会话 %s' % sid)

    try:
        first, secs = post(base, sid, ref,
                           '先用一句话说明这是哪个页面，然后列出页面上所有可点的控件（不要 JSON）。',
                           images=[IMAGE])
        print('\n[第一轮 %.1fs] %s' % (secs, first[:300]))

        print('\n挂起 %d 秒（模拟用户改模块）...' % args.wait)
        time.sleep(args.wait)

        alive = requests.get('%s/session/%s' % (base, sid), timeout=15)
        print('会话查询 %s' % alive.status_code)

        second, secs = post(base, sid, ref,
                            '就刚才那张图，登录卡片里的输入框和链接分别是什么？'
                            '逐个念出它们上面的文字，不要猜。')
        print('\n[第二轮 %.1fs 不带图] %s' % (secs, second[:600]))

        hits = [word for word in EXPECT if word in second]
        print('\n图中文字命中 %d/%d：%s' % (len(hits), len(EXPECT), hits))

        third, secs = post(base, sid, ref,
                           '现在只依据上文，不要重新看图：第一轮你一共列出了几个控件？'
                           '把它们的名字按顺序重复一遍。')
        print('\n[第三轮 %.1fs 只考记忆] %s' % (secs, third[:400]))
    finally:
        requests.delete('%s/session/%s' % (base, sid), timeout=10)


if __name__ == '__main__':
    main()
