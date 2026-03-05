from app.utils.json_connection import connect_to_processor
from app.utils.lutron_helpers import (
    get_root_area,
    get_child_areas,
    read_area_details
)
from app.models.processor import Processor
from app.models.floor_proc_mapping import FloorProcMapping
from app.models.area import Area
from sqlalchemy.orm import Session


# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------

def extract_id_from_href(href: str) -> int:
    """Extract numeric area_code from LEAP href, e.g. '/area/1022' → 1022."""
    return int(href.strip("/").split("/")[-1])


def prune_missing_leaf_areas(node: dict, db: Session, processor_id: int):
    """
    Keep ONLY leaf nodes that exist in Area table and have area_id.
    Intermediate nodes are never removed.
    """

    children = node.get("children", [])

    # -------- LEAF NODE --------
    if not children:
        area_code = node.get("area_code")

        if area_code is None:
            return None

        db_area = (
            db.query(Area)
            .filter(
                Area.code == str(area_code),
                Area.processor_id == processor_id
            )
            .first()
        )

        if not db_area:
            return None  #  remove leaf

        #  enforce area_id presence
        node["area_id"] = db_area.id
        return node

    # -------- INTERMEDIATE NODE --------
    pruned_children = []

    for child in children:
        pruned_child = prune_missing_leaf_areas(child, db, processor_id)
        if pruned_child:
            pruned_children.append(pruned_child)

    node["children"] = pruned_children
    return node



# ---------------------------------------------------------
# TREE BUILDING
# ---------------------------------------------------------

def build_area_tree(ip: str, sock, area_href: str, db: Session, processor_id: int) -> dict:
    """
    Recursively builds area hierarchy starting from area_href.
    Adds area_id if present in DB.
    """
    area_code = extract_id_from_href(area_href)
    area_details = read_area_details(area_href, sock)
    children = get_child_areas(area_href, sock)

    db_area = (
        db.query(Area)
        .filter(
            Area.code == str(area_code),
            Area.processor_id == processor_id
        )
        .first()
    )

    area_node = {
        "area_code": area_code,
        "name": area_details.get("Name", "Unnamed"),
        "href": area_href,
        "children": [
            build_area_tree(ip, sock, c["href"], db, processor_id)
            for c in children
        ]
    }

    if db_area:
        area_node["area_id"] = db_area.id

    return area_node


# ---------------------------------------------------------
# PUBLIC API
# ---------------------------------------------------------

def get_area_tree_by_floor(db: Session, floor_id: int) -> list:
    """
    Builds area trees for all processors mapped to the floor,
    prunes invalid leaf areas, and merges trees.
    """

    processors = (
        db.query(Processor)
        .join(FloorProcMapping, FloorProcMapping.processor_id == Processor.id)
        .filter(FloorProcMapping.floor_id == floor_id)
        .all()
    )

    if not processors:
        return []

    processor_trees = []

    for processor in processors:
        sock = None
        try:
            sock = connect_to_processor(
                processor.ipv4,
                processor.mac,
                processor.system,
                processor_ipv4=processor.ipv4
            )

            if not sock:
                continue

            root_area = get_root_area(sock)
            if not root_area or "href" not in root_area:
                continue

            tree = build_area_tree(
                processor.ipv4,
                sock,
                root_area["href"],
                db,
                processor.id
            )

            # PRUNE INVALID LEAF AREAS
            tree = prune_missing_leaf_areas(tree, db, processor.id)

            if tree:
                processor_trees.append(tree)

        except Exception as e:
            print(
                f"Error building area tree for processor {processor.id} "
                f"on floor {floor_id}: {e}"
            )
        finally:
            if sock:
                sock.close()

    if not processor_trees:
        return []

    if len(processor_trees) == 1:
        return processor_trees

    merged_tree = merge_area_trees(processor_trees)
    return [merged_tree] if merged_tree else []


# ---------------------------------------------------------
# TREE MERGE
# ---------------------------------------------------------

def merge_area_trees(trees: list) -> dict:
    """
    Merge multiple processor trees into a single tree.
    Uses area_code as the unique key.
    """

    if not trees:
        return None

    if len(trees) == 1:
        return trees[0]

    unique_nodes = {}

    def get_key(node):
        return f"code_{node.get('area_code')}"

    def collect(node, parent_key=None):
        key = get_key(node)

        if key not in unique_nodes:
            unique_nodes[key] = {
                "area_code": node.get("area_code"),
                "name": node.get("name"),
                "href": node.get("href"),
                "area_id": node.get("area_id"),
                "parents": set(),
                "children": set()
            }
        else:
            if node.get("area_id") and not unique_nodes[key]["area_id"]:
                unique_nodes[key]["area_id"] = node.get("area_id")

        if parent_key:
            unique_nodes[key]["parents"].add(parent_key)

        for child in node.get("children", []):
            child_key = get_key(child)
            unique_nodes[key]["children"].add(child_key)
            collect(child, key)

    for tree in trees:
        collect(tree)

    def build(key, visited):
        if key in visited:
            return None

        visited.add(key)
        data = unique_nodes[key]

        children = []
        for child_key in data["children"]:
            if child_key in unique_nodes:
                child_node = build(child_key, visited)
                if child_node:
                    children.append(child_node)

        node = {
            "area_code": data["area_code"],
            "name": data["name"],
            "href": data["href"],
            "children": children
        }

        if data.get("area_id"):
            node["area_id"] = data["area_id"]

        return node

    root_keys = [
        k for k, v in unique_nodes.items() if not v["parents"]
    ]

    visited = set()
    roots = [build(k, visited) for k in root_keys if k in unique_nodes]
    roots = [r for r in roots if r]

    if not roots:
        return None

    if len(roots) == 1:
        return roots[0]

    return {
        "name": "Floor Areas",
        "children": roots
    }
