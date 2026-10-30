"""AI 配置列表里的 Key 脱敏。

原来的写法是 `api_key[:20] + '***'`：看着像脱敏，其实常见 key 也就 30~40 位，
20 位已经覆盖大半；短 key（比如 11 位的测试 key）会整段原样返回。
现在只留前 6 + 后 4，长度 <= 12 的全遮。
"""
import unittest

from app.models.ai_config import mask_key


class MaskKeyTest(unittest.TestCase):
    def test_long_key_keeps_only_ends(self):
        out = mask_key('yg-85fb8bbbc7914cb47f0d3e2a1b')
        self.assertEqual(out, 'yg-85f••••2a1b')

    def test_masked_output_never_contains_middle(self):
        key = 'sk-1234567890abcdefghijklmnop'
        out = mask_key(key)
        self.assertNotIn('7890abcdef', out)
        self.assertNotIn(key, out)

    def test_short_key_fully_masked(self):
        self.assertEqual(mask_key('short'), '•••••')
        self.assertEqual(mask_key('sk-1cd7f938'), '•' * 11)

    def test_twelve_chars_is_the_boundary(self):
        # 12 位仍算短，全遮；13 位开始才露头尾
        self.assertEqual(mask_key('a' * 12), '•' * 12)
        self.assertEqual(mask_key('a' * 13), 'aaaaaa••••aaaa')

    def test_empty(self):
        self.assertIsNone(mask_key(''))
        self.assertIsNone(mask_key(None))


if __name__ == '__main__':
    unittest.main()
