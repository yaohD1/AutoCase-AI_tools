"""文件导入 / 导出：资产落盘、等落盘、内容校验。

这些都不是「跑一遍浏览器就能看出来」的东西，恰恰是这类场景最容易假通过的地方：
下载目录里有上一轮的残留、浏览器还没把 .crdownload 改名、同名文件被加了 (1)。
所以这里全部按纯逻辑测，不启浏览器。
"""
import os
import sys
import tempfile
import threading
import time
import types
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from app.services.recording import assets, emit, files, files_bundle, ir
from app.services.recording.session import RecordingError, RecordingSession


def _xlsx(path, rows):
    """造一个只有行数的最小 xlsx：真 Excel 打开会报缺样式，但数行够用。"""
    body = ''.join('<row r="%d"/>' % (i + 1) for i in range(rows))
    with zipfile.ZipFile(str(path), 'w') as bundle:
        bundle.writestr('xl/worksheets/sheet1.xml',
                        '<worksheet><sheetData>%s</sheetData></worksheet>' % body)
    return path


def _target(alias='选择文件', **extra):
    item = {'alias': alias, 'snap': {}, 'candidates': [], 'primary': None, 'status': 'ok'}
    item.update(extra)
    return item


class AssetNameTest(unittest.TestCase):
    """文件名要同时出现在磁盘、步骤 JSON 和生成的代码里，脏字符会一路传染。"""

    def test_strips_directories(self):
        self.assertEqual(assets.safe_name('../../etc/终端 导入.xlsx'), '终端 导入.xlsx')
        self.assertEqual(assets.safe_name(r'C:\Users\a\模板.xlsx'), '模板.xlsx')

    def test_strips_illegal_chars(self):
        self.assertEqual(assets.safe_name('a<b>:c|d?.csv'), 'abcd.csv')

    def test_windows_reserved(self):
        # con.xlsx 在 Windows 上根本创建不了，必须改名而不是让写入抛 OSError。
        self.assertEqual(assets.safe_name('CON.xlsx'), '_CON.xlsx')
        self.assertEqual(assets.safe_name('lpt1.csv'), '_lpt1.csv')

    def test_empty_falls_back(self):
        self.assertEqual(assets.safe_name(''), 'file')
        self.assertEqual(assets.safe_name('   '), 'file')
        self.assertEqual(assets.safe_name('...'), 'file')

    def test_keeps_chinese_and_suffix(self):
        self.assertEqual(assets.safe_name('终端导入模板.xlsx'), '终端导入模板.xlsx')

    def test_truncates_long_stem(self):
        name = assets.safe_name('长' * 200 + '.xlsx')
        self.assertLessEqual(len(name), assets.MAX_STEM + 5)
        self.assertTrue(name.endswith('.xlsx'))


class AssetSaveTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='ac-assets-')

    def test_saves_and_dedups(self):
        first = assets.save(self.root, '模板.xlsx', b'hello')
        self.assertEqual(first['path'], '.assets/模板.xlsx')
        self.assertFalse(first['dedup'])
        second = assets.save(self.root, '别的名字.xlsx', b'hello')
        # 内容一样就是同一个资产：不然同一份模板被十条用例引用就会存十份。
        self.assertTrue(second['dedup'])
        self.assertEqual(second['path'], first['path'])
        self.assertEqual(len(assets.listing(self.root)), 1)

    def test_same_name_different_content(self):
        first = assets.save(self.root, '模板.xlsx', b'v1')
        second = assets.save(self.root, '模板.xlsx', b'v2')
        self.assertNotEqual(first['path'], second['path'])
        self.assertEqual(second['name'], '模板-2.xlsx')

    def test_rejects_empty_file(self):
        with self.assertRaises(assets.AssetError):
            assets.save(self.root, '空.txt', b'')

    def test_requires_workspace(self):
        with self.assertRaises(assets.AssetError):
            assets.save(None, 'a.txt', b'x')

    def test_listing_skips_hidden(self):
        assets.save(self.root, 'a.txt', b'a')
        (Path(self.root) / '.assets' / '.gitkeep').write_text('', encoding='utf-8')
        names = [item['name'] for item in assets.listing(self.root)]
        self.assertEqual(names, ['a.txt'])


class AssetPathTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='ac-path-')
        assets.save(self.root, 'a.txt', b'a')

    def test_resolves_inside(self):
        found = assets.abs_path(self.root, '.assets/a.txt')
        self.assertTrue(found.is_file())

    def test_rejects_escape(self):
        for bad in ('../outside.txt', '/etc/passwd', r'C:\Windows\win.ini'):
            with self.assertRaises(assets.AssetError):
                assets.abs_path(self.root, bad)

    def test_rejects_empty(self):
        with self.assertRaises(assets.AssetError):
            assets.abs_path(self.root, '  ')


class FileRowsTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='ac-rows-')

    def test_xlsx_rows(self):
        path = _xlsx(Path(self.root) / 'a.xlsx', 4)
        self.assertEqual(files.count_rows(path, 'xlsx'), 4)

    def test_csv_rows_ignores_blank(self):
        path = Path(self.root) / 'a.csv'
        path.write_text('姓名,年龄\n张三,20\n\n,\n', encoding='utf-8-sig')
        # 只有分隔符的行是导出工具惯常补的尾巴，不能算数据。
        self.assertEqual(files.count_rows(path, 'csv'), 2)

    def test_csv_gbk(self):
        path = Path(self.root) / 'b.csv'
        path.write_bytes('姓名,年龄\n李四,30\n'.encode('gbk'))
        self.assertEqual(files.count_rows(path, 'csv'), 2)

    def test_broken_xlsx_message(self):
        path = Path(self.root) / 'c.xls'
        path.write_bytes(b'not a zip')
        with self.assertRaises(ValueError) as ctx:
            files.count_rows(path, 'xlsx')
        self.assertIn('xlsx', str(ctx.exception))


class VerifyTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='ac-verify-')
        self.xlsx = _xlsx(Path(self.root) / '报表.xlsx', 3)

    def test_pass(self):
        result = files.verify(self.xlsx, {'name': '*.xlsx', 'min_bytes': 10,
                                         'parse': 'xlsx', 'min_rows': 2})
        self.assertTrue(result['ok'])
        self.assertEqual(result['rows'], 3)

    def test_missing(self):
        result = files.verify(Path(self.root) / '没有.xlsx', {})
        self.assertFalse(result['ok'])
        self.assertIn('没有这个文件', result['error'])

    def test_too_small(self):
        result = files.verify(self.xlsx, {'min_bytes': 10 ** 9})
        self.assertFalse(result['ok'])
        self.assertIn('小于要求', result['error'])

    def test_name_mismatch(self):
        result = files.verify(self.xlsx, {'name': '*.pdf'})
        self.assertFalse(result['ok'])
        self.assertIn('不符合期望', result['error'])

    def test_empty_sheet_is_caught(self):
        # 这一条是这套校验存在的理由：导出成功、文件也有体积，但表是空的。
        empty = _xlsx(Path(self.root) / '空表.xlsx', 1)
        result = files.verify(empty, {'parse': 'xlsx', 'min_rows': 2})
        self.assertFalse(result['ok'])
        self.assertIn('空表', result['error'])

    def test_parse_none_skips_content(self):
        self.assertTrue(files.verify(self.xlsx, {'parse': 'none'})['ok'])


class WaitDownloadTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='ac-wait-')

    def test_ignores_incomplete(self):
        folder = Path(self.root)
        (folder / '报表.xlsx.crdownload').write_bytes(b'x' * 10)
        started = time.monotonic()
        with self.assertRaises(files.FileWaitError):
            files.wait_download(folder, set(), timeout=1.0)
        self.assertGreaterEqual(time.monotonic() - started, 0.9)

    def test_ignores_previous_run(self):
        folder = Path(self.root)
        (folder / '上次的.xlsx').write_bytes(b'old')
        with self.assertRaises(files.FileWaitError):
            files.wait_download(folder, {'上次的.xlsx'}, timeout=1.0)

    def test_returns_new_file(self):
        folder = Path(self.root)
        before = files.baseline(folder)
        (folder / '新的.xlsx').write_bytes(b'x' * 20)
        found = files.wait_download(folder, before, timeout=3.0)
        self.assertEqual(found.name, '新的.xlsx')

    def test_pattern_filters(self):
        folder = Path(self.root)
        (folder / 'a.txt').write_bytes(b'a')
        with self.assertRaises(files.FileWaitError):
            files.wait_download(folder, set(), timeout=1.0, pattern='*.xlsx')


class IrStepTest(unittest.TestCase):
    def test_upload_has_file_only(self):
        step = ir.make_step('upload', 1, file={'path': 'a.xlsx'})
        self.assertEqual(step['file']['path'], 'a.xlsx')
        self.assertNotIn('expect', step)

    def test_download_has_expect_only(self):
        step = ir.make_step('download', 2, expect={'name': '*.xlsx'})
        self.assertEqual(step['expect']['name'], '*.xlsx')
        self.assertNotIn('file', step)

    def test_public_view_keeps_them(self):
        step = ir.make_step('upload', 1, file={'path': 'a.xlsx', 'name': 'a.xlsx'})
        step['target'] = _target()
        published = ir.public_step(step)
        self.assertEqual(published['file']['name'], 'a.xlsx')


class EnsureUtilsTest(unittest.TestCase):
    """两个框架各落一份，位置不同但语义对齐。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='ac-utils-')

    def test_playwright(self):
        rel = emit._ensure_files_utils(self.root, 'tests/login.spec.ts',
                                       [{'type': 'upload'}], emit.PW_FRAMEWORK)
        self.assertEqual(rel, 'tests/utils/files.ts')
        body = (Path(self.root) / rel).read_text(encoding='utf-8')
        self.assertIn('uploadFile', body)
        self.assertIn('downloadFile', body)

    def test_robot(self):
        rel = emit._ensure_files_utils(self.root, 'tests/login.robot',
                                       [{'type': 'download'}], emit.RF_FRAMEWORK)
        # 库在 工作区根/.autocase，路径是从 CURDIR 往上退一层。
        self.assertEqual(rel, '../.autocase/libraries/FilesLibrary.py')
        body = (Path(self.root) / '.autocase/libraries/FilesLibrary.py').read_text(encoding='utf-8')
        self.assertIn('Upload File', body)
        compile(body, rel, 'exec')  # 写出去的东西必须真是合法 Python

    def test_robot_shares_one_copy_across_cases(self):
        first = []
        second = []
        rel_a = emit._ensure_files_utils(self.root, '测试用例层/终端管理/终端资产/a.robot',
                                         [{'type': 'upload'}], emit.RF_FRAMEWORK, notes=first)
        rel_b = emit._ensure_files_utils(self.root, '测试用例层/接口测试/b.robot',
                                         [{'type': 'upload'}], emit.RF_FRAMEWORK, notes=second)
        # 两条用例深度不同，相对路径不同，但落在同一个文件上。
        self.assertEqual(rel_a, '../../../.autocase/libraries/FilesLibrary.py')
        self.assertEqual(rel_b, '../../.autocase/libraries/FilesLibrary.py')
        # 相对路径是按各自用例目录算的，从各自目录还原后必须指向同一份。
        self.assertEqual(
            (Path(self.root) / '测试用例层/终端管理/终端资产' / rel_a).resolve(),
            (Path(self.root) / '测试用例层/接口测试' / rel_b).resolve())
        self.assertEqual(first[0]['state'], 'created')
        # 第二次内容没变：不重写，直接复用。
        self.assertEqual(second[0]['state'], 'reused')

    def test_robot_rewrites_stale_library(self):
        # 模板修过（比如上传那个 TypeError）时不能因为「文件已存在」就继续用坏的。
        stale = Path(self.root) / '.autocase/libraries/FilesLibrary.py'
        stale.parent.mkdir(parents=True)
        stale.write_text('# 旧版本\n', encoding='utf-8')
        notes = []
        emit._ensure_files_utils(self.root, 'tests/a.robot', [{'type': 'upload'}],
                                 emit.RF_FRAMEWORK, notes=notes)
        self.assertEqual(notes[0]['state'], 'updated')
        self.assertIn('Upload File', stale.read_text(encoding='utf-8'))

    def test_robot_library_line_points_at_shared_dir(self):
        gen = tempfile.mkdtemp(prefix='ac-gen-')
        target = '测试用例层/终端管理/终端资产/用例.robot'
        path = Path(gen) / target
        path.parent.mkdir(parents=True)
        path.write_text('*** Test Cases ***\n录制流程\n    Go To    https://a/#/\n', encoding='utf-8')
        emit._normalize_rf_script(gen, target, [{'type': 'upload'}],
                                  files_lib='../../../.autocase/libraries/FilesLibrary.py')
        body = path.read_text(encoding='utf-8')
        self.assertIn('${CURDIR}/../../../.autocase/libraries/FilesLibrary.py', body)
        self.assertIn('${CURDIR}/downloads', body)
        self.assertNotIn('${CURDIR}/libraries/FilesLibrary.py', body)

    def test_skipped_without_relevant_steps(self):
        for framework in (emit.PW_FRAMEWORK, emit.RF_FRAMEWORK):
            self.assertIsNone(emit._ensure_files_utils(
                self.root, 'tests/a.spec.ts', [{'type': 'click'}], framework))

    def test_robots_download_rules_exist(self):
        # 规则里没有这两句，写手就会自己点按钮、自己 while 循环等文件。
        rf = emit.FRAMEWORK_RULES[emit.RF_FRAMEWORK]
        self.assertIn('FilesLibrary', rf)
        self.assertIn('Download File', rf)
        pw = emit.FRAMEWORK_RULES[emit.PW_FRAMEWORK]
        self.assertIn('uploadFile', pw)
        self.assertIn('downloadFile', pw)


class RobotUploadLibraryTest(unittest.TestCase):
    """生成给 Robot 的 FilesLibrary：上传必须真的把文件塞进 input。

    这条链路曾经整段是坏的：`Execute Javascript` 在 SeleniumLibrary 3.x 里把每个
    参数当成「同一段代码的另一行」，内部是 ''.join()，把 WebElement 拼进去直接
    TypeError: sequence item 1: expected str instance, WebElement found。
    报错跟「文件」毫无关系，所以既查源码不回退，也把生成的类 exec 出来跑一遍。
    """

    def _library(self):
        # 测试环境不一定装了 robot，给个只提供 @keyword 的桩，避免为一个装饰器跳过。
        deco = types.ModuleType('robot.api.deco')

        def keyword(name=None, **kwargs):
            if callable(name):
                return name

            def wrap(fn):
                return fn

            return wrap

        deco.keyword = keyword
        robot = types.ModuleType('robot')
        api = types.ModuleType('robot.api')
        api.deco = deco
        robot.api = api
        namespace = {}
        with mock.patch.dict(sys.modules, {'robot': robot, 'robot.api': api,
                                           'robot.api.deco': deco}):
            exec(files_bundle.FILES_PY, namespace)
        return namespace['FilesLibrary'], namespace['FIND_INPUT_JS']

    def test_does_not_use_seleniumlibrary_execute_javascript(self):
        # 这行一旦回来，upload 就会在真机上崩，且报错完全指不到文件。
        self.assertNotIn('execute_javascript(', files_bundle.FILES_PY)
        self.assertIn('driver.execute_script(', files_bundle.FILES_PY)

    def test_file_input_passes_element_to_native_driver(self):
        library, script = self._library()
        lib = library.__new__(library)
        driver = mock.Mock()
        driver.execute_script.return_value = 'INPUT'
        lib._selenium = mock.Mock(driver=driver,
                                  execute_javascript=mock.Mock(
                                      side_effect=AssertionError('不能走 Execute Javascript')))
        element = object()
        self.assertEqual(lib._file_input(element), 'INPUT')
        driver.execute_script.assert_called_once_with(script, element)

    def test_project_path_falls_back_to_parent_of_cwd(self):
        # 从用例目录启动 robot 时 cwd 不是工作区根，资产仍然在根下的 .assets。
        root = Path(tempfile.mkdtemp(prefix='ac-proj-'))
        (root / '.assets').mkdir()
        (root / '.assets' / 'a.xlsx').write_bytes(b'x')
        deep = root / '测试用例层' / '终端资产'
        deep.mkdir(parents=True)
        library, _ = self._library()
        lib = library.__new__(library)
        previous = os.getcwd()
        os.chdir(str(deep))
        try:
            self.assertEqual(lib._project_path('.assets/a.xlsx'),
                             (root / '.assets' / 'a.xlsx').resolve())
        finally:
            os.chdir(previous)


class SessionGuardTest(unittest.TestCase):
    """插入接口的几道闸：没拾取、没选文件、文件不在工作区。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='ac-session-')
        self.saved = assets.save(self.root, '模板.xlsx', b'data')
        self.session = RecordingSession.__new__(RecordingSession)
        self.session.workspace = self.root
        self.session._lock = threading.RLock()
        self.session._steps = []
        self.session._picks = [{'seq': 1, 'target': _target('选择文件')}]

    def _patches(self):
        return (
            mock.patch.object(RecordingSession, '_current_page', return_value=None),
            mock.patch.object(RecordingSession, '_schedule_hud', lambda *a, **k: None),
            mock.patch.object(RecordingSession, 'snapshot', lambda *a, **k: {'steps': []}),
        )

    def _run(self, name, config):
        patches = self._patches()
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        return getattr(self.session, name)(config, None)

    def test_upload_without_target(self):
        with self.assertRaises(RecordingError):
            self.session._add_upload({'file': self.saved}, None)

    def test_upload_without_file(self):
        with self.assertRaises(RecordingError):
            self.session._add_upload({'target': '选择文件'}, None)

    def test_upload_missing_asset(self):
        # 路径合法但文件不存在：不能只信前端传来的描述。
        with self.assertRaises(RecordingError):
            self.session._add_upload({'target': '选择文件', 'file': {'path': '.assets/no.xlsx'}}, None)

    def test_upload_inserts_step(self):
        self._run('_add_upload', {'target': '选择文件', 'file': self.saved})
        self.assertEqual(len(self.session._steps), 1)
        step = self.session._steps[0]
        self.assertEqual(step['type'], 'upload')
        self.assertEqual(step['file']['path'], self.saved['path'])
        self.assertEqual(step['no'], 1)

    def test_download_clamps_expect(self):
        self._run('_add_download', {
            'target': '选择文件',
            'expect': {'name': '  *.xlsx  ', 'min_bytes': -5, 'parse': 'PDF', 'min_rows': 99999999,
                       'timeout': 9999},
        })
        expect = self.session._steps[0]['expect']
        self.assertEqual(expect['name'], '*.xlsx')
        self.assertEqual(expect['min_bytes'], 0)
        # 认不出来的校验方式退回 none，不能因为一个字段让整步插不进去。
        self.assertEqual(expect['parse'], 'none')
        self.assertEqual(expect['min_rows'], 1000000)
        self.assertEqual(expect['timeout'], 180)

    def test_download_without_target(self):
        with self.assertRaises(RecordingError):
            self.session._add_download({'expect': {}}, None)


if __name__ == '__main__':
    unittest.main()
