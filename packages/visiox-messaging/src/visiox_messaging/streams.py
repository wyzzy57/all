from typing import Any

from visiox_common.tasks import TaskCommand, TaskType


STREAM_BY_TASK_TYPE: dict[TaskType, str] = {
    TaskType.VALIDATE_DATASET_FORMAT: "stream:training.commands",
    TaskType.TRAIN_MODEL: "stream:training.commands",
    TaskType.CONVERT_MODEL: "stream:training.commands",
    TaskType.SYNC_LABEL_STUDIO_DATA: "stream:label_sync.commands",
    TaskType.IMPORT_LABEL_STUDIO_ANNOTATION: "stream:label_sync.commands",
}


class RedisStreamProducer:
    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    async def enqueue(self, command: TaskCommand) -> str:
        stream_name = STREAM_BY_TASK_TYPE[command.task_type]
        message_id = await self._redis.xadd(stream_name, command.to_stream_fields())
        return message_id.decode() if isinstance(message_id, bytes) else str(message_id)


class RedisStreamConsumer:
    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    async def read_group(
        self,
        stream_name: str,
        group_name: str,
        consumer_name: str,
        count: int = 10,
        block_ms: int = 1000,
    ):
        return await self._redis.xreadgroup(
            group_name,
            consumer_name,
            {stream_name: ">"},
            count=count,
            block=block_ms,
        )

    async def ack(self, stream_name: str, group_name: str, message_id: str) -> int:
        return int(await self._redis.xack(stream_name, group_name, message_id))
