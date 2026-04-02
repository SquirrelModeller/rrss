from abc import ABC, abstractmethod
from models import SendResult, NotificationMessage


class Sink(ABC):
    @abstractmethod
    async def send(self, message: NotificationMessage) -> SendResult:
        """Fan-out a notification message to all configured destinations."""
        ...

    @abstractmethod
    async def reply(self, recipient: str, text: str) -> None:
        """Send a command response back to the specific recipient/channel that issued the command."""
        ...

    @abstractmethod
    async def start_listener(self) -> None:
        """Long-running coroutine. Drives inbound events and dispatches commands."""
        ...
