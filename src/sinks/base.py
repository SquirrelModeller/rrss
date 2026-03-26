from abc import ABC, abstractmethod
from models import SendResult, NotificationMessage


class Notifier(ABC):
    @abstractmethod
    async def send(self, message: NotificationMessage) -> SendResult: ...
