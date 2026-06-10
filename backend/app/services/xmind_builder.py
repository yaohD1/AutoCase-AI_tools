import subprocess
import json
import os
from typing import List
from app.models import TestCase

class XMindBuilder:
    def __init__(self, project_name: str):
        self.project_name = project_name
        self.testcases_data = []
    
    def build(self, testcases: List[TestCase]):
        grouped = self._group_by_module(testcases)
        self.testcases_data = grouped
    
    def _group_by_module(self, testcases: List[TestCase]):
        grouped = {}
        
        for case in testcases:
            module = case.module or "未分类模块"
            test_point = case.test_point or "未分类测试点"
            
            if module not in grouped:
                grouped[module] = {}
            
            if test_point not in grouped[module]:
                grouped[module][test_point] = []
            
            steps_list = []
            if case.steps:
                try:
                    steps = case.steps if isinstance(case.steps, list) else eval(case.steps)
                    steps_list = [str(step).replace('步骤：', '').strip() for step in steps]
                except:
                    pass
            
            case_data = {
                'title': case.title.replace('用例：', '').replace('#' + case.priority, '').strip(),
                'priority': case.priority,
                'preconditions': case.preconditions.replace('前置条件：', '').strip() if case.preconditions else '',
                'steps': steps_list,
                'expected': case.expected.replace('预期：', '').strip() if case.expected else ''
            }
            grouped[module][test_point].append(case_data)
        
        modules_list = []
        for module_name, test_points in grouped.items():
            module_data = {
                'name': module_name,
                'testpoints': []
            }
            for test_point_name, cases in test_points.items():
                module_data['testpoints'].append({
                    'name': test_point_name,
                    'cases': cases
                })
            modules_list.append(module_data)
        
        return modules_list
    
    def save(self, filepath: str):
        data = {
            'project_name': self.project_name,
            'testcases': self.testcases_data
        }
        json_data = json.dumps(data, ensure_ascii=False)
        
        script_path = os.path.join(os.path.dirname(__file__), '..', '..', 'generate_xmind.js')
        script_abs_path = os.path.abspath(script_path)
        filepath_abs = os.path.abspath(filepath)
        
        result = subprocess.run(
            ['node', script_abs_path, json_data, filepath_abs],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode != 0:
            raise Exception(f"XMind generation failed: {result.stderr}")
        
        if 'Failed' in result.stdout or 'Error' in result.stdout:
            raise Exception(f"XMind generation failed: {result.stdout}")