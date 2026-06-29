from typing import List, Dict
import json
from app.models import AIConfig
from app.adapters import GeneralAdapter
from app.utils import PromptTemplates
from app.utils.file_utils import is_document, read_document_content
from app.utils.retriever import ReferenceRetriever

class CaseGenerator:
    def __init__(self, ai_config: AIConfig, project_id: str = None):
        self.ai_config = ai_config
        self.adapter = GeneralAdapter(ai_config)
        self.project_id = project_id
        self.retriever = ReferenceRetriever(project_id) if project_id else None

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

    def generate(self, image_path: str, case_types: List[str], case_count: int = 10, description: str = '') -> List[Dict]:
        all_testcases = []

        prompt = PromptTemplates.get_combined_prompt(case_types, case_count)
        if description:
            prompt = f"{prompt}\n\n## 额外功能介绍/业务说明\n{description}"

        try:
            if is_document(image_path):
                doc_content = read_document_content(image_path)
                prompt = f"{prompt}\n\n## 参考文档内容\n```\n{doc_content}\n```"
                testcases = self.adapter.generate_from_text(prompt)
            else:
                testcases = self.adapter.generate_cases(image_path, prompt)

            for case in testcases:
                priority = case.get('priority', 'P2')
                if not priority.startswith('P'):
                    priority = 'P' + priority

                case['priority'] = priority

                if 'case_type' not in case:
                    case['case_type'] = 'functional'

            all_testcases.extend(testcases)

        except Exception as e:
            raise Exception(f"Failed to generate test cases: {str(e)}")

        return all_testcases

    def analyze_image(self, image_path: str, description: str = '') -> List[Dict]:
        prompt = PromptTemplates.IMAGE_ANALYSIS_PROMPT_SINGLE
        if description:
            prompt = f"{description}\n\n{prompt}"
        return self.adapter.analyze_image(image_path, prompt)
    
    def analyze_images(self, image_paths: List[str], description: str = '') -> List[Dict]:
        prompt = PromptTemplates.IMAGE_ANALYSIS_PROMPT.format(count=len(image_paths))
        if description:
            prompt = f"{description}\n\n{prompt}"
        return self.adapter.analyze_images(image_paths, prompt)
    
    def generate_from_modules(self, modules: List[Dict], case_types: List[str], case_count: int, description: str = '', smart_mode: bool = False, image_paths: list = None) -> List[Dict]:
        all_testcases = []
        
        if smart_mode:
            prompt = PromptTemplates.SMART_TEST_TEMPLATE
        else:
            prompt = PromptTemplates.get_combined_prompt(case_types, case_count)
        if description:
            prompt = f"{prompt}\n\n## 额外功能介绍/业务说明\n{description}"
        
        modules_text = json.dumps(modules, ensure_ascii=False, indent=2)
        full_prompt = f"{prompt}\n\n## 模块描述（基于设计图分析）\n```json\n{modules_text}\n```"
        full_prompt = self._inject_reference_cases(full_prompt, modules)
        
        try:
            if image_paths and len(image_paths) > 0 and self.adapter.supports_vision:
                testcases = self.adapter.generate_with_image(image_paths, full_prompt)
            else:
                testcases = self.adapter.generate_from_text(full_prompt)
            
            for case in testcases:
                priority = case.get('priority', 'P2')
                if not priority.startswith('P'):
                    priority = 'P' + priority
                case['priority'] = priority
                if 'case_type' not in case:
                    case['case_type'] = 'functional'
            
            all_testcases.extend(testcases)
        
        except Exception as e:
            raise Exception(f"Failed to generate test cases: {str(e)}")
        
        return all_testcases