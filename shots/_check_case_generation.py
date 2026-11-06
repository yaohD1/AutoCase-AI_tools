"""验证「首页生成用例」是否真的走 opencode：服务层 + HTTP 两条路径。

用法：
    .venv/Scripts/python.exe shots/_check_case_generation.py            # 服务层
    .venv/Scripts/python.exe shots/_check_case_generation.py --http     # 走真实接口（需后端在跑）

服务层只测形状与链路（图片有没有传进去、JSON 能不能解析、耗时）；
HTTP 那条会建临时项目、上传截图、真生成用例，跑完自己删项目。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'backend'))

MODEL = '4rount-hyper/gpt-6-luna'
IMAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_case_src_login.png')
API = 'http://127.0.0.1:5000/api'


def service_check():
    from app.services import models_config
    from app.services.case_generator import CaseGenerator
    from app.services.oc_text import TextError

    resolved = models_config.resolve(MODEL)
    print('模型: %s  vision=%s' % (resolved.ref, resolved.supports_vision))

    if not resolved.supports_vision:
        generator = CaseGenerator(resolved)
        try:
            generator.analyze_image(IMAGE)
            raise SystemExit('应该被拒绝却成功了')
        except TextError as exc:
            print('未开图片时的提示: %s' % str(exc)[:70])

    generator = CaseGenerator(resolved)
    started = time.time()
    modules = generator.analyze_image(IMAGE, '知识库产品登录页')
    print('图片分析 %.1fs，模块 %d 个：%s'
          % (time.time() - started, len(modules), [m.get('module') for m in modules][:4]))

    started = time.time()
    cases = generator.generate(IMAGE, ['functional', 'boundary'], 6, '登录页')
    print('单图生成 %.1fs，用例 %d 条' % (time.time() - started, len(cases)))
    for case in cases[:3]:
        print('  [%s] %s' % (case.get('priority'), case.get('title')))
    missing = [c for c in cases
               if not all(c.get(k) for k in ('module', 'title', 'priority', 'steps', 'expected'))]
    assert not missing, '有 %d 条用例缺字段' % len(missing)


def http_check():
    import requests
    project = requests.post(API + '/projects', json={'name': '临时-用例生成验证'}).json()
    project_id = (project.get('project') or project).get('id')
    try:
        with open(IMAGE, 'rb') as handle:
            upload = requests.post(API + '/upload',
                                   files={'file': ('login.png', handle, 'image/png')},
                                   data={'project_id': project_id or ''})
        image_id = (upload.json().get('image') or {}).get('id')
        print('上传: %s  image=%s' % (upload.status_code, image_id))

        started = time.time()
        analyzed = requests.post(API + '/analyze-image',
                                 json={'image_id': image_id, 'project_id': project_id,
                                       'config_id': MODEL, 'description': '登录页'},
                                 timeout=300)
        print('分析接口: %s %.1fs 模块 %d 个'
              % (analyzed.status_code, time.time() - started,
                 len((analyzed.json() or {}).get('modules') or [])))

        started = time.time()
        generated = requests.post(API + '/generate',
                                  json={'image_id': image_id, 'project_id': project_id,
                                        'config_id': MODEL, 'case_types': ['functional'],
                                        'case_count': 4, 'description': '登录页'},
                                  timeout=400)
        print('生成接口: %s %.1fs %s'
              % (generated.status_code, time.time() - started, generated.text[:120]))
        assert generated.status_code == 201, generated.text

        cases = requests.get(API + '/cases', params={'project_id': project_id}).json()
        items = cases.get('testcases') or cases.get('cases') or []
        print('入库: %d 条' % len(items))
        for case in items[:3]:
            print('  [%s] %s' % (case.get('priority'), case.get('title')))
    finally:
        if project_id:
            requests.delete(API + '/projects/%s' % project_id)
            print('临时项目已删除')


if __name__ == '__main__':
    if '--http' in sys.argv:
        http_check()
    else:
        service_check()
