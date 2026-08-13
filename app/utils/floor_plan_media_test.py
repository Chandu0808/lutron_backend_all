import os

import pytest
from fastapi import HTTPException

from app.utils.floor_plan_media import (
    floor_plan_client_url,
    resolve_floor_plan_disk_path,
)


def test_floor_plan_client_url():
    assert floor_plan_client_url(12) == "/floor/12/plan"


def test_resolve_floor_plan_disk_path_rejects_traversal(tmp_path, monkeypatch):
    plans = tmp_path / "floor_plans"
    plans.mkdir()
    monkeypatch.setattr(
        "app.utils.floor_plan_media.FLOOR_PLANS_DIR",
        str(plans),
    )
    with pytest.raises(HTTPException) as exc:
        resolve_floor_plan_disk_path("/floor_plans/../secrets.txt")
    assert exc.value.status_code == 404


def test_resolve_floor_plan_disk_path_resolves_file(tmp_path, monkeypatch):
    plans = tmp_path / "floor_plans"
    plans.mkdir()
    pdf = plans / "1st Floor_965.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(
        "app.utils.floor_plan_media.FLOOR_PLANS_DIR",
        str(plans),
    )
    path = resolve_floor_plan_disk_path("/floor_plans/1st Floor_965.pdf")
    assert path == str(pdf)
