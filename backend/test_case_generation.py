"""用例生成链路：JSON 解析、提示词形状、视觉开关。

模型调用一律用假 worker，不真的起 opencode——这一层要保证的是「传对了什么」，
真机跑不跑得通由 `shots/_verify_cases.py` 那种脚本覆盖。
"""
import json
import os
import tempfile
import unittest

from app.services import models_config
from app.services.case_generator import CaseGenerator, _normalise
from app.services.oc_text import TextError, parse_json


class FakeWorker(object):
    def __init__(self, payload=None, raw=None):
        self.payload = payload if payload is not None else [
            {'module': '登录', 'title': '用例：登录成功 #P0', 'priority': 'P0',
             'steps': ['步骤：输入账号', '步骤：点击登录'], 'expected': '预期：进入首页'},
        ]
        self.raw = raw
        self.calls = []

    def ask(self, model, system, text, images=None, timeout=None):
        self.calls.append({'model': model, 'system': system, 'text': text,
                           'images': list(images or [])})
        if self.raw is not None:
            return self.raw
        return json.dumps(self.payload, ensure_ascii=False)

    def ask_json(self, model, system, text, images=None, timeout=None):
        return parse_json(self.ask(model, system, text, images=images, timeout=timeout))


class FakeModel(object):
    def __init__(self, ref='p/m', vision=True):
        self.ref = ref
        self.provider = ref.split('/')[0]
        self.model = ref.split('/')[1]
        self.supports_vision = vision


class ParseJsonTest(unittest.TestCase):
    def test_plain_array(self):
        self.assertEqual([{'a': 1}], parse_json('[{"a":1}]'))

    def test_fenced_block(self):
        text = '这是结果：\n```json\n[{"a":1}]\n```\n以上。'
        self.assertEqual([{'a': 1}], parse_json(text))

    def test_object_inside_prose(self):
        self.assertEqual({'cases': []}, parse_json('好的，结果如下 {"cases": []} 完毕'))

    def test_trailing_comma(self):
        self.assertEqual([{'a': 1}, {'b': 2}], parse_json('[{"a":1},{"b":2},]'))

    def test_brace_inside_string_does_not_confuse_counter(self):
        data = [{'title': '用例：包含 } 和 ] 的标题'}]
        self.assertEqual(data, parse_json(json.dumps(data, ensure_ascii=False)))

    def test_empty_reply(self):
        with self.assertRaises(TextError):
            parse_json('   ')

    def test_garbage_mentions_snippet(self):
        with self.assertRaises(TextError) as ctx:
            parse_json('抱歉，我无法完成')
        self.assertIn('抱歉', str(ctx.exception))


class NormaliseTest(unittest.TestCase):
    def test_priority_and_type_defaults(self):
        out = _normalise([{'priority': '0'}, {'priority': 'p1', 'case_type': 'ui'}, {}])
        self.assertEqual(['P0', 'Pp1', 'P2'], [c['priority'] for c in out])
        self.assertEqual(['functional', 'ui', 'functional'], [c['case_type'] for c in out])

    def test_drops_non_dict(self):
        self.assertEqual(1, len(_normalise([{'priority': 'P0'}, 'oops', None])))


class CaseGeneratorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, content):
        path = os.path.join(self.tmp, name)
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(content)
        return path

    def test_generate_from_image_passes_image_and_description(self):
        worker = FakeWorker()
        generator = CaseGenerator(FakeModel(), worker=worker)
        cases = generator.generate(self._write('a.png', 'x'), ['functional'], 5, '登录页')
        call = worker.calls[0]
        self.assertEqual('p/m', call['model'])
        self.assertEqual(['登录页' in call['text']], [True])
        self.assertEqual(1, len(call['images']))
        self.assertEqual('P0', cases[0]['priority'])
        self.assertEqual('functional', cases[0]['case_type'])

    def test_generate_from_document_sends_no_image(self):
        worker = FakeWorker()
        generator = CaseGenerator(FakeModel(), worker=worker)
        doc = self._write('req.md', '# 登录需求\n账号密码登录。')
        generator.generate(doc, ['functional'], 3)
        call = worker.calls[0]
        self.assertEqual([], call['images'])
        self.assertIn('登录需求', call['text'])

    def test_generate_refuses_image_when_vision_off(self):
        generator = CaseGenerator(FakeModel(vision=False), worker=FakeWorker())
        with self.assertRaises(TextError) as ctx:
            generator.generate(self._write('a.png', 'x'), ['functional'], 3)
        self.assertIn('支持图片', str(ctx.exception))

    def test_document_still_works_without_vision(self):
        worker = FakeWorker()
        generator = CaseGenerator(FakeModel(vision=False), worker=worker)
        generator.generate(self._write('req.md', '需求'), ['functional'], 3)
        self.assertEqual([], worker.calls[0]['images'])

    def test_analyze_single_image_uses_single_prompt(self):
        worker = FakeWorker(payload=[{'module': '登录'}])
        generator = CaseGenerator(FakeModel(), worker=worker)
        generator.analyze_image(self._write('a.png', 'x'), '登录页')
        prompt = worker.calls[0]['text']
        self.assertIn('这张UI设计图', prompt)

    def test_analyze_multiple_images_replaces_count(self):
        worker = FakeWorker(payload=[{'module': '列表'}, {'module': '详情'}])
        generator = CaseGenerator(FakeModel(), worker=worker)
        paths = [self._write('a.png', 'x'), self._write('b.png', 'y')]
        modules = generator.analyze_images(paths, '列表和详情')
        self.assertEqual(2, len(worker.calls[0]['images']))
        self.assertIn('以下2张UI设计图', worker.calls[0]['text'])
        self.assertEqual(2, len(modules))

    def test_analyze_requires_vision(self):
        generator = CaseGenerator(FakeModel(vision=False), worker=FakeWorker())
        with self.assertRaises(TextError):
            generator.analyze_image(self._write('a.png', 'x'))

    def test_modules_without_vision_skips_images(self):
        worker = FakeWorker()
        generator = CaseGenerator(FakeModel(vision=False), worker=worker)
        generator.generate_from_modules([{'module': '登录'}], ['functional'], 4,
                                        image_paths=[self._write('a.png', 'x')])
        self.assertEqual([], worker.calls[0]['images'])
        self.assertIn('模块描述', worker.calls[0]['text'])


class PoolWorker(FakeWorker):
    """带会话能力的替身：open_session / send / close_session 都记下来。"""

    def __init__(self, payloads=None, fail_after=None):
        super(PoolWorker, self).__init__()
        self.payloads = payloads if payloads is not None else [
            [{'module': '登录', 'title': '用例：登录成功 #P0', 'priority': 'P0',
              'steps': ['步骤：输入账号'], 'expected': '预期：进入首页'}]
        ]
        self.fail_after = fail_after
        self.opened, self.sent, self.closed = [], [], []
        self.held = 0

    def open_session(self, base_url=None, title=''):
        session_id = 'sess_%d' % len(self.opened)
        self.opened.append(session_id)
        return 'http://fake', session_id

    def send(self, base_url, session_id, model, system, text, images=None, timeout=None):
        self.sent.append({'session': session_id, 'system': system, 'text': text,
                          'images': list(images or [])})
        if self.fail_after is not None and len(self.sent) > self.fail_after:
            raise TextError('会话中断')
        payload = self.payloads[min(len(self.sent) - 1, len(self.payloads) - 1)]
        return json.dumps(payload, ensure_ascii=False)

    def close_session(self, base_url, session_id):
        self.closed.append(session_id)

    def hold(self, seconds):
        self.held = seconds

    def release(self):
        self.held = 0

    def ask(self, model, system, text, images=None, timeout=None):
        # 降级路径会落到 ask_json → ask
        self.calls.append({'model': model, 'system': system, 'text': text,
                           'images': list(images or [])})
        payload = self.payloads[0]
        return json.dumps(payload, ensure_ascii=False)


class SessionFlowTest(unittest.TestCase):
    """拆模块挂起 → 用户改 → 接续生成。"""

    def setUp(self):
        from app.services.session_pool import pool
        self.pool = pool
        self.pool.clear()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        self.pool.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _png(self, name='a.png'):
        path = os.path.join(self.tmp, name)
        with open(path, 'wb') as handle:
            handle.write(b'png')
        return path

    def test_analyze_opens_session_and_holds(self):
        worker = PoolWorker(payloads=[[{'module': '登录方式切换', 'ui_elements': ['按钮']}]])
        generator = CaseGenerator(FakeModel(), worker=worker)
        modules, token = generator.analyze_in_session([self._png()], '登录页')
        self.assertEqual(1, len(modules))
        self.assertTrue(token)
        self.assertEqual(['sess_0'], worker.opened)
        self.assertEqual(1, worker.held > 0)
        self.assertEqual('sess_0', self.pool.take(token)['session_id'])

    def test_generate_continues_same_session_without_image(self):
        worker = PoolWorker(payloads=[
            [{'module': '登录', 'title': '用例：A #P0', 'priority': 'P0'}],
            [{'module': '登录', 'title': '用例：B #P1', 'priority': 'P1'}],
        ])
        generator = CaseGenerator(FakeModel(), worker=worker)
        _, token = generator.analyze_in_session([self._png()], '登录页')

        modules = [{'module': '登录', 'case_count': 3, 'case_types': ['ui']}]
        cases, meta = generator.generate_in_session(token, modules)

        self.assertEqual(2, len(worker.sent))
        second = worker.sent[1]
        self.assertEqual('sess_0', second['session'])
        self.assertEqual([], second['images'])
        self.assertIn('模块「登录」（3 条，类型：ui）', second['text'])
        self.assertFalse(meta['degraded'])
        self.assertEqual(1, len(cases))
        self.assertIsNone(self.pool.take(token))

    def test_removed_module_not_in_prompt(self):
        worker = PoolWorker()
        generator = CaseGenerator(FakeModel(), worker=worker)
        _, token = generator.analyze_in_session([self._png()])
        modules = [{'module': '登录方式切换', 'case_count': 2}]
        generator.generate_in_session(token, modules)
        prompt = worker.sent[1]['text']
        self.assertIn('登录方式切换', prompt)
        self.assertIn('清单外的模块一条都不要', prompt)

    def test_falls_back_when_token_gone(self):
        worker = PoolWorker()
        generator = CaseGenerator(FakeModel(), worker=worker)
        cases, meta = generator.generate_in_session(
            'not-a-token', [{'module': '登录', 'case_count': 2}], '登录页', [self._png()])
        self.assertTrue(meta['degraded'])
        self.assertEqual(1, len(worker.calls))          # 走 ask（新会话）
        self.assertEqual(1, len(worker.calls[0]['images']))
        self.assertIn('登录', worker.calls[0]['text'])
        self.assertEqual(1, len(cases))

    def test_partial_result_kept_when_session_breaks(self):
        worker = PoolWorker(payloads=[
            [{'module': 'A', 'title': '用例：A #P0', 'priority': 'P0'}],
            [{'module': 'B', 'title': '用例：B #P0', 'priority': 'P0'}],
        ], fail_after=2)   # 第 1 次是分析，第 2 次第一批生成成功，第 3 次断
        generator = CaseGenerator(FakeModel(), worker=worker)
        _, token = generator.analyze_in_session([self._png()])
        modules = [{'module': 'A', 'case_count': 30}, {'module': 'B', 'case_count': 20}]
        cases, meta = generator.generate_in_session(token, modules)
        self.assertTrue(meta['degraded'])
        self.assertTrue(meta['partial'])
        self.assertEqual(1, len(cases))                 # 第一批保住了

    def test_batches_split_by_case_count(self):
        from app.services.case_generator import split_batches
        modules = [{'module': 'A', 'case_count': 20}, {'module': 'B', 'case_count': 15},
                   {'module': 'C', 'case_count': 10}]
        self.assertEqual([['A', 'B'], ['C']],
                         [[m['module'] for m in batch] for batch in split_batches(modules)])

    def test_vision_off_refuses_analyze(self):
        generator = CaseGenerator(FakeModel(vision=False), worker=PoolWorker())
        with self.assertRaises(TextError) as ctx:
            generator.analyze_in_session([self._png()])
        self.assertIn('多模态', str(ctx.exception))


class SessionPoolTest(unittest.TestCase):
    def setUp(self):
        from app.services.session_pool import SessionPool
        self.pool = SessionPool(ttl=60, limit=2)

    def test_take_returns_meta(self):
        token = self.pool.put('s1', 'http://x', 'p/m', ['a.png'], 'proj')
        item = self.pool.take(token)
        self.assertEqual('s1', item['session_id'])
        self.assertEqual('p/m', item['model'])

    def test_expired_returns_none(self):
        token = self.pool.put('s1', 'http://x', 'p/m', [])
        self.pool._items[token]['created_at'] -= 3600
        self.assertIsNone(self.pool.take(token))

    def test_limit_evicts_oldest(self):
        first = self.pool.put('s1', 'http://x', 'p/m', [])
        self.pool.put('s2', 'http://x', 'p/m', [])
        self.pool.put('s3', 'http://x', 'p/m', [])
        self.assertIsNone(self.pool.take(first))
        self.assertEqual(2, self.pool.active())

    def test_drop(self):
        token = self.pool.put('s1', 'http://x', 'p/m', [])
        self.pool.drop(token)
        self.assertIsNone(self.pool.take(token))


class VisionFlagTest(unittest.TestCase):
    def test_explicit_setting_wins(self):
        self.assertFalse(models_config._vision_flag({'modalities': {'input': ['text', 'image']}},
                                                    {'vision': False}))
        self.assertTrue(models_config._vision_flag({}, {'vision': True}))

    def test_falls_back_to_modalities(self):
        self.assertTrue(models_config._vision_flag(
            {'modalities': {'input': ['text', 'image']}}, {}))
        self.assertFalse(models_config._vision_flag({'modalities': {'input': ['text']}}, {}))
        self.assertFalse(models_config._vision_flag({}, {}))


class SyncModalitiesTest(unittest.TestCase):
    def test_truthy_vision_adds_image(self):
        providers = {'p': {'models': {'m': {'name': 'm'}}}}
        models_config._sync_modalities(providers, {'autocase': {'models': {'p/m': {'vision': True}}}})
        self.assertEqual(['text', 'image'],
                         providers['p']['models']['m']['modalities']['input'])

    def test_falsy_vision_strips_image(self):
        providers = {'p': {'models': {'m': {'modalities': {'input': ['text', 'image']}}}}}
        models_config._sync_modalities(providers, {'autocase': {'models': {'p/m': {'vision': False}}}})
        self.assertEqual(['text'], providers['p']['models']['m']['modalities']['input'])

    def test_untouched_when_not_configured(self):
        providers = {'p': {'models': {'m': {'name': 'm'}}}}
        models_config._sync_modalities(providers, {'autocase': {'models': {}}})
        self.assertNotIn('modalities', providers['p']['models']['m'])


if __name__ == '__main__':
    unittest.main()
