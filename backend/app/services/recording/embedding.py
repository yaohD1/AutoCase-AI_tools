"""被测系统能不能放进 iframe。

内网系统很多在 nginx / 网关上加了 `X-Frame-Options: SAMEORIGIN`（或 CSP 的
`frame-ancestors`）。嵌入模式下外壳页（AutoCase 自己，localhost:5173）和被测系统
不同源，iframe 会被浏览器直接换成错误页 `chrome-error://chromewebdata/`，
界面上显示的是「xxx 拒绝了我们的连接请求」——**和「端口不通」长得一模一样**，
但改协议、改端口都没用，用户只会觉得录制功能坏了。

这里在启动浏览器之前读一次响应头：命中就直接改用独立窗口布局，不把用户丢进
一张点不动的错误页。判断不了（网络错误、超时、需要登录）时返回 None 放行——
预检只是提前避坑，不该因为探测失败挡住正常录制。
"""
import ssl
import urllib.error
import urllib.request
from urllib.parse import urlparse

TIMEOUT = 6


def frame_block_reason(url, shell_origin=None, timeout=TIMEOUT):
    """被测系统拒绝被嵌入的原因；可以嵌（或判断不了）返回 None。

    shell_origin 是外壳页的源（如 http://localhost:5173）。CSP 里显式允许它时不算拒绝。
    """
    headers = _headers(url, timeout)
    if headers is None:
        return None

    value = (headers.get('X-Frame-Options') or '').strip()
    if value:
        lowered = value.lower()
        # 外壳页永远和被测系统不同源，DENY / SAMEORIGIN 都等于拒绝。
        if 'deny' in lowered or 'sameorigin' in lowered:
            return 'X-Frame-Options: %s' % value

    csp = headers.get('Content-Security-Policy') or ''
    return _frame_ancestors_block(csp, shell_origin)


def _headers(url, timeout):
    """只取响应头，尽量不下载正文。"""
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    opener = urllib.request.build_opener(
        # 内网地址不该绕到系统代理上：代理会把它当成外网请求，探测结果就不可信了。
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=context),
    )
    for method in ('HEAD', 'GET'):
        request = urllib.request.Request(
            url, method=method, headers={'User-Agent': 'AutoCase/1.0'})
        try:
            with opener.open(request, timeout=timeout) as response:
                return response.headers
        except urllib.error.HTTPError as exc:
            # 不支持 HEAD 的服务返回 405/501，换成 GET 再看一次响应头。
            if method == 'HEAD' and exc.code in (405, 501):
                continue
            # 401/403/404 也带着响应头，X-Frame-Options 一样要看。
            return exc.headers
        except Exception:
            # 连不上 / 超时：换 GET 也是同样的结果，别让开始录制白等两轮超时。
            return None
    return None


def _frame_ancestors_block(csp, shell_origin):
    for directive in csp.split(';'):
        parts = directive.strip().split()
        if not parts or parts[0].lower() != 'frame-ancestors':
            continue
        sources = [part.strip("'\"").lower() for part in parts[1:]]
        if not sources:
            return None
        if 'none' in sources:
            return "Content-Security-Policy: frame-ancestors 'none'"
        if '*' in sources:
            return None
        origin = _origin_of(shell_origin)
        if origin and origin in sources:
            return None
        return 'Content-Security-Policy: frame-ancestors %s' % ' '.join(parts[1:])
    return None


def _origin_of(url):
    if not url:
        return ''
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return ''
    return '%s://%s' % (parsed.scheme, parsed.netloc)
