"""图形验证码识别：截图 → 放大 → 问视觉模型 → 填入输入框。

只做一件事：把验证码上的字符认出来，填进对应的输入框。不点提交、不碰账号密码 ——
「提交」留给后面的 click 步骤，这样同一步也能用在注册、找回密码、点「获取短信验证码」
这类场景上。

**为什么不读 DOM 文本**：只有极少数站点会把验证码明文写进 DOM（本项目验证用的那台
工单系统就是，`.verification-code` 下面 6 个 span）。真实站点的验证码是图片、canvas
或 CSS 背景图，DOM 里什么都没有，只能看图。所以这里只走视觉这一条路，不做「能读
文本就读」的捷径 —— 那种捷径在真实项目里永远不会命中，只会让人误以为测过了。

**放大倍数是实测出来的**，不是随手取的。同一批 120×40 的 6 位数字码，同一句提示词：

    原图直接问   7/10    失败的 3 次全是「模型返回空」——图太小，它看不清就不猜
    放大 3 倍   10/10    平均 6.4 秒
    放大 4 倍    9/10    过度放大反而不如 3 倍

所以 SCALE = 3。注意这个结论依赖提示词：换成「只输出数字」的窄提示词，原图也能到
97%，但那样就识别不了字母码和算术题，通用性没了。

能力边界：
- 识别准确率约 97.7%（42/43 次实测），看错一位且位数正确时**步骤自己发现不了**，
  要等后续断言暴露。所以识别阶段的重试只覆盖「返回空 / 位数不符」这两类。
- 带行为风控的验证码（要看鼠标轨迹、设备指纹）不在范围内，这一类也不该靠 OCR 硬闯。
"""

import base64
import io
import logging
import re
import time

import requests
from PIL import Image

from app.services import models_config

logger = logging.getLogger(__name__)

SCALE = 3           # 发给模型前放大倍数，实测 3 倍最优（见模块注释）
MAX_W = 640         # 超过这个宽度多半拾取到了整块容器，不是验证码本身
MAX_H = 320
MIN_W = 20
MIN_H = 12

# selector 为空时的兜底：按常见的图片命名找一遍。
# 只用来「先试一下」，试不到就报错让用户拾取 —— 猜错元素比报错更难查。
FALLBACK_SELECTORS = (
    'img[src*="captcha"]',
    'img[src*="kaptcha"]',
    'img[src*="verifyCode"]',
    'img[src*="verify_code"]',
    'img[src*="code"]',
    '[class*="captcha"] img',
    '[class*="captcha"] canvas',
    '[class*="verify"] img',
    '[class*="verify"] canvas',
)

# 提示词要覆盖三类常见形态。写成「只输出数字」会让字母码和算术题全部失效，
# 而这三类在真实站点里都很常见。
PROMPT = (
    '这是网页上的验证码图片。请只输出验证码本身的内容：\n'
    '- 数字或字母：原样输出，区分大小写，不要加空格、标点或引号\n'
    '- 算术题：直接输出计算结果\n'
    '- 汉字：原样输出\n'
    '不要输出任何解释文字。'
)

CHARSETS = ('digits', 'alnum', 'any')


class CaptchaError(Exception):
    """识别或填入失败，message 直接给用户看。"""


def _short(value, width=80):
    text = str(value or '').replace('\n', ' ')
    return text if len(text) <= width else text[:width - 1] + '…'


def _clean(raw, charset='digits', length=0):
    """从模型回话里抠出验证码。

    模型不一定老实只回一个词，实测会遇到三种：
      「449158」                 直接给答案
      「449158（6 位）」         带一句计数，括号里的 6 不能当成答案
      「The code is a3B9」       带英文解释
    所以不能简单「取最后一段数字」或「去掉所有空白」—— 后者会把英文单词连成一串。
    这里的顺序是：

    1. 按字符集在**原文**上切候选（空白留着当分隔符）。
    2. 候选全是单字符时，说明模型是按位拆开写的（「4 2 6 8」），拼起来；
       但只有长度对得上才采用，否则「4 2 6（6 位）」会被拼进那个 6。
    3. 字母数字模式下优先在含数字的候选里挑：英文单词不含数字，天然被排除。
    4. 配了 length 就优先取正好这么多位的。
    5. 还选不出来就取最长的（解释性文字一般比验证码短）。
    """
    text = str(raw or '').strip()
    if not text:
        return ''
    if charset == 'any':
        # 中文验证码没有「词」的概念，只能去掉空白和标点。
        return re.sub(r'[\s，。、：；！？"\'`（）()\[\]{}<>《》|]+', '', text)

    # 按字符集切候选，只把「位置相邻、中间只有空白、且都是单字符」的片段合并。
    # 这样「4 2 6 8」会拼成 4268，而「4 2 6 8（6 位）」里括号那个 6 不会被拼进来
    # ——它前面隔的是「（」不是空白。「The code is a3B9」里的单词也不会被连起来。
    pattern = r'\d+' if charset == 'digits' else r'[A-Za-z0-9]+'
    groups = []
    cur = ''
    last_end = None
    prev_len = 0
    for match in re.finditer(pattern, text):
        token = match.group()
        gap = text[last_end:match.start()] if last_end is not None else ''
        if cur and len(token) == 1 and prev_len == 1 and not gap.strip():
            cur += token
        else:
            if cur:
                groups.append(cur)
            cur = token
        last_end = match.end()
        prev_len = len(token)
    if cur:
        groups.append(cur)
    if not groups:
        return ''

    if charset == 'alnum':
        mixed = [g for g in groups if any(c.isdigit() for c in g)]
        groups = mixed or groups

    if length:
        exact = [g for g in groups if len(g) == length]
        if exact:
            return exact[-1]
    return max(groups, key=len)


def _inner_media(item):
    """拾取到的是容器时，取里面那张真图。

    截容器会把旁边的输入框、刷新按钮一起截进去，模型会被干扰；
    但如果容器本身就是一个画布/图片，`query_selector_all` 自然是空的，
    所以这个函数对两种拾取结果都成立。
    """
    try:
        for sel in ('img', 'canvas'):
            for inner in item['el'].query_selector_all(sel):
                try:
                    box = inner.bounding_box()
                except Exception:  # noqa: BLE001 - 遍历中消失是常事
                    continue
                if not box:
                    continue
                if MIN_W <= box['width'] <= MAX_W and MIN_H <= box['height'] <= MAX_H:
                    return {'el': inner, 'box': box, 'selector': item['selector']}
    except Exception:  # noqa: BLE001
        pass
    return None


def find_captcha(page, selector=''):
    """定位验证码元素，返回 {'el', 'box', 'selector'}；找不到返回 None。

    用户拾取的选择器优先；为空才按常见命名猜一遍。
    """
    if selector:
        try:
            candidates = page.query_selector_all(selector)
        except Exception as exc:  # noqa: BLE001 - 选择器写错要给出能看懂的话
            raise CaptchaError('验证码选择器不可用：%s' % _short(exc)) from exc
        for el in candidates:
            try:
                if not el.is_visible():
                    continue
                box = el.bounding_box()
            except Exception:  # noqa: BLE001
                continue
            if not box or box['width'] < MIN_W or box['height'] < MIN_H:
                continue
            item = {'el': el, 'box': box, 'selector': selector}
            inner = _inner_media(item)
            if inner is not None:
                return inner
            if box['width'] <= MAX_W and box['height'] <= MAX_H:
                return item
            # 尺寸超标：拾取的是一整块区域（比如登录卡片），不是验证码图本身。
            raise CaptchaError(
                '拾取到的元素是 %d×%d，比验证码大太多。请重新拾取，只点验证码图片本身。'
                % (round(box['width']), round(box['height'])))
        return None

    for sel in FALLBACK_SELECTORS:
        try:
            found = page.query_selector_all(sel)
        except Exception:  # noqa: BLE001
            continue
        for el in found:
            try:
                if not el.is_visible():
                    continue
                box = el.bounding_box()
            except Exception:  # noqa: BLE001
                continue
            if not box or not (MIN_W <= box['width'] <= MAX_W):
                continue
            if not (MIN_H <= box['height'] <= MAX_H):
                continue
            return {'el': el, 'box': box, 'selector': ''}
    return None


def wait_ready(page, el, timeout=4.0):
    """等图真的画出来再截。

    <img> 还在加载时截图，截到的是空白或占位符，模型会返回空字符串 ——
    看起来像「识别失败」，实际是截早了。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            state = el.evaluate("""el => {
                if (el.tagName === 'IMG') return el.complete && el.naturalWidth > 0;
                if (el.tagName === 'CANVAS') return el.width > 0 && el.height > 0;
                return true;
            }""")
        except Exception:  # noqa: BLE001 - 元素被替换或页面跳走
            return False
        if state:
            # 有些组件是「先给占位、后填内容」：complete 为真但画的还是上一帧。
            # 再等 120ms，代价很小，换来不会截到旧图。
            page.wait_for_timeout(120)
            return True
        page.wait_for_timeout(150)
    return False


def capture(page, item):
    """截验证码本身。不额外加 padding：元素边框已经包含在 box 里。"""
    box = item['box']
    return page.screenshot(clip={
        'x': box['x'], 'y': box['y'], 'width': box['width'], 'height': box['height'],
    })


def enlarge(png, scale=SCALE):
    """放大后发给模型，返回 (png, 原宽, 原高)。"""
    img = Image.open(io.BytesIO(png)).convert('RGB')
    width, height = img.size
    img = img.resize((width * scale, height * scale), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue(), width, height


def ask_model(png, charset='digits', length=0, ref=None):
    """问视觉模型要验证码，返回 (code, 说明)。失败抛 CaptchaError。"""
    ref = ref or models_config.slider_model()
    if not ref:
        raise CaptchaError('没有配置视觉模型。请到录制回放页的设置里选一个能看图的模型。')
    resolved = models_config.resolve(ref)
    if resolved is None:
        raise CaptchaError('视觉模型 %s 不存在，请到设置里重新选择。' % ref)

    body = {
        'model': resolved.model,
        # 这个模型会先输出 reasoning，max_tokens 给小了 content 会是空串（实测）。
        'max_tokens': max(1200, int(resolved.max_tokens or 0)),
        'messages': [{'role': 'user', 'content': [
            {'type': 'text', 'text': PROMPT},
            {'type': 'image_url', 'image_url': {
                'url': 'data:image/png;base64,' + base64.b64encode(png).decode()}},
        ]}],
    }
    url = (resolved.api_base or '').rstrip('/') + '/chat/completions'
    try:
        resp = requests.post(url, headers={'Authorization': 'Bearer ' + (resolved.api_key or ''),
                                          'Content-Type': 'application/json'},
                             json=body, timeout=120)
    except requests.RequestException as exc:
        raise CaptchaError('调用视觉模型失败：%s' % _short(exc)) from exc
    if resp.status_code != 200:
        raise CaptchaError('视觉模型返回 %d：%s' % (resp.status_code, _short(resp.text, 160)))

    message = (resp.json().get('choices') or [{}])[0].get('message') or {}
    # content 是答案，reasoning_content 是思考过程。两个都看，因为模型偶尔只把
    # 结论写在思考里（content 空）。但必须先试 content：思考里出现的数字可能是
    # 被它否掉的候选，拿那个填进去必错。
    for field in ('content', 'reasoning_content'):
        code = _clean(message.get(field), charset, length)
        if code:
            return code, '%s · %s' % (ref, field)
    raise CaptchaError('模型没有识别出内容：%s'
                       % _short(message.get('content') or message.get('reasoning_content') or '', 120))


def find_input(page, cap_el, selector=''):
    """找验证码输入框。

    指定了就用指定的；没指定就从验证码元素往上逐层找第一个含 input 的祖先。
    同一层有多个时按垂直距离取最近的 —— 验证码和它的输入框基本都在同一行。
    实测目标站点：向上 1 层（.code-input）就命中唯一一个 input，垂直差 1px。
    """
    if selector:
        try:
            el = page.query_selector(selector)
        except Exception as exc:  # noqa: BLE001
            raise CaptchaError('输入框选择器不可用：%s' % _short(exc)) from exc
        if el is None:
            raise CaptchaError('按选择器没找到验证码输入框：%s' % selector)
        return el
    try:
        handle = cap_el.evaluate_handle("""cap => {
            let p = cap;
            for (let i = 1; i <= 4; i += 1) {
                p = p.parentElement;
                if (!p) return null;
                const inputs = Array.from(p.querySelectorAll('input')).filter(
                    (el) => el.type !== 'hidden' && el.type !== 'password' && !el.disabled);
                if (!inputs.length) continue;
                const cy = cap.getBoundingClientRect().y;
                inputs.sort((a, b) => Math.abs(a.getBoundingClientRect().y - cy)
                                    - Math.abs(b.getBoundingClientRect().y - cy));
                return inputs[0];
            }
            return null;
        }""")
    except Exception as exc:  # noqa: BLE001
        raise CaptchaError('找验证码输入框失败：%s' % _short(exc)) from exc
    return handle.as_element() if handle is not None else None


def _value(el):
    try:
        return el.input_value() or ''
    except Exception:  # noqa: BLE001
        return ''


def fill_code(el, code):
    """填入并读回。

    受控组件偶尔吞掉 fill 的输入（值被框架覆写回去），读回不一致就改逐字敲，
    这样 keydown 事件也会走一遍，Vue/React 的受控绑定能接住。
    """
    try:
        el.fill(code)
    except Exception:  # noqa: BLE001
        el.click()
    value = _value(el)
    if value == code:
        return value
    try:
        el.click()
        el.press_sequentially(code, delay=60)
    except AttributeError:      # 老版本 Playwright 没有 press_sequentially
        try:
            el.type(code, delay=60)
        except Exception:       # noqa: BLE001
            pass
    except Exception:           # noqa: BLE001
        pass
    return _value(el)


def refresh(page, item, before, timeout=5.0):
    """点验证码换一张，返回 (新元素, 新截图)；换不出来返回 (None, None)。

    不靠 DOM 判断「换没换」—— 图片验证码的 DOM 里本来就没有码可读。
    用截图字节是否变化来判断，这个判据对所有形态都成立。
    """
    try:
        item['el'].click()
    except Exception:  # noqa: BLE001 - 元素被替换或页面跳走
        return None, None
    deadline = time.time() + timeout
    while time.time() < deadline:
        page.wait_for_timeout(300)
        fresh = find_captcha(page, item['selector'])
        if fresh is None:
            continue
        try:
            png = capture(page, fresh)
        except Exception:  # noqa: BLE001 - 重绘中截图会失败，下一轮再来
            continue
        if png != before:
            return fresh, png
    return None, None


def solve(page, config=None, on_tick=None):
    """识别验证码并填入。返回结果字典；识别/填入失败抛 CaptchaError。

    `config` 来自步骤里的 captcha 字段：
      selector        验证码元素，拾取得到（空则按常见命名猜）
      input_selector  输入框，空则从验证码元素向上找
      charset         digits | alnum | any，解析模型回话时用
      length          期望位数，0 = 不校验
      attempts        识别失败后最多刷新重试几次（默认 2）
      timeout         单次等图片就绪的秒数（默认 20，目前只影响等待上限）

    **重试只覆盖识别失败**（返回空、位数不符）。「看错一位但位数正确」步骤自己
    发现不了 —— 那需要提交后看服务端提示，属于后续断言或提交闭环的职责。
    所以不要因为「有 attempts」就假设它一定能填对。
    """
    config = config or {}
    attempts = max(0, min(int(config.get('attempts') or 2), 5))
    selector = str(config.get('selector') or '').strip()
    input_selector = str(config.get('input_selector') or '').strip()
    charset = str(config.get('charset') or 'digits').strip()
    if charset not in CHARSETS:
        charset = 'digits'
    length = max(0, min(int(config.get('length') or 0), 12))

    started = time.perf_counter()
    reads = []
    reason = ''

    for attempt in range(1, attempts + 2):
        if on_tick:
            # 一次识别 6～8 秒，中间不回帧的话回放画面会像卡死。
            on_tick()

        item = find_captcha(page, selector)
        if item is None:
            if not selector:
                raise CaptchaError('当前页面上没有找到验证码图片。请拾取一次验证码元素再试。')
            raise CaptchaError('按选择器没找到验证码元素：%s（页面可能还没加载完）' % selector)

        if not wait_ready(page, item['el']):
            reason = '验证码图片没有加载出来'
            reads.append({'attempt': attempt, 'code': '', 'via': 'not-ready'})
            if attempt <= attempts:
                fresh, _ = refresh(page, item, b'')
                if fresh is None:
                    break
                item = fresh
                continue
            break

        try:
            png = capture(page, item)
        except Exception as exc:  # noqa: BLE001
            reason = '截图失败：%s' % _short(exc)
            reads.append({'attempt': attempt, 'code': '', 'via': 'capture'})
            continue

        big, width, height = enlarge(png)
        code, note = ask_model(big, charset, length)
        reads.append({'attempt': attempt, 'code': code, 'via': note})
        logger.info('验证码第 %d 次识别：%r（%s）', attempt, code, note)

        if not code:
            reason = '模型没有识别出内容'
        elif length and len(code) != length:
            reason = '识别出 %d 位（%s），与设定的 %d 位不符' % (len(code), code, length)
        else:
            box = find_input(page, item['el'], input_selector)
            if box is None:
                raise CaptchaError('没找到验证码输入框。请拾取一次输入框再试。')
            value = fill_code(box, code)
            if value != code:
                raise CaptchaError(
                    '验证码 %s 没能填进输入框（读回来是 %r）。可能这个框有输入掩码或长度限制。'
                    % (code, value))
            return {
                'ok': True,
                'code': code,
                'attempt': attempt,
                'via': note,
                'size': '%d×%d' % (width, height),
                'elapsed': round(time.perf_counter() - started, 1),
                'reads': reads,
            }

        if attempt > attempts:
            break
        # 识别失败：刷新换一张再认。旧图上的答案可能本来就没认全，重试同图无意义。
        fresh, _ = refresh(page, item, png)
        if fresh is None:
            reason += '；刷新验证码失败'
            break
        item = fresh

    raise CaptchaError('验证码识别失败：%s（已尝试 %d 次）' % (reason or '原因不明', len(reads)))


def describe(result):
    """给回放明细用的一句话。"""
    code = result.get('code') or ''
    masked = code if len(code) <= 6 else code[:6] + '…'
    return '验证码已填入：%s（%s，第 %d 次识别，%.1fs）' % (
        masked, result.get('size', '?'), result.get('attempt', 1),
        result.get('elapsed', 0))
