import importlib.util
from pathlib import Path

import pytest


def _gateway_module():
    path = Path("apps/label-studio-gateway/app.py")
    spec = importlib.util.spec_from_file_location("visiox_label_studio_gateway", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/projects/12/data", "/projects/12/data"),
        ("https://evil.example", "/"),
        ("//evil.example/path", "/"),
        ("/projects/1\\evil", "/"),
    ],
)
def test_gateway_rejects_external_login_destinations(value, expected):
    assert _gateway_module()._safe_destination(value) == expected
