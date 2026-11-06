# -*- coding: utf-8 -*-
"""models_config 的单测：合并视图、增删、白名单、runtime 合并。"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.services import models_config as mc


GLOBAL = {
    'model': 'glob/gpt-6-luna',
    'provider': {
        'glob': {
            'npm': '@ai-sdk/openai',
            'options': {'baseURL': 'https://glob.example/v1', 'apiKey': 'sk-glob-1234567890'},
            'models': {'gpt-6-luna': {'name': 'luna'}, 'gpt-6-sol': {'name': 'sol'}},
        }
    },
}

BASE = {
    '$schema': 'https://opencode.ai/config.json',
    'agent': {'recording-emitter': {'mode': 'subagent', 'permission': {}}},
    'mcp': {'x': {'type': 'local', 'command': ['echo']}},
}


class ModelsConfigTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='mc-test-'))
        self.global_file = self.root / 'global.json'
        self.global_file.write_text(json.dumps(GLOBAL), encoding='utf-8')
        (self.root / 'opencode.json').write_text(json.dumps(BASE), encoding='utf-8')
        self.patches = [
            mock.patch.object(mc, 'project_root', lambda: self.root),
            mock.patch.object(mc, 'global_path', lambda: self.global_file),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.root, ignore_errors=True)

    def local(self):
        return json.loads((self.root / mc.LOCAL_FILE).read_text(encoding='utf-8'))

    # ---------- 初始化 ----------

    def test_first_load_imports_global(self):
        view = mc.merged_view()
        self.assertTrue((self.root / mc.LOCAL_FILE).is_file())
        self.assertEqual(['glob'], [p['id'] for p in view['providers']])
        self.assertEqual('glob/gpt-6-luna', view['default'])
        # key 不能明文出现在视图里
        self.assertNotIn('sk-glob-1234567890', json.dumps(view))
        self.assertEqual('sk-glo••••7890', view['providers'][0]['key_masked'])

    def test_flat_models_carry_sampling_fields(self):
        mc.update_model('glob', 'gpt-6-sol', temperature=0.2, max_tokens=8192, vision=False)
        item = next(m for m in mc.flat_models() if m['id'] == 'glob/gpt-6-sol')
        self.assertEqual(0.2, item['temperature'])
        self.assertEqual(8192, item['max_tokens'])
        self.assertFalse(item['vision'])
        self.assertEqual('https://glob.example/v1', item['api_base'])

    # ---------- provider ----------

    def test_create_provider_is_local_and_masked(self):
        view = mc.upsert_provider('mine', base_url='https://mine/v1', api_key='sk-mine-abcdefgh')
        provider = next(p for p in view['providers'] if p['id'] == 'mine')
        self.assertEqual('local', provider['source'])
        self.assertTrue(provider['has_key'])
        self.assertEqual('sk-min••••efgh', provider['key_masked'])
        self.assertEqual('@ai-sdk/openai', provider['npm'])

    def test_blank_api_key_keeps_old_one(self):
        mc.upsert_provider('mine', base_url='https://mine/v1', api_key='sk-mine-abcdefgh')
        mc.upsert_provider('mine', base_url='https://mine/v2', api_key=None)
        raw = mc._raw_provider('mine')
        self.assertEqual('sk-mine-abcdefgh', raw['options']['apiKey'])
        self.assertEqual('https://mine/v2', raw['options']['baseURL'])

    def test_delete_provider_removes_local_only(self):
        mc.upsert_provider('mine', base_url='https://mine/v1', api_key='sk-mine-abcdefgh')
        view = mc.delete_provider('mine')
        self.assertEqual(['glob'], [p['id'] for p in view['providers']])

    def test_hidden_provider_disappears_from_flat(self):
        mc.set_provider_hidden('glob', True)
        self.assertIn('glob', mc.merged_view()['disabled_providers'])
        self.assertEqual([], [m for m in mc.flat_models() if m['provider'] == 'glob'])
        # 但仍能在视图里看到，界面上要能取消隐藏
        self.assertTrue(next(p for p in mc.merged_view()['providers']
                             if p['id'] == 'glob')['disabled'])
        mc.set_provider_hidden('glob', False)
        self.assertTrue([m for m in mc.flat_models() if m['provider'] == 'glob'])

    # ---------- model ----------

    def test_add_model_updates_runtime_after_apply(self):
        mc.add_model('glob', 'gpt-6-mini', temperature=0.1, max_tokens=1024, vision=True)
        ids = [m['id'] for p in mc.merged_view()['providers'] if p['id'] == 'glob'
               for m in p['models']]
        self.assertIn('gpt-6-mini', ids)
        runtime = json.loads(mc.runtime_config().read_text(encoding='utf-8'))
        self.assertIn('gpt-6-mini', runtime['provider']['glob']['models'])
        # agent / mcp 必须还在，否则生成链路会崩
        self.assertIn('agent', runtime)
        self.assertEqual(BASE['agent'], runtime['agent'])

    def test_remove_global_model_writes_whitelist(self):
        view = mc.remove_model('glob', 'gpt-6-sol')
        provider = next(p for p in view['providers'] if p['id'] == 'glob')
        self.assertEqual(['gpt-6-luna'], [m['id'] for m in provider['models']])
        self.assertEqual(['gpt-6-luna'], provider['whitelist'])
        # 全局文件本身不能被改
        self.assertIn('gpt-6-sol', json.loads(self.global_file.read_text(encoding='utf-8'))
                      ['provider']['glob']['models'])

    def test_remove_local_model_drops_definition(self):
        mc.add_model('glob', 'extra')
        mc.remove_model('glob', 'extra')
        ids = [m['id'] for p in mc.merged_view()['providers'] if p['id'] == 'glob'
               for m in p['models']]
        self.assertNotIn('extra', ids)
        self.assertIsNone(next(p for p in mc.merged_view()['providers']
                               if p['id'] == 'glob')['whitelist'])

    def test_whitelist_keeps_newly_added_model(self):
        mc.remove_model('glob', 'gpt-6-sol')
        mc.add_model('glob', 'gpt-6-sol')  # 再加回来
        provider = next(p for p in mc.merged_view()['providers'] if p['id'] == 'glob')
        self.assertIn('gpt-6-sol', [m['id'] for m in provider['models']])
        self.assertIn('gpt-6-sol', provider['whitelist'])

    def test_model_name_does_not_change_id(self):
        mc.update_model('glob', 'gpt-6-luna', name='Luna Pro')
        provider = next(p for p in mc.merged_view()['providers'] if p['id'] == 'glob')
        model = next(m for m in provider['models'] if m['id'] == 'gpt-6-luna')
        self.assertEqual('Luna Pro', model['name'])
        self.assertEqual('gpt-6-luna', model['id'])

    # ---------- 默认模型 / 解析 ----------

    def test_set_default_rejects_unknown(self):
        with self.assertRaises(mc.ModelConfigError):
            mc.set_default('nope/xxx')

    def test_set_default_persists(self):
        mc.set_default('glob/gpt-6-sol')
        self.assertEqual('glob/gpt-6-sol', mc.merged_view()['default'])

    def test_resolve_uses_real_key_not_mask(self):
        resolved = mc.resolve('glob/gpt-6-sol')
        self.assertEqual('sk-glob-1234567890', resolved.api_key)
        self.assertEqual('https://glob.example/v1', resolved.api_base)
        self.assertEqual('gpt-6-sol', resolved.model)

    def test_resolve_falls_back_to_first_model(self):
        resolved = mc.resolve('glob')
        self.assertEqual('glob/gpt-6-luna', resolved.ref)

    def test_resolve_hidden_provider_returns_none(self):
        mc.set_provider_hidden('glob', True)
        self.assertIsNone(mc.resolve('glob/gpt-6-luna'))

    # ---------- runtime ----------

    def test_runtime_keeps_agent_and_merges_provider(self):
        mc.upsert_provider('mine', base_url='https://mine/v1', api_key='sk-mine-abcdefgh')
        runtime = json.loads(mc.runtime_config().read_text(encoding='utf-8'))
        self.assertIn('mine', runtime['provider'])
        self.assertIn('glob', runtime['provider'])  # 本地导入后也在
        self.assertEqual(BASE['mcp'], runtime['mcp'])

    def test_runtime_falls_back_when_base_missing(self):
        (self.root / 'opencode.json').unlink()
        path = mc.runtime_config()
        self.assertEqual(self.root / 'opencode.json', Path(path))

    def test_local_file_is_invalid_json(self):
        (self.root / mc.LOCAL_FILE).write_text('{oops', encoding='utf-8')
        with self.assertRaises(mc.ModelConfigError):
            mc.load_local()

    def test_bad_provider_name_rejected(self):
        with self.assertRaises(mc.ModelConfigError):
            mc.upsert_provider('a/b', base_url='x', api_key='y')
        with self.assertRaises(mc.ModelConfigError):
            mc.add_model('glob', 'm/1')


if __name__ == '__main__':
    unittest.main()
