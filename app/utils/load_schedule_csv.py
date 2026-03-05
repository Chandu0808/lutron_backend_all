"""
Load Schedule CSV parsing for zone-wise max_power / high_end_trim upload.
No app dependencies (auth, FastAPI) so scripts can import without full stack.
high_end_trim is None when column not present or cell empty; API uses high_end_trim_default for those zones.
Uses header names (not column indices) to support different Lutron Load Schedule export formats.
"""
import csv
import io
from typing import List, Optional, Tuple

ZONE_NUMBER_COL = 0

# Invisible/format characters that can appear in Lutron Designer exports (e.g. LTR/RTL marks)
INVISIBLE_CHARS = frozenset("\u200e\u200f\u200b\u200c\u200d\ufeff")

# Fallback column indices for backward compatibility if header detection fails
FALLBACK_ZONE_NAME_COL = 1
FALLBACK_TOTAL_WATTAGE_COL = 7


def _normalize_header(s: str) -> str:
    """Normalize header cell for case-insensitive matching."""
    if not s:
        return ""
    return "".join(c for c in s if c not in INVISIBLE_CHARS).strip().lower()


def _find_column_index(header_row: List[str], candidates: List[Tuple[str, ...]]) -> Optional[int]:
    """
    Find column index by header name. Returns first match where normalized header
    contains all substrings in candidate (e.g. ("zone", "name") matches "Zone Name").
    """
    for j, cell in enumerate(header_row):
        normalized = _normalize_header(cell or "")
        if not normalized:
            continue
        for parts in candidates:
            if all(p in normalized for p in parts):
                return j
    return None


def normalize_name(s: str) -> str:
    """Remove LTR/RTL and other invisible chars so parsed names match DB."""
    if not s:
        return s
    return "".join(c for c in s if c not in INVISIBLE_CHARS).strip()


def parse_load_schedule_csv(content: str) -> List[Tuple[str, List[Tuple[str, float, Optional[float]]]]]:
    """
    Parse Load Schedule CSV format. Returns list of (area_name, zone_rows)
    where zone_rows = [(zone_name, max_power, high_end_trim), ...].
    high_end_trim is None when column not present or cell empty.
    """
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    if not rows:
        return []

    i = 6
    result = []
    while i < len(rows):
        row = rows[i]
        if not row:
            i += 1
            continue
        first_cell = (row[0] or "").strip()
        if first_cell and ("\\" in first_cell or "/" in first_cell):
            # Use backslash as path separator; do not replace "/" in area name (e.g. "WS OS-05/06")
            if "\\" in first_cell:
                area_name = normalize_name(first_cell.split("\\")[-1].strip())
            else:
                area_name = normalize_name(first_cell.split("/")[-1].strip())
            i += 1
            if i >= len(rows):
                break
            header = rows[i]
            i += 1

            zone_name_col = _find_column_index(
                header, [("zone name",), ("zone", "name")]
            )
            wattage_col = _find_column_index(
                header, [("total wattage",), ("total", "wattage")]
            )
            trim_col = _find_column_index(
                header, [("trim",), ("highend", "trim")]
            )

            if zone_name_col is None:
                zone_name_col = FALLBACK_ZONE_NAME_COL
            if wattage_col is None:
                wattage_col = FALLBACK_TOTAL_WATTAGE_COL
            zone_rows = []
            while i < len(rows):
                data_row = rows[i]
                if not data_row or not any((c or "").strip() for c in data_row):
                    i += 1
                    continue  # skip blank lines (e.g. between header and first zone row in Lutron export)
                first = (data_row[0] or "").strip()
                if first and ("\\" in first or "/" in first):
                    break  # next area path row; outer loop will process it
                if len(data_row) > ZONE_NUMBER_COL and first and first.isdigit():
                    zone_name = normalize_name((data_row[zone_name_col] or "").strip() if len(data_row) > zone_name_col else "")
                    wattage_str = (data_row[wattage_col] or "").strip() if len(data_row) > wattage_col else ""
                    if zone_name and wattage_str:
                        try:
                            wattage = float(wattage_str)
                        except ValueError:
                            pass
                        else:
                            trim_val = (data_row[trim_col] or "").strip() if trim_col is not None and len(data_row) > trim_col else ""
                            trim_value = None
                            if trim_val:
                                try:
                                    t = float(trim_val)
                                    trim_value = max(0.0, min(100.0, t))
                                except ValueError:
                                    pass
                            zone_rows.append((zone_name, wattage, trim_value))
                i += 1
            result.append((area_name, zone_rows))
            continue
        i += 1
    return result
