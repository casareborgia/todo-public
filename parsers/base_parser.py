"""파서 공통 인터페이스 (강조사항 4-3, 4-4 반영).

기본 동작은 100% 규칙 기반 코드 파서(RuleBasedExtractor)로 수행하며,
향후 로컬 오프라인 AI/NLP 모델을 연결할 때 동일 인터페이스로 교체·보완할 수 있도록 설계합니다.
"""

from abc import ABC, abstractmethod
from datetime import date
from typing import List, Optional

from core.models import TaskItem


class BaseTaskExtractor(ABC):
    """문서 또는 메시지 텍스트에서 할 일(TaskItem) 목록을 추출하는 추상 인터페이스."""

    @abstractmethod
    def extract_tasks(
        self,
        raw_text: str,
        source_hint: str = "AUTO",
        source_ref: str = "",
        ref_date: Optional[date] = None,
    ) -> List[TaskItem]:
        """원본 텍스트에서 1개 이상의 TaskItem을 추출하여 반환."""
        raise NotImplementedError
