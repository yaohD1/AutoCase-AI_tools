"""目录浏览的新建文件夹接口。

浏览模块原本是纯只读的，mkdir 是它唯一的写入口，所以这里重点守三件事：
只建在根目录内、只建一层、名字不会被列表自己过滤掉。
"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix='browse-mkdir-')
os.environ.setdefault('DATABASE_URL', 'sqlite:///' + os.path.join(_TMP, 'test.db').replace(chr(92), '/'))

from app import create_app  # noqa: E402

MKDIR = '/api/automation/browse/mkdir'
FILES = '/api/automation/browse/files'


class BrowseMkdirTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config['TESTING'] = True
        cls.client = cls.app.test_client()

    def setUp(self):
        # 每个用例一套干净的根目录，避免互相看到对方建的文件夹。
        self.root = Path(tempfile.mkdtemp(prefix='ws-root-'))
        self.app.config['AUTOMATION_WORKSPACE_ROOT'] = str(self.root)
        (self.root / 'proj').mkdir()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _mkdir(self, **payload):
        return self.client.post(MKDIR, json=payload)

    def test_create_inside_workspace(self):
        response = self._mkdir(workspace='proj', path='', name='终端资产')
        self.assertEqual(response.status_code, 201)
        data = response.get_json()
        self.assertEqual(data['path'], '终端资产')
        self.assertTrue((self.root / 'proj' / '终端资产').is_dir())

    def test_create_nested_folder_under_current_path(self):
        (self.root / 'proj' / '终端管理').mkdir()
        response = self._mkdir(workspace='proj', path='终端管理', name='配置管理')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()['path'], '终端管理/配置管理')
        self.assertTrue((self.root / 'proj' / '终端管理' / '配置管理').is_dir())

    def test_create_at_root_when_no_workspace_given(self):
        """选工作区的场景走 /automation/browse，基准是根目录，不是某个工作区。"""
        response = self._mkdir(name='新项目')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()['workspace'], '')
        self.assertTrue((self.root / '新项目').is_dir())

    def test_duplicate_name_is_conflict(self):
        (self.root / 'proj' / '终端资产').mkdir()
        response = self._mkdir(workspace='proj', path='', name='终端资产')
        self.assertEqual(response.status_code, 409)
        self.assertIn('同名', response.get_json()['error'])

    def test_rejects_names_the_listing_would_hide(self):
        """点开头 / SKIP_DIRS 里的名字建出来也不显示，当场拒绝并说清楚。"""
        for name in ('.hidden', 'node_modules', '__pycache__', '..', '.'):
            with self.subTest(name=name):
                response = self._mkdir(workspace='proj', name=name)
                self.assertEqual(response.status_code, 400)
                self.assertTrue(response.get_json()['error'])
                # . 和 .. 解析出来就是已有目录，没法用「存在」判断，只看有没有被拒绝。
                if name not in ('.', '..'):
                    self.assertFalse((self.root / 'proj' / name).exists())

    def test_rejects_path_separators_and_windows_specials(self):
        for name in ('a/b', 'a\\b', 'a:b', 'a?b', 'a*b', '', '   '):
            with self.subTest(name=name):
                response = self._mkdir(workspace='proj', name=name)
                self.assertEqual(response.status_code, 400)

    def test_rejects_path_outside_workspace(self):
        response = self._mkdir(workspace='proj', path='../..', name='越界')
        self.assertEqual(response.status_code, 400)
        self.assertFalse((self.root.parent / '越界').exists())

    def test_workspace_must_exist(self):
        response = self._mkdir(workspace='not-there', name='x')
        self.assertEqual(response.status_code, 404)

    def test_new_folder_shows_up_in_listing(self):
        self._mkdir(workspace='proj', name='终端资产')
        response = self.client.get(FILES, query_string={'workspace': 'proj', 'framework': 'robot-selenium'})
        self.assertEqual(response.status_code, 200)
        names = [item['name'] for item in response.get_json()['dirs']]
        self.assertIn('终端资产', names)


if __name__ == '__main__':
    unittest.main()
