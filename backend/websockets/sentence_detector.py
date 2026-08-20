"""
Sentence boundary detection для streaming TTS
"""
import re
from typing import Optional, List

class StreamingSentenceDetector:
    """Production-ready детектор границ предложений для realtime стриминга"""
    
    def __init__(self, language: str = 'ru', min_chunk_length: int = 40):
        self.buffer = ""
        self.language = language
        self.min_chunk_length = min_chunk_length
        
        # Паттерны по языкам
        self.patterns = {
            'en': {
                'strong': r'[.!?]+\s+(?=[A-Z])',
                'medium': r'[,;:]\s+',
                'false_pos': r'\b(Mr|Mrs|Dr|Prof|Inc|Ltd|Co|vs|etc|e\.g|i\.e|Sr|Jr)\.$'
            },
            'ru': {
                'strong': r'[.!?]+\s+(?=[А-ЯЁ])',
                'medium': r'[,;:]\s+',
                'false_pos': r'\b(г|ул|д|кв|р|руб|т\.д|т\.п|и\.т\.д|и\.т\.п)\.$'
            }
        }
    
    def add_chunk(self, text_chunk: str) -> List[str]:
        """
        Добавляет чанк текста и возвращает готовые предложения
        
        Returns:
            List[str]: Список готовых предложений для отправки в TTS
        """
        self.buffer += text_chunk
        sentences = []
        
        # Минимальная длина перед проверкой
        if len(self.buffer) < self.min_chunk_length:
            return []
        
        # 1. Ищем сильные границы (точка + заглавная)
        while True:
            sentence = self._extract_sentence('strong')
            if sentence:
                sentences.append(sentence)
            else:
                break
        
        # 2. Если буфер большой, но нет сильных границ - берем по запятой
        if len(self.buffer) > 120 and not sentences:
            sentence = self._extract_sentence('medium')
            if sentence:
                sentences.append(sentence)
        
        # 3. Если буфер очень большой - форсируем отправку
        if len(self.buffer) > 200:
            sentence = self.buffer.strip()
            self.buffer = ""
            sentences.append(sentence)
        
        return sentences
    
    def _extract_sentence(self, boundary_type: str) -> Optional[str]:
        """Извлекает первое предложение по паттерну"""
        pattern = self.patterns[self.language][boundary_type]
        false_pos = self.patterns[self.language]['false_pos']
        
        matches = list(re.finditer(pattern, self.buffer))
        if not matches:
            return None
        
        match = matches[0]
        
        # Проверяем на false positive
        context = self.buffer[:match.end()]
        if re.search(false_pos, context):
            return None
        
        sentence = self.buffer[:match.end()].strip()
        self.buffer = self.buffer[match.end():].lstrip()
        
        return sentence if len(sentence) > 10 else None
    
    def flush(self) -> Optional[str]:
        """Возвращает остаток буфера в конце стрима"""
        if self.buffer.strip():
            sentence = self.buffer.strip()
            self.buffer = ""
            return sentence
        return None
