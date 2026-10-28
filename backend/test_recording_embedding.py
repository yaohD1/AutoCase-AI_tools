"""被测系统禁止被 iframe 嵌入时的处理。

内网系统常见的 `X-Frame-Options: SAMEORIGIN` 会让嵌入模式的 iframe 变成
`chrome-error://chromewebdata/`，界面上就一句「xxx 拒绝了我们的连接请求」，
和「端口不通」长得一模一样。这里把三件事钉住：

1. 预检能认出拒绝嵌入（X-Frame-Options / CSP），不认得的头一律放行；
2. 预检失败（连不上、超时）不许挡住正常录制；
3. 路由在命中时确实换成独立窗口布局，并把原因带回前端。
"""
import http.server
import threading
import unittest
from unittest import mock

from app.services.recording import embedding

BODY = b'<!doctype html><html><body>page</body></html>'


class _Handler(http.server.BaseHTTPRequestHandler):
    routes = {}
    head_405 = ()

    def _serve(self, with_body=True):
        if not with_body and self.path in self.head_405:
            # 有些服务压根不实现 HEAD，只靠 HEAD 会漏掉同一条规则。
            self.send_response(405)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        for key, value in self.routes.get(self.path, {}).items():
            self.send_header(key, value)
        self.send_header('Content-Length', str(len(BODY)))
        self.end_headers()
        if with_body:
            self.wfile.write(BODY)

    def do_GET(self):
        self._serve()

    def do_HEAD(self):
        self._serve(with_body=False)

    def log_message(self, *args):
        pass


def _serve(routes, head_405=()):
    handler = type('Handler', (_Handler,), {'routes': routes, 'head_405': head_405})
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, 'http://127.0.0.1:%d' % server.server_address[1]


class FrameBlockReasonTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.base = _serve({
            '/sameorigin': {'X-Frame-Options': 'SAMEORIGIN'},
            '/deny': {'X-Frame-Options': 'DENY'},
            '/clear': {},
            '/csp-none': {'Content-Security-Policy': "frame-ancestors 'none'"},
            '/csp-star': {'Content-Security-Policy': 'frame-ancestors *'},
            '/csp-shell': {'Content-Security-Policy': 'frame-ancestors http://localhost:5173'},
            '/csp-other': {'Content-Security-Policy': "default-src 'self'; frame-ancestors https://a.example"},
            '/head-refused': {'X-Frame-Options': 'SAMEORIGIN'},
        }, head_405=('/head-refused',))

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_sameorigin_is_blocked(self):
        reason = embedding.frame_block_reason(self.base + '/sameorigin')
        self.assertIn('SAMEORIGIN', reason)

    def test_deny_is_blocked(self):
        reason = embedding.frame_block_reason(self.base + '/deny')
        self.assertIn('DENY', reason)

    def test_plain_page_is_allowed(self):
        self.assertIsNone(embedding.frame_block_reason(self.base + '/clear'))

    def test_csp_none_is_blocked(self):
        reason = embedding.frame_block_reason(self.base + '/csp-none')
        self.assertIn("frame-ancestors 'none'", reason)

    def test_csp_star_is_allowed(self):
        self.assertIsNone(embedding.frame_block_reason(self.base + '/csp-star'))

    def test_csp_allowing_shell_origin_is_allowed(self):
        # 外壳页显式被允许时不该误判：这是唯一能继续用嵌入模式的情况。
        self.assertIsNone(embedding.frame_block_reason(
            self.base + '/csp-shell', shell_origin='http://localhost:5173/recording'))

    def test_csp_other_origin_is_blocked(self):
        reason = embedding.frame_block_reason(self.base + '/csp-other')
        self.assertIn('frame-ancestors', reason)

    def test_head_not_supported_falls_back_to_get(self):
        # 有些服务不实现 HEAD，只靠 HEAD 会漏掉同一条规则。
        reason = embedding.frame_block_reason(self.base + '/head-refused')
        self.assertIn('SAMEORIGIN', reason)

    def test_unreachable_host_is_allowed(self):
        # 连不上时预检必须放行：否则网络抖动会让用户连录制都开不了。
        self.assertIsNone(embedding.frame_block_reason(
            'http://127.0.0.1:1/', timeout=1))


class StartRouteFallbackTest(unittest.TestCase):
    """路由命中拒绝嵌入时，必须换成独立窗口，并把原因带回前端。"""

    def setUp(self):
        from app import create_app
        self.app = create_app()
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()

    def _start(self, blocked):
        fake = mock.Mock()
        fake.snapshot.return_value = {'id': 's1', 'status': 'recording', 'steps': []}
        target = 'app.routes.recording.embedding.frame_block_reason'
        with mock.patch(target, return_value=blocked), \
                mock.patch('app.routes.recording.RecordingSession.start_new', return_value=fake) as start:
            response = self.client.post('/api/automation/recording/start',
                                        json={'base_url': 'https://192.168.123.135'})
        return response, start

    def test_blocked_site_switches_to_split(self):
        response, start = self._start('X-Frame-Options: SAMEORIGIN')
        self.assertEqual(response.status_code, 201)
        data = response.get_json()
        self.assertIn('独立窗口', data['notice'])
        self.assertIn('SAMEORIGIN', data['notice'])
        kwargs = start.call_args.kwargs
        self.assertIsNone(kwargs['embed_url'])
        self.assertIsNotNone(kwargs['panel_url'])

    def test_normal_site_keeps_embed(self):
        response, start = self._start(None)
        self.assertEqual(response.status_code, 201)
        self.assertNotIn('notice', response.get_json())
        self.assertIsNotNone(start.call_args.kwargs['embed_url'])


if __name__ == '__main__':
    unittest.main()
