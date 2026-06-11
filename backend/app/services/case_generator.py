from typing import List, Dict
from app.models import AIConfig
from app.adapters import VolcengineAdapter, KimiAdapter
from app.utils import PromptTemplates
from app.utils.file_utils import is_document, read_document_content

class CaseGenerator:
    def __init__(self, ai_config: AIConfig):
        self.ai_config = ai_config
        self.adapter = self._get_adapter()

    def _get_adapter(self):
        if self.ai_config.provider == 'volcengine':
            return VolcengineAdapter(
                api_key=self.ai_config.api_key,
                api_base=self.ai_config.api_base or "https://api.yygu.cn/v3/llm.chat/chat/completions",
                model=self.ai_config.model or "doubao-seed-2.0-lite",
                temperature=self.ai_config.temperature,
                max_tokens=self.ai_config.max_tokens
            )
        elif self.ai_config.provider == 'kimi':
            return KimiAdapter(
                api_key=self.ai_config.api_key,
                api_base=self.ai_config.api_base or "https://api.yygu.cn/v3/llm.chat/chat/completions",
                model=self.ai_config.model or "kimi-k2.6",
                temperature=self.ai_config.temperature,
                max_tokens=self.ai_config.max_tokens
            )
        else:
            raise ValueError(f"Unsupported AI provider: {self.ai_config.provider}")

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