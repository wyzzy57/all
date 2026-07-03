import asyncio
import contextlib
import json
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any, Protocol

from visiox_common.tasks import TaskProgressEvent


TASK_PROGRESS_CHANNEL = "pubsub:task.progress"


class TaskProgressBroker(Protocol):
    def subscribe(self, task_id: str) -> AsyncIterator[TaskProgressEvent]:
        pass


class TaskProgressPublisher:
    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    async def publish(self, event: TaskProgressEvent) -> int:
        payload = event.model_dump_json()
        return int(await self._redis.publish(TASK_PROGRESS_CHANNEL, payload))


class RedisTaskProgressBroker:
    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    async def subscribe(self, task_id: str) -> AsyncIterator[TaskProgressEvent]:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(TASK_PROGRESS_CHANNEL)
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                data = message.get("data")
                if isinstance(data, bytes):
                    data = data.decode()
                event = TaskProgressEvent.model_validate_json(data)
                if event.task_id == task_id:
                    yield event
        finally:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe(TASK_PROGRESS_CHANNEL)
            close = getattr(pubsub, "aclose", None) or getattr(pubsub, "close", None)
            if close is not None:
                result = close()
                if asyncio.iscoroutine(result):
                    await result


class InMemoryTaskProgressBroker:
    def __init__(self) -> None:
        self._subscribers: dict[
            str,
            list[tuple[asyncio.AbstractEventLoop, asyncio.Queue[TaskProgressEvent]]],
        ] = defaultdict(list)
        self.published: list[TaskProgressEvent] = []

    def publish(self, event: TaskProgressEvent) -> int:
        self.published.append(event)
        subscribers = list(self._subscribers[event.task_id])
        for loop, queue in subscribers:
            loop.call_soon_threadsafe(queue.put_nowait, event)
        return len(subscribers)

    async def subscribe(self, task_id: str) -> AsyncIterator[TaskProgressEvent]:
        queue: asyncio.Queue[TaskProgressEvent] = asyncio.Queue()
        subscriber = (asyncio.get_running_loop(), queue)
        self._subscribers[task_id].append(subscriber)
        try:
            while True:
                event = await queue.get()
                yield event
        finally:
            self._subscribers[task_id].remove(subscriber)
            if not self._subscribers[task_id]:
                del self._subscribers[task_id]


def event_from_pubsub_payload(payload: str | bytes) -> TaskProgressEvent:
    if isinstance(payload, bytes):
        payload = payload.decode()
    return TaskProgressEvent.model_validate(json.loads(payload))
