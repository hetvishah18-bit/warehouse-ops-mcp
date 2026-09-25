-- =====================================================================
-- Warehouse Ops MCP - database schema (SQLite)
-- All timestamps are stored as UTC text: 'YYYY-MM-DD HH:MM:SS'
-- =====================================================================

PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS meta;
DROP TABLE IF EXISTS trailer_flags;
DROP TABLE IF EXISTS pick_lines;
DROP TABLE IF EXISTS inventory;
DROP TABLE IF EXISTS locations;
DROP TABLE IF EXISTS skus;
DROP TABLE IF EXISTS appointments;
DROP TABLE IF EXISTS yard_moves;
DROP TABLE IF EXISTS trailers;
DROP TABLE IF EXISTS carriers;
DROP TABLE IF EXISTS dock_doors;
DROP TABLE IF EXISTS sites;

-- ---------------------------------------------------------------------
-- Metadata (when the data was generated)
-- ---------------------------------------------------------------------
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- ---------------------------------------------------------------------
-- Sites and facilities
-- ---------------------------------------------------------------------
CREATE TABLE sites (
    site_id     TEXT PRIMARY KEY,          -- e.g. 'CHI1'
    name        TEXT NOT NULL,
    city        TEXT NOT NULL,
    state       TEXT NOT NULL,
    timezone    TEXT NOT NULL
);

CREATE TABLE dock_doors (
    door_id     TEXT PRIMARY KEY,          -- e.g. 'CHI1-D07'
    site_id     TEXT NOT NULL REFERENCES sites(site_id),
    door_number INTEGER NOT NULL,
    door_type   TEXT NOT NULL CHECK (door_type IN ('inbound', 'outbound')),
    status      TEXT NOT NULL CHECK (status IN ('open', 'occupied', 'out_of_service')),
    UNIQUE (site_id, door_number)
);

CREATE TABLE carriers (
    carrier_id  INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    scac        TEXT NOT NULL UNIQUE       -- 4-letter carrier code
);

-- ---------------------------------------------------------------------
-- Yard: trailers, their movements, and dock appointments
-- ---------------------------------------------------------------------
CREATE TABLE trailers (
    trailer_id       TEXT PRIMARY KEY,     -- one row per trailer visit
    site_id          TEXT NOT NULL REFERENCES sites(site_id),
    carrier_id       INTEGER NOT NULL REFERENCES carriers(carrier_id),
    trailer_number   TEXT NOT NULL,        -- number painted on the trailer
    direction        TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    load_status      TEXT NOT NULL CHECK (load_status IN ('loaded', 'empty', 'partial')),
    status           TEXT NOT NULL CHECK (status IN ('in_yard', 'at_door', 'departed')),
    current_location TEXT,                 -- yard spot ('Y-014'), door_id, or NULL if departed
    checked_in_at    TEXT NOT NULL,
    departed_at      TEXT
);

CREATE TABLE yard_moves (
    move_id       INTEGER PRIMARY KEY,
    trailer_id    TEXT NOT NULL REFERENCES trailers(trailer_id),
    from_location TEXT NOT NULL,           -- 'GATE', 'Y-###', or door_id
    to_location   TEXT NOT NULL,
    moved_at      TEXT NOT NULL,
    move_type     TEXT NOT NULL CHECK (move_type IN ('gate_in', 'to_door', 'from_door', 'gate_out'))
);

CREATE TABLE appointments (
    appt_id          INTEGER PRIMARY KEY,
    site_id          TEXT NOT NULL REFERENCES sites(site_id),
    door_id          TEXT NOT NULL REFERENCES dock_doors(door_id),
    carrier_id       INTEGER NOT NULL REFERENCES carriers(carrier_id),
    trailer_id       TEXT REFERENCES trailers(trailer_id),   -- NULL until a trailer arrives
    direction        TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    scheduled_start  TEXT NOT NULL,
    scheduled_end    TEXT NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('scheduled', 'arrived', 'completed', 'no_show', 'cancelled'))
);

-- Written by the MCP server later (Phase 4). Empty after seeding.
CREATE TABLE trailer_flags (
    flag_id     INTEGER PRIMARY KEY,
    trailer_id  TEXT NOT NULL REFERENCES trailers(trailer_id),
    reason      TEXT NOT NULL,
    flagged_by  TEXT NOT NULL,
    flagged_at  TEXT NOT NULL,
    resolved_at TEXT
);

-- ---------------------------------------------------------------------
-- Inventory side: SKUs, storage locations, stock, and pick history
-- ---------------------------------------------------------------------
CREATE TABLE skus (
    sku          TEXT PRIMARY KEY,         -- e.g. 'TRF-00042'
    description  TEXT NOT NULL,
    category     TEXT NOT NULL,
    length_in    REAL NOT NULL,
    width_in     REAL NOT NULL,
    height_in    REAL NOT NULL,
    weight_lb    REAL NOT NULL,
    cube_cuft    REAL NOT NULL             -- length*width*height / 1728
);

CREATE TABLE locations (
    location_id        TEXT PRIMARY KEY,   -- e.g. 'CHI1-A03-07-2'
    site_id            TEXT NOT NULL REFERENCES sites(site_id),
    zone               TEXT NOT NULL,      -- A/B/C = pick zones, R = reserve
    aisle              INTEGER NOT NULL,
    bay                INTEGER NOT NULL,
    level              INTEGER NOT NULL,   -- 1 = floor
    location_type      TEXT NOT NULL CHECK (location_type IN ('pick', 'reserve')),
    travel_distance_ft REAL NOT NULL,      -- distance from pick start / pack-out
    is_golden_zone     INTEGER NOT NULL CHECK (is_golden_zone IN (0, 1))  -- waist-to-shoulder height
);

CREATE TABLE inventory (
    location_id TEXT NOT NULL REFERENCES locations(location_id),
    sku         TEXT NOT NULL REFERENCES skus(sku),
    site_id     TEXT NOT NULL REFERENCES sites(site_id),
    quantity    INTEGER NOT NULL CHECK (quantity >= 0),
    PRIMARY KEY (location_id, sku)
);

CREATE TABLE pick_lines (
    pick_id     INTEGER PRIMARY KEY,
    site_id     TEXT NOT NULL REFERENCES sites(site_id),
    order_id    TEXT NOT NULL,
    sku         TEXT NOT NULL REFERENCES skus(sku),
    location_id TEXT NOT NULL REFERENCES locations(location_id),
    quantity    INTEGER NOT NULL CHECK (quantity > 0),
    picked_at   TEXT NOT NULL
);

-- ---------------------------------------------------------------------
-- Indexes for the queries the MCP tools will run most
-- ---------------------------------------------------------------------
CREATE INDEX idx_trailers_site_status   ON trailers (site_id, status);
CREATE INDEX idx_yard_moves_trailer     ON yard_moves (trailer_id, moved_at);
CREATE INDEX idx_appts_site_start       ON appointments (site_id, scheduled_start);
CREATE INDEX idx_inventory_sku          ON inventory (sku);
CREATE INDEX idx_locations_site_type    ON locations (site_id, location_type);
CREATE INDEX idx_picks_site_time        ON pick_lines (site_id, picked_at);
CREATE INDEX idx_picks_sku_time         ON pick_lines (sku, picked_at);
