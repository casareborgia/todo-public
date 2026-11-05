"""수집기 공통 어댑터 인터페이스 (강조사항 4-4, 4-5 반영)."""

from abc import ABC, abstractmethod
from typing import List

from core.models import TaskItem


class BaseCollector(ABC):
    """사내 환경의 다양한 데이터 소스로부터 할 일을 수집하는 어댑터 인터페이스."""

    @abstractmethod
    def collect_and_register(self) -> List[TaskItem]:
        """새로운 업무 항목을 스캔하여 DB에 등록하고, 새로 추가된 TaskItem 리스트를 반환."""
        raise NotImplementedError
