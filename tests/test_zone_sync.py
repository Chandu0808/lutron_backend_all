from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.session import Base
from app.database.migrate_zones_processor import ensure_zones_processor_scope
from app.crud.zone_sync import parse_zone_entries, apply_zone_metadata_for_area, sync_zones_for_floor
from app.models.area import Area
from app.models.floor_proc_mapping import FloorProcMapping
from app.models.processor import Processor
from app.models.zone import Zone


def test_parse_zone_entries_basic():
    metadata = [
        {"href": "/zone/101", "Name": "Lobby Lights", "ControlType": "Dimmed"},
        {"href": "/zone/202", "Name": "Shade 1", "ControlType": "Shade"},
        {"href": "", "Name": "Bad"},
        "not-a-dict",
    ]
    parsed = parse_zone_entries(metadata)
    assert {"code": "101", "name": "Lobby Lights", "type": "Dimmed"} in parsed
    assert {"code": "202", "name": "Shade 1", "type": "Shade"} in parsed
    assert all("code" in z and "name" in z and "type" in z for z in parsed)


def test_sqlite_migration_rebuild_adds_processor_id_and_unique(tmp_path, monkeypatch):
    db_path = tmp_path / "zones_mig.sqlite"
    engine = create_engine(f"sqlite:///{db_path}")

    # Create a legacy zones table (no processor_id, unique code) + minimal referenced tables.
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        conn.exec_driver_sql("CREATE TABLE processor (id INTEGER PRIMARY KEY)")
        conn.exec_driver_sql("CREATE TABLE areas (id INTEGER PRIMARY KEY, processor_id INTEGER NOT NULL)")
        conn.exec_driver_sql(
            """
            CREATE TABLE zones (
                id INTEGER PRIMARY KEY,
                code VARCHAR(50) NOT NULL UNIQUE,
                name VARCHAR(100) NOT NULL,
                type VARCHAR(50),
                area_id INTEGER NOT NULL,
                max_power FLOAT,
                high_end_trim FLOAT,
                energy_trim FLOAT,
                low_end_trim FLOAT,
                loadcontroller_code INTEGER
            )
            """
        )
        conn.exec_driver_sql("INSERT INTO processor(id) VALUES (1)")
        conn.exec_driver_sql("INSERT INTO areas(id, processor_id) VALUES (10, 1)")
        conn.exec_driver_sql("INSERT INTO zones(id, code, name, type, area_id) VALUES (5, '99', 'Z', 'Dimmed', 10)")
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")

    ensure_zones_processor_scope(engine)

    from sqlalchemy import inspect
    insp = inspect(engine)
    cols = [c["name"] for c in insp.get_columns("zones")]
    assert "processor_id" in cols

    # Verify backfill happened
    with engine.connect() as conn:
        row = conn.exec_driver_sql("SELECT processor_id, code FROM zones WHERE id = 5").fetchone()
        assert row[0] == 1
        assert row[1] == "99"

    # Verify composite unique index exists
    with engine.connect() as conn:
        idx = conn.exec_driver_sql("PRAGMA index_list('zones')").fetchall()
        names = {r[1] for r in idx}
        assert "uq_zones_processor_code" in names


def test_apply_zone_metadata_for_area_upsert_and_delete(tmp_path):
    db_path = tmp_path / "apply.sqlite"
    engine = create_engine(f"sqlite:///{db_path}")
    # Create a minimal floors table; the full Floor model uses JSONB (Postgres-only).
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS floors (id INTEGER PRIMARY KEY, name VARCHAR, image_path VARCHAR)"
        )

    Base.metadata.create_all(
        bind=engine,
        tables=[Processor.__table__, Area.__table__, Zone.__table__],
    )
    ensure_zones_processor_scope(engine)

    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db = SessionLocal()
    try:
        p = Processor(serial="s1")
        db.add(p)
        db.flush()

        area = Area(code="10", name="A", processor_id=p.id, floor_id=None)
        db.add(area)
        db.flush()

        # Existing zones: 1,2 (processor-scoped)
        z1 = Zone(code="1", name="Old1", type="Dimmed", area_id=area.id, processor_id=p.id)
        z2 = Zone(code="2", name="Old2", type="Switched", area_id=area.id, processor_id=p.id)
        db.add_all([z1, z2])
        db.commit()

        # Incoming: update 1, create 3, delete 2
        incoming = [
            {"code": "1", "name": "New1", "type": "Dimmed"},
            {"code": "3", "name": "New3", "type": "Shade"},
        ]
        counts = apply_zone_metadata_for_area(db=db, area=area, metadata_zones=incoming)
        db.commit()

        assert counts["deleted"] == 1
        assert counts["created"] == 1
        assert counts["updated"] == 1

        zones = db.query(Zone).filter(Zone.processor_id == p.id).order_by(Zone.code).all()
        assert [z.code for z in zones] == ["1", "3"]
        assert zones[0].name == "New1"
        assert zones[0].area_id == area.id
    finally:
        db.close()


def test_sync_zones_for_floor_updates_area_name(tmp_path, monkeypatch):
    db_path = tmp_path / "sync.sqlite"
    engine = create_engine(f"sqlite:///{db_path}")

    # Create floors table manually; Floor model uses JSONB (Postgres-only) so we avoid ORM DDL here.
    with engine.begin() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS floors (
                id INTEGER PRIMARY KEY,
                name VARCHAR NOT NULL,
                image_path VARCHAR NOT NULL,
                area_tree TEXT,
                x_left FLOAT,
                x_right FLOAT,
                y_top FLOAT,
                y_bottom FLOAT
            )
            """
        )

    Base.metadata.create_all(
        bind=engine,
        tables=[Processor.__table__, Area.__table__, Zone.__table__, FloorProcMapping.__table__],
    )
    ensure_zones_processor_scope(engine)

    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db = SessionLocal()
    try:
        # Seed floor row (raw SQL, since we didn't create Floor ORM table via metadata)
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO floors(id, name, image_path) VALUES (1, 'F1', '/x.png')"
            )

        p = Processor(serial="s1", ipv4="1.2.3.4", mac="aa", system="bb")
        db.add(p)
        db.flush()

        # Map processor to floor
        db.add(FloorProcMapping(floor_id=1, processor_id=p.id))
        db.flush()

        area = Area(code="73378", name="OLD NAME", processor_id=p.id, floor_id=1)
        db.add(area)
        db.commit()

        # --- stub processor comms ---
        class _Sock:
            def close(self):
                return None

        state = {"last_url": None}

        def fake_create_ssl_connection(*args, **kwargs):
            return _Sock()

        def fake_send_json(sock, payload):
            state["last_url"] = (payload or {}).get("Header", {}).get("Url")

        def fake_recv_json(sock):
            url = state["last_url"]
            if url == "/area/73378":
                return {"Body": {"Area": {"Name": "PASSAGE HS-33"}}}
            if url == "/area/73378/associatedzone":
                return {"Body": {"Zones": [{"href": "/zone/91617"}]}}
            return {}

        import app.crud.zone_sync as zs

        monkeypatch.setattr(zs, "create_ssl_connection", fake_create_ssl_connection)
        monkeypatch.setattr(zs, "send_json", fake_send_json)
        monkeypatch.setattr(zs, "recv_json", fake_recv_json)

        out = sync_zones_for_floor(db=db, floor_id=1)
        assert out["status"] == "success"
        assert out["areas_renamed"] == 1

        db.refresh(area)
        assert area.name == "PASSAGE HS-33"
    finally:
        db.close()

