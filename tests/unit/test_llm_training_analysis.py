from __future__ import annotations

from visiox_api.services.llm_training_analysis import analyze_llm_training


def points(*values: float) -> list[dict[str, float]]:
    return [
        {"step": float(index), "value": value, "timestamp": float(index)}
        for index, value in enumerate(values, start=1)
    ]


def test_analysis_reports_non_finite_loss_and_exploding_gradient() -> None:
    findings = analyze_llm_training(
        {
            "loss": points(1.0, float("nan"), 0.8),
            "grad_norm": points(2.0, 120.0, 140.0),
        },
        {},
    )

    assert {finding["code"] for finding in findings} == {
        "NON_FINITE_LOSS",
        "EXPLODING_GRADIENT_NORM",
    }
    assert all(finding["metric_names"] for finding in findings)


def test_analysis_reports_widening_train_eval_gap_and_low_gpu_utilization() -> None:
    findings = analyze_llm_training(
        {
            "loss": points(1.0, 0.8, 0.6, 0.5, 0.4),
            "eval_loss": points(1.1, 1.2, 1.4, 1.6, 1.8),
        },
        {"gpu.GPU-a.utilization_percent": points(10, 12, 15, 8, 11)},
    )

    assert {finding["code"] for finding in findings} == {
        "WIDENING_TRAIN_EVAL_GAP",
        "LOW_GPU_UTILIZATION",
    }


def test_analysis_does_not_diagnose_with_insufficient_samples() -> None:
    assert analyze_llm_training({"loss": points(1.0, 0.9)}, {}, minimum_samples=5) == []
