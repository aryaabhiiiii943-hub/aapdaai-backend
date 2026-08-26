"""A plausible Khordha district inventory.

SEEDED, AND SAY SO
    No Indian state publishes live ambulance positions over an API. Pretending
    otherwise is the kind of claim that unravels in one question.

    What is real here is the SHAPE: kinds, statuses, locations, capacities.
    A genuine 108/ERSS feed replaces this function and nothing above it
    changes - which is the actual claim worth making.

Coordinates are real Bhubaneswar landmarks so the map looks like the city it
says it is. Unit names and statuses are invented.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.db import get_db

# name, kind, lat, lng, status, org
RESOURCES = [
    # --- 108 ambulances, spread across the city -----------------------------
    ("Ambulance 108-01", "ambulance", 20.2961, 85.8245, "available", "108 EMRI"),
    ("Ambulance 108-02", "ambulance", 20.3550, 85.8180, "available", "108 EMRI"),
    ("Ambulance 108-03", "ambulance", 20.2440, 85.7770, "available", "AIIMS Bhubaneswar"),
    ("Ambulance 108-04", "ambulance", 20.2700, 85.8340, "available", "Capital Hospital"),
    ("Ambulance 108-05", "ambulance", 20.2870, 85.7800, "available", "KIMS"),
    ("Ambulance 108-06", "ambulance", 20.2380, 85.8340, "deployed",  "108 EMRI"),
    ("Ambulance 108-07", "ambulance", 20.3200, 85.8400, "available", "108 EMRI"),
    ("Ambulance 108-08", "ambulance", 20.2650, 85.7900, "offline",   "108 EMRI"),

    # --- fire services -------------------------------------------------------
    ("Fire Tender Unit-8",   "fire_truck", 20.2900, 85.8320, "available", "Odisha Fire Service"),
    ("Fire Tender Patia",    "fire_truck", 20.3520, 85.8190, "available", "Odisha Fire Service"),
    ("Fire Tender Old Town", "fire_truck", 20.2400, 85.8360, "available", "Odisha Fire Service"),
    ("Fire Tender Mancheswar", "fire_truck", 20.3100, 85.8500, "deployed", "Odisha Fire Service"),

    # --- rescue --------------------------------------------------------------
    ("ODRAF Team Bhubaneswar-1", "rescue_team", 20.2870, 85.8280, "available", "ODRAF"),
    ("ODRAF Team Bhubaneswar-2", "rescue_team", 20.3300, 85.8100, "available", "ODRAF"),
    ("NDRF 3rd Bn Team A",       "rescue_team", 20.4600, 85.7400, "available", "NDRF"),
    ("NDRF 3rd Bn Team B",       "rescue_team", 20.4600, 85.7400, "deployed",  "NDRF"),
    ("Fire Service Rescue Sqd",  "rescue_team", 20.2950, 85.8300, "available", "Odisha Fire Service"),

    # --- flood-specific ------------------------------------------------------
    ("ODRAF Boat Unit 1", "boat", 20.2870, 85.8280, "available", "ODRAF"),
    ("ODRAF Boat Unit 2", "boat", 20.3400, 85.8050, "available", "ODRAF"),
    ("NDRF Boat Team",    "boat", 20.4600, 85.7400, "available", "NDRF"),

    # --- collapse / structural ----------------------------------------------
    ("NDRF Heavy Rescue A", "heavy_rescue", 20.4600, 85.7400, "available", "NDRF"),
    ("ODRAF Cutting Unit",  "heavy_rescue", 20.2870, 85.8280, "available", "ODRAF"),

    # --- relief supply -------------------------------------------------------
    # Somebody has to bring the water. Easy to forget in an inventory built
    # around rescue, and the first thing missing when the need is a shortage
    # rather than an emergency.
    ("Relief Truck BBSR-1", "supply_vehicle", 20.2700, 85.8400, "available", "Khordha DDMA"),
    ("Relief Truck BBSR-2", "supply_vehicle", 20.3350, 85.8150, "available", "Khordha DDMA"),
    ("Water Tanker WT-04",  "supply_vehicle", 20.2950, 85.8250, "available", "BMC"),
    ("Water Tanker WT-07",  "supply_vehicle", 20.3450, 85.8220, "deployed",  "BMC"),
]

# name, kind, lat, lng, capacity, occupancy
FACILITIES = [
    ("AIIMS Bhubaneswar",        "hospital", 20.2440, 85.7770, 960, 720),
    ("Capital Hospital",         "hospital", 20.2700, 85.8340, 500, 410),
    ("KIMS Hospital",            "hospital", 20.2870, 85.7800, 750, 600),
    ("SUM Ultimate Medicare",    "hospital", 20.2830, 85.7760, 400, 250),
    ("Govt High School Patia",   "shelter",  20.3559, 85.8195, 300,  40),
    ("Unit-9 Community Centre",  "shelter",  20.2930, 85.8290, 200,   0),
    ("Old Town Cyclone Shelter", "shelter",  20.2390, 85.8350, 500, 180),
    ("Chandrasekharpur Shelter", "shelter",  20.3300, 85.8090, 250,   0),
]

# lat, lng, reason
ROAD_BLOCKS = [
    (20.3480, 85.8210, "Waterlogged underpass - impassable"),
    (20.2620, 85.8300, "Tree down across carriageway"),
]


def seed(force: bool = False) -> dict:
    """Fill the inventory. Idempotent unless `force`."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        existing = conn.execute(
            "SELECT COUNT(*) AS n FROM resources").fetchone()["n"]
        if existing and not force:
            return {"seeded": False, "reason": f"{existing} resources already"}

        if force:
            for table in ("assignments", "resources", "facilities", "road_blocks"):
                conn.execute(f"DELETE FROM {table}")

        for name, kind, lat, lng, status, org in RESOURCES:
            conn.execute(
                "INSERT INTO resources "
                "(name, kind, lat, lng, status, org, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (name, kind, lat, lng, status, org, now))

        for name, kind, lat, lng, cap, occ in FACILITIES:
            conn.execute(
                "INSERT INTO facilities "
                "(name, kind, lat, lng, capacity, occupancy) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (name, kind, lat, lng, cap, occ))

        for lat, lng, reason in ROAD_BLOCKS:
            conn.execute(
                "INSERT INTO road_blocks (lat, lng, reason, active, reported_at) "
                "VALUES (?, ?, ?, TRUE, ?)", (lat, lng, reason, now))

    return {"seeded": True, "resources": len(RESOURCES),
            "facilities": len(FACILITIES), "road_blocks": len(ROAD_BLOCKS)}
