YOLO26_TASKS = ("detect", "segment", "semantic", "pose", "obb", "classify")
YOLO26_SCALES = ("n", "s", "m", "l", "x")


def task_scale_key(task: str, scale: str) -> str:
    if task not in YOLO26_TASKS:
        raise ValueError(f"unsupported YOLO26 task: {task}")
    if scale not in YOLO26_SCALES:
        raise ValueError(f"unsupported YOLO26 scale: {scale}")
    return f"yolo26{scale}-{task}"
