"""
FOFP admin API routes.

All endpoints live under the ``/fofp`` prefix and operate exclusively on the
new ``zone_floorplan_positions`` table. They never mutate ``floors``,
``areas``, ``zones``, or ``coordinates`` and never touch occupancy / energy
data. Existing routes remain untouched.

Permission model (mirrors existing project pattern):
- GET  /fofp/config          : Authenticated user only
- PUT  /fofp/config          : Superadmin only (``required_level=5``)
- POST /fofp/generate-layout : Superadmin only (``required_level=5``)
- PUT  /fofp/layout          : Superadmin only (``required_level=5``)
- GET  /fofp/layout/{id}     : Authenticated user only (no scope check)
"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.crud.fofp_layout import generate_layout_for_floor
from app.crud.fofp_settings import (
    get_fofp_config,
    normalize_marker_size,
    normalize_shape,
    resolve_marker_half_axes,
    record_generation_result,
    update_fofp_config,
)
from app.crud.fofp_placement_validation import (
    resolve_saved_marker_state,
    validate_marker_geometry_for_area,
)
from app.database.session import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.permissions import require_operator_permission_for_scope
from app.models.area import Area
from app.models.floor import Floor
from app.models.fofp import ZoneFloorplanPosition
from app.models.user_model import User
from app.models.zone import Zone
from app.schemas.fofp import (
    FOFPConfigOut,
    FOFPConfigUpdate,
    FOFPGenerateRequest,
    FOFPGenerateResponse,
    FOFPLayoutResponse,
    FOFPPositionOut,
    FOFPSaveRequest,
    FOFPSaveResponse,
)


router = APIRouter()


@router.get("/config", response_model=FOFPConfigOut)
def fofp_get_config(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> FOFPConfigOut:
    """Return global FOFP configuration (authenticated)."""
    return FOFPConfigOut(**get_fofp_config(db))


@router.put("/config", response_model=FOFPConfigOut)
def fofp_put_config(
    payload: FOFPConfigUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> FOFPConfigOut:
    """Update global FOFP configuration (Superadmin only)."""
    require_operator_permission_for_scope(
        required_level=5,
        db=db,
        current_user=current_user,
    )
    try:
        data = update_fofp_config(
            db,
            enabled=payload.fofp_enabled,
            shape=payload.shape,
            marker_size=payload.marker_size,
            marker_color=payload.marker_color,
        )
        return FOFPConfigOut(**data)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("/generate-layout", response_model=FOFPGenerateResponse)
def fofp_generate_layout(
    payload: FOFPGenerateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> FOFPGenerateResponse:
    """
    Generate missing FOFP positions for a floor.

    Preserves any existing positions (manual or auto) and only fills in zones
    that currently have no placement. Internally delegates to
    :func:`app.crud.fofp_layout.generate_layout_for_floor`.
    """
    require_operator_permission_for_scope(
        required_level=5,
        db=db,
        current_user=current_user,
    )

    floor = db.query(Floor).filter(Floor.id == payload.floor_id).first()
    if not floor:
        raise HTTPException(status_code=404, detail="Floor not found")

    summary = generate_layout_for_floor(db, payload.floor_id)
    record_generation_result(db, summary)
    return FOFPGenerateResponse(**summary)


@router.get("/layout/{floor_id}", response_model=FOFPLayoutResponse)
def fofp_get_layout(
    floor_id: int = Path(..., gt=0, description="Floor ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> FOFPLayoutResponse:
    """
    Return the current FOFP layout for a floor.

    Read-only. Does not join occupancy or energy tables; does not alter floor
    rendering or any existing endpoint behavior.
    """
    floor = db.query(Floor).filter(Floor.id == floor_id).first()
    if not floor:
        raise HTTPException(status_code=404, detail="Floor not found")

    try:
        rows = (
            db.query(ZoneFloorplanPosition)
            .filter(
                ZoneFloorplanPosition.floor_id == floor_id,
                ZoneFloorplanPosition.zone_available.is_(True),
                ZoneFloorplanPosition.zone_id.isnot(None),
            )
            .order_by(ZoneFloorplanPosition.id.asc())
            .all()
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "FOFP layout storage is not ready. Restart the backend after "
                "migrations, or run migrations/apply_fofp.py. "
                f"({exc.__class__.__name__})"
            ),
        ) from exc

    from app.crud.fofp_overlay import lookup_zone_names

    zone_names = lookup_zone_names(db, [r.zone_id for r in rows if r.zone_id is not None])
    positions: List[FOFPPositionOut] = []
    for row in rows:
        base = FOFPPositionOut.model_validate(row)
        positions.append(
            base.model_copy(update={"zone_name": zone_names.get(int(row.zone_id))})
        )
    return FOFPLayoutResponse(floor_id=floor_id, positions=positions)


@router.put("/layout", response_model=FOFPSaveResponse)
def fofp_save_layout(
    payload: FOFPSaveRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> FOFPSaveResponse:
    """
    Bulk upsert of manual FOFP positions for a floor.

    For each entry: update the existing position (if one exists for that
    ``zone_id``) or create a new row, always stamping
    ``placement_source="manual"``.

    The whole operation runs in a single transaction. Any validation or
    integrity failure rolls back every change in this call; no partial state
    is left in ``zone_floorplan_positions``.
    """
    require_operator_permission_for_scope(
        required_level=5,
        db=db,
        current_user=current_user,
    )

    floor = db.query(Floor).filter(Floor.id == payload.floor_id).first()
    if not floor:
        raise HTTPException(status_code=404, detail="Floor not found")

    # Reject duplicate zone_ids in the payload to keep the upsert deterministic.
    zone_ids = [p.zone_id for p in payload.positions]
    if len(zone_ids) != len(set(zone_ids)):
        raise HTTPException(
            status_code=400, detail="Duplicate zone_id in positions payload"
        )

    if not payload.positions:
        return FOFPSaveResponse(updated=0, created=0)

    area_ids = list({p.area_id for p in payload.positions})

    zones_map = {
        z.id: z
        for z in db.query(Zone).filter(Zone.id.in_(zone_ids)).all()
    }
    areas_map = {
        a.id: a
        for a in db.query(Area).filter(Area.id.in_(area_ids)).all()
    }

    layout_defaults = get_fofp_config(db)

    existing = {
        row.zone_id: row
        for row in db.query(ZoneFloorplanPosition)
        .filter(ZoneFloorplanPosition.zone_id.in_(zone_ids))
        .all()
    }

    for entry in payload.positions:
        zone = zones_map.get(entry.zone_id)
        if zone is None:
            raise HTTPException(
                status_code=400, detail=f"Zone {entry.zone_id} not found"
            )
        area = areas_map.get(entry.area_id)
        if area is None:
            raise HTTPException(
                status_code=400, detail=f"Area {entry.area_id} not found"
            )
        if area.floor_id != payload.floor_id:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Area {entry.area_id} does not belong to floor "
                    f"{payload.floor_id}"
                ),
            )
        if zone.area_id != entry.area_id:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Zone {entry.zone_id} does not belong to area "
                    f"{entry.area_id}"
                ),
            )
        row = existing.get(entry.zone_id)
        shape, hx, hy, _legacy = resolve_saved_marker_state(
            entry_marker_shape=entry.marker_shape,
            entry_shape_size=entry.shape_size,
            entry_shape_size_x=entry.shape_size_x,
            entry_shape_size_y=entry.shape_size_y,
            existing_row=row,
            layout_defaults=layout_defaults,
        )
        geom_error = validate_marker_geometry_for_area(
            area,
            float(entry.x),
            float(entry.y),
            shape,
            hx,
            hy,
        )
        if geom_error:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Zone {entry.zone_id} in area {entry.area_id}: {geom_error}"
                ),
            )

    default_shape = normalize_shape(layout_defaults.get("shape"))
    default_size = normalize_marker_size(layout_defaults.get("marker_size"))

    updated = 0
    created = 0
    try:
        for entry in payload.positions:
            marker_shape = (
                normalize_shape(entry.marker_shape)
                if entry.marker_shape is not None
                else None
            )
            half_x = half_y = legacy_size = None
            if (
                entry.shape_size is not None
                or entry.shape_size_x is not None
                or entry.shape_size_y is not None
            ):
                base = (
                    entry.shape_size
                    if entry.shape_size is not None
                    else default_size
                )
                half_x, half_y, legacy_size = resolve_marker_half_axes(
                    base,
                    entry.shape_size_x,
                    entry.shape_size_y,
                )
            row = existing.get(entry.zone_id)
            if row is not None:
                row.floor_id = payload.floor_id
                row.area_id = entry.area_id
                row.x = float(entry.x)
                row.y = float(entry.y)
                row.placement_source = "manual"
                if marker_shape is not None:
                    row.marker_shape = marker_shape
                if legacy_size is not None:
                    row.shape_size = legacy_size
                    row.shape_size_x = half_x
                    row.shape_size_y = half_y
                updated += 1
            else:
                create_half_x, create_half_y, create_legacy = resolve_marker_half_axes(
                    default_size,
                    None,
                    None,
                )
                if legacy_size is not None:
                    create_half_x, create_half_y, create_legacy = (
                        half_x,
                        half_y,
                        legacy_size,
                    )
                db.add(
                    ZoneFloorplanPosition(
                        floor_id=payload.floor_id,
                        area_id=entry.area_id,
                        zone_id=entry.zone_id,
                        x=float(entry.x),
                        y=float(entry.y),
                        marker_shape=marker_shape if marker_shape is not None else default_shape,
                        shape_size=create_legacy,
                        shape_size_x=create_half_x,
                        shape_size_y=create_half_y,
                        placement_source="manual",
                    )
                )
                created += 1
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))

    return FOFPSaveResponse(updated=updated, created=created)
