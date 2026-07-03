from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from visiox_messaging.pubsub import RedisTaskProgressBroker, TaskProgressBroker


router = APIRouter(tags=["task-progress"])


def get_progress_broker(websocket: WebSocket) -> TaskProgressBroker:
    return RedisTaskProgressBroker(websocket.app.state.redis)


@router.websocket("/ws/tasks/{task_id}")
async def task_progress_websocket(
    websocket: WebSocket,
    task_id: str,
    broker: TaskProgressBroker = Depends(get_progress_broker),
) -> None:
    await websocket.accept()
    try:
        async for event in broker.subscribe(task_id):
            await websocket.send_json(event.model_dump(mode="json"))
    except WebSocketDisconnect:
        return
