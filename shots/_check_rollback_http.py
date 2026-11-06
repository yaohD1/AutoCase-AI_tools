"""回滚后确认老链路还能跑：上传 → /analyze-image → /generate（带 modules）。

    .venv/Scripts/python.exe shots/_check_rollback_http.py
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
    project = requests.post(API + '/projects', json={'name': '临时-回滚验证'}).json()
    project_id = (project.get('project') or project).get('id')
    print('项目 %s' % project_id)
    try:
        with open(IMAGE, 'rb') as handle:
            upload = requests.post(API + '/upload',
                                   files={'file': ('login.png', handle, 'image/png')},
                                   data={'project_id': project_id or ''})
        image_id = (upload.json().get('image') or {}).get('id')
        print('上传 %s image=%s' % (upload.status_code, image_id))

        started = time.time()
        analyzed = requests.post(API + '/analyze-image', json={
            'project_id': project_id, 'config_id': MODEL, 'image_id': image_id,
            'description': '产品登录页，支持微信扫码登录与账号密码登录',
        }, timeout=900)
        modules = (analyzed.json() or {}).get('modules') or []
        print('分析 %s %.1fs 模块 %d 个：%s'
              % (analyzed.status_code, time.time() - started, len(modules),
                 [m.get('module') for m in modules][:4]))
        if not modules:
            print(json.dumps(analyzed.json(), ensure_ascii=False)[:300])
            return

        started = time.time()
        generated = requests.post(API + '/generate', json={
            'project_id': project_id,
            'config_id': MODEL,
            'case_types': ['functional', 'ui'],
            'case_count': 6,
            'description': '产品登录页',
            'modules': [modules[0]],
        }, timeout=900)
        print('生成 %s %.1fs %s' % (generated.status_code, time.time() - started,
                                   generated.json().get('count')))

        cases = requests.get(API + '/cases', params={'project_id': project_id}).json()
        rows = cases.get('testcases') or []
        print('库里 %d 条：%s' % (len(rows), [str(r.get('title'))[:30] for r in rows[:3]]))
    finally:
        print('清理 %s' % requests.delete(API + '/projects/%s' % project_id).status_code)


if __name__ == '__main__':
    main()
