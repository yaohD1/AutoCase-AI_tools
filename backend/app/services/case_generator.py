"""从设计图 / 文档 / 模块描述生成测试用例。

模型调用统一走 `oc_text.TextWorker`——和录制写脚本、断言解析共用同一份
`opencode.models.json`，不再自己拼 HTTP 请求。原来那套 GeneralAdapter / Kimi /
DeepSeek 适配器已经删掉：它们的 `api_base` 语义是「完整端点」，而模型配置里存的是
base URL，两套并存时只要没人补 `/chat/completions` 就整个 404。
"""
import json
from typing import Dict, List

from app.utils import PromptTemplates
from app.utils.file_utils import is_document, read_document_content
from app.utils.retriever import ReferenceRetriever

from app.services.oc_text import ASK_TIMEOUT, TextError, TextWorker, parse_json
from app.services.session_pool import TTL as SESSION_TTL
from app.services.session_pool import pool as session_pool


SYSTEM_CASES = """你是一位专业的测试工程师，擅长从UI设计图、文档或模块描述中识别测试点并生成详细的测试用例。
请严格按照以下格式输出（JSON 数组，不要 Markdown 围栏，不要解释）：

[
  {
    "module": "模块名",
    "test_point": "测试点",
    "title": "用例：用例名 #优先级",
    "priority": "P0/P1/P2",
    "preconditions": "前置条件：...",
    "steps": ["步骤：步骤1", "步骤：步骤2"],
    "expected": "预期：预期结果",
    "case_type": "functional/ui/boundary/exception"
  }
]

注意：
1. 每个测试点下可生成多个测试用例
2. 模块、测试点默认以「模块：」「测试点：」开头
3. 用例格式为「用例：用例名 #优先级」，优先级只能是 P0/P1/P2
4. 前置条件、步骤、预期都要带前缀
5. 只输出 JSON 数组本身"""

SYSTEM_ANALYZE = ("你是一位资深的UI测试分析师，擅长分析UI设计图并输出结构化模块描述。"
                  "只输出 JSON 数组，不要加任何解释。")

# 一次请求最多出多少条。实测 88 条会顶到 300s 超时，40 条以内稳。
# 超过就按模块切批，仍在同一个会话里连发——用户看到的还是「一次生成」。
MAX_ONESHOT_CASES = 40

MODULES_TEMPLATE = """## 用户已确认的模块清单
这份清单是用户在原型分析结果上增删改之后确认的，请严格按它生成：
{listing}

硬性要求：
- 只为清单里的模块生成用例，清单外的模块一条都不要
- module 字段必须用清单里的名字，一个字都不要改
- 每个模块的条数以清单为准，类型只能取 functional / ui / boundary / exception
- 输出 JSON 数组，不要 Markdown 围栏，不要解释"""

# 图片相关的补充说明。文本模式下这几条不适用（没有图可看），单独拼。
IMAGE_RULES = """
补充要求：
- 结合 UI 设计图的视觉信息（布局、颜色、间距、控件状态）和文字描述一起分析
- 图片提供视觉上下文，模块描述提供功能语义
"""


def _normalise(cases):
    """补齐 priority 前缀与 case_type 默认值。模型偶尔会写「0」而不是「P0」。"""
    out = []
    for case in cases or []:
        if not isinstance(case, dict):
            continue
        priority = str(case.get('priority') or 'P2').strip()
        case['priority'] = priority if priority.startswith('P') else 'P' + priority
        if not case.get('case_type'):
            case['case_type'] = 'functional'
        out.append(case)
    return out


def _case_count(module):
    try:
        return max(1, int(module.get('case_count') or 10))
    except (TypeError, ValueError):
        return 10


def _module_line(index, module):
    """一个模块压成一段文字：名字、条数、类型、以及用户在弹窗里填的那几栏。"""
    types = module.get('case_types') or []
    if module.get('smart_case_type'):
        type_text = '按覆盖面自由发挥'
    else:
        type_text = '、'.join(str(t) for t in types) or 'functional'
    parts = ['%d. 模块「%s」（%d 条，类型：%s）'
             % (index, str(module.get('module') or '').strip(), _case_count(module), type_text)]
    for key, label in (('ui_elements', 'UI 元素'), ('function_description', '功能说明'),
                       ('interaction_flow', '操作流程'), ('test_focus', '测试关注点')):
        value = module.get(key)
        if isinstance(value, (list, tuple)):
            value = '、'.join(str(v) for v in value if str(v).strip())
        value = str(value or '').strip()
        if value:
            parts.append('   - %s：%s' % (label, value))
    return '\n'.join(parts)


def modules_prompt(modules):
    listing = '\n'.join(_module_line(i + 1, m) for i, m in enumerate(modules or []))
    return MODULES_TEMPLATE.format(listing=listing)


def split_batches(modules, limit=MAX_ONESHOT_CASES):
    """按条数切批。单个模块超限也照发，否则它会永远进不了任何一批。"""
    batch, total = [], 0
    for module in modules or []:
        count = _case_count(module)
        if batch and total + count > limit:
            yield batch
            batch, total = [], 0
        batch.append(module)
        total += count
    if batch:
        yield batch


class CaseGenerator(object):
    def __init__(self, ai_config, project_id: str = None, worker=None):
        # ai_config 是 models_config.ResolvedModel：带 ref(s) / provider / model /
        # supports_vision。这里不再关心 key 和 baseURL——openCode 自己管。
        self.ai_config = ai_config
        self.model = getattr(ai_config, 'ref', None) or '%s/%s' % (
            getattr(ai_config, 'provider', ''), getattr(ai_config, 'model', ''))
        self.supports_vision = getattr(ai_config, 'supports_vision', True) is not False
        self.project_id = project_id
        self.retriever = ReferenceRetriever(project_id) if project_id else None
        self._worker = worker

    # ---- 模型调用 ----

    @property
    def worker(self):
        if self._worker is None:
            self._worker = TextWorker.shared(_app_config())
        return self._worker

    def _ask(self, system, prompt, images=None, timeout=None):
        kwargs = {'images': images}
        if timeout:
            kwargs['timeout'] = timeout
        return self.worker.ask(self.model, system, prompt, **kwargs)

    def _ask_json(self, system, prompt, images=None, timeout=None):
        kwargs = {'images': images}
        if timeout:
            kwargs['timeout'] = timeout
        return self.worker.ask_json(self.model, system, prompt, **kwargs)

    def _inject_reference_cases(self, prompt: str, modules: List[Dict] = None) -> str:
        if not self.retriever or not modules:
            return prompt
        try:
            references = self.retriever.retrieve_for_modules(modules)
            ref_text = self.retriever.format_for_prompt(references)
            if ref_text:
                return f"{prompt}\n\n## 参考用例（同项目已审批通过的历史用例）\n{ref_text}"
        except Exception:
            pass
        return prompt

    # ---- 对外 ----

    def generate(self, image_path: str, case_types: List[str], case_count: int = 10,
                 description: str = '') -> List[Dict]:
        prompt = PromptTemplates.get_combined_prompt(case_types, case_count)
        if description:
            prompt = f"{prompt}\n\n## 额外功能介绍/业务说明\n{description}"

        try:
            if is_document(image_path):
                document = read_document_content(image_path)
                prompt = f"{prompt}\n\n## 参考文档内容\n```\n{document}\n```"
                return _normalise(self._ask_json(SYSTEM_CASES, prompt))
            if not self.supports_vision:
                raise TextError('当前模型没有开启图片输入，无法从设计图生成用例。'
                                '到「设置 → 模型服务」里给它勾上「支持图片」，或改用文档/模块方式生成')
            return _normalise(self._ask_json(SYSTEM_CASES, prompt + IMAGE_RULES,
                                             images=[image_path]))
        except TextError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise Exception('Failed to generate test cases: %s' % str(exc))

    def analyze_image(self, image_path: str, description: str = '') -> List[Dict]:
        return self.analyze_images([image_path], description)

    def analyze_images(self, image_paths: List[str], description: str = '') -> List[Dict]:
        if not self.supports_vision:
            raise TextError('当前模型没有开启图片输入，无法分析设计图。'
                            '到「设置 → 模型服务」里给它勾上「支持图片」，保存后再试')
        # 单图和多图是两套提示词：单图那套说的是「这张UI设计图」，多图要讲页面之间的跳转。
        # 注意不能对多图模板用 .format()——正文里带 JSON 示例的 `{`，会抛 KeyError。
        if len(image_paths) <= 1:
            prompt = PromptTemplates.IMAGE_ANALYSIS_PROMPT_SINGLE
        else:
            prompt = PromptTemplates.IMAGE_ANALYSIS_PROMPT.replace(
                '{count}', str(len(image_paths)))
        if description:
            prompt = f"{description}\n\n{prompt}"
        return _normalise(self._ask_json(SYSTEM_ANALYZE, prompt, images=image_paths))

    def generate_from_modules(self, modules: List[Dict], case_types: List[str], case_count: int,
                              description: str = '', smart_mode: bool = False,
                              image_paths: list = None) -> List[Dict]:
        prompt = PromptTemplates.SMART_TEST_TEMPLATE if smart_mode \
            else PromptTemplates.get_combined_prompt(case_types, case_count)
        if description:
            prompt = f"{prompt}\n\n## 额外功能介绍/业务说明\n{description}"

        modules_text = json.dumps(modules, ensure_ascii=False, indent=2)
        prompt = f"{prompt}\n\n## 模块描述（基于设计图分析）\n```json\n{modules_text}\n```"
        prompt = self._inject_reference_cases(prompt, modules)

        images = [path for path in (image_paths or []) if path] if self.supports_vision else []
        if images:
            prompt += IMAGE_RULES
        return _normalise(self._ask_json(SYSTEM_CASES, prompt, images=images))

    # ---- 挂起会话：拆模块和生成落在同一个会话里 ----
    #
    # 首页这条链路是「AI 拆模块 → 用户在弹窗里改 → AI 接着生成」。分析那一轮开的
    # 会话要留着，用户点生成时在同一个会话里发消息：图还在上下文里，不用重传，
    # 模型也记得自己刚拆过什么。实测同一页面：同会话 184s / 30 条，
    # 新开会话重发图 249s / 25 条，两者都严格遵守用户的增删改。

    def _analysis_prompt(self, description, count):
        if count <= 1:
            prompt = PromptTemplates.IMAGE_ANALYSIS_PROMPT_SINGLE
        else:
            prompt = PromptTemplates.IMAGE_ANALYSIS_PROMPT.replace('{count}', str(count))
        if description:
            prompt = f"{description}\n\n{prompt}"
        return prompt

    def analyze_in_session(self, image_paths, description: str = ''):
        """拆模块，并把这轮会话挂起。返回 (modules, token)。

        token 交给前端保管，用户在模块审核弹窗里改完点生成时带回来。
        """
        paths = [p for p in (image_paths if isinstance(image_paths, list) else [image_paths])
                 if p]
        if not paths:
            raise TextError('没有可用的图片')
        if not self.supports_vision:
            raise TextError('当前模型没有开启图片输入，无法分析设计图。'
                            '到「设置 → 模型服务」里给它勾上「支持多模态」')

        worker = self.worker
        base_url, session_id = worker.open_session(title='AutoCase 原型分析')
        try:
            text = worker.send(base_url, session_id, self.model, SYSTEM_ANALYZE,
                               self._analysis_prompt(description, len(paths)),
                               images=paths, timeout=ASK_TIMEOUT)
            modules = _normalise(parse_json(text))
        except Exception:  # noqa: BLE001
            worker.close_session(base_url, session_id)
            raise

        token = session_pool.put(session_id, base_url, self.model, paths, self.project_id)
        # 挂起期间别让 serve 按空闲回收——回收之后会话就发不出消息了。
        worker.hold(SESSION_TTL)
        return modules, token

    def generate_in_session(self, token, modules: List[Dict], description: str = '',
                            image_paths: list = None):
        """接续分析那一轮，按用户确认的模块清单生成。返回 (cases, meta)。"""
        modules = [m for m in (modules or []) if str(m.get('module') or '').strip()]
        if not modules:
            raise TextError('没有可生成的模块')

        item = session_pool.take(token)
        if not item:
            return self._generate_fresh(modules, description, image_paths,
                                        '会话已过期，已重新开会话生成')

        cases, reason = [], ''
        try:
            for batch in split_batches(modules):
                text = self.worker.send(item['base_url'], item['session_id'], self.model,
                                        SYSTEM_CASES, modules_prompt(batch),
                                        timeout=ASK_TIMEOUT)
                cases.extend(_normalise(parse_json(text)))
        except TextError as exc:
            reason = '会话中断：%s' % str(exc)[:120]
        finally:
            session_pool.drop(token)
            self.worker.close_session(item['base_url'], item['session_id'])
            if session_pool.active() == 0:
                self.worker.release()

        if not cases:
            return self._generate_fresh(modules, description, image_paths,
                                        reason or '会话没有产出用例')
        if reason:
            # 已经拿到一部分就先交付，整份重来会把成功那批再生成一遍
            return cases, {'degraded': True, 'reason': reason, 'partial': True}
        return cases, {'degraded': False, 'reason': '', 'partial': False}

    def _generate_fresh(self, modules, description, image_paths, reason):
        """降级路径：新开会话，重新带上原图和用户清单。"""
        prompt = modules_prompt(modules)
        if description:
            prompt = f"{prompt}\n\n## 额外功能介绍/业务说明\n{description}"
        paths = [p for p in (image_paths or []) if p]
        images = paths if self.supports_vision else []
        cases = _normalise(self._ask_json(SYSTEM_CASES, prompt, images=images))
        return cases, {'degraded': True, 'reason': reason, 'partial': False}

def _app_config():
    """拿当前 Flask 配置；脱离应用上下文（单测、脚本）时退回空字典。

    TextWorker 只从中读 OPENCODE_BIN / OPENCODE_CONFIG / 工作区根目录，
    缺省时由 OpenCodeRunner 用默认值兜底。
    """
    try:
        from flask import current_app
        return dict(current_app.config)
    except Exception:  # noqa: BLE001
        return {}
