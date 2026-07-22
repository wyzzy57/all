from __future__ import annotations

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from visiox_training_worker.dispatcher import (
        TrainingWorkerDispatchError,
        TrainingWorkerRunners,
        dispatch_training_worker_task,
    )
    from visiox_training_worker.main import (
        CommandResult,
        TrainingCommandRunner,
        TrainingResult,
        run_training_job,
    )


_DISPATCHER_EXPORTS = {
    "TrainingWorkerDispatchError",
    "TrainingWorkerRunners",
    "dispatch_training_worker_task",
}
_MAIN_EXPORTS = {
    "CommandResult",
    "TrainingCommandRunner",
    "TrainingResult",
    "run_training_job",
}

__all__ = [
    "CommandResult",
    "TrainingCommandRunner",
    "TrainingResult",
    "TrainingWorkerDispatchError",
    "TrainingWorkerRunners",
    "dispatch_training_worker_task",
    "run_training_job",
]


def __getattr__(name: str) -> Any:
    if name in _DISPATCHER_EXPORTS:
        from visiox_training_worker import dispatcher

        value = getattr(dispatcher, name)
    elif name in _MAIN_EXPORTS:
        from visiox_training_worker import main

        value = getattr(main, name)
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value
