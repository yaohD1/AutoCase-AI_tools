"""真机验证：分析挂起会话 → 用户改模块 → 同一会话接续生成。

    .venv/Scripts/python.exe shots/_check_session_flow_http.py
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
    project = requests.post(API + '/projects', json={'name': '临时-挂起会话验证'}).json()
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
        analyzed = requests.post(API + '/analyze-session', json={
            'project_id': project_id,
            'config_id': MODEL,
            'image_id': image_id,
            'description': '产品登录页，支持微信扫码登录与账号密码登录',
        }, timeout=900)
        analyze_secs = time.time() - started
        body = analyzed.json()
        modules = body.get('modules') or []
        token = body.get('token') or ''
        print('分析 %s %.1fs 模块 %d 个 token=%s'
              % (analyzed.status_code, analyze_secs, len(modules), token[:8]))
        if not modules or not token:
            print(json.dumps(body, ensure_ascii=False)[:400])
            return
        for module in modules:
            print('   - %s' % module.get('module'))

        # 模拟用户在弹窗里的操作：删掉最后一个、第一个改名、每模块 3 条
        removed = modules[-1].get('module')
        confirmed = []
        for index, module in enumerate(modules[:-1]):
            item = dict(module)
            if index == 0:
                item['module'] = str(item.get('module')) + '（用户改名）'
            item['case_count'] = 3
            item['case_types'] = ['functional', 'ui']
            confirmed.append(item)
        renamed = confirmed[0]['module']
        print('编辑：删「%s」，改名「%s」，每模块 3 条' % (removed, renamed))

        started = time.time()
        generated = requests.post(API + '/generate-session', json={
            'project_id': project_id,
            'config_id': MODEL,
            'token': token,
            'image_id': image_id,
            'description': '产品登录页',
            'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'modules': confirmed,
        }, timeout=1200)
        gen_secs = time.time() - started
        result = generated.json()
        print('生成 %s %.1fs count=%s degraded=%s reason=%s'
              % (generated.status_code, gen_secs, result.get('count'),
                 result.get('degraded'), str(result.get('reason'))[:80]))
        if generated.status_code != 201:
            print(json.dumps(result, ensure_ascii=False)[:400])
            return

        cases = requests.get(API + '/cases', params={'project_id': project_id}).json()
        rows = cases.get('testcases') or []
        names = {str(r.get('module')) for r in rows}
        leaked = [r for r in rows if removed and removed in str(r.get('module'))]
        renamed_hit = [r for r in rows if str(r.get('module')) == renamed]
        outside = names - {str(m.get('module')) for m in confirmed}
        print('库里 %d 条，模块 %d 个' % (len(rows), len(names)))
        print('  删除模块漏出 %d 条 / 改名命中 %d 条 / 清单外模块 %s'
              % (len(leaked), len(renamed_hit), outside or '无'))

        library = requests.get(API + '/pending-modules/approved',
                               params={'project_id': project_id}).json()
        lib_names = [m.get('module') for m in (library.get('modules') or [])]
        print('模块库 %d 个：%s' % (len(lib_names), lib_names))
        print('总耗时 %.1fs' % (analyze_secs + gen_secs))
    finally:
        print('清理 %s' % requests.delete(API + '/projects/%s' % project_id).status_code)


if __name__ == '__main__':
    main()
