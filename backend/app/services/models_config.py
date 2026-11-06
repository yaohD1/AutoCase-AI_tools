# -*- coding: utf-8 -*-
"""opencode 模型配置的唯一读写口。

数据放在项目根的 `opencode.models.json`（已 gitignore，含明文密钥），格式就是
opencode 原生配置，只写 `provider` / `model` 两段，外加一个 opencode 会忽略的
`autocase` 段存 AutoCase 自己的采样参数：

    {
      "model": "cctq-codex/gpt-6-luna",
      "provider": {
        "cctq-codex": {
          "npm": "@ai-sdk/openai",
          "options": {"baseURL": "...", "apiKey": "sk-..."},
          "models": {"gpt-6-luna": {"name": "luna"}},
          "whitelist": ["gpt-6-luna"]        // 只在要砍掉全局模型时才写
        }
      },
      "autocase": {"models": {"cctq-codex/gpt-6-luna": {"temperature": 0.7,
                                                        "maxTokens": 4000,
                                                        "vision": true}}}
    }

三个必须记住的实测结论（1.18.31）：

- 项目级 provider 与全局 `~/.config/opencode/opencode.json` 是**深合并**，所以
  全局有的模型删不掉；要砍只能用 `whitelist`（实测 `opencode models` 立刻只剩
  白名单里的）。`disabled: true` 写在模型上会被 opencode 直接丢弃，不能用。
- `disabled_providers` 能让整个 provider 消失，用于「隐藏整个服务」。
- `OPENCODE_CONFIG` 与工作区自带的 `opencode.json` 是叠加的，所以本文件作为
  额外一层传入即可，不需要把 provider 塞进那份进仓库的配置。
"""
import copy
import json
import os
import threading
from pathlib import Path

import requests

LOCAL_FILE = 'opencode.models.json'
BASE_FILE = 'opencode.json'
RUNTIME_FILE = 'opencode.runtime.json'
MAX_MODELS_PER_PROVIDER = 200
MAX_PROVIDERS = 50

_LOCK = threading.RLock()


class ModelConfigError(Exception):
    """配置读写失败，消息直接给前端看。"""


def project_root():
    return Path(__file__).resolve().parents[3]


def local_path():
    return project_root() / LOCAL_FILE


def base_path():
    return project_root() / BASE_FILE


def runtime_path():
    return project_root() / RUNTIME_FILE


def global_path():
    return Path.home() / '.config' / 'opencode' / 'opencode.json'


def mask_key(key):
    """只露前 6 后 4。短 key 全遮，避免「脱敏」成明文。"""
    if not key:
        return None
    key = str(key)
    if len(key) <= 12:
        return '•' * len(key)
    return '%s••••%s' % (key[:6], key[-4:])


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ModelConfigError('读取 %s 失败：%s' % (Path(path).name, exc))


def _write_json(path, data):
    """原子写：先写临时文件再替换，避免半截 JSON 让 opencode 起不来。"""
    path = Path(path)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(str(tmp), str(path))


def _deep_merge(base, extra):
    """对象深合并、数组整体替换——和 opencode 自己合并配置的规则保持一致。"""
    if not isinstance(base, dict) or not isinstance(extra, dict):
        return copy.deepcopy(extra)
    out = copy.deepcopy(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _public_provider(data):
    """去掉密钥再给前端。apiKey 只留掩码，baseURL 留着。"""
    out = {k: copy.deepcopy(v) for k, v in data.items() if k not in ('options',)}
    options = dict(data.get('options') or {})
    key = options.pop('apiKey', None)
    out['options'] = options
    out['has_key'] = bool(key)
    out['key_masked'] = mask_key(key)
    return out


def empty_local():
    return {'$schema': 'https://opencode.ai/config.json', 'provider': {},
            'autocase': {'models': {}}}


def load_local(create=True):
    """读本地配置。首次使用时从全局导一份，避免页面一片空白。"""
    path = local_path()
    if not path.is_file():
        if not create:
            return empty_local()
        data = _import_from_global()
        _write_json(path, data)
        return data
    data = _read_json(path)
    if not isinstance(data, dict):
        raise ModelConfigError('%s 不是一个 JSON 对象' % LOCAL_FILE)
    data.setdefault('provider', {})
    data.setdefault('autocase', {}).setdefault('models', {})
    return data


def _import_from_global():
    """首次初始化：把全局 provider 抄一份过来。

    用户本机的 key 本来就在全局配置里，抄过来开箱即用；此后只维护本地这份，
    改全局不会反过来影响 AutoCase。
    """
    data = empty_local()
    glob = _read_json(global_path())
    providers = glob.get('provider') or {}
    if isinstance(providers, dict) and providers:
        data['provider'] = copy.deepcopy(providers)
    if glob.get('model'):
        data['model'] = glob['model']
    return data


def save_local(data):
    """落盘并让 runtime 配置失效。"""
    with _LOCK:
        _write_json(local_path(), data)
        runtime_path().unlink(missing_ok=True)


# ---------------------------------------------------------------- 视图

def _vision_flag(model, extra):
    """模型能不能收图。

    真相在 opencode 的 `modalities.input`——它也是 opencode 自己判断收不收图依据。
    用户在设置页显式勾过「支持图片」时以勾选为准（runtime 会把勾选同步成 modalities，
    两边最终一致）。都看不出来就算不支持，避免界面写「可看图」、实际被 opencode 拒绝。
    """
    explicit = (extra or {}).get('vision')
    if explicit is not None:
        return bool(explicit)
    inputs = ((model or {}).get('modalities') or {}).get('input') or []
    return 'image' in inputs


def merged_view():
    """全局 + 本地合并后的只读视图，前端就渲染这个。

    source 说明：
      provider.source = local  → 本地文件里定义了（可改可删）
                        global → 只在全局里有（只能砍模型或整个隐藏）
      model.source    = local  → 本地 models 里定义
                        global → 全局带的
    """
    local = load_local()
    glob = _read_json(global_path())
    base = _read_json(base_path())
    local_providers = local.get('provider') or {}
    glob_providers = glob.get('provider') or {}
    base_providers = base.get('provider') or {}
    meta = (local.get('autocase') or {}).get('models') or {}
    disabled_providers = list(local.get('disabled_providers') or [])

    order = list(dict.fromkeys(
        list(glob_providers.keys()) + list(local_providers.keys())))

    providers = []
    for pid in order[:MAX_PROVIDERS]:
        g = glob_providers.get(pid) or {}
        l = local_providers.get(pid) or {}
        merged = _deep_merge(g, l)
        models = merged.get('models') or {}
        # whitelist 是本地的裁剪手段：写了就只认列出来的模型。
        whitelist = l.get('whitelist')
        local_models = l.get('models') or {}
        items = []
        for mid, model in list(models.items())[:MAX_MODELS_PER_PROVIDER]:
            if whitelist is not None and mid not in whitelist:
                continue
            extra = meta.get('%s/%s' % (pid, mid)) or {}
            items.append({
                'id': mid,
                'name': (model or {}).get('name') or mid,
                'source': 'local' if mid in local_models else 'global',
                'vision': _vision_flag(model, extra),
                'temperature': extra.get('temperature'),
                'maxTokens': extra.get('maxTokens'),
            })
        if whitelist is not None:
            # 白名单里可能有全局没有的模型（用户自己补的），也要列出来。
            for mid in whitelist:
                if mid in models:
                    continue
                items.append({'id': mid, 'name': mid, 'source': 'local',
                              'vision': False, 'temperature': None,
                              'maxTokens': None})
        src = 'local' if (l or pid in base_providers) else 'global'
        providers.append({
            'id': pid,
            'source': src,
            'npm': merged.get('npm') or '',
            'base_url': (merged.get('options') or {}).get('baseURL') or '',
            'has_key': bool((merged.get('options') or {}).get('apiKey')),
            'key_masked': mask_key((merged.get('options') or {}).get('apiKey')),
            'whitelist': whitelist,
            'disabled': pid in disabled_providers,
            'models': items,
        })

    default_model = local.get('model') or base.get('model') or ''
    return {
        'default': default_model,
        'providers': providers,
        'file': str(local_path()),
        'from_global': str(global_path()),
        'disabled_providers': disabled_providers,
    }


def flat_models(include_hidden=False):
    """扁平模型列表，给「选模型」的下拉用。id 就是 `provider/model`。"""
    view = merged_view()
    out = []
    for provider in view['providers']:
        if provider.get('disabled') and not include_hidden:
            continue
        for model in provider['models']:
            out.append({
                'id': '%s/%s' % (provider['id'], model['id']),
                'provider': provider['id'],
                'model': model['id'],
                'label': model['name'] or model['id'],
                'vision': model.get('vision', True),
                'temperature': model.get('temperature'),
                'max_tokens': model.get('maxTokens'),
                'api_base': provider.get('base_url') or '',
                'source': model.get('source'),
            })
    return out


# ---------------------------------------------------------------- 写操作

def _require_local_provider(data, pid, allow_global_shadow=False):
    providers = data.setdefault('provider', {})
    if pid not in providers:
        if not allow_global_shadow:
            raise ModelConfigError('服务「%s」不在本地配置里，请先新增' % pid)
        providers[pid] = {}
    return providers[pid]


def upsert_provider(pid, npm='', base_url='', api_key=None, name=None):
    """新增或修改一个 provider。已存在时只改传进来的字段。"""
    pid = (pid or '').strip()
    if not pid or '/' in pid or len(pid) > 64:
        raise ModelConfigError('服务名不能为空、不能带 /，最长 64 字符')
    with _LOCK:
        data = load_local()
        providers = data.setdefault('provider', {})
        current = providers.get(pid) or {}
        if not isinstance(current, dict):
            current = {}
        if name:
            current['name'] = name
        if npm:
            current['npm'] = npm
        elif not current.get('npm'):
            current.setdefault('npm', '@ai-sdk/openai')
        options = dict(current.get('options') or {})
        if base_url:
            options['baseURL'] = base_url.strip()
        if api_key:
            options['apiKey'] = api_key.strip()
        if options:
            current['options'] = options
        current.setdefault('models', {})
        providers[pid] = current
        # 新加的 provider 如果之前在隐藏名单里，顺手放出来。
        disabled = [p for p in (data.get('disabled_providers') or []) if p != pid]
        if disabled:
            data['disabled_providers'] = disabled
        else:
            data.pop('disabled_providers', None)
        save_local(data)
        return merged_view()


def delete_provider(pid):
    """删本地 provider。全局来的只移除本地覆盖，等于「还原成全局的样子」。"""
    with _LOCK:
        data = load_local()
        providers = data.setdefault('provider', {})
        providers.pop(pid, None)
        meta = (data.get('autocase') or {}).get('models') or {}
        for key in [k for k in meta if k.split('/', 1)[0] == pid]:
            meta.pop(key, None)
        default = data.get('model') or ''
        if default.startswith(pid + '/'):
            data.pop('model', None)
        save_local(data)
        return merged_view()


def add_model(pid, model_id, name=None, temperature=None, max_tokens=None,
              vision=True):
    model_id = (model_id or '').strip()
    if not model_id or '/' in model_id or len(model_id) > 128:
        raise ModelConfigError('模型 ID 不能为空、不能带 /，最长 128 字符')
    with _LOCK:
        data = load_local()
        provider = _require_local_provider(data, pid, allow_global_shadow=True)
        models = provider.setdefault('models', {})
        if len(models) >= MAX_MODELS_PER_PROVIDER:
            raise ModelConfigError('单个服务最多 %d 个模型' % MAX_MODELS_PER_PROVIDER)
        models[model_id] = {'name': (name or model_id).strip()}
        # 如果之前用 whitelist 裁过，新模型要一起放进去，否则会被过滤掉。
        if isinstance(provider.get('whitelist'), list):
            if model_id not in provider['whitelist']:
                provider['whitelist'].append(model_id)
        meta = data.setdefault('autocase', {}).setdefault('models', {})
        meta['%s/%s' % (pid, model_id)] = {
            'temperature': temperature,
            'maxTokens': max_tokens,
            'vision': bool(vision),
        }
        save_local(data)
        return merged_view()


def update_model(pid, model_id, name=None, temperature=None, max_tokens=None,
                 vision=None):
    """改展示名 / 采样参数。模型 ID 本身不可改——那等于换了个模型。"""
    with _LOCK:
        data = load_local()
        provider = _require_local_provider(data, pid, allow_global_shadow=True)
        model = (provider.get('models') or {}).get(model_id)
        if isinstance(model, dict):
            if name is not None:
                model['name'] = name or model_id
        elif name is not None:
            provider.setdefault('models', {})[model_id] = {'name': name or model_id}
        meta = data.setdefault('autocase', {}).setdefault('models', {})
        entry = meta.setdefault('%s/%s' % (pid, model_id), {})
        if temperature is not None:
            entry['temperature'] = temperature
        if max_tokens is not None:
            entry['maxTokens'] = max_tokens
        if vision is not None:
            entry['vision'] = bool(vision)
        save_local(data)
        return merged_view()


def remove_model(pid, model_id):
    """删模型。

    全局带的模型删不掉（深合并会合并回来），只能写 whitelist 把它排除；本地定义的
    直接删。两种都做了，调用方不用关心来源。
    """
    with _LOCK:
        data = load_local()
        glob = _read_json(global_path())
        in_global = model_id in (((glob.get('provider') or {}).get(pid) or {})
                                 .get('models') or {})
        provider = _require_local_provider(data, pid, allow_global_shadow=True)
        models = provider.setdefault('models', {})
        models.pop(model_id, None)

        if in_global:
            # 用白名单表达「这个服务只要剩下的这些」。
            keep = [m for m in ((glob.get('provider') or {}).get(pid) or {}).get('models', {})
                    if m != model_id]
            current = provider.get('whitelist')
            if isinstance(current, list):
                keep = [m for m in current if m != model_id]
            keep += [m for m in models if m not in keep]
            provider['whitelist'] = keep
        elif isinstance(provider.get('whitelist'), list):
            provider['whitelist'] = [m for m in provider['whitelist'] if m != model_id]

        meta = data.setdefault('autocase', {}).setdefault('models', {})
        meta.pop('%s/%s' % (pid, model_id), None)
        default = data.get('model') or ''
        if default == '%s/%s' % (pid, model_id):
            data.pop('model', None)
        if not models and not provider.get('whitelist') and not in_global:
            data['provider'].pop(pid, None)
        save_local(data)
        return merged_view()


def set_provider_hidden(pid, hidden):
    """整个 provider 隐藏/恢复，用 opencode 的 disabled_providers。"""
    with _LOCK:
        data = load_local()
        names = [p for p in (data.get('disabled_providers') or []) if p != pid]
        if hidden:
            names.append(pid)
        if names:
            data['disabled_providers'] = names
        else:
            data.pop('disabled_providers', None)
        save_local(data)
        return merged_view()


def set_default(value):
    """设默认模型，值形如 `provider/model`。"""
    with _LOCK:
        data = load_local()
        if value:
            provider, _, model = value.partition('/')
            exists = any(p['id'] == provider and any(m['id'] == model for m in p['models'])
                         for p in merged_view()['providers'])
            if not exists:
                raise ModelConfigError('模型「%s」不存在' % value)
            data['model'] = value
        else:
            data.pop('model', None)
        save_local(data)
        return merged_view()


# ---------------------------------------------------------------- 解析

class ResolvedModel(object):
    """给 case_generator / oc_text 用的轻量对象。

    保持和原来 AIConfig 一样的属性名，调用方按属性取模型信息即可。
    """

    def __init__(self, provider, model, api_key, api_base, temperature,
                 max_tokens, supports_vision, ref):
        self.id = ref
        self.ref = ref
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.supports_vision = supports_vision

    def to_dict(self):
        return {'id': self.id, 'provider': self.provider, 'model': self.model,
                'api_base': self.api_base, 'has_key': bool(self.api_key),
                'temperature': self.temperature, 'max_tokens': self.max_tokens,
                'supports_vision': self.supports_vision}


def resolve(ref=None):
    """把 `provider/model`（或只给 provider）解析成可直接发请求的模型。

    不传 ref 时用配置里的默认模型。找不到返回 None，由调用方给出可读的报错。
    """
    view = merged_view()
    local = load_local(create=False)
    meta = (local.get('autocase') or {}).get('models') or {}
    target = ref or view.get('default') or ''
    provider_id, _, model_id = str(target).partition('/')

    by_id = {p['id']: p for p in view['providers'] if not p.get('disabled')}
    provider = by_id.get(provider_id)
    if provider is None:
        # 只给了 provider 名（旧数据里 config_id 可能就是这么存的）
        provider = next((p for p in by_id.values()
                         if p['id'] == str(target) and p['models']), None)
    if provider is None or not provider['models']:
        return None

    model = next((m for m in provider['models'] if m['id'] == model_id), None)
    if model is None:
        model = provider['models'][0]

    # key / baseURL 要读原始值，不能读掩码后的视图。
    raw = _raw_provider(provider_id)
    extra = meta.get('%s/%s' % (provider_id, model['id'])) or {}
    return ResolvedModel(
        provider=provider_id,
        model=model['id'],
        api_key=(raw.get('options') or {}).get('apiKey'),
        api_base=(raw.get('options') or {}).get('baseURL'),
        temperature=extra.get('temperature'),
        max_tokens=extra.get('maxTokens'),
        supports_vision=model.get('vision', False),
        ref='%s/%s' % (provider_id, model['id']),
    )


def _raw_provider(pid):
    glob = _read_json(global_path())
    local = load_local(create=False)
    return _deep_merge((glob.get('provider') or {}).get(pid) or {},
                       (local.get('provider') or {}).get(pid) or {})


# ---------------------------------------------------------------- 测试连通

def test_connection(provider_id, model_id=None, api_key=None, base_url=None):
    """发一次最小 chat 请求。合并后的 key/baseURL 为准，允许弹窗里临时覆盖。"""
    raw = _raw_provider(provider_id) if provider_id else {}
    options = raw.get('options') or {}
    key = api_key or options.get('apiKey')
    base = (base_url or options.get('baseURL') or '').rstrip('/')
    model = model_id
    if not model:
        models = list((raw.get('models') or {}).keys())
        model = models[0] if models else ''
    if not key:
        return {'success': False, 'error': '还没有填 API Key'}
    if not base:
        return {'success': False, 'error': '还没有填 Base URL'}
    if not model:
        return {'success': False, 'error': '还没有模型'}

    url = base if base.endswith('/chat/completions') else base + '/chat/completions'
    try:
        resp = requests.post(
            url,
            headers={'Authorization': 'Bearer %s' % key,
                     'Content-Type': 'application/json'},
            json={'model': model, 'messages': [{'role': 'user', 'content': 'hi'}],
                  'max_tokens': 8},
            timeout=20,
        )
    except requests.RequestException as exc:
        return {'success': False, 'error': '请求失败：%s' % str(exc)[:200], 'url': url}
    if resp.status_code == 200:
        return {'success': True, 'url': url, 'model': model}
    return {'success': False, 'url': url, 'model': model,
            'error': 'HTTP %s：%s' % (resp.status_code, (resp.text or '')[:200])}


# ---------------------------------------------------------------- 运行时配置

def _sync_modalities(providers, local):
    """把「支持图片」开关翻译成 opencode 认的 `modalities`。

    设置页只存 `autocase.models['provider/model'].vision`，但 opencode 判断能不能
    收图看的是模型条目的 `modalities.input` 里有没有 `image`——没有就直接回
    「当前模型不支持图像输入」，连请求都不发。所以写 runtime 时补这一层，
    不要求用户手改 JSON。

    只对「用户显式设置过 vision」的模型生效：没设置过的保持 opencode 自己的判断，
    免得把人家本来就支持的模型误标成纯文本。
    """
    meta = (local.get('autocase') or {}).get('models') or {}
    for ref, entry in meta.items():
        vision = (entry or {}).get('vision')
        if vision is None:
            continue
        provider_id, _, model_id = str(ref).partition('/')
        model = ((providers.get(provider_id) or {}).get('models') or {}).get(model_id)
        if not isinstance(model, dict):
            continue
        modalities = dict(model.get('modalities') or {})
        inputs = [item for item in (modalities.get('input') or ['text'])]
        if vision:
            if 'image' not in inputs:
                inputs.append('image')
        else:
            inputs = [item for item in inputs if item != 'image'] or ['text']
        modalities['input'] = inputs
        modalities.setdefault('output', ['text'])
        model['modalities'] = modalities


def runtime_config():
    """把 opencode.json 和本地模型配置合成一份，给 OPENCODE_CONFIG 用。

    为什么不直接把 provider 写进 opencode.json：那份文件进仓库，密钥会跟着进去。
    为什么不在 /tmp 生成：配置里的 `{file:.opencode/prompts/...}` 是相对配置文件
    所在目录解析的，挪走会让 agent 提示词全部加载失败。
    """
    with _LOCK:
        base = _read_json(base_path())
        if not base:
            # 没有基础配置时不要把 agent/mcp 弄没：直接退回原文件，provider
            # 由全局配置兜底。宁可模型少一个，也不能让生成链路起不来。
            return base_path()
        local = load_local()
        merged = dict(base)
        merged['provider'] = _deep_merge(base.get('provider') or {},
                                         local.get('provider') or {})
        _sync_modalities(merged['provider'], local)
        if local.get('model'):
            merged['model'] = local['model']
        if local.get('disabled_providers'):
            merged['disabled_providers'] = list(local['disabled_providers'])
        path = runtime_path()
        _write_json(path, merged)
        return path


def model_refs():
    """所有 `provider/model` 形式的可选值，给前端下拉和校验用。"""
    return [m['id'] for m in flat_models()]
