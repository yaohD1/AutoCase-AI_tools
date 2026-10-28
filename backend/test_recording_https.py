"""内网自签名证书：https://192.168.x.x 这类被测系统必须能打开。

Chromium 默认不信任自签名证书，`page.goto` 直接抛 net::ERR_CERT_AUTHORITY_INVALID，
录制连页面都进不去（界面上的表现就是「录制启动失败」）。这里起一个真的 HTTPS 服务
（自签证书）把几件事钉住：不放开就失败、录制能开、嵌入模式的 iframe 能开、回放能开；
另外顺带验证「iframe 打不开时不许装作录制成功」。
"""
import http.server
import os
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest

from playwright.sync_api import sync_playwright

from app.services.recording import replay as replay_mod
from app.services.recording.session import RecordingError, RecordingSession

PAGE = '''<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:0">
<h1 id="title">内网系统</h1>
<button id="go" style="position:absolute;left:40px;top:80px;width:160px;height:50px">进入</button>
</body></html>'''


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGE.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class _ShellHandler(http.server.BaseHTTPRequestHandler):
    """模拟嵌入模式的外壳页：AutoCase 自己那层，被测系统在 iframe 里。"""
    target = ''

    def do_GET(self):
        body = ('<!doctype html><html><body><iframe src="%s" '
                'style="width:600px;height:400px"></iframe></body></html>'
                % self.target).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _make_cert(folder):
    """自签证书只给测试用：CN/SAN 都是 127.0.0.1，两天就过期。"""
    cert = os.path.join(folder, 'cert.pem')
    key = os.path.join(folder, 'key.pem')
    subprocess.run(
        ['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
         '-keyout', key, '-out', cert, '-days', '2',
         '-subj', '/CN=127.0.0.1', '-addext', 'subjectAltName=IP:127.0.0.1'],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return cert, key


class SelfSignedHttpsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('openssl'):
            raise unittest.SkipTest('没有 openssl，无法生成自签证书')
        cls.tmp = tempfile.mkdtemp(prefix='autocase-cert-')
        cert, key = _make_cert(cls.tmp)
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert, key)
        cls.server.socket = tls.wrap_socket(cls.server.socket, server_side=True)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = 'https://127.0.0.1:%d/' % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        RecordingSession._current = None

    def tearDown(self):
        RecordingSession.dispose_current()

    def test_plain_context_is_blocked_by_the_certificate(self):
        """对照组：不加参数时 Chromium 就是打不开——这条解释了「为什么录制启动失败」。"""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            with self.assertRaises(Exception) as caught:
                page.goto(self.url, wait_until='domcontentloaded', timeout=15000)
            self.assertIn('ERR_CERT', str(caught.exception))
            context.close()
            browser.close()

    def test_recording_opens_self_signed_site(self):
        # 证书不被信任时 start_new 会带着 net::ERR_CERT_AUTHORITY_INVALID 抛出来，
        # 所以「能返回会话」本身就是页面真的打开了。
        session = RecordingSession.start_new(self.url, headless=True, ask_daemon=False)
        self.assertEqual(session.status, 'recording', session.error)
        steps = session.snapshot()['steps']
        self.assertEqual(steps[0]['type'], 'goto')
        self.assertEqual(steps[0]['url'], self.url)

    def test_shell_iframe_can_load_self_signed_site(self):
        """默认的嵌入模式：被测系统在外壳页的 iframe 里。

        证书不被信任时这个 iframe 会变成 chrome-error://chromewebdata/ ——
        录制窗口看起来「打开了」，里面却是一张打不开的错误页，这和用户实际看到的一致。
        """
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(ignore_https_errors=True)
            page = context.new_page()
            page.set_content(
                '<!doctype html><html><body><iframe src="%s" '
                'style="width:600px;height:400px"></iframe></body></html>' % self.url)
            frame = None
            deadline = time.time() + 10
            while time.time() < deadline:
                frames = [f for f in page.frames if f is not page.main_frame]
                if frames and frames[0].url.startswith('https://127.0.0.1'):
                    frame = frames[0]
                    break
                page.wait_for_timeout(200)
            self.assertIsNotNone(frame, 'iframe 没有加载出被测系统（多半是证书被拦了）')
            self.assertEqual(frame.locator('#title').inner_text(), '内网系统')
            context.close()
            browser.close()

    def test_embed_mode_reports_error_page_instead_of_starting_silently(self):
        """外壳页能开、iframe 打不开时必须报错：否则状态是「录制中」，用户只看到错误页。"""
        closed = socket.socket()
        closed.bind(('127.0.0.1', 0))
        dead_port = closed.getsockname()[1]
        closed.close()

        _ShellHandler.target = 'http://127.0.0.1:%d/' % dead_port
        shell = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _ShellHandler)
        threading.Thread(target=shell.serve_forever, daemon=True).start()
        try:
            with self.assertRaises(RecordingError) as caught:
                RecordingSession.start_new(
                    'http://127.0.0.1:%d/' % dead_port,
                    embed_url='http://127.0.0.1:%d/' % shell.server_address[1],
                    headless=True, ask_daemon=False)
            self.assertIn('错误页', str(caught.exception))
        finally:
            shell.shutdown()
            shell.server_close()

    def test_replay_opens_self_signed_site(self):
        steps = [{'no': 1, 'type': 'goto', 'url': self.url}]
        result = replay_mod.replay_checked(steps, framework='playwright-ts', headless=True)
        self.assertTrue(result['ok'], result.get('error'))


if __name__ == '__main__':
    unittest.main()
