"""真机验证两段式：上传 → /generate-draft → /generate-review → 查库。

    .venv/Scripts/python.exe shots/_check_two_stage_http.py

跑完自己删临时项目，不留垃圾数据。
"""
import json
import os
import sys
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = 'http://127.0.0.1:5000/api'
MODEL = os.environ.get('AB_MODEL', '4rount-hyper/gpt-6-luna')
IMAGE = os.path.join(ROOT, 'shots', '_case_src_login.png')


def main():
    created = requests.post(API + '/projects', json={'name': '临时-两段式验证'}).json()
    project = created.get('project') or created
    project_id = project.get('id')
    print('项目 %s' % project_id)
    try:
        with open(IMAGE, 'rb') as handle:
            upload = requests.post(API + '/upload',
                                   files={'file': ('login.png', handle, 'image/png')},
                                   data={'project_id': project_id or ''})
        image_id = (upload.json().get('image') or {}).get('id')
        print('上传 %s image=%s' % (upload.status_code, image_id))

        started = time.time()
        draft_res = requests.post(API + '/generate-draft', json={
            'project_id': project_id,
            'config_id': MODEL,
            'image_id': image_id,
            'case_types': ['functional', 'ui', 'boundary', 'exception'],
            'case_count': 12,
            'description': '产品登录页，支持微信扫码登录与账号密码登录',
        }, timeout=900)
        draft_secs = time.time() - started
        draft_body = draft_res.json()
        print('草稿 %s %.1fs %s 条' % (draft_res.status_code, draft_secs,
                                     len(draft_body.get('draft') or [])))
        if draft_res.status_code != 200:
            print(json.dumps(draft_body, ensure_ascii=False)[:400])
            return

        started = time.time()
        review_res = requests.post(API + '/generate-review', json={
            'project_id': project_id,
            'config_id': MODEL,
            'image_id': image_id,
            'description': '产品登录页，支持微信扫码登录与账号密码登录',
            'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'draft': draft_body['draft'],
        }, timeout=900)
        review_secs = time.time() - started
        review_body = review_res.json()
        print('自检 %s %.1fs 入库 %s 条 reviewed=%s'
              % (review_res.status_code, review_secs, review_body.get('count'),
                 review_body.get('reviewed')))
        if review_body.get('degraded'):
            print('  降级原因：%s' % str(review_body['degraded'])[:120])

        cases = requests.get(API + '/cases', params={'project_id': project_id}).json()
        rows = cases.get('testcases') or []
        print('库里的用例 %d 条，模块 %d 个'
              % (len(rows), len({r.get('module') for r in rows})))
        for row in rows[:6]:
            print('   [%s] %s' % (row.get('priority'), str(row.get('title'))[:48]))

        modules = requests.get(API + '/pending-modules/approved',
                               params={'project_id': project_id}).json()
        names = [m.get('module') for m in (modules.get('modules') or [])]
        print('模块库 %d 个：%s' % (len(names), names))

        print('总耗时 %.1fs' % (draft_secs + review_secs))
    finally:
        deleted = requests.delete(API + '/projects/%s' % project_id)
        print('清理项目 %s' % deleted.status_code)


if __name__ == '__main__':
    main()
