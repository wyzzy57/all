from __future__ import annotations

from typing import Any
import math


def analyze_llm_training(
    scalars: dict[str, list[dict[str, float]]],
    resources: dict[str, list[dict[str, float]]],
    *,
    minimum_samples: int = 5,
    exploding_grad_norm: float = 100.0,
    low_gpu_utilization: float = 20.0,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    loss = scalars.get("loss", [])
    grad_norm = scalars.get("grad_norm", [])
    if any(not math.isfinite(point["value"]) for point in loss):
        findings.append(
            _finding(
                "NON_FINITE_LOSS",
                "critical",
                "训练损失出现非有限值",
                "损失中出现 NaN 或无穷值，训练结果不再可信。",
                ["loss"],
                loss,
            )
        )
    if len(grad_norm) >= 2 and max(point["value"] for point in grad_norm) >= exploding_grad_norm:
        findings.append(
            _finding(
                "EXPLODING_GRADIENT_NORM",
                "critical",
                "梯度范数异常增大",
                f"梯度范数达到阈值 {exploding_grad_norm:g} 以上。",
                ["grad_norm"],
                grad_norm,
            )
        )
    eval_loss = scalars.get("eval_loss", [])
    comparable = min(len(loss), len(eval_loss))
    if comparable >= minimum_samples:
        gaps = [eval_loss[-comparable + index]["value"] - loss[-comparable + index]["value"] for index in range(comparable)]
        if gaps[-1] > 0.5 and gaps[-1] > gaps[0] * 1.5:
            findings.append(
                _finding(
                    "WIDENING_TRAIN_EVAL_GAP",
                    "warning",
                    "训练集与验证集差距扩大",
                    "验证损失相对训练损失持续偏高，存在过拟合迹象。",
                    ["loss", "eval_loss"],
                    [*loss[-comparable:], *eval_loss[-comparable:]],
                )
            )
    gpu_series = {
        name: values
        for name, values in resources.items()
        if name.startswith("gpu.") and name.endswith(".utilization_percent")
    }
    gpu_points = [point for values in gpu_series.values() for point in values]
    if len(gpu_points) >= minimum_samples:
        average = sum(point["value"] for point in gpu_points) / len(gpu_points)
        if average < low_gpu_utilization:
            findings.append(
                _finding(
                    "LOW_GPU_UTILIZATION",
                    "warning",
                    "GPU 利用率持续偏低",
                    f"采样平均 GPU 利用率为 {average:.1f}%，低于 {low_gpu_utilization:.1f}%。",
                    sorted(gpu_series),
                    gpu_points,
                )
            )
    return findings


def _finding(
    code: str,
    severity: str,
    title: str,
    message: str,
    metric_names: list[str],
    points: list[dict[str, float]],
) -> dict[str, Any]:
    finite_values = [point["value"] for point in points if math.isfinite(point["value"])]
    steps = [point["step"] for point in points]
    return {
        "code": code,
        "severity": severity,
        "title": title,
        "message": message,
        "metric_names": metric_names,
        "step_range": [min(steps), max(steps)] if steps else None,
        "observed_values": {
            "minimum": min(finite_values) if finite_values else None,
            "maximum": max(finite_values) if finite_values else None,
            "latest": finite_values[-1] if finite_values else None,
        },
    }
