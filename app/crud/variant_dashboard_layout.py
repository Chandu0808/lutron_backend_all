from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.crud.variant_config_defaults import get_default_layouts, normalize_variant_slug
from app.models.variant_dashboard_layout import VariantDashboardLayout


def list_variant_layouts(db: Session, variant: str) -> List[VariantDashboardLayout]:
    slug = normalize_variant_slug(variant)
    return (
        db.query(VariantDashboardLayout)
        .filter(VariantDashboardLayout.variant == slug)
        .order_by(VariantDashboardLayout.layout_key)
        .all()
    )


def get_variant_layout(
    db: Session,
    variant: str,
    layout_key: str,
) -> Optional[VariantDashboardLayout]:
    slug = normalize_variant_slug(variant)
    return (
        db.query(VariantDashboardLayout)
        .filter(
            VariantDashboardLayout.variant == slug,
            VariantDashboardLayout.layout_key == layout_key,
        )
        .first()
    )


def upsert_variant_layout(
    db: Session,
    variant: str,
    layout_key: str,
    layout_json: Any,
    *,
    layout_version: int = 1,
    updated_by: Optional[int] = None,
) -> VariantDashboardLayout:
    row = get_variant_layout(db, variant, layout_key)
    slug = normalize_variant_slug(variant)
    if row is None:
        row = VariantDashboardLayout(
            variant=slug,
            layout_key=layout_key,
            layout_json=deepcopy(layout_json),
            layout_version=layout_version,
            updated_by=updated_by,
        )
        db.add(row)
    else:
        row.layout_json = deepcopy(layout_json)
        row.layout_version = layout_version
        row.updated_by = updated_by
    db.commit()
    db.refresh(row)
    return row


def seed_variant_layouts(db: Session, variant: str) -> int:
    slug = normalize_variant_slug(variant)
    existing = (
        db.query(VariantDashboardLayout.id)
        .filter(VariantDashboardLayout.variant == slug)
        .first()
    )
    if existing is not None:
        return 0

    count = 0
    for layout_key, layout_json in get_default_layouts(slug).items():
        row = VariantDashboardLayout(
            variant=slug,
            layout_key=layout_key,
            layout_json=deepcopy(layout_json),
            layout_version=1,
        )
        db.add(row)
        count += 1
    db.commit()
    return count
