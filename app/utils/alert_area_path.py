"""
DB-first alert location mapping (connection-safe).

Lutron processors allow max 10 concurrent LEAP connections. Steady-state LMS
already holds 2 (listener + loadcontroller_listener). Mapping must prefer DB
(zones.loadcontroller_code → areas) and never pile ReadRequests onto the
subscribe socket.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session, joinedload

from app.models.area import Area
from app.models.zone import Zone

# After this many failed map attempts, stop LEAP remaps until path appears.
MAX_AREA_MAP_FAILURES = 2


def _ids_equal(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return False
    try:
        return int(left) == int(right)
    except (TypeError, ValueError):
        return str(left).strip() == str(right).strip()


def _is_area_code_token(token: str, area_code: Any = None) -> bool:
    """True for numeric LEAP codes that must not appear in the display path."""
    text = (token or "").strip()
    if not text:
        return True
    if area_code is not None and text == str(area_code).strip():
        return True
    return text.isdigit()


def join_display_path(parts: Optional[List[str]], area_code: Any = None) -> Optional[str]:
    """Join hierarchy names; omit empty segments and numeric area codes."""
    cleaned: List[str] = []
    for part in parts or []:
        token = (part or "").strip()
        if _is_area_code_token(token, area_code):
            continue
        if cleaned and cleaned[-1].lower() == token.lower():
            continue
        cleaned.append(token)
    return "/".join(cleaned) if cleaned else None


def sanitize_stored_path(path: Optional[str], area_code: Any = None) -> Optional[str]:
    if path is None:
        return None
    text = str(path).strip()
    if not text:
        return None
    return join_display_path(text.split("/"), area_code)


def node_leap_code(node: Any) -> Optional[str]:
    """LEAP area id from a tree node. Never treat LMS primary keys as LEAP ids."""
    if not isinstance(node, dict):
        return None
    href = node.get("href")
    if isinstance(href, str) and href.strip("/"):
        try:
            return str(int(href.strip("/").split("/")[-1]))
        except (TypeError, ValueError):
            tail = href.strip("/").split("/")[-1]
            return tail or None
    if node.get("area_code") is not None and str(node.get("area_code")).strip() != "":
        return str(node.get("area_code")).strip()
    return None


def find_named_path_in_tree(
    tree: Any,
    area_id: Any = None,
    area_code: Any = None,
) -> Optional[List[str]]:
    """
    Walk a cached area tree and return name chain from root to the leaf.

    Match LEAP area code only (href /area/993, area_code).
    Do not match LMS areas.id against node id — those namespaces collide.
    """
    del area_id  # LMS pk is the wrong key for processor trees
    if area_code is None or str(area_code).strip() == "":
        return None
    target = str(area_code).strip()
    try:
        target = str(int(target))
    except (TypeError, ValueError):
        pass

    def walk(node: Any, names: List[str]) -> Optional[List[str]]:
        if not isinstance(node, dict):
            return None
        raw_name = (node.get("name") or "").strip()
        next_names = names + [raw_name] if raw_name else names
        if _ids_equal(node_leap_code(node), target):
            return next_names
        for child in node.get("children") or []:
            found = walk(child, next_names)
            if found:
                return found
        return None

    if isinstance(tree, list):
        for root in tree:
            found = walk(root, [])
            if found:
                return found
        return None
    if isinstance(tree, dict):
        return walk(tree, [])
    return None


def _short_floor_leaf_path(area: Area) -> Optional[str]:
    parts: List[str] = []
    floor = getattr(area, "floor", None)
    if floor is not None and getattr(floor, "name", None):
        parts.append(floor.name)
    if area.name:
        parts.append(area.name)
    return join_display_path(parts, getattr(area, "code", None))


class AreaPathResolver:
    """Resolve leaf-area display paths from cached floor.area_tree (no LEAP)."""

    def __init__(self, db: Session):
        self.db = db
        self._trees: Optional[List[Any]] = None
        self._areas: Dict[int, Optional[Area]] = {}

    def _load_trees(self) -> List[Any]:
        if self._trees is None:
            from app.models.floor import Floor

            self._trees = [
                floor.area_tree
                for floor in self.db.query(Floor).all()
                if floor.area_tree
            ]
        return self._trees

    def get_area(self, area_id: Optional[int]) -> Optional[Area]:
        if area_id is None:
            return None
        try:
            key = int(area_id)
        except (TypeError, ValueError):
            return None
        if key not in self._areas:
            self._areas[key] = (
                self.db.query(Area)
                .options(joinedload(Area.floor))
                .filter(Area.id == key)
                .first()
            )
        return self._areas[key]

    def get_area_by_code(
        self, area_code: Any, processor_id: Optional[int] = None
    ) -> Optional[Area]:
        if area_code is None or str(area_code).strip() == "":
            return None
        code = str(area_code).strip()
        q = self.db.query(Area).options(joinedload(Area.floor)).filter(Area.code == code)
        if processor_id is not None:
            q = q.filter(Area.processor_id == processor_id)
        return q.first()

    def path_from_leap_code(self, area_code: Any) -> Optional[str]:
        if area_code is None or str(area_code).strip() == "":
            return None
        for tree in self._load_trees():
            names = find_named_path_in_tree(tree, area_code=area_code)
            if names:
                return join_display_path(names, area_code)
        return None

    def path_from_sibling_areas(self, area: Area) -> Optional[str]:
        """
        When this leaf is missing from a sparse floor.area_tree, reuse a sibling
        leaf's known full path and swap the last segment for this area's name.
        """
        if area is None or area.floor_id is None or not area.name:
            return None
        siblings = (
            self.db.query(Area)
            .filter(Area.floor_id == area.floor_id, Area.id != area.id)
            .all()
        )
        for sib in siblings:
            if sib.code is None or str(sib.code).strip() == "":
                continue
            sib_path = self.path_from_leap_code(sib.code)
            if not sib_path:
                continue
            parts = [p for p in sib_path.split("/") if p]
            if not parts:
                continue
            parts[-1] = area.name
            candidate = join_display_path(parts, getattr(area, "code", None))
            if candidate:
                return candidate
        return None

    def resolve(
        self,
        area: Optional[Area],
        stored_path: Optional[str] = None,
        area_code: Any = None,
    ) -> Optional[str]:
        code = None
        if area is not None and area.code is not None and str(area.code).strip() != "":
            code = area.code
        elif area_code is not None and str(area_code).strip() != "":
            code = area_code

        tree_path = self.path_from_leap_code(code)
        if tree_path:
            return tree_path

        if area is not None:
            sibling_path = self.path_from_sibling_areas(area)
            if sibling_path:
                return sibling_path
            short = _short_floor_leaf_path(area)
            if short:
                return short

        return sanitize_stored_path(stored_path, code)


def build_area_path(
    area: Optional[Area],
    db: Optional[Session] = None,
    resolver: Optional[AreaPathResolver] = None,
) -> Optional[str]:
    """Build display path for a leaf area: full tree names, no area codes."""
    if area is None:
        return None
    if resolver is not None:
        return resolver.resolve(area, None)
    if db is not None:
        return AreaPathResolver(db).resolve(area, None)
    return _short_floor_leaf_path(area)


def map_loadcontroller_from_db(
    db: Session,
    processor_id: int,
    loadcontroller_code: int,
    resolver: Optional[AreaPathResolver] = None,
) -> Tuple[Optional[int], Optional[int], Optional[int], Optional[str]]:
    """
    Resolve LC → zone → area from LMS DB only (no LEAP).

    Returns: (area_id, area_code, zone_code, area_path)
    """
    if processor_id is None or loadcontroller_code is None:
        return None, None, None, None

    zone = (
        db.query(Zone)
        .filter(
            Zone.processor_id == processor_id,
            Zone.loadcontroller_code == int(loadcontroller_code),
        )
        .first()
    )
    if not zone:
        return None, None, None, None

    zone_code = None
    try:
        zone_code = int(zone.code) if zone.code is not None else None
    except (TypeError, ValueError):
        zone_code = None

    path_resolver = resolver or AreaPathResolver(db)
    area = path_resolver.get_area(zone.area_id) if zone.area_id else None
    area_id = area.id if area else None
    area_code = None
    if area and area.code is not None:
        try:
            area_code = int(area.code)
        except (TypeError, ValueError):
            area_code = None

    return area_id, area_code, zone_code, build_area_path(area, resolver=path_resolver)


def map_area_from_codes(
    db: Session,
    processor_id: Optional[int],
    area_code: Optional[str],
    resolver: Optional[AreaPathResolver] = None,
) -> Tuple[Optional[int], Optional[str]]:
    """Resolve sensors/modules area_id + area_path from DB by area_code."""
    if processor_id is None or area_code is None or str(area_code).strip() == "":
        return None, None
    path_resolver = resolver or AreaPathResolver(db)
    area = path_resolver.get_area_by_code(area_code, processor_id=processor_id)
    if not area:
        return None, None
    return area.id, build_area_path(area, resolver=path_resolver)


def area_alert_scope(
    area: Optional[Area],
    *,
    area_id: Optional[int] = None,
    area_code: Any = None,
) -> dict:
    """Ids used by heatmap/alert click-through. Never include these in location text."""
    resolved_id = area.id if area is not None else area_id
    resolved_floor = area.floor_id if area is not None else None
    resolved_code = None
    if area is not None and area.code is not None:
        resolved_code = str(area.code)
    elif area_code is not None and str(area_code).strip() != "":
        resolved_code = str(area_code).strip()
    return {
        "area_id": resolved_id,
        "floor_id": resolved_floor,
        "area_code": resolved_code,
    }


def should_attempt_area_map(area_path: Optional[str], fail_count: Optional[int]) -> bool:
    """Skip remap when path already stored or failures exceeded (dummy LC)."""
    if area_path and str(area_path).strip():
        return False
    return int(fail_count or 0) < MAX_AREA_MAP_FAILURES
