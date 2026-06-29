from app.models import TestCase

class ReferenceRetriever:
    def __init__(self, project_id):
        self.project_id = project_id

    def retrieve_for_modules(self, modules, limit_per_module=3):
        references = {}
        for mod in modules:
            module_name = mod.get('module', '')
            if not module_name:
                continue
            refs = self._retrieve_by_module(module_name, limit_per_module)
            if refs:
                references[module_name] = refs
        return references

    def _retrieve_by_module(self, module_name, limit=3):
        q = TestCase.query.filter(
            TestCase.project_id == self.project_id,
            TestCase.status == 'approved',
            TestCase.module == module_name
        ).limit(limit).all()
        if not q:
            q = TestCase.query.filter(
                TestCase.project_id == self.project_id,
                TestCase.status == 'approved',
                TestCase.module.like(f'%{module_name}%')
            ).limit(limit).all()
        if not q:
            q = TestCase.query.filter(
                TestCase.project_id == self.project_id,
                TestCase.status == 'approved'
            ).order_by(TestCase.updated_at.desc()).limit(limit).all()
        return [tc.to_dict() for tc in q]

    @staticmethod
    def format_for_prompt(references):
        if not references:
            return ''

        lines = ['以下用例已存在，不要生成相同的用例，请重点补充未覆盖的测试场景：']
        for module_name, cases in references.items():
            lines.append(f'\n### 模块：{module_name}')
            for i, case in enumerate(cases, 1):
                title = case.get('title', '').replace('用例：', '').strip()
                lines.append(f'\n{i}. {title} #{case.get("priority", "")}')
                if case.get('steps'):
                    steps = case['steps']
                    if isinstance(steps, str):
                        try:
                            steps = json.loads(steps)
                        except Exception:
                            pass
                    if isinstance(steps, list):
                        lines.append(f'   步骤数：{len(steps)}个')
                    else:
                        lines.append(f'   步骤：{str(steps)[:60]}')
        return '\n'.join(lines)
