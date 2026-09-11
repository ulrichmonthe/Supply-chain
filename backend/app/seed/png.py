"""Papua New Guinea reference dataset.

**What is real and what is not, stated plainly, because the tool's whole argument is
that provenance must travel with the number.**

REAL:  facility locations. Every coordinate is a town, station or hospital location
       in Papua New Guinea, accurate to the settlement. Provinces and districts are
       real. The structure of the network is real: five Area Medical Stores, no road
       link between Port Moresby and the rest of the country, the Highlands Highway
       out of Lae, coastal shipping to the islands, air charter to the interior.

ILLUSTRATIVE:  demand, storage capacity, costs, vessel schedules and vessel
       capacities. Every demand row is marked ``proxy`` and every cost is an
       assumption. These are placeholders with plausible magnitudes, and the
       application says so on every screen that shows them.

The build plan is explicit that the real versions of these come from the National
Department of Health facility master list, mSupply consumption data, the cold chain
inventory, and — the one that decides whether the signature feature has any data at
all — the actual boat and flight schedules serving the AMS catchments. Until those
arrive this dataset exists to prove the machinery, not to advise anybody.
"""

from __future__ import annotations

COUNTRY = {
    "code": "PNG",
    "name": "Papua New Guinea",
    "currency": "PGK",
    "config": {
        "level_labels": {
            0: "National Medical Store",
            1: "Area Medical Store",
            2: "Provincial store",
            3: "Health facility",
        },
        "bbox": {"min_lat": -11.9, "max_lat": -1.0, "min_lon": 140.5, "max_lon": 160.2},
        "center": {"lat": -6.4, "lon": 147.5, "zoom": 5.2},
        "seasons": {
            "wet_northwest_monsoon": [12, 1, 2, 3],
            "transition": [4, 11],
            "dry_southeast_trades": [5, 6, 7, 8, 9, 10],
        },
        "temperature_bands": ["ambient", "+2-8", "-20", "-70"],
        "equity_definition": (
            "Population-weighted quintiles of a structural vulnerability index built from "
            "remoteness, terrain, months of restricted access and transport mode dependency."
        ),
        "national_buffer_stock_days": 14,
        "capex_amortisation_years": 10,
        # What it is worth, per cubic metre, to avoid failing to supply a facility.
        # Set above the most expensive lane in the network (air charter, ~3,100) so
        # that under the standing policy the model always prefers to deliver. A
        # scenario that wants to ask "what would pure cost minimisation do?" lowers
        # its service weight, which scales this down.
        "unmet_penalty_per_m3": 6000.0,
        "data_provenance": {
            "status": "ILLUSTRATIVE",
            "real": "Facility locations, provinces, network structure, transport modes.",
            "illustrative": "Demand, storage capacity, costs, vessel and aircraft schedules.",
            "before_use": (
                "Replace with the NDoH facility master list, mSupply consumption data, the "
                "national cold chain inventory, and the real coastal shipping and air "
                "charter timetables."
            ),
        },
    },
}

# --- hubs -------------------------------------------------------------------------------
# (code, name, level, lat, lon, admin1, dry_m3, cold_2_8, throughput_m3, fixed_cost,
#  operating_status, open_capex)
HUBS: list[tuple] = [
    ("NMS-BADILI", "Badili National Medical Store", 0, -9.4830, 147.1730, "National Capital District",
     9800.0, 220.0, 60000.0, 2_450_000.0, "operational", 0.0),
    ("AMS-POM", "Port Moresby Area Medical Store", 1, -9.4520, 147.2100, "National Capital District",
     2600.0, 90.0, 11000.0, 1_180_000.0, "operational", 0.0),
    ("AMS-LAE", "Lae Area Medical Store", 1, -6.7250, 146.9900, "Morobe",
     2900.0, 105.0, 13500.0, 1_240_000.0, "operational", 0.0),
    ("AMS-MAD", "Madang Area Medical Store", 1, -5.2200, 145.7900, "Madang",
     1700.0, 62.0, 7200.0, 940_000.0, "operational", 0.0),
    ("AMS-HGN", "Mount Hagen Area Medical Store", 1, -5.8500, 144.2400, "Western Highlands",
     2100.0, 78.0, 9400.0, 1_060_000.0, "operational", 0.0),
    ("AMS-KOK", "Kokopo Area Medical Store", 1, -4.3450, 152.2600, "East New Britain",
     1500.0, 55.0, 6300.0, 880_000.0, "operational", 0.0),
    # Candidate hubs. Closed in the baseline; a scenario can open them.
    ("AMS-WWK", "Wewak Area Medical Store (proposed)", 1, -3.5670, 143.6600, "East Sepik",
     1200.0, 48.0, 5200.0, 790_000.0, "planned", 1_850_000.0),
    ("AMS-ALO", "Alotau Area Medical Store (proposed)", 1, -10.3150, 150.4550, "Milne Bay",
     900.0, 38.0, 3800.0, 640_000.0, "planned", 1_250_000.0),
    ("AMS-BUK", "Buka Area Medical Store (proposed)", 1, -5.4250, 154.6700, "Bougainville",
     850.0, 36.0, 3400.0, 610_000.0, "planned", 1_400_000.0),
]

# --- facilities --------------------------------------------------------------------------
# (code, name, type, admin1, admin2, lat, lon, terrain_class, catchment_population,
#  primary_hub, primary_mode, access_profile, service_key or None)
#
# `service_key` names a scheduled service defined in SERVICES below. A facility with no
# service key is served on demand by road.
FACILITIES: list[tuple] = [
    # --- National Capital District ---
    ("PNG-NCD-001", "Port Moresby General Hospital", "provincial_hospital", "National Capital District", "Moresby North East", -9.4747, 147.1925, "mainland_road", 168000, "AMS-POM", "road", "all_weather", None),
    ("PNG-NCD-002", "Gerehu District Hospital", "district_hospital", "National Capital District", "Moresby North West", -9.3892, 147.1444, "mainland_road", 132000, "AMS-POM", "road", "all_weather", None),
    ("PNG-NCD-003", "Six Mile Clinic", "health_centre", "National Capital District", "Moresby North East", -9.4500, 147.2200, "mainland_road", 61000, "AMS-POM", "road", "all_weather", None),
    ("PNG-NCD-004", "Hohola Urban Clinic", "health_centre", "National Capital District", "Moresby North West", -9.4450, 147.1750, "mainland_road", 54000, "AMS-POM", "road", "all_weather", None),
    ("PNG-NCD-005", "Bomana Health Centre", "health_centre", "National Capital District", "Moresby North West", -9.3800, 147.2300, "mainland_road", 38000, "AMS-POM", "road", "all_weather", None),

    # --- Central ---
    ("PNG-CEN-001", "Kwikila Health Centre", "district_hospital", "Central", "Rigo", -9.8322, 147.7331, "coastal_road", 47000, "AMS-POM", "road", "coastal_road", None),
    ("PNG-CEN-002", "Bereina Health Centre", "district_hospital", "Central", "Kairuku", -8.6500, 146.5000, "coastal_road", 42000, "AMS-POM", "road", "lowland_floodplain", None),
    ("PNG-CEN-003", "Tapini Health Centre", "health_centre", "Central", "Goilala", -8.3500, 146.9833, "highlands_road", 19000, "AMS-POM", "road", "highlands_unsealed", None),
    ("PNG-CEN-004", "Kupiano Health Centre", "district_hospital", "Central", "Abau", -10.0667, 148.1833, "coastal_road", 38000, "AMS-POM", "road", "coastal_road", None),
    ("PNG-CEN-005", "Woitape Health Centre", "health_centre", "Central", "Goilala", -8.5500, 147.2500, "remote_air_only", 12000, "AMS-POM", "air", "air_highlands", "AIR-GOILALA"),
    ("PNG-CEN-006", "Hisiu Health Centre", "health_centre", "Central", "Kairuku", -8.9000, 146.6500, "coastal_road", 16000, "AMS-POM", "road", "lowland_floodplain", None),

    # --- Gulf ---
    ("PNG-GUL-001", "Kerema General Hospital", "provincial_hospital", "Gulf", "Kerema", -7.9633, 145.7783, "coastal_road", 58000, "AMS-POM", "road", "coastal_road", None),
    ("PNG-GUL-002", "Ihu Health Centre", "health_centre", "Gulf", "Kikori", -7.8833, 145.4000, "riverine", 21000, "AMS-POM", "sea", "sea_exposed", "SEA-GULF"),
    ("PNG-GUL-003", "Kikori Health Centre", "district_hospital", "Gulf", "Kikori", -7.4200, 144.2500, "riverine", 32000, "AMS-POM", "sea", "sea_exposed", "SEA-GULF"),
    ("PNG-GUL-004", "Baimuru Health Centre", "health_centre", "Gulf", "Kikori", -7.5000, 144.8167, "riverine", 18000, "AMS-POM", "sea", "sea_exposed", "SEA-GULF"),
    ("PNG-GUL-005", "Malalaua Health Centre", "health_centre", "Gulf", "Kerema", -8.0833, 146.1667, "coastal_road", 24000, "AMS-POM", "road", "lowland_floodplain", None),
    ("PNG-GUL-006", "Kaintiba Health Centre", "health_centre", "Gulf", "Kerema", -7.4667, 146.0333, "remote_air_only", 11000, "AMS-POM", "air", "air_highlands", "AIR-GULF"),

    # --- Western ---
    ("PNG-WES-001", "Daru General Hospital", "provincial_hospital", "Western", "South Fly", -9.0763, 143.2092, "island", 44000, "AMS-POM", "sea", "sea_exposed", "SEA-WESTERN"),
    ("PNG-WES-002", "Kiunga District Hospital", "district_hospital", "Western", "Middle Fly", -6.1219, 141.2917, "riverine", 39000, "AMS-POM", "river", "river_navigable", "RIV-FLY"),
    ("PNG-WES-003", "Tabubil Hospital", "district_hospital", "Western", "North Fly", -5.2667, 141.2258, "riverine", 28000, "AMS-POM", "river", "river_navigable", "RIV-FLY"),
    ("PNG-WES-004", "Balimo District Hospital", "district_hospital", "Western", "Middle Fly", -8.0500, 142.9333, "riverine", 31000, "AMS-POM", "river", "river_navigable", "RIV-FLY"),
    ("PNG-WES-005", "Morehead Health Centre", "health_centre", "Western", "South Fly", -8.7167, 141.6333, "remote_air_only", 9000, "AMS-POM", "air", "air_all_year", "AIR-WESTERN"),
    ("PNG-WES-006", "Lake Murray Health Centre", "health_centre", "Western", "Middle Fly", -7.0000, 141.5000, "remote_air_only", 8500, "AMS-POM", "air", "air_all_year", "AIR-WESTERN"),
    ("PNG-WES-007", "Suki Health Centre", "health_centre", "Western", "South Fly", -8.0500, 141.7833, "riverine", 7500, "AMS-POM", "river", "river_navigable", "RIV-FLY"),

    # --- Milne Bay ---
    ("PNG-MBP-001", "Alotau Provincial Hospital", "provincial_hospital", "Milne Bay", "Alotau", -10.3167, 150.4500, "coastal_road", 62000, "AMS-POM", "sea", "sea_exposed", "SEA-MILNEBAY"),
    ("PNG-MBP-002", "Losuia Health Centre", "district_hospital", "Milne Bay", "Kiriwina-Goodenough", -8.5050, 151.0800, "island", 29000, "AMS-POM", "sea", "sea_exposed", "SEA-LOUISIADE"),
    ("PNG-MBP-003", "Bwagaoia Health Centre", "district_hospital", "Milne Bay", "Samarai-Murua", -10.6833, 152.7500, "island", 21000, "AMS-POM", "sea", "sea_exposed", "SEA-LOUISIADE"),
    ("PNG-MBP-004", "Esa'ala Health Centre", "health_centre", "Milne Bay", "Esa'ala", -9.9000, 150.8000, "island", 24000, "AMS-POM", "sea", "sea_exposed", "SEA-LOUISIADE"),
    ("PNG-MBP-005", "Samarai Health Centre", "health_centre", "Milne Bay", "Samarai-Murua", -10.6167, 150.6667, "island", 14000, "AMS-POM", "sea", "sea_exposed", "SEA-LOUISIADE"),
    ("PNG-MBP-006", "Rabaraba Health Centre", "health_centre", "Milne Bay", "Alotau", -9.9833, 149.8333, "coastal_road", 17000, "AMS-POM", "sea", "sea_exposed", "SEA-MILNEBAY"),
    ("PNG-MBP-007", "Bolubolu Health Centre", "health_centre", "Milne Bay", "Kiriwina-Goodenough", -9.3500, 150.2500, "island", 15000, "AMS-POM", "sea", "sea_exposed", "SEA-LOUISIADE"),

    # --- Oro (Northern) ---
    ("PNG-ORO-001", "Popondetta General Hospital", "provincial_hospital", "Oro", "Ijivitari", -8.7656, 148.2333, "coastal_road", 71000, "AMS-LAE", "sea", "sea_exposed", "SEA-ORO"),
    ("PNG-ORO-002", "Kokoda Health Centre", "health_centre", "Oro", "Sohe", -8.8783, 147.7350, "highlands_road", 22000, "AMS-LAE", "sea", "sea_exposed", "SEA-ORO"),
    ("PNG-ORO-003", "Tufi Health Centre", "health_centre", "Oro", "Ijivitari", -9.0833, 149.3167, "coastal_road", 13000, "AMS-LAE", "sea", "sea_exposed", "SEA-ORO"),
    ("PNG-ORO-004", "Afore Health Centre", "health_centre", "Oro", "Ijivitari", -9.2000, 148.4000, "remote_air_only", 10000, "AMS-LAE", "air", "air_highlands", "AIR-ORO"),
    ("PNG-ORO-005", "Wanigela Health Centre", "health_centre", "Oro", "Ijivitari", -9.3333, 149.1667, "coastal_road", 11000, "AMS-LAE", "sea", "sea_exposed", "SEA-ORO"),
    ("PNG-ORO-006", "Saiho Health Centre", "health_centre", "Oro", "Sohe", -8.8000, 147.9000, "highlands_road", 14000, "AMS-LAE", "sea", "sea_exposed", "SEA-ORO"),

    # --- Morobe ---
    ("PNG-MOR-001", "Angau Memorial Hospital", "provincial_hospital", "Morobe", "Lae", -6.7300, 146.9950, "mainland_road", 188000, "AMS-LAE", "road", "all_weather", None),
    ("PNG-MOR-002", "Bulolo District Hospital", "district_hospital", "Morobe", "Bulolo", -7.2000, 146.6500, "highlands_road", 54000, "AMS-LAE", "road", "highlands_sealed", None),
    ("PNG-MOR-003", "Wau Health Centre", "health_centre", "Morobe", "Bulolo", -7.3333, 146.7167, "highlands_road", 31000, "AMS-LAE", "road", "highlands_unsealed", None),
    ("PNG-MOR-004", "Braun Memorial Hospital", "district_hospital", "Morobe", "Finschhafen", -6.6000, 147.8500, "coastal_road", 42000, "AMS-LAE", "sea", "sea_exposed", "SEA-HUON"),
    ("PNG-MOR-005", "Menyamya Health Centre", "health_centre", "Morobe", "Menyamya", -7.2000, 146.0167, "highlands_road", 36000, "AMS-LAE", "road", "highlands_unsealed", None),
    ("PNG-MOR-006", "Kabwum Health Centre", "health_centre", "Morobe", "Kabwum", -6.1833, 147.2000, "remote_air_only", 19000, "AMS-LAE", "air", "air_highlands", "AIR-HUON"),
    ("PNG-MOR-007", "Wasu Health Centre", "health_centre", "Morobe", "Kabwum", -5.9667, 147.1833, "coastal_road", 17000, "AMS-LAE", "sea", "sea_exposed", "SEA-HUON"),
    ("PNG-MOR-008", "Mumeng Health Centre", "health_centre", "Morobe", "Bulolo", -7.0333, 146.5667, "highlands_road", 23000, "AMS-LAE", "road", "highlands_sealed", None),

    # --- Madang ---
    ("PNG-MAD-001", "Modilon General Hospital", "provincial_hospital", "Madang", "Madang", -5.2246, 145.7981, "mainland_road", 96000, "AMS-MAD", "road", "all_weather", None),
    ("PNG-MAD-002", "Bogia Health Centre", "district_hospital", "Madang", "Bogia", -4.2667, 144.9667, "coastal_road", 41000, "AMS-MAD", "road", "lowland_floodplain", None),
    ("PNG-MAD-003", "Saidor Health Centre", "health_centre", "Madang", "Rai Coast", -5.6167, 146.4667, "coastal_road", 26000, "AMS-MAD", "sea", "sea_exposed", "SEA-RAICOAST"),
    ("PNG-MAD-004", "Kinim Health Centre", "health_centre", "Madang", "Sumkar", -4.6500, 145.9667, "island", 22000, "AMS-MAD", "sea", "sea_sheltered", "SEA-RAICOAST"),
    ("PNG-MAD-005", "Usino Health Centre", "health_centre", "Madang", "Usino-Bundi", -5.9000, 145.7500, "highlands_road", 28000, "AMS-MAD", "road", "lowland_floodplain", None),
    ("PNG-MAD-006", "Josephstaal Health Centre", "health_centre", "Madang", "Middle Ramu", -4.7500, 145.0333, "remote_air_only", 12000, "AMS-MAD", "air", "air_all_year", "AIR-RAMU"),
    ("PNG-MAD-007", "Simbai Health Centre", "health_centre", "Madang", "Middle Ramu", -5.2833, 144.5333, "remote_air_only", 14000, "AMS-MAD", "air", "air_highlands", "AIR-RAMU"),

    # --- East Sepik ---
    ("PNG-ESP-001", "Boram General Hospital", "provincial_hospital", "East Sepik", "Wewak", -3.5670, 143.6700, "coastal_road", 88000, "AMS-MAD", "sea", "sea_exposed", "SEA-SEPIK"),
    ("PNG-ESP-002", "Maprik District Hospital", "district_hospital", "East Sepik", "Maprik", -3.6333, 143.0500, "mainland_road", 52000, "AMS-MAD", "sea", "sea_exposed", "SEA-SEPIK"),
    ("PNG-ESP-003", "Angoram Health Centre", "district_hospital", "East Sepik", "Angoram", -4.0667, 144.0667, "riverine", 44000, "AMS-MAD", "sea", "sea_exposed", "SEA-SEPIK"),
    ("PNG-ESP-004", "Ambunti Health Centre", "health_centre", "East Sepik", "Ambunti-Drekikier", -4.2333, 142.8500, "riverine", 24000, "AMS-MAD", "river", "river_navigable", "RIV-SEPIK"),
    ("PNG-ESP-005", "Yangoru Health Centre", "health_centre", "East Sepik", "Yangoru-Saussia", -3.7333, 143.3167, "mainland_road", 33000, "AMS-MAD", "sea", "sea_exposed", "SEA-SEPIK"),
    ("PNG-ESP-006", "Kunjingini Health Centre", "health_centre", "East Sepik", "Wosera-Gawi", -3.9000, 143.1000, "mainland_road", 27000, "AMS-MAD", "sea", "sea_exposed", "SEA-SEPIK"),
    ("PNG-ESP-007", "Timbunke Health Centre", "health_centre", "East Sepik", "Angoram", -4.1833, 143.5333, "riverine", 15000, "AMS-MAD", "river", "river_navigable", "RIV-SEPIK"),

    # --- Sandaun (West Sepik) ---
    ("PNG-SAN-001", "Vanimo General Hospital", "provincial_hospital", "Sandaun", "Vanimo-Green", -2.6833, 141.3000, "coastal_road", 47000, "AMS-MAD", "sea", "sea_exposed", "SEA-SEPIK"),
    ("PNG-SAN-002", "Aitape District Hospital", "district_hospital", "Sandaun", "Aitape-Lumi", -3.1372, 142.3486, "coastal_road", 39000, "AMS-MAD", "sea", "sea_exposed", "SEA-SEPIK"),
    ("PNG-SAN-003", "Lumi Health Centre", "health_centre", "Sandaun", "Aitape-Lumi", -3.4833, 142.0333, "highlands_road", 24000, "AMS-MAD", "sea", "sea_exposed", "SEA-SEPIK"),
    ("PNG-SAN-004", "Nuku Health Centre", "health_centre", "Sandaun", "Nuku", -3.6667, 142.4833, "highlands_road", 22000, "AMS-MAD", "sea", "sea_exposed", "SEA-SEPIK"),
    ("PNG-SAN-005", "Telefomin Health Centre", "health_centre", "Sandaun", "Telefomin", -5.1167, 141.6333, "remote_air_only", 18000, "AMS-MAD", "air", "air_highlands", "AIR-SANDAUN"),
    ("PNG-SAN-006", "Amanab Health Centre", "health_centre", "Sandaun", "Vanimo-Green", -3.5833, 141.2167, "remote_air_only", 13000, "AMS-MAD", "air", "air_all_year", "AIR-SANDAUN"),
    ("PNG-SAN-007", "Oksapmin Health Centre", "health_centre", "Sandaun", "Telefomin", -5.2333, 142.2167, "remote_air_only", 11000, "AMS-MAD", "air", "air_highlands", "AIR-SANDAUN"),

    # --- Enga ---
    ("PNG-ENG-001", "Wabag General Hospital", "provincial_hospital", "Enga", "Wabag", -5.4900, 143.7200, "highlands_road", 72000, "AMS-HGN", "road", "highlands_sealed", None),
    ("PNG-ENG-002", "Wapenamanda Health Centre", "district_hospital", "Enga", "Wapenamanda", -5.6333, 143.8833, "highlands_road", 48000, "AMS-HGN", "road", "highlands_sealed", None),
    ("PNG-ENG-003", "Laiagam Health Centre", "health_centre", "Enga", "Lagaip-Porgera", -5.4167, 143.5000, "highlands_road", 38000, "AMS-HGN", "road", "highlands_unsealed", None),
    ("PNG-ENG-004", "Paiam Hospital", "district_hospital", "Enga", "Lagaip-Porgera", -5.4667, 143.1333, "highlands_road", 41000, "AMS-HGN", "road", "highlands_unsealed", None),
    ("PNG-ENG-005", "Kompiam District Hospital", "district_hospital", "Enga", "Kompiam-Ambum", -5.3667, 143.9000, "highlands_road", 29000, "AMS-HGN", "road", "highlands_unsealed", None),
    ("PNG-ENG-006", "Kandep Health Centre", "health_centre", "Enga", "Kandep", -5.8333, 143.5167, "highlands_road", 32000, "AMS-HGN", "road", "highlands_unsealed", None),

    # --- Hela ---
    ("PNG-HEL-001", "Tari Hospital", "provincial_hospital", "Hela", "Tari-Pori", -5.8500, 142.9500, "highlands_road", 84000, "AMS-HGN", "road", "highlands_unsealed", None),
    ("PNG-HEL-002", "Koroba Health Centre", "district_hospital", "Hela", "Koroba-Lake Kopiago", -5.6833, 142.7333, "highlands_road", 43000, "AMS-HGN", "road", "highlands_unsealed", None),
    ("PNG-HEL-003", "Komo Health Centre", "health_centre", "Hela", "Komo-Magarima", -6.0333, 142.8500, "highlands_road", 31000, "AMS-HGN", "road", "highlands_unsealed", None),
    ("PNG-HEL-004", "Margarima Health Centre", "health_centre", "Hela", "Komo-Magarima", -5.9833, 143.2000, "highlands_road", 27000, "AMS-HGN", "road", "highlands_unsealed", None),
    ("PNG-HEL-005", "Lake Kopiago Health Centre", "health_centre", "Hela", "Koroba-Lake Kopiago", -5.3833, 142.4833, "remote_air_only", 16000, "AMS-HGN", "air", "air_highlands", "AIR-HELA"),

    # --- Southern Highlands ---
    ("PNG-SHP-001", "Mendi General Hospital", "provincial_hospital", "Southern Highlands", "Mendi-Munihu", -6.1478, 143.6572, "highlands_road", 79000, "AMS-HGN", "road", "highlands_sealed", None),
    ("PNG-SHP-002", "Ialibu District Hospital", "district_hospital", "Southern Highlands", "Imbonggu", -6.2833, 143.9833, "highlands_road", 44000, "AMS-HGN", "road", "highlands_sealed", None),
    ("PNG-SHP-003", "Kagua Health Centre", "health_centre", "Southern Highlands", "Kagua-Erave", -6.3833, 143.8500, "highlands_road", 36000, "AMS-HGN", "road", "highlands_unsealed", None),
    ("PNG-SHP-004", "Nipa Health Centre", "district_hospital", "Southern Highlands", "Nipa-Kutubu", -6.1667, 143.4333, "highlands_road", 39000, "AMS-HGN", "road", "highlands_unsealed", None),
    ("PNG-SHP-005", "Erave Health Centre", "health_centre", "Southern Highlands", "Kagua-Erave", -6.6000, 143.9000, "highlands_road", 23000, "AMS-HGN", "road", "highlands_unsealed", None),
    ("PNG-SHP-006", "Pangia Health Centre", "health_centre", "Southern Highlands", "Ialibu-Pangia", -6.3333, 144.0833, "highlands_road", 28000, "AMS-HGN", "road", "highlands_unsealed", None),

    # --- Western Highlands ---
    ("PNG-WHP-001", "Mount Hagen Provincial Hospital", "provincial_hospital", "Western Highlands", "Mount Hagen", -5.8575, 144.2300, "highlands_road", 112000, "AMS-HGN", "road", "all_weather", None),
    ("PNG-WHP-002", "Tambul Health Centre", "health_centre", "Western Highlands", "Tambul-Nebilyer", -5.8833, 143.9500, "highlands_road", 34000, "AMS-HGN", "road", "highlands_unsealed", None),
    ("PNG-WHP-003", "Baiyer River Health Centre", "health_centre", "Western Highlands", "Mul-Baiyer", -5.5333, 144.1500, "highlands_road", 31000, "AMS-HGN", "road", "highlands_unsealed", None),
    ("PNG-WHP-004", "Kotna Health Centre", "health_centre", "Western Highlands", "Mul-Baiyer", -5.7333, 144.1000, "highlands_road", 22000, "AMS-HGN", "road", "highlands_unsealed", None),
    ("PNG-WHP-005", "Dei Health Centre", "health_centre", "Western Highlands", "Dei", -5.7000, 144.2500, "highlands_road", 26000, "AMS-HGN", "road", "highlands_sealed", None),

    # --- Jiwaka ---
    ("PNG-JIW-001", "Minj District Hospital", "district_hospital", "Jiwaka", "Anglimp-South Waghi", -5.8833, 144.6667, "highlands_road", 58000, "AMS-HGN", "road", "highlands_sealed", None),
    ("PNG-JIW-002", "Banz Health Centre", "health_centre", "Jiwaka", "Anglimp-South Waghi", -5.8000, 144.6167, "highlands_road", 41000, "AMS-HGN", "road", "highlands_sealed", None),
    ("PNG-JIW-003", "Kudjip Nazarene Hospital", "district_hospital", "Jiwaka", "Jimi", -5.8667, 144.5833, "highlands_road", 47000, "AMS-HGN", "road", "highlands_sealed", None),
    ("PNG-JIW-004", "Nondugl Health Centre", "health_centre", "Jiwaka", "Anglimp-South Waghi", -5.8667, 144.7500, "highlands_road", 24000, "AMS-HGN", "road", "highlands_unsealed", None),
    ("PNG-JIW-005", "Tabibuga Health Centre", "health_centre", "Jiwaka", "Jimi", -5.5667, 144.6333, "remote_air_only", 19000, "AMS-HGN", "air", "air_highlands", "AIR-JIWAKA"),

    # --- Chimbu (Simbu) ---
    ("PNG-CHM-001", "Kundiawa General Hospital", "provincial_hospital", "Chimbu", "Kundiawa-Gembogl", -6.0167, 144.9667, "highlands_road", 74000, "AMS-HGN", "road", "highlands_sealed", None),
    ("PNG-CHM-002", "Kerowagi Health Centre", "district_hospital", "Chimbu", "Kerowagi", -5.9833, 144.8000, "highlands_road", 46000, "AMS-HGN", "road", "highlands_sealed", None),
    ("PNG-CHM-003", "Gumine Health Centre", "health_centre", "Chimbu", "Gumine", -6.2500, 144.8167, "highlands_road", 33000, "AMS-HGN", "road", "highlands_unsealed", None),
    ("PNG-CHM-004", "Karimui Health Centre", "health_centre", "Chimbu", "Karimui-Nomane", -6.4833, 144.8167, "remote_air_only", 21000, "AMS-HGN", "air", "air_highlands", "AIR-CHIMBU"),
    ("PNG-CHM-005", "Chuave Health Centre", "health_centre", "Chimbu", "Chuave", -6.1333, 145.1167, "highlands_road", 29000, "AMS-HGN", "road", "highlands_sealed", None),
    ("PNG-CHM-006", "Gembogl Health Centre", "health_centre", "Chimbu", "Kundiawa-Gembogl", -5.8500, 145.0333, "highlands_road", 25000, "AMS-HGN", "road", "highlands_unsealed", None),

    # --- Eastern Highlands ---
    ("PNG-EHP-001", "Goroka Provincial Hospital", "provincial_hospital", "Eastern Highlands", "Goroka", -6.0833, 145.3833, "highlands_road", 108000, "AMS-LAE", "road", "highlands_sealed", None),
    ("PNG-EHP-002", "Kainantu District Hospital", "district_hospital", "Eastern Highlands", "Kainantu", -6.2833, 145.8667, "highlands_road", 62000, "AMS-LAE", "road", "highlands_sealed", None),
    ("PNG-EHP-003", "Henganofi Health Centre", "health_centre", "Eastern Highlands", "Henganofi", -6.2333, 145.6167, "highlands_road", 34000, "AMS-LAE", "road", "highlands_unsealed", None),
    ("PNG-EHP-004", "Lufa Health Centre", "health_centre", "Eastern Highlands", "Lufa", -6.3167, 145.2000, "highlands_road", 31000, "AMS-LAE", "road", "highlands_unsealed", None),
    ("PNG-EHP-005", "Okapa Health Centre", "health_centre", "Eastern Highlands", "Okapa", -6.5333, 145.5167, "highlands_road", 28000, "AMS-LAE", "road", "highlands_unsealed", None),
    ("PNG-EHP-006", "Wonenara Health Centre", "health_centre", "Eastern Highlands", "Obura-Wonenara", -6.7833, 145.9000, "remote_air_only", 13000, "AMS-LAE", "air", "air_highlands", "AIR-EHP"),
    ("PNG-EHP-007", "Marawaka Health Centre", "health_centre", "Eastern Highlands", "Obura-Wonenara", -6.9667, 145.8333, "remote_air_only", 11000, "AMS-LAE", "air", "air_highlands", "AIR-EHP"),

    # --- Manus ---
    ("PNG-MAN-001", "Lorengau General Hospital", "provincial_hospital", "Manus", "Manus", -2.0226, 147.2712, "island", 34000, "AMS-MAD", "sea", "sea_exposed", "SEA-MANUS"),
    ("PNG-MAN-002", "Lombrum Health Centre", "health_centre", "Manus", "Manus", -2.0333, 147.3667, "island", 9000, "AMS-MAD", "sea", "sea_exposed", "SEA-MANUS"),
    ("PNG-MAN-003", "Bipi Island Health Centre", "health_centre", "Manus", "Manus", -1.7167, 146.3000, "island", 6500, "AMS-MAD", "sea", "sea_exposed", "SEA-MANUS"),
    ("PNG-MAN-004", "Rambutyo Health Centre", "health_centre", "Manus", "Manus", -2.3167, 147.8333, "island", 5500, "AMS-MAD", "sea", "sea_exposed", "SEA-MANUS"),
    ("PNG-MAN-005", "Baluan Health Centre", "health_centre", "Manus", "Manus", -2.5667, 147.2833, "island", 5000, "AMS-MAD", "sea", "sea_exposed", "SEA-MANUS"),

    # --- New Ireland ---
    ("PNG-NIP-001", "Kavieng General Hospital", "provincial_hospital", "New Ireland", "Kavieng", -2.5744, 150.7967, "island", 58000, "AMS-KOK", "sea", "sea_sheltered", "SEA-NEWIRELAND"),
    ("PNG-NIP-002", "Namatanai District Hospital", "district_hospital", "New Ireland", "Namatanai", -3.6667, 152.4333, "island", 46000, "AMS-KOK", "sea", "sea_sheltered", "SEA-NEWIRELAND"),
    ("PNG-NIP-003", "Londolovit Health Centre", "health_centre", "New Ireland", "Namatanai", -3.0500, 152.6333, "island", 19000, "AMS-KOK", "sea", "sea_exposed", "SEA-NEWIRELAND"),
    ("PNG-NIP-004", "Konos Health Centre", "health_centre", "New Ireland", "Namatanai", -3.1667, 151.7167, "island", 17000, "AMS-KOK", "sea", "sea_sheltered", "SEA-NEWIRELAND"),
    ("PNG-NIP-005", "Tabar Health Centre", "health_centre", "New Ireland", "Namatanai", -2.8500, 152.0333, "island", 8000, "AMS-KOK", "sea", "sea_exposed", "SEA-NEWIRELAND"),
    ("PNG-NIP-006", "Lavongai Health Centre", "health_centre", "New Ireland", "Kavieng", -2.5667, 150.2000, "island", 14000, "AMS-KOK", "sea", "sea_exposed", "SEA-NEWIRELAND"),

    # --- East New Britain ---
    ("PNG-ENB-001", "Nonga Base Hospital", "provincial_hospital", "East New Britain", "Rabaul", -4.1833, 152.1500, "coastal_road", 92000, "AMS-KOK", "road", "coastal_road", None),
    ("PNG-ENB-002", "Kokopo Health Centre", "district_hospital", "East New Britain", "Kokopo", -4.3500, 152.2667, "coastal_road", 61000, "AMS-KOK", "road", "all_weather", None),
    ("PNG-ENB-003", "Kerevat Health Centre", "health_centre", "East New Britain", "Gazelle", -4.3333, 152.0333, "coastal_road", 38000, "AMS-KOK", "road", "coastal_road", None),
    ("PNG-ENB-004", "Sikut Health Centre", "health_centre", "East New Britain", "Gazelle", -4.4667, 152.2833, "coastal_road", 21000, "AMS-KOK", "road", "coastal_road", None),
    ("PNG-ENB-005", "Palmalmal Health Centre", "health_centre", "East New Britain", "Pomio", -5.9000, 151.4000, "coastal_road", 23000, "AMS-KOK", "sea", "sea_exposed", "SEA-POMIO"),
    ("PNG-ENB-006", "Pomio Health Centre", "health_centre", "East New Britain", "Pomio", -5.5333, 151.5167, "coastal_road", 18000, "AMS-KOK", "sea", "sea_exposed", "SEA-POMIO"),

    # --- West New Britain ---
    ("PNG-WNB-001", "Kimbe Provincial Hospital", "provincial_hospital", "West New Britain", "Talasea", -5.5500, 150.1500, "coastal_road", 78000, "AMS-KOK", "sea", "sea_sheltered", "SEA-WNB"),
    ("PNG-WNB-002", "Talasea Health Centre", "health_centre", "West New Britain", "Talasea", -5.2833, 150.0167, "coastal_road", 32000, "AMS-KOK", "sea", "sea_sheltered", "SEA-WNB"),
    ("PNG-WNB-003", "Gasmata Health Centre", "health_centre", "West New Britain", "Kandrian-Gloucester", -6.2833, 150.3333, "coastal_road", 16000, "AMS-KOK", "sea", "sea_exposed", "SEA-WNB"),
    ("PNG-WNB-004", "Bialla Health Centre", "district_hospital", "West New Britain", "Talasea", -5.3167, 151.0000, "coastal_road", 41000, "AMS-KOK", "sea", "sea_sheltered", "SEA-WNB"),
    ("PNG-WNB-005", "Kandrian Health Centre", "health_centre", "West New Britain", "Kandrian-Gloucester", -6.2167, 149.5500, "coastal_road", 24000, "AMS-KOK", "sea", "sea_exposed", "SEA-WNB"),

    # --- Bougainville ---
    ("PNG-BOU-001", "Buka General Hospital", "provincial_hospital", "Bougainville", "North Bougainville", -5.4225, 154.6725, "island", 67000, "AMS-KOK", "sea", "sea_exposed", "SEA-BOUGAINVILLE"),
    ("PNG-BOU-002", "Arawa District Hospital", "district_hospital", "Bougainville", "Central Bougainville", -6.2167, 155.5667, "island", 52000, "AMS-KOK", "sea", "sea_exposed", "SEA-BOUGAINVILLE"),
    ("PNG-BOU-003", "Buin District Hospital", "district_hospital", "Bougainville", "South Bougainville", -6.8333, 155.7333, "island", 44000, "AMS-KOK", "sea", "sea_exposed", "SEA-BOUGAINVILLE"),
    ("PNG-BOU-004", "Wakunai Health Centre", "health_centre", "Bougainville", "Central Bougainville", -5.8667, 155.2000, "island", 18000, "AMS-KOK", "sea", "sea_exposed", "SEA-BOUGAINVILLE"),
    ("PNG-BOU-005", "Nissan Island Health Centre", "health_centre", "Bougainville", "North Bougainville", -4.5000, 154.2333, "island", 7000, "AMS-KOK", "sea", "sea_exposed", "SEA-BOUGAINVILLE"),
    ("PNG-BOU-006", "Tinputz Health Centre", "health_centre", "Bougainville", "North Bougainville", -5.5500, 154.7167, "island", 16000, "AMS-KOK", "sea", "sea_exposed", "SEA-BOUGAINVILLE"),
]

# --- scheduled services -------------------------------------------------------------------
# The timetables. Every value here is ILLUSTRATIVE and must be replaced with the real
# coastal shipping and air charter schedules before any of it is quoted to anybody.
#
# key: (display name, mode, frequency, service_days, capacity_m3, cold_m3,
#       fixed_cost_per_trip, variable_cost_per_km, cost_per_m3, reliability, lead_time_sd)
#
# Two kinds of service, and the difference is structural rather than cosmetic:
#
#   liner    a vessel on a run has a hold, and you get your allocated share of it.
#            capacity_m3 > 0 and the lane is capacity-constrained.
#   charter  an aircraft is chartered per flight. If you need more space you buy
#            another flight, so capacity is elastic and the binding constraint is
#            money. capacity_m3 = 0 and the lane carries a quoted rate per m3.
#
# Both still have a *frequency*, because how often the service calls is what drives
# stockout risk at the facility whether or not the hold is the binding constraint.
SERVICES: dict[str, tuple] = {
    "SEA-GULF": ("Moresby–Gulf coastal run", "sea", "FORTNIGHTLY", ["TUE"], 55.0, 3.5, 4200.0, 3.1, 0.0, 0.82, 3.5),
    "SEA-WESTERN": ("Moresby–Daru coastal service", "sea", "MONTHLY", ["THU"], 90.0, 5.0, 9800.0, 3.4, 0.0, 0.78, 5.0),
    "RIV-FLY": ("Fly River barge (Daru–Kiunga–Tabubil)", "river", "MONTHLY", ["MON"], 120.0, 4.0, 11500.0, 2.4, 0.0, 0.72, 7.0),
    "SEA-MILNEBAY": ("Moresby–Alotau coastal service", "sea", "FORTNIGHTLY", ["WED"], 75.0, 4.5, 6400.0, 3.2, 0.0, 0.85, 3.0),
    "SEA-LOUISIADE": ("Milne Bay island run", "sea", "WEEKLY", ["TUE"], 18.0, 1.2, 2100.0, 3.9, 0.0, 0.74, 4.0),
    "SEA-ORO": ("Lae–Oro coastal run", "sea", "FORTNIGHTLY", ["FRI"], 60.0, 3.5, 4600.0, 3.0, 0.0, 0.84, 3.0),
    "SEA-HUON": ("Lae–Huon coastal run", "sea", "WEEKLY", ["MON", "THU"], 32.0, 2.0, 2600.0, 3.3, 0.0, 0.86, 2.5),
    "SEA-RAICOAST": ("Madang–Rai Coast workboat", "sea", "WEEKLY", ["WED"], 16.0, 1.0, 1500.0, 3.6, 0.0, 0.80, 3.0),
    "SEA-SEPIK": ("Madang–Sepik coastal service", "sea", "FORTNIGHTLY", ["SAT"], 85.0, 5.0, 7200.0, 3.0, 0.0, 0.83, 3.5),
    "RIV-SEPIK": ("Sepik River workboat", "river", "FORTNIGHTLY", ["TUE"], 14.0, 0.8, 1800.0, 2.8, 0.0, 0.70, 5.0),
    "SEA-MANUS": ("Madang–Manus service", "sea", "MONTHLY", ["SUN"], 45.0, 2.5, 8600.0, 3.5, 0.0, 0.76, 5.5),
    "SEA-NEWIRELAND": ("Kokopo–New Ireland coastal run", "sea", "WEEKLY", ["TUE"], 40.0, 2.8, 3100.0, 3.2, 0.0, 0.87, 2.5),
    "SEA-WNB": ("Kokopo–West New Britain coastal run", "sea", "FORTNIGHTLY", ["THU"], 65.0, 3.8, 5200.0, 3.1, 0.0, 0.85, 3.0),
    "SEA-POMIO": ("Kokopo–Pomio workboat", "sea", "MONTHLY", ["MON"], 22.0, 1.4, 1900.0, 3.8, 0.0, 0.71, 5.0),
    "SEA-BOUGAINVILLE": ("Kokopo–Bougainville service", "sea", "FORTNIGHTLY", ["WED"], 70.0, 4.0, 7400.0, 3.3, 0.0, 0.80, 4.0),
    "AIR-GOILALA": ("Goilala air charter", "air", "MONTHLY", ["TUE"], 0.0, 0.0, 0.0, 0.0, 2350.0, 0.75, 4.0),
    "AIR-GULF": ("Gulf interior air charter", "air", "MONTHLY", ["WED"], 0.0, 0.0, 0.0, 0.0, 2500.0, 0.74, 4.0),
    "AIR-WESTERN": ("Western interior air charter", "air", "MONTHLY", ["THU"], 0.0, 0.0, 0.0, 0.0, 2650.0, 0.72, 4.5),
    "AIR-ORO": ("Oro interior air charter", "air", "MONTHLY", ["MON"], 0.0, 0.0, 0.0, 0.0, 2400.0, 0.75, 4.0),
    "AIR-HUON": ("Huon interior air charter", "air", "MONTHLY", ["FRI"], 0.0, 0.0, 0.0, 0.0, 2300.0, 0.76, 4.0),
    "AIR-RAMU": ("Ramu–Middle Ramu air charter", "air", "MONTHLY", ["TUE"], 0.0, 0.0, 0.0, 0.0, 2450.0, 0.74, 4.0),
    "AIR-SANDAUN": ("Telefomin–Oksapmin air charter", "air", "MONTHLY", ["WED"], 0.0, 0.0, 0.0, 0.0, 2800.0, 0.70, 5.0),
    "AIR-HELA": ("Lake Kopiago air charter", "air", "MONTHLY", ["THU"], 0.0, 0.0, 0.0, 0.0, 2600.0, 0.72, 4.5),
    "AIR-JIWAKA": ("Jimi Valley air charter", "air", "MONTHLY", ["MON"], 0.0, 0.0, 0.0, 0.0, 2200.0, 0.76, 4.0),
    "AIR-CHIMBU": ("Karimui air charter", "air", "MONTHLY", ["FRI"], 0.0, 0.0, 0.0, 0.0, 2250.0, 0.75, 4.0),
    "AIR-EHP": ("Obura-Wonenara air charter", "air", "MONTHLY", ["WED"], 0.0, 0.0, 0.0, 0.0, 2400.0, 0.73, 4.5),
}

# --- primary distribution ------------------------------------------------------------------
# There is no road between Port Moresby and the rest of the country. That single fact
# shapes the whole network and it is why the primary legs are almost all maritime.
#
# (code, from, to, mode, frequency, capacity_m3, cold_m3, fixed_cost, var_per_km, profile,
#  reliability, service name)
PRIMARY_LANES: list[tuple] = [
    ("PRI-NMS-POM", "NMS-BADILI", "AMS-POM", "road", "TWICE_WEEKLY", 240.0, 18.0, 320.0, 2.1, "all_weather", 0.97, "Port Moresby local shuttle"),
    ("PRI-NMS-LAE", "NMS-BADILI", "AMS-LAE", "sea", "FORTNIGHTLY", 620.0, 34.0, 31000.0, 2.6, "sea_sheltered", 0.90, "Moresby–Lae main line"),
    ("PRI-NMS-MAD", "NMS-BADILI", "AMS-MAD", "sea", "MONTHLY", 480.0, 26.0, 38000.0, 2.6, "sea_sheltered", 0.88, "Moresby–Madang main line"),
    ("PRI-NMS-KOK", "NMS-BADILI", "AMS-KOK", "sea", "MONTHLY", 430.0, 24.0, 46000.0, 2.7, "sea_sheltered", 0.87, "Moresby–Rabaul main line"),
    ("PRI-LAE-HGN", "AMS-LAE", "AMS-HGN", "road", "WEEKLY", 180.0, 12.0, 1900.0, 2.4, "highlands_sealed", 0.92, "Highlands Highway trunk"),
    # Candidate hubs receive their primary supply from the nearest operating hub.
    ("PRI-MAD-WWK", "AMS-MAD", "AMS-WWK", "sea", "FORTNIGHTLY", 210.0, 14.0, 9800.0, 2.9, "sea_exposed", 0.84, "Madang–Wewak feeder"),
    ("PRI-NMS-ALO", "NMS-BADILI", "AMS-ALO", "sea", "MONTHLY", 190.0, 12.0, 15500.0, 2.9, "sea_exposed", 0.85, "Moresby–Alotau feeder"),
    ("PRI-KOK-BUK", "AMS-KOK", "AMS-BUK", "sea", "MONTHLY", 160.0, 10.0, 12800.0, 3.0, "sea_exposed", 0.82, "Rabaul–Buka feeder"),
]

# --- alternate lanes from candidate hubs ------------------------------------------------------
# Which facilities a candidate hub would serve if it were opened. This is what makes
# "should we build an Alotau store?" a question the model can answer rather than an
# opinion.
CANDIDATE_HUB_CATCHMENTS: dict[str, tuple[str, ...]] = {
    "AMS-WWK": (
        "PNG-ESP-001", "PNG-ESP-002", "PNG-ESP-003", "PNG-ESP-004", "PNG-ESP-005",
        "PNG-ESP-006", "PNG-ESP-007", "PNG-SAN-001", "PNG-SAN-002", "PNG-SAN-003",
        "PNG-SAN-004", "PNG-SAN-005", "PNG-SAN-006", "PNG-SAN-007",
    ),
    "AMS-ALO": (
        "PNG-MBP-001", "PNG-MBP-002", "PNG-MBP-003", "PNG-MBP-004", "PNG-MBP-005",
        "PNG-MBP-006", "PNG-MBP-007",
    ),
    "AMS-BUK": (
        "PNG-BOU-001", "PNG-BOU-002", "PNG-BOU-003", "PNG-BOU-004", "PNG-BOU-005",
        "PNG-BOU-006",
    ),
}

# --- emergency air fallback ---------------------------------------------------------------
# Facilities whose only surface lane closes completely in the wet season need a modelled
# alternative, otherwise the season slider shows them as simply unreachable and the
# obvious next question -- "so what does the workaround cost?" -- has no answer.
AIR_FALLBACK_FOR: tuple[str, ...] = (
    # Road lanes that close outright in the northwest monsoon.
    "PNG-GUL-005", "PNG-CEN-002", "PNG-CEN-006", "PNG-MAD-002", "PNG-MAD-005",
    "PNG-CEN-003", "PNG-HEL-001", "PNG-HEL-002", "PNG-ENG-003", "PNG-SHP-005",
    # River and exposed-coast lanes that become unreliable in the same window.
    "PNG-GUL-002", "PNG-GUL-003", "PNG-GUL-004",
    "PNG-WES-002", "PNG-WES-003", "PNG-WES-004", "PNG-WES-007",
    "PNG-ESP-004", "PNG-ESP-007",
)
AIR_FALLBACK = ("Wet season air charter", "air", "MONTHLY", ["ANY"], 0.0, 0.0, 0.0, 0.0, 3100.0, 0.70, 5.0)

# --- products ------------------------------------------------------------------------------
# (sku, name, temperature_band, volume_per_unit_cm3, unit_cost, shelf_life_days,
#  units per 1000 population per year)
PRODUCTS: list[tuple] = [
    ("ESSMED-KIT", "Essential medicines kit (Area Medical Store kit)", "ambient", 62000.0, 1850.0, 730, 31.0),
    ("MEDSUP-BOX", "Medical and surgical consumables box", "ambient", 28000.0, 640.0, 1095, 25.0),
    ("ARVTB-KIT", "ARV and TB treatment kit", "ambient", 15000.0, 980.0, 730, 19.0),
    ("MAL-KIT", "Malaria RDT and ACT kit", "ambient", 9000.0, 410.0, 730, 27.0),
    ("VAC-EPI", "Routine EPI vaccine carton", "+2-8", 1200.0, 520.0, 365, 175.0),
    ("VAC-MEAS", "Measles-rubella vaccine carton", "+2-8", 900.0, 380.0, 365, 78.0),
    ("OXY-AMP", "Oxytocin ampoule carton", "+2-8", 450.0, 145.0, 545, 78.0),
    ("VAC-ULT", "Ultra-cold chain vaccine carton", "-70", 2500.0, 1240.0, 180, 14.0),
]

#: Hospitals hold more than health centres. Days-of-cover by terrain live in the
#: loader, next to the code that turns them into cubic metres.
STORAGE_TYPE_MULTIPLIER: dict[str, float] = {
    "provincial_hospital": 2.0,
    "district_hospital": 1.4,
    "health_centre": 1.0,
    "aid_post": 0.7,
}


# --- coarse land mask -------------------------------------------------------------
# Rings are (lon, lat). Traced from well-known coastal settlements; accurate to roughly
# 10-25 km at the coast, which is well inside the tolerance we apply below.

MAINLAND = [
    # North coast, west to east: Wutung on the border, Vanimo, Aitape, Wewak,
    # the Sepik mouth, Bogia, Madang, the Rai Coast.
    (140.97, -2.61), (141.30, -2.66), (142.00, -2.98), (142.35, -3.10),
    (143.00, -3.36), (143.63, -3.48), (144.20, -3.72), (144.62, -3.88),
    (145.02, -4.22), (145.42, -4.58), (145.82, -5.16), (146.05, -5.32),
    (146.50, -5.55),
    # Huon Peninsula: north shore out to Sialum, round the point, back to Lae.
    (147.20, -5.86), (147.55, -6.00), (147.92, -6.32), (147.88, -6.66),
    (147.40, -6.76), (146.96, -6.72),
    # Morobe and Oro coasts, south-east to Milne Bay and East Cape.
    (147.08, -7.04), (147.60, -7.78), (148.25, -8.08), (148.52, -8.90),
    (149.35, -9.10), (150.05, -9.82), (150.52, -10.20), (150.92, -10.24),
    # South coast, east to west: Alotau, Abau, Port Moresby, Yule Island, Kerema.
    (150.45, -10.38), (149.90, -10.36), (149.00, -10.16), (148.20, -10.08),
    (147.80, -10.00), (147.18, -9.48), (146.53, -8.84), (145.78, -7.98),
    # Gulf of Papua and the Fly delta, out to the border at Torres Strait.
    (145.00, -7.96), (144.00, -7.82), (143.60, -8.12), (143.30, -8.62),
    (142.90, -8.82), (141.80, -8.62), (141.00, -9.13),
    # Western border, running back north along 141°E.
    (141.00, -6.00),
]

NEW_BRITAIN = [
    # North coast, Cape Gloucester east past the Willaumez Peninsula to the
    # Gazelle Peninsula and Rabaul.
    (148.42, -5.45), (149.30, -5.42), (150.00, -4.98), (150.30, -5.30),
    (151.00, -5.05), (151.60, -4.55), (152.20, -4.10), (152.45, -4.28),
    # East and south coasts, Kokopo round to Pomio, Gasmata and Kandrian.
    (152.35, -4.55), (151.90, -5.15), (151.60, -5.75), (151.30, -6.05),
    (150.80, -6.20), (150.20, -6.40), (149.40, -6.25), (148.70, -5.90),
]

BOUGAINVILLE = [
    (154.55, -5.20), (155.00, -5.55), (155.40, -6.00), (155.80, -6.50),
    (156.05, -6.85), (155.75, -7.05), (155.35, -6.75), (154.95, -6.20),
    (154.60, -5.60),
]

LAND_POLYGONS = [MAINLAND, NEW_BRITAIN, BOUGAINVILLE]

# Smaller islands and narrow chains: (lat, lon, radius_km). A circular buffer is a
# better model than a bad polygon for a 10 km wide island.
ISLAND_BUFFERS: list[tuple[float, float, float]] = [
    (-2.57, 150.80, 45.0),   # Kavieng / northern New Ireland
    (-3.10, 151.50, 45.0),   # central New Ireland
    (-3.67, 152.43, 45.0),   # Namatanai
    (-4.30, 152.90, 40.0),   # southern New Ireland
    (-3.12, 152.63, 20.0),   # Lihir
    (-2.02, 147.27, 45.0),   # Manus / Lorengau
    (-1.72, 146.30, 25.0),   # Bipi and the western Manus islands
    (-2.32, 147.83, 20.0),   # Rambutyo
    (-2.57, 147.28, 18.0),   # Baluan
    (-4.65, 145.97, 20.0),   # Karkar Island
    (-4.08, 145.03, 15.0),   # Manam Island
    (-8.51, 151.08, 30.0),   # Kiriwina / Trobriands
    (-9.35, 150.28, 25.0),   # Goodenough
    (-9.75, 150.80, 45.0),   # Fergusson / Normanby
    (-10.68, 152.75, 25.0),  # Misima
    (-10.62, 150.67, 20.0),  # Samarai
    (-9.07, 152.75, 25.0),   # Woodlark
    (-11.60, 153.30, 30.0),  # Louisiade / Sudest
    (-5.42, 154.67, 25.0),   # Buka
    (-4.68, 154.20, 20.0),   # Nissan / Green Islands
    (-2.70, 141.30, 20.0),   # Vanimo coastal strip
    (-9.08, 143.21, 25.0),   # Daru
    (-5.55, 147.90, 25.0),   # Umboi / Siassi
    (-2.85, 152.03, 22.0),   # Tabar group
    (-2.57, 150.20, 30.0),   # Lavongai / New Hanover
]


#: Everything the offshore check and the offline basemap need, in the shape the
#: Country.boundary column stores.
BOUNDARY = {"polygons": LAND_POLYGONS, "buffers": ISLAND_BUFFERS}
