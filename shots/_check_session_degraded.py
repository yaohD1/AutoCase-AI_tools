"""真机验证降级：令牌无效时，后端应重新开会话并照常生成。

    .venv/Scripts/python.exe shots/_check_session_degraded.py
"""
import json
import os
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = 'http://127.0.0.1:5000/api'
MODEL = os.environ.get('AB_MODEL', '4rount-hyper/gpt-6-luna')
IMAGE = os.path.join(ROOT, 'shots', '_case_src_login.png')


def main():
    project = requests.post(API + '/projects', json={'name': '临时-降级验证'}).json()
    project_id = (project.get('project') or project).get('id')
    print('项目 %s' % project_id)
    try:
        with open(IMAGE, 'rb') as handle:
            upload = requests.post(API + '/upload',
                                   files={'file': ('login.png', handle, 'image/png')},
                                   data={'project_id': project_id or ''})
        image_id = (upload.json().get('image') or {}).get('id')

        analyzed = requests.post(API + '/analyze-session', json={
            'project_id': project_id, 'config_id': MODEL, 'image_id': image_id,
            'description': '产品登录页',
        }, timeout=900).json()
        modules = analyzed.get('modules') or []
        print('分析得到 %d 个模块' % len(modules))
        if not modules:
            print(json.dumps(analyzed, ensure_ascii=False)[:300])
            return

        confirmed = [dict(modules[0], case_count=2)]
        started = time.time()
        result = requests.post(API + '/generate-session', json={
            'project_id': project_id,
            'config_id': MODEL,
            'token': 'token-that-does-not-exist',
            'image_id': image_id,
            'description': '产品登录页',
            'modules': confirmed,
        }, timeout=1200)
        body = result.json()
        print('降级生成 %s %.1fs count=%s degraded=%s'
              % (result.status_code, time.time() - started, body.get('count'),
                 body.get('degraded')))
        print('原因：%s' % str(body.get('reason'))[:100])
        cases = requests.get(API + '/cases', params={'project_id': project_id}).json()
        print('库里 %d 条' % len(cases.get('testcases') or []))
    finally:
        print('清理 %s' % requests.delete(API + '/projects/%s' % project_id).status_code)


if __name__ == '__main__':
    main()
