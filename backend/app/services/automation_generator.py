import json
import os
import re
import shutil
import zipfile
from pathlib import Path

from app.adapters import GeneralAdapter


ACTION_SYSTEM_PROMPT = '''你是资深 Playwright 自动化工程师。
把输入的测试用例转换为严格 JSON 数组，不要输出 Markdown 或解释。
每个动作只能使用以下 type：goto、click、fill、select、check、uncheck、assert_text、assert_visible、wait、todo。
字段约束：
- goto: url
- click/check/uncheck/assert_visible: target、role（可选）
- fill: target、value、role（可选）
- select: target、value
- assert_text: target、expected
- wait: milliseconds
- todo: note
定位优先使用语义角色和可访问名称；无法从用例确定定位信息时必须输出 todo，不要猜 CSS/XPath。
'''


def _safe_name(value, fallback='case'):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', '-', str(value or ''))
    value = re.sub(r'\s+', '-', value).strip('.-')
    return value[:100] or fallback


def _parse_steps(value):
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    except (TypeError, ValueError):
        pass
    return [line.strip() for line in str(value).splitlines() if line.strip()]


def _strip_prefix(value):
    return re.sub(r'^(步骤|操作|预期|前置条件)\s*\d*\s*[：:]\s*', '', value).strip()


def _ts_string(value):
    return json.dumps(str(value or ''), ensure_ascii=False)


def _comment(value):
    return re.sub(r'[\r\n]+', ' ', str(value or '')).replace('*/', '* /')


def _fallback_actions(testcase, base_url):
    actions = []
    warnings = []
    for step in _parse_steps(testcase.steps):
        text = _strip_prefix(step)
        if re.search(r'打开|访问|进入|跳转|navigate|open', text, re.I):
            actions.append({'type': 'goto', 'url': base_url or '/'})
        else:
            actions.append({'type': 'todo', 'note': text})
            warnings.append(f"{testcase.title}: 无法从步骤中确定元素定位，已生成 TODO：{text}")
    if testcase.expected:
        actions.append({'type': 'todo', 'note': f'请补充预期结果断言：{testcase.expected}'})
        warnings.append(f"{testcase.title}: 预期结果需要人工确认断言定位")
    return actions or [{'type': 'todo', 'note': '请补充自动化步骤'}], warnings


def _locator(action):
    target = str(action.get('target') or '')
    role = str(action.get('role') or '').strip()
    if role and target:
        return f"page.getByRole({_ts_string(role)}, {{ name: {_ts_string(target)} }})"
    if target:
        return f"page.getByText({_ts_string(target)})"
    return "page.locator('[data-testid=\"TODO\"]')"


def _render_action(action, indent='    '):
    kind = action.get('type')
    if kind == 'goto':
        return f"{indent}await page.goto({_ts_string(action.get('url') or '/')});"
    if kind == 'click':
        return f"{indent}await {_locator(action)}.click();"
    if kind == 'fill':
        return f"{indent}await {_locator(action)}.fill({_ts_string(action.get('value'))});"
    if kind == 'select':
        return f"{indent}await {_locator(action)}.selectOption({_ts_string(action.get('value'))});"
    if kind == 'check':
        return f"{indent}await {_locator(action)}.check();"
    if kind == 'uncheck':
        return f"{indent}await {_locator(action)}.uncheck();"
    if kind == 'assert_text':
        return f"{indent}await expect({_locator(action)}).toContainText({_ts_string(action.get('expected'))});"
    if kind == 'assert_visible':
        return f"{indent}await expect({_locator(action)}).toBeVisible();"
    if kind == 'wait':
        milliseconds = int(action.get('milliseconds') or 500)
        return f"{indent}await page.waitForTimeout({max(0, min(milliseconds, 30000))});"
    note = _comment(action.get('note') or '请补充自动化步骤')
    return f"{indent}// TODO: {note}"


class AutomationScriptGenerator:
    def __init__(self, app_config):
        self.app_config = app_config

    def _resolve_workspace(self, workspace_path):
        root = Path(self.app_config['AUTOMATION_WORKSPACE_ROOT']).resolve()
        provided = Path(workspace_path or 'default').expanduser()
        workspace = (provided if provided.is_absolute() else root / provided).resolve()
        if workspace != root and root not in workspace.parents:
            raise ValueError('workspace_path must stay inside AUTOMATION_WORKSPACE_ROOT')
        return root, workspace

    def _get_actions(self, testcase, config, ai_config):
        if not ai_config or not ai_config.api_key:
            return _fallback_actions(testcase, config.get('base_url'))
        prompt = json.dumps({
            'title': testcase.title,
            'preconditions': testcase.preconditions or '',
            'steps': _parse_steps(testcase.steps),
            'expected': testcase.expected or ''
        }, ensure_ascii=False, indent=2)
        try:
            actions = GeneralAdapter(ai_config).generate_structured_json(
                prompt, ACTION_SYSTEM_PROMPT, max_tokens=max(ai_config.max_tokens or 4096, 4096)
            )
            if isinstance(actions, dict):
                actions = actions.get('actions', [])
            if not isinstance(actions, list):
                raise ValueError('AI actions must be an array')
            valid_types = {'goto', 'click', 'fill', 'select', 'check', 'uncheck', 'assert_text', 'assert_visible', 'wait', 'todo'}
            normalised = []
            for action in actions:
                if not isinstance(action, dict) or action.get('type') not in valid_types:
                    continue
                kind = action['type']
                if kind == 'wait':
                    try:
                        milliseconds = int(action.get('milliseconds', 500))
                    except (TypeError, ValueError):
                        normalised.append({'type': 'todo', 'note': 'AI 返回了无效的等待时间'})
                        continue
                    normalised.append({'type': 'wait', 'milliseconds': max(0, min(milliseconds, 30000))})
                    continue
                if kind == 'goto':
                    normalised.append({'type': 'goto', 'url': str(action.get('url') or '/')})
                    continue
                if kind == 'todo':
                    normalised.append({'type': 'todo', 'note': str(action.get('note') or '请补充自动化步骤')})
                    continue
                target = str(action.get('target') or '').strip()
                if not target:
                    normalised.append({'type': 'todo', 'note': f'AI 未提供 {kind} 动作的定位信息'})
                    continue
                normalised.append({
                    'type': kind,
                    'target': target,
                    'role': str(action.get('role') or '').strip(),
                    'value': str(action.get('value') or ''),
                    'expected': str(action.get('expected') or '')
                })
            actions = normalised
            if not actions:
                raise ValueError('AI returned no valid actions')
            warnings = [f"{testcase.title}: AI 生成结果仍需人工检查定位器"]
            return actions, warnings
        except Exception as exc:
            actions, warnings = _fallback_actions(testcase, config.get('base_url'))
            warnings.insert(0, f"{testcase.title}: AI 转换失败，已回退为 TODO：{exc}")
            return actions, warnings

    def _render_spec(self, testcase, config, ai_config):
        actions, warnings = self._get_actions(testcase, config, ai_config)
        title = _ts_string(testcase.title or '自动化用例')
        source = f'''import {{ test, expect }} from '@playwright/test';

// Source testcase: {testcase.id}
// Generated by AutoCase at generation time.
test({title}, async ({{ page }}) => {{
'''
        if testcase.preconditions:
            source += f"    // 前置条件: {_comment(testcase.preconditions)}\n"
        source += '\n'.join(_render_action(action) for action in actions)
        source += '\n});\n'
        return source, warnings

    def generate(self, project, testcases, config, ai_config, generation_id):
        root, workspace = self._resolve_workspace(config.get('workspace_path'))
        specs_path = Path(config.get('specs_path') or 'tests')
        if specs_path.is_absolute() or '..' in specs_path.parts:
            raise ValueError('specs_path must be a relative directory without ..')
        output_dir = (workspace / specs_path).resolve()
        if output_dir != workspace and workspace not in output_dir.parents:
            raise ValueError('specs_path must stay inside workspace_path')

        files = []
        warnings = []
        rendered = {}
        for testcase in testcases:
            module = _safe_name(testcase.module, 'uncategorized')
            filename = _safe_name(testcase.title, testcase.id[:8]) + f'-{testcase.id[:8]}.spec.ts'
            relative = (specs_path / module / filename).as_posix()
            if relative in rendered:
                raise ValueError(f'Duplicate generated file path: {relative}')
            content, case_warnings = self._render_spec(testcase, config, ai_config)
            rendered[relative] = content
            files.append({'path': relative, 'testcase_id': testcase.id, 'title': testcase.title})
            warnings.extend(case_warnings)

        base_url = _ts_string(config.get('base_url'))
        auth_line = ''
        if config.get('auth_state_path'):
            auth = str(config['auth_state_path']).replace('\\', '/')
            auth_line = f"    storageState: {_ts_string(auth)},\n"
        browser = config.get('browser') or 'chromium'
        rendered['playwright.config.ts'] = f'''import {{ defineConfig }} from '@playwright/test';

export default defineConfig({{
  testDir: {_ts_string('./' + specs_path.as_posix())},
  use: {{
    baseURL: process.env.BASE_URL || {base_url},
    browserName: {_ts_string(browser)},
{auth_line}    actionTimeout: {int(config.get('action_timeout') or 10000)},
    navigationTimeout: {int(config.get('navigation_timeout') or 30000)},
    trace: 'on-first-retry',
    screenshot: 'only-on-failure'
  }},
  expect: {{ timeout: {int(config.get('expect_timeout') or 5000)} }}
}});
'''
        rendered['.env.example'] = f"BASE_URL={_comment(config.get('base_url') or 'https://test.example.com')}\n"
        rendered['AUTOMATION_README.md'] = f'''# {project.name} automation scripts

Generated by AutoCase from approved test cases.

- Environment: {config.get('environment_name') or 'test'}
- Base URL: configured by `BASE_URL` or `playwright.config.ts`
- Generated files: {len(files)}

Review every `TODO` before committing the scripts.
'''

        workspace.mkdir(parents=True, exist_ok=True)
        staging = workspace / f'.autocase-staging-{generation_id}'
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True, exist_ok=True)
        zip_path = None
        try:
            targets = [(workspace / path).resolve() for path in rendered]
            for target in targets:
                if target != workspace and workspace not in target.parents:
                    raise ValueError('Generated file escaped workspace_path')
                if target.exists() and config.get('overwrite_policy', 'reject') == 'reject':
                    relative_target = target.relative_to(workspace).as_posix()
                    raise FileExistsError(f'File already exists: {relative_target}')

            for relative, content in rendered.items():
                staged_target = (staging / relative).resolve()
                staged_target.parent.mkdir(parents=True, exist_ok=True)
                staged_target.write_text(content, encoding='utf-8')

            export_folder = Path(self.app_config['EXPORT_FOLDER'])
            export_folder.mkdir(parents=True, exist_ok=True)
            zip_path = export_folder / f'automation_{generation_id}.zip'
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as archive:
                for relative in rendered:
                    archive.write(staging / relative, relative)
            for relative in rendered:
                source = staging / relative
                target = (workspace / relative).resolve()
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(f'.{target.name}.{generation_id}.tmp')
                shutil.copyfile(source, temporary)
                os.replace(temporary, target)
            return files, warnings, str(zip_path)
        except Exception:
            if zip_path and zip_path.exists():
                zip_path.unlink()
            raise
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
