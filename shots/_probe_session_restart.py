"""探针：serve 重启后，挂起中的 session 还在不在。

决定「挂起」方案必须配一条降级路径，还是可以直接假设会话一直在。

    .venv/Scripts/python.exe shots/_probe_session_restart.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'backend'))

import requests                                    # noqa: E402

from app.services.oc_text import TextWorker        # noqa: E402


def main():
    worker = TextWorker.shared({})
    base = worker._ensure_serve()
    created = requests.post('%s/session' % base, json={'title': 'restart-probe'}, timeout=15)
    sid = (created.json() or {}).get('id')
    print('旧 serve %s  session %s' % (base, sid))
    print('查询 %s' % requests.get('%s/session/%s' % (base, sid), timeout=10).status_code)

    worker.close()
    print('已杀掉 serve')

    base2 = worker._ensure_serve()
    print('新 serve %s' % base2)
    if base2 == base:
        print('端口复用，结论不可信')
    alive = requests.get('%s/session/%s' % (base2, sid), timeout=10)
    print('新 serve 上查旧 session：%s' % alive.status_code)
    print('列会话：%s' % requests.get('%s/session' % base2, timeout=10).text[:200])


if __name__ == '__main__':
    main()
