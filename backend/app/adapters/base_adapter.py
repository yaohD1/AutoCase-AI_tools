from abc import ABC, abstractmethod
from typing import List, Dict

class BaseAIAdapter(ABC):
    def __init__(self, api_key: str, api_base: str = None, model: str = None, temperature: float = 0.7, max_tokens: int = 2000):
        self.api_key = api_key
        self.api_base = api_base
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
    
    @abstractmethod
    def generate_cases(self, image_path: str, prompt: str) -> List[Dict]:
        pass
    
    @abstractmethod
    def optimize_image(self, image_path: str) -> str:
        pass