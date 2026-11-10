"""模型名校验：错在大小写就自动纠正，真不存在就直接说清楚。

为什么必须有这道关：OpenCode 对「不存在的 provider / model」既不报错也不排队，
prompt_async 照样 204，会话却永远不进 busy。实测同一份项目同一个模型——写对
1.5 秒开始，把 provider 首字母大写（4rount-hyper → 4Rount-hyper）就永远不开始，
最后只能等 busy 超时，抛出一句「派发后 60 秒内没有开始处理」，对不了因也给不了方向。
"""
import unittest
from unittest.mock import patch

from app.services.opencode_runner import OpenCodeRunner

ENTRIES = [
    {'id': '4rount-hyper/gpt-6-luna', 'provider': '4rount-hyper', 'model': 'gpt-6-luna'},
    {'id': '4rount-hyper/gpt-5.6-luna', 'provider': '4rount-hyper', 'model': 'gpt-5.6-luna'},
    {'id': 'cctq-codex/gpt-6-luna', 'provider': 'cctq-codex', 'model': 'gpt-6-luna'},
]


class ResolveModelTest(unittest.TestCase):
    def setUp(self):
        self.runner = OpenCodeRunner({})

    def _resolve(self, model):
        with patch('app.services.models_config.flat_models', return_value=ENTRIES):
            return self.runner._resolve_model(model)

    def test_exact_model_passes_through(self):
        self.assertEqual(self._resolve('4rount-hyper/gpt-6-luna'),
                         {'id': 'gpt-6-luna', 'providerID': '4rount-hyper'})

    def test_provider_case_is_corrected(self):
        with self.assertLogs('app.services.opencode_runner', level='WARNING') as logs:
            ref = self._resolve('4Rount-hyper/gpt-6-luna')
        self.assertEqual(ref, {'id': 'gpt-6-luna', 'providerID': '4rount-hyper'})
        self.assertIn('大小写', ''.join(logs.output))

    def test_whole_name_case_is_corrected(self):
        self.assertEqual(self._resolve('4ROUNT-HYPER/GPT-6-LUNA'),
                         {'id': 'gpt-6-luna', 'providerID': '4rount-hyper'})

    def test_unknown_provider_raises_with_hint(self):
        with self.assertRaises(ValueError) as ctx:
            self._resolve('nope-xxx/gpt-6-luna')
        message = str(ctx.exception)
        self.assertIn('nope-xxx/gpt-6-luna', message)
        self.assertIn('4rount-hyper/gpt-6-luna', message)

    def test_unknown_model_raises_with_hint(self):
        with self.assertRaises(ValueError) as ctx:
            self._resolve('4rount-hyper/nope-model')
        self.assertIn('4rount-hyper/nope-model', str(ctx.exception))

    def test_bad_format_still_returns_none(self):
        # 格式问题沿用原来的报错（「模型格式应为 providerID/modelID」），不在这里抢答
        self.assertIsNone(self._resolve(''))
        self.assertIsNone(self._resolve('no-slash'))

    def test_unreadable_config_does_not_block(self):
        with patch('app.services.models_config.flat_models', side_effect=RuntimeError('boom')):
            self.assertEqual(self.runner._resolve_model('whatever/model'),
                             {'id': 'model', 'providerID': 'whatever'})

    def test_empty_catalog_does_not_block(self):
        with patch('app.services.models_config.flat_models', return_value=[]):
            self.assertEqual(self.runner._resolve_model('whatever/model'),
                             {'id': 'model', 'providerID': 'whatever'})


if __name__ == '__main__':
    unittest.main()
