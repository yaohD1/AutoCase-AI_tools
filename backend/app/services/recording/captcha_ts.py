"""生成到目标项目里的验证码工具源码（TypeScript）。

和 Python 侧 `captcha.py` 是同一套流程，但有一处**必须不同**：

Python 侧用 PIL 放大（服务器上装了 Pillow），生成的测试项目里没有图片处理库。
所以这里在**浏览器里用 canvas 放大**：把一个 dataURL 画到 3 倍尺寸的 canvas 上再导出。
这不是妥协 —— 放大倍数是识别准确率的关键（原图 70%，3 倍 100%），少了它生成出来的
代码会明显比平台内回放差。canvas 的 imageSmoothingQuality='high' 效果接近 PIL 的
LANCZOS，实测同一批图两边都能认对。

模型配置走环境变量，优先 CAPTCHA_MODEL_*，缺了回落 SLIDER_MODEL_*（同一个视觉模型，
没必要配两遍）。
"""

CAPTCHA_TS = r"""/**
 * 图形验证码识别：截图 → 放大 → 问视觉模型 → 填入输入框。
 *
 * 由 AutoCase 生成。只识别字符并填入，不点提交 —— 提交留给后面的 click 步骤，
 * 这样同一步也能用在注册、找回密码、点「获取短信验证码」上。
 *
 * 模型配置来自环境变量，不要硬编码：
 *   CAPTCHA_MODEL_BASE_URL / CAPTCHA_MODEL_NAME / CAPTCHA_MODEL_API_KEY
 * 缺省时自动回落到 SLIDER_MODEL_*（滑块用的同一组）。
 *
 * 这个文件不要手改：平台重新生成时会覆盖。
 */
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';

import type { ElementHandle, Page } from '@playwright/test';

export interface CaptchaConfig {
  /** 验证码元素选择器（拾取得到）；留空则按常见命名猜一遍 */
  selector?: string;
  /** 验证码输入框选择器；留空则从验证码元素向上逐层找 */
  inputSelector?: string;
  /** 只认数字 / 字母数字 / 任意字符 */
  charset?: 'digits' | 'alnum' | 'any';
  /** 期望位数，0 = 不校验 */
  length?: number;
  /** 识别失败后最多刷新重试几次（默认 2） */
  attempts?: number;
  /** 单次等图片就绪的秒数（默认 20） */
  timeout?: number;
}

/** 放大倍数。实测：原图 7/10，3 倍 10/10，4 倍 9/10 —— 不要随手改。 */
const SCALE = 3;
const MAX_W = 640;
const MAX_H = 320;
const MIN_W = 20;
const MIN_H = 12;

const FALLBACK_SELECTORS = [
  'img[src*="captcha"]',
  'img[src*="kaptcha"]',
  'img[src*="verifyCode"]',
  'img[src*="verify_code"]',
  'img[src*="code"]',
  '[class*="captcha"] img',
  '[class*="captcha"] canvas',
  '[class*="verify"] img',
  '[class*="verify"] canvas',
];

const PROMPT =
  '这是网页上的验证码图片。请只输出验证码本身的内容：\n' +
  '- 数字或字母：原样输出，区分大小写，不要加空格、标点或引号\n' +
  '- 算术题：直接输出计算结果\n' +
  '- 汉字：原样输出\n' +
  '不要输出任何解释文字。';

interface CaptchaEl {
  el: ElementHandle;
  selector: string;
}

let dotenvCache: Record<string, string> | null = null;

/**
 * Playwright 不会自动加载 .env，这里补上：从这个文件往上找 .env。
 * 不引入 dotenv 依赖——目标项目不一定装了，而这段逻辑只有二十行。
 */
function readDotenv(): Record<string, string> {
  if (dotenvCache) return dotenvCache;
  const out: Record<string, string> = {};
  let dir = typeof __dirname !== 'undefined' ? __dirname : process.cwd();
  for (let depth = 0; depth < 4; depth += 1) {
    const file = join(dir, '.env');
    if (existsSync(file)) {
      for (const raw of readFileSync(file, 'utf8').split(/\r?\n/)) {
        const line = raw.trim();
        if (!line || line.startsWith('#')) continue;
        const eq = line.indexOf('=');
        if (eq <= 0) continue;
        const key = line.slice(0, eq).trim();
        let value = line.slice(eq + 1).trim();
        if ((value.startsWith('"') && value.endsWith('"')) ||
            (value.startsWith("'") && value.endsWith("'"))) {
          value = value.slice(1, -1);
        }
        if (!(key in out)) out[key] = value;
      }
      break;
    }
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  dotenvCache = out;
  return out;
}

function env(name: string): string {
  const fromFile = readDotenv()[name];
  if (fromFile) return fromFile;
  return process.env[name] || '';
}

/** 验证码模型优先，缺了回落到滑块那组（同一个视觉模型，不必配两遍）。 */
function modelEnv(suffix: string): string {
  return env('CAPTCHA_MODEL_' + suffix) || env('SLIDER_MODEL_' + suffix);
}

function clean(raw: unknown, charset: string, length: number): string {
  // 不能「去掉所有空白」：会把 "The code is a3B9" 连成 "Thecodeisa3B9"。
  // 只合并「位置相邻、中间只有空白、且都是单字符」的片段：
  // "4 2 6 8" → 4268，而 "4 2 6 8（6 位）" 里括号那个 6 前面隔的是「（」，不合并。
  const text = String(raw || '').trim();
  if (!text) return '';
  if (charset === 'any') {
    return text.replace(/[\s，。、：；！？"'`（）()\[\]{}<>《》|]+/g, '');
  }
  const pattern = charset === 'digits' ? /\d+/g : /[A-Za-z0-9]+/g;
  const groups: string[] = [];
  let cur = '';
  let lastEnd = -1;
  let prevLen = 0;
  for (const m of text.matchAll(pattern)) {
    const token = m[0];
    const start = m.index ?? 0;
    const gap = lastEnd >= 0 ? text.slice(lastEnd, start) : '';
    // prevLen 要单独记：累积串变长后 cur.length 就不再是「前一个 token 的长度」了。
    if (cur && token.length === 1 && prevLen === 1 && !gap.trim()) {
      cur += token;
    } else {
      if (cur) groups.push(cur);
      cur = token;
    }
    lastEnd = start + token.length;
    prevLen = token.length;
  }
  if (cur) groups.push(cur);
  if (!groups.length) return '';

  let pool = groups;
  if (charset === 'alnum') {
    const mixed = groups.filter((g) => /\d/.test(g));
    if (mixed.length) pool = mixed;
  }

  if (length) {
    const exact = pool.filter((g) => g.length === length);
    if (exact.length) return exact[exact.length - 1];
  }
  return pool.reduce((a, b) => (b.length >= a.length ? b : a), '');
}

/** 拾取到容器时取里面那张真图：截容器会把输入框、刷新按钮一起截进去。 */
async function innerMedia(item: CaptchaEl): Promise<CaptchaEl | null> {
  for (const sel of ['img', 'canvas']) {
    const handles = await item.el.$$(sel).catch(() => [] as ElementHandle[]);
    for (const el of handles) {
      const box = await el.boundingBox().catch(() => null);
      if (!box) continue;
      if (box.width < MIN_W || box.width > MAX_W) continue;
      if (box.height < MIN_H || box.height > MAX_H) continue;
      return { el, selector: item.selector };
    }
  }
  return null;
}

async function findCaptcha(page: Page, selector?: string): Promise<CaptchaEl | null> {
  const list = selector ? [selector] : FALLBACK_SELECTORS;
  for (const sel of list) {
    const handles = await page.$$(sel).catch(() => [] as ElementHandle[]);
    for (const el of handles) {
      if (!(await el.isVisible().catch(() => false))) continue;
      const box = await el.boundingBox().catch(() => null);
      if (!box) continue;
      if (box.width < MIN_W || box.height < MIN_H) continue;
      const item = { el, selector: selector || '' };
      const inner = await innerMedia(item);
      if (inner) return inner;
      if (box.width <= MAX_W && box.height <= MAX_H) return item;
      if (selector) {
        throw new Error(
          `拾取到的元素是 ${Math.round(box.width)}×${Math.round(box.height)}，`
          + '比验证码大太多。请重新拾取，只点验证码图片本身。');
      }
    }
    if (selector) return null;
  }
  return null;
}

/** 等图真的画出来再截：img 还在加载时截到的是空白，模型会返回空。 */
async function waitReady(page: Page, el: ElementHandle, timeoutMs = 4000): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const state = await el.evaluate((node: Element) => {
      if (node.tagName === 'IMG') {
        const img = node as HTMLImageElement;
        return img.complete && img.naturalWidth > 0;
      }
      if (node.tagName === 'CANVAS') {
        const c = node as HTMLCanvasElement;
        return c.width > 0 && c.height > 0;
      }
      return true;
    }).catch(() => null);
    if (state === null) return false;
    if (state === true) {
      await page.waitForTimeout(120);
      return true;
    }
    await page.waitForTimeout(150);
  }
  return false;
}

async function capture(page: Page, item: CaptchaEl): Promise<Buffer> {
  const box = await item.el.boundingBox();
  if (!box) throw new Error('验证码元素不可见，截不到图');
  return page.screenshot({
    clip: { x: box.x, y: box.y, width: box.width, height: box.height },
  });
}

/**
 * 在浏览器里把截图放大。
 *
 * 为什么不在 Node 里做：目标项目没有 sharp/jimp，装一个只为了放大不值当。
 * 而 canvas 本来就在浏览器里，imageSmoothingQuality='high' 的效果接近 LANCZOS。
 * 失败就退回原图 —— 能认出就认，认不出会走重试，不会因此中断用例。
 */
async function upscale(page: Page, png: Buffer, factor: number): Promise<Buffer> {
  try {
    const b64 = await page.evaluate(async (args: { data: string; factor: number }) => {
      const img = new Image();
      await new Promise<void>((resolve, reject) => {
        img.onload = () => resolve();
        img.onerror = () => reject(new Error('decode failed'));
        img.src = 'data:image/png;base64,' + args.data;
      });
      const canvas = document.createElement('canvas');
      canvas.width = Math.round(img.width * args.factor);
      canvas.height = Math.round(img.height * args.factor);
      const ctx = canvas.getContext('2d');
      if (!ctx) throw new Error('no 2d context');
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = 'high';
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      return canvas.toDataURL('image/png').split(',')[1];
    }, { data: png.toString('base64'), factor });
    if (!b64) return png;
    return Buffer.from(b64, 'base64');
  } catch {
    // 页面可能跳走了，或者 CSP 挡了 data: 图片。退回原图继续。
    return png;
  }
}

async function askModel(png: Buffer, charset: string, length: number): Promise<{ code: string; note: string }> {
  const base = modelEnv('BASE_URL').replace(/\/+$/, '');
  const model = modelEnv('NAME');
  if (!base || !model) {
    throw new Error('没有配置视觉模型：.env 里缺 CAPTCHA_MODEL_* 或 SLIDER_MODEL_*');
  }
  const resp = await fetch(`${base}/chat/completions`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${modelEnv('API_KEY')}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      model,
      // 这个模型会先输出 reasoning，给小了 content 会是空的。
      max_tokens: 1200,
      messages: [{
        role: 'user',
        content: [
          { type: 'text', text: PROMPT },
          { type: 'image_url', image_url: { url: `data:image/png;base64,${png.toString('base64')}` } },
        ],
      }],
    }),
  });
  if (!resp.ok) {
    throw new Error(`验证码模型返回 ${resp.status}: ${(await resp.text()).slice(0, 160)}`);
  }
  const body: any = await resp.json();
  const message = body?.choices?.[0]?.message || {};
  // content 是答案，reasoning_content 是思考过程。必须 content 优先：
  // 思考里出现的数字可能是被它自己否掉的候选，拿去填必错。
  for (const field of ['content', 'reasoning_content']) {
    const code = clean(message[field], charset, length);
    if (code) return { code, note: `${model} · ${field}` };
  }
  throw new Error(`模型没有识别出内容：${String(message.content || '').slice(0, 120)}`);
}

async function findInput(page: Page, cap: ElementHandle, selector?: string): Promise<ElementHandle | null> {
  if (selector) {
    const handles = await page.$$(selector).catch(() => [] as ElementHandle[]);
    if (!handles.length) throw new Error(`按选择器没找到验证码输入框：${selector}`);
    return handles[0];
  }
  const handle = await cap.evaluateHandle((node: Element) => {
    let p: Element | null = node;
    for (let i = 1; i <= 4; i += 1) {
      p = p ? p.parentElement : null;
      if (!p) return null;
      const inputs = Array.from(p.querySelectorAll('input')).filter(
        (el) => el.type !== 'hidden' && el.type !== 'password' && !el.disabled,
      );
      if (!inputs.length) continue;
      const cy = node.getBoundingClientRect().y;
      inputs.sort((a, b) => Math.abs(a.getBoundingClientRect().y - cy)
                          - Math.abs(b.getBoundingClientRect().y - cy));
      return inputs[0];
    }
    return null;
  });
  const el = handle.asElement();
  return el as ElementHandle | null;
}

/** 填入并读回。受控组件偶尔吞掉 fill，读回不一致就改逐字敲。 */
async function fillCode(el: ElementHandle, code: string): Promise<string> {
  await el.fill(code).catch(async () => { await el.click().catch(() => {}); });
  let value = await el.inputValue().catch(() => '');
  if (value === code) return value;
  await el.click().catch(() => {});
  await el.type(code, { delay: 60 }).catch(() => {});
  value = await el.inputValue().catch(() => '');
  return value;
}

/** 点验证码换一张。用截图字节判断换没换 —— 图片验证码的 DOM 里没有码可读。 */
async function refresh(page: Page, item: CaptchaEl, before: Buffer | null): Promise<CaptchaEl | null> {
  await item.el.click().catch(() => {});
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    await page.waitForTimeout(300);
    const fresh = await findCaptcha(page, item.selector);
    if (!fresh) continue;
    const png = await capture(page, fresh).catch(() => null);
    if (!png) continue;
    if (!before || !png.equals(before)) return fresh;
  }
  return null;
}

/**
 * 识别验证码并填入。失败抛错。
 *
 * 重试只覆盖「返回空 / 位数不符」。看错一位但位数正确时这个方法发现不了，
 * 要等后续断言暴露 —— 不要因为配了 attempts 就假设它一定填对。
 */
export async function solveCaptcha(page: Page, config: CaptchaConfig = {}): Promise<void> {
  const attempts = Math.min(Math.max(config.attempts ?? 2, 0), 5);
  const charset = config.charset ?? 'digits';
  const length = Math.min(Math.max(config.length ?? 0, 0), 12);
  const selector = (config.selector || '').trim();
  const inputSelector = (config.inputSelector || '').trim();

  let reason = '';
  for (let attempt = 1; attempt <= attempts + 1; attempt += 1) {
    let item = await findCaptcha(page, selector);
    if (!item) {
      throw new Error(selector
        ? `按选择器没找到验证码元素：${selector}（页面可能还没加载完）`
        : '当前页面上没有找到验证码图片。请拾取一次验证码元素再试。');
    }
    if (!(await waitReady(page, item.el))) {
      reason = '验证码图片没有加载出来';
      const fresh = await refresh(page, item, null);
      if (!fresh) break;
      item = fresh;
      continue;
    }

    const png = await capture(page, item);
    const big = await upscale(page, png, SCALE);
    const { code } = await askModel(big, charset, length);

    if (!code) {
      reason = '模型没有识别出内容';
    } else if (length && code.length !== length) {
      reason = `识别出 ${code.length} 位（${code}），与设定的 ${length} 位不符`;
    } else {
      const input = await findInput(page, item.el, inputSelector);
      if (!input) throw new Error('没找到验证码输入框。请拾取一次输入框再试。');
      const value = await fillCode(input, code);
      if (value !== code) {
        throw new Error(
          `验证码 ${code} 没能填进输入框（读回来是 "${value}"）。`
          + '可能这个框有输入掩码或长度限制。');
      }
      return;
    }

    if (attempt > attempts) break;
    const fresh = await refresh(page, item, png);
    if (!fresh) {
      reason += '；刷新验证码失败';
      break;
    }
    item = fresh;
  }
  throw new Error(`验证码识别失败：${reason || '原因不明'}`);
}
"""
