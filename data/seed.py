"""
Synthetic data generator for Warehouse Ops MCP.

Builds two fictional distribution centers with realistic history:
dock doors, carriers, trailer visits + yard moves, appointments,
SKUs, storage locations, inventory, and pick history.

The data is anchored to the current time, so questions like
"which trailers have been here over 48 hours?" always have answers.

Built-in "problems" for the MCP tools to find:
  - A few forgotten trailers sitting in the yard for days
  - Fast-moving SKUs slotted far from pick start / outside the golden zone
  - SKUs whose demand is trending up or down in the last 30 days

Usage (from the project root):
    python data/seed.py
    python data/seed.py --days 60 --seed 7 --db data/warehouse.db
"""

import argparse
import math
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA_PATH = HERE / "schema.sql"
FMT = "%Y-%m-%d %H:%M:%S"

# ---------------------------------------------------------------------
# Reference data (all fictional)
# ---------------------------------------------------------------------
SITES = [
    ("CHI1", "Chicago Area DC", "Joliet", "IL", "America/Chicago"),
    ("SOC1", "Southern California DC", "Fontana", "CA", "America/Los_Angeles"),
]

CARRIERS = [
    ("Prairie Freight Lines", "PRFL"),
    ("Coastline Transport", "CSTL"),
    ("Summit Carriers", "SMTC"),
    ("Blue River Logistics", "BRVL"),
    ("Ironhorse Trucking", "IRHT"),
    ("Redwood Express", "RDWX"),
    ("Great Lakes Haulers", "GLHL"),
    ("Mesa Freightways", "MESF"),
    ("Keystone Cartage", "KYCT"),
    ("Northstar Dedicated", "NSTD"),
]

# (category, sku prefix, variants, length range, width range, height range, weight range)
CATEGORIES = [
    ("Troffer", "TRF", ["2x2 LED Troffer", "2x4 LED Troffer", "1x4 LED Troffer"],
     (24, 50), (12, 26), (4, 7), (8, 22)),
    ("Downlight", "DWN", ["4in Downlight", "6in Downlight", "Wafer Downlight"],
     (6, 10), (6, 10), (4, 8), (1, 4)),
    ("High Bay", "HBY", ["Round High Bay", "Linear High Bay"],
     (14, 26), (14, 24), (8, 14), (10, 28)),
    ("Wall Pack", "WPK", ["Full Cutoff Wall Pack", "Mini Wall Pack"],
     (10, 16), (8, 12), (6, 10), (5, 14)),
    ("Strip Light", "STP", ["4ft Strip", "8ft Strip", "Wraparound"],
     (24, 96), (3, 6), (3, 5), (3, 12)),
    ("Emergency/Exit", "EMX", ["Exit Sign", "Emergency Combo", "Remote Head"],
     (10, 14), (3, 10), (8, 12), (2, 6)),
    ("Driver", "DRV", ["Dimmable LED Driver", "Constant Current Driver"],
     (6, 12), (2, 4), (1, 3), (0.5, 2)),
    ("Controls", "CTL", ["Occupancy Sensor", "Wall Dimmer", "Photocell"],
     (3, 6), (2, 4), (1, 3), (0.2, 1)),
    ("Pole/Bracket", "POL", ["Mounting Bracket", "Pole Adapter"],
     (36, 120), (4, 8), (4, 8), (15, 60)),
]
CCT_OPTIONS = ["3000K", "3500K", "4000K", "5000K"]

PICK_ZONES = {"A": 0, "B": 120, "C": 240}      # zone -> travel offset (ft)
PICK_AISLES, PICK_BAYS, PICK_LEVELS = 8, 12, 4
RESERVE_AISLES, RESERVE_BAYS, RESERVE_LEVELS = 6, 12, 5

INBOUND_DOORS = range(1, 17)       # doors 1-16
OUTBOUND_DOORS = range(17, 31)     # doors 17-30
OUT_OF_SERVICE_DOOR = 7            # one broken door per site
YARD_SPOTS = 220

N_SKUS = 600


def ts(dt: datetime) -> str:
    return dt.strftime(FMT)


def hours(h: float) -> timedelta:
    return timedelta(hours=h)


# ---------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------
def build_doors(site_id):
    doors = []
    for n in list(INBOUND_DOORS) + list(OUTBOUND_DOORS):
        door_type = "inbound" if n in INBOUND_DOORS else "outbound"
        status = "out_of_service" if n == OUT_OF_SERVICE_DOOR else "open"
        doors.append({"door_id": f"{site_id}-D{n:02d}", "site_id": site_id,
                      "door_number": n, "door_type": door_type, "status": status})
    return doors


def build_skus(rng):
    skus = []
    counters = {}
    for i in range(N_SKUS):
        cat, prefix, variants, lr, wr, hr, wtr = rng.choice(CATEGORIES)
        counters[prefix] = counters.get(prefix, 0) + 1
        length = round(rng.uniform(*lr), 1)
        width = round(rng.uniform(*wr), 1)
        height = round(rng.uniform(*hr), 1)
        skus.append({
            "sku": f"{prefix}-{counters[prefix]:05d}",
            "description": f"{rng.choice(variants)} {rng.choice(CCT_OPTIONS)}",
            "category": cat,
            "length_in": length, "width_in": width, "height_in": height,
            "weight_lb": round(rng.uniform(*wtr), 2),
            "cube_cuft": round(length * width * height / 1728, 3),
        })
    return skus


def build_locations(site_id):
    locs = []
    for zone, offset in PICK_ZONES.items():
        for aisle in range(1, PICK_AISLES + 1):
            for bay in range(1, PICK_BAYS + 1):
                for level in range(1, PICK_LEVELS + 1):
                    locs.append({
                        "location_id": f"{site_id}-{zone}{aisle:02d}-{bay:02d}-{level}",
                        "site_id": site_id, "zone": zone, "aisle": aisle, "bay": bay,
                        "level": level, "location_type": "pick",
                        "travel_distance_ft": float(offset + aisle * 14 + bay * 5),
                        "is_golden_zone": 1 if level in (2, 3) else 0,
                    })
    for aisle in range(1, RESERVE_AISLES + 1):
        for bay in range(1, RESERVE_BAYS + 1):
            for level in range(1, RESERVE_LEVELS + 1):
                locs.append({
                    "location_id": f"{site_id}-R{aisle:02d}-{bay:02d}-{level}",
                    "site_id": site_id, "zone": "R", "aisle": aisle, "bay": bay,
                    "level": level, "location_type": "reserve",
                    "travel_distance_ft": float(400 + aisle * 16 + bay * 5),
                    "is_golden_zone": 0,
                })
    return locs


def build_demand_profile(rng, skus):
    """Pareto-style demand: a few fast movers, a long tail. Plus trends."""
    order = list(range(len(skus)))
    rng.shuffle(order)
    base = {}
    for rank, idx in enumerate(order):
        base[skus[idx]["sku"]] = 1.0 / (rank + 1) ** 0.9

    trend = {s["sku"]: 1.0 for s in skus}
    ranked = [skus[idx]["sku"] for idx in order]          # fastest first
    # Rising stars: mid-pack SKUs whose demand jumped in the last 30 days
    for sku in rng.sample(ranked[25:150], 10):
        trend[sku] = 5.0
    # Fading SKUs: former fast movers whose demand dropped
    for sku in rng.sample(ranked[:40], 6):
        trend[sku] = 0.2
    # Background noise on the long tail
    for sku in ranked[150:]:
        r = rng.random()
        if r < 0.05:
            trend[sku] = 2.5
        elif r < 0.10:
            trend[sku] = 0.4
    return base, trend


def slot_skus(rng, site_id, skus, locations, base_weight, site_factor):
    """
    Assign each SKU a pick location. The slotting was done "a while ago"
    with imperfect data (noise), so some fast movers end up in bad spots.
    """
    pick_locs = [l for l in locations if l["site_id"] == site_id and l["location_type"] == "pick"]
    # Best locations first: close to pick start, golden zone preferred
    pick_locs.sort(key=lambda l: l["travel_distance_ft"] + (0 if l["is_golden_zone"] else 60))

    scored = []
    for s in skus:
        w = base_weight[s["sku"]] * site_factor[s["sku"]]
        scored.append((math.log(w) + rng.gauss(0, 1.2), s["sku"]))
    scored.sort(reverse=True)
    order = [sku for _, sku in scored]

    # Slotting drift: a handful of fast movers ended up in poor locations
    # (far zones, floor level or top shelf) and a few slow movers took their spots.
    n = len(order)
    for i in rng.sample(range(0, 30), 6):
        j = rng.randint(n // 2, n - 1)
        order[i], order[j] = order[j], order[i]
    for _ in range(int(n * 0.05)):
        i, j = rng.randrange(n), rng.randrange(n)
        order[i], order[j] = order[j], order[i]

    return {sku: pick_locs[i]["location_id"] for i, sku in enumerate(order)}


def build_inventory(rng, site_id, slot_map, locations, base_weight):
    rows = []
    for sku, loc in slot_map.items():
        rows.append({"location_id": loc, "sku": sku, "site_id": site_id,
                     "quantity": rng.randint(8, 180)})
    # Reserve pallets for the top ~150 SKUs
    reserve = [l["location_id"] for l in locations
               if l["site_id"] == site_id and l["location_type"] == "reserve"]
    rng.shuffle(reserve)
    top = sorted(slot_map, key=lambda s: base_weight[s], reverse=True)[:150]
    for sku, loc in zip(top, reserve):
        rows.append({"location_id": loc, "sku": sku, "site_id": site_id,
                     "quantity": rng.randint(200, 900)})
    return rows


def build_picks(rng, site_id, skus, slot_map, base_weight, trend, site_factor,
                start, now, days, order_counter):
    sku_ids = [s["sku"] for s in skus]
    w_old = [base_weight[s] * site_factor[s] for s in sku_ids]
    w_new = [base_weight[s] * site_factor[s] * trend[s] for s in sku_ids]
    cum_old, cum_new, acc_o, acc_n = [], [], 0.0, 0.0
    for a, b in zip(w_old, w_new):
        acc_o += a
        acc_n += b
        cum_old.append(acc_o)
        cum_new.append(acc_n)

    recent_cutoff = now - timedelta(days=30)
    hour_choices = list(range(5, 23))
    hour_weights = [1, 3, 5, 6, 6, 6, 5, 5, 6, 6, 5, 4, 4, 3, 3, 2, 2, 1]
    qty_choices = [1, 1, 1, 2, 2, 3, 4, 6, 12]

    rows = []
    for d in range(days + 1):
        day = (start + timedelta(days=d)).replace(hour=0, minute=0, second=0, microsecond=0)
        weekend = day.weekday() >= 5
        n_orders = max(5, int(rng.gauss(35, 8) if weekend else rng.gauss(140, 20)))
        cum = cum_new if day >= recent_cutoff else cum_old
        for _ in range(n_orders):
            order_counter[0] += 1
            order_id = f"{site_id}-SO{order_counter[0]:07d}"
            picked_at = day + hours(rng.choices(hour_choices, hour_weights)[0]) \
                + timedelta(minutes=rng.randint(0, 59))
            if picked_at > now:
                continue
            n_lines = rng.choices([1, 2, 3, 4, 5, 6], [30, 25, 18, 12, 9, 6])[0]
            for sku in set(rng.choices(sku_ids, cum_weights=cum, k=n_lines)):
                rows.append({
                    "site_id": site_id, "order_id": order_id, "sku": sku,
                    "location_id": slot_map[sku], "quantity": rng.choice(qty_choices),
                    "picked_at": ts(picked_at + timedelta(minutes=rng.randint(0, 20))),
                })
    return rows


def build_yard(rng, site_id, doors, carrier_ids, start, now, days, trailer_counter):
    """Simulate trailer visits: gate in -> yard -> door -> yard -> gate out."""
    visits = []
    for d in range(days + 1):
        day = (start + timedelta(days=d)).replace(hour=0, minute=0, second=0, microsecond=0)
        weekend = day.weekday() >= 5
        n_in = max(0, int(rng.gauss(4, 1) if weekend else rng.gauss(16, 3)))
        n_out = max(0, int(rng.gauss(3, 1) if weekend else rng.gauss(11, 2)))
        for direction, n in (("inbound", n_in), ("outbound", n_out)):
            for _ in range(n):
                arrival = day + hours(rng.triangular(0, 24, 10))
                if arrival > now:
                    continue
                visits.append({
                    "direction": direction,
                    "carrier_id": rng.choice(carrier_ids),
                    "arrival": arrival,
                    "mode": "live" if rng.random() < 0.6 else "drop",
                    "forgotten": rng.random() < 0.05,
                })

    # Time waiting in the yard before going to a door
    for v in visits:
        wait = rng.expovariate(1 / 1.0) if v["mode"] == "live" else rng.uniform(4, 36)
        v["forgotten_pre"] = v["forgotten"] and rng.random() < 0.5
        if v["forgotten_pre"]:
            wait += rng.uniform(48, 240)
        v["ready"] = v["arrival"] + hours(wait)

    # Assign doors in order of readiness (a door holds one trailer at a time)
    door_free = {dr["door_id"]: start - timedelta(days=1) for dr in doors
                 if dr["status"] != "out_of_service"}
    door_type = {dr["door_id"]: dr["door_type"] for dr in doors}
    for v in sorted(visits, key=lambda x: x["ready"]):
        options = [d for d in door_free if door_type[d] == v["direction"]]
        door = min(options, key=lambda d: door_free[d])
        v["door_id"] = door
        v["door_start"] = max(v["ready"], door_free[door])
        dur = rng.uniform(1.5, 4) if v["direction"] == "inbound" else rng.uniform(2, 5)
        v["door_end"] = v["door_start"] + hours(dur)
        door_free[door] = v["door_end"] + timedelta(minutes=15)

        if v["direction"] == "inbound":
            post = rng.uniform(0.1, 0.5) if v["mode"] == "live" else rng.uniform(6, 72)
        else:
            post = rng.uniform(2, 24)   # loaded, waiting for carrier pickup
        if v["forgotten"] and not v["forgotten_pre"]:
            post += rng.uniform(48, 240)
        v["depart"] = v["door_end"] + hours(post)

    # Assign yard spots for each stay (before door, after door)
    stays = []
    for v in visits:
        stays.append((v["arrival"], v["door_start"], v, "spot_pre"))
        stays.append((v["door_end"], v["depart"], v, "spot_post"))
    spot_free = {f"Y-{i:03d}": start - timedelta(days=1) for i in range(1, YARD_SPOTS + 1)}
    for s_start, s_end, v, key in sorted(stays, key=lambda x: x[0]):
        free = [s for s, t in spot_free.items() if t <= s_start]
        spot = free[0] if free else min(spot_free, key=spot_free.get)
        v[key] = spot
        spot_free[spot] = s_end

    # Build trailer rows, moves, and appointments
    trailers, moves, appts = [], [], []
    for v in sorted(visits, key=lambda x: x["arrival"]):
        trailer_counter[0] += 1
        tid = f"{site_id}-T{trailer_counter[0]:06d}"
        carrier_prefix = CARRIERS[v["carrier_id"] - 1][1]
        inbound = v["direction"] == "inbound"

        if v["depart"] <= now:
            status, loc = "departed", None
            load = "empty" if inbound else "loaded"
        elif v["door_start"] <= now < v["door_end"]:
            status, loc, load = "at_door", v["door_id"], "partial"
        elif now < v["door_start"]:
            status, loc = "in_yard", v["spot_pre"]
            load = "loaded" if inbound else "empty"
        else:
            status, loc = "in_yard", v["spot_post"]
            load = "empty" if inbound else "loaded"

        trailers.append({
            "trailer_id": tid, "site_id": site_id, "carrier_id": v["carrier_id"],
            "trailer_number": f"{carrier_prefix}{rng.randint(10000, 99999)}",
            "direction": v["direction"], "load_status": load, "status": status,
            "current_location": loc, "checked_in_at": ts(v["arrival"]),
            "departed_at": ts(v["depart"]) if status == "departed" else None,
        })

        for when, frm, to, mtype in (
            (v["arrival"], "GATE", v["spot_pre"], "gate_in"),
            (v["door_start"], v["spot_pre"], v["door_id"], "to_door"),
            (v["door_end"], v["door_id"], v["spot_post"], "from_door"),
            (v["depart"], v["spot_post"], "GATE", "gate_out"),
        ):
            if when <= now:
                moves.append({"trailer_id": tid, "from_location": frm,
                              "to_location": to, "moved_at": ts(when), "move_type": mtype})

        if inbound:
            sched = v["arrival"] + hours(rng.gauss(0, 0.6))
            sched = sched.replace(minute=0 if sched.minute < 30 else 30, second=0, microsecond=0)
            appts.append({
                "site_id": site_id, "door_id": v["door_id"], "carrier_id": v["carrier_id"],
                "trailer_id": tid, "direction": "inbound",
                "scheduled_start": ts(sched), "scheduled_end": ts(sched + hours(2)),
                "status": "completed" if v["door_end"] <= now else "arrived",
            })
        v["trailer_id"] = tid

    # Past no-shows and future scheduled appointments
    inbound_doors = [d for d in door_free if door_type[d] == "inbound"]
    carrier_ids_list = list(carrier_ids)
    for d in range(days):
        day = (start + timedelta(days=d)).replace(hour=0, minute=0, second=0, microsecond=0)
        if day.weekday() < 5 and rng.random() < 0.5:
            sched = day + hours(rng.randint(6, 18))
            appts.append({
                "site_id": site_id, "door_id": rng.choice(inbound_doors),
                "carrier_id": rng.choice(carrier_ids_list), "trailer_id": None,
                "direction": "inbound", "scheduled_start": ts(sched),
                "scheduled_end": ts(sched + hours(2)), "status": "no_show",
            })
    for d in range(0, 3):
        day = (now + timedelta(days=d)).replace(hour=0, minute=0, second=0, microsecond=0)
        n = 4 if day.weekday() >= 5 else 14
        for _ in range(n):
            sched = day + hours(rng.randint(5, 19)) + timedelta(minutes=rng.choice([0, 30]))
            if sched <= now or sched > now + timedelta(hours=48):
                continue
            appts.append({
                "site_id": site_id, "door_id": rng.choice(inbound_doors),
                "carrier_id": rng.choice(carrier_ids_list), "trailer_id": None,
                "direction": "inbound", "scheduled_start": ts(sched),
                "scheduled_end": ts(sched + hours(2)), "status": "scheduled",
            })

    # Door status reflects trailers currently at doors
    occupied = {t["current_location"] for t in trailers if t["status"] == "at_door"}
    for dr in doors:
        if dr["door_id"] in occupied:
            dr["status"] = "occupied"

    return trailers, moves, appts


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------
def insert(conn, table, rows):
    if not rows:
        return
    cols = list(rows[0].keys())
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
    conn.executemany(sql, [tuple(r[c] for c in cols) for r in rows])


def main():
    parser = argparse.ArgumentParser(description="Seed the Warehouse Ops database.")
    parser.add_argument("--db", default=str(HERE / "warehouse.db"), help="SQLite file path")
    parser.add_argument("--days", type=int, default=90, help="Days of history to generate")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    now = datetime.now(timezone.utc).replace(tzinfo=None, second=0, microsecond=0)
    start = now - timedelta(days=args.days)

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_PATH.read_text())

    insert(conn, "sites", [dict(zip(["site_id", "name", "city", "state", "timezone"], s))
                           for s in SITES])
    insert(conn, "carriers", [{"carrier_id": i + 1, "name": n, "scac": c}
                              for i, (n, c) in enumerate(CARRIERS)])
    carrier_ids = range(1, len(CARRIERS) + 1)

    skus = build_skus(rng)
    insert(conn, "skus", skus)
    base_weight, trend = build_demand_profile(rng, skus)

    trailer_counter, order_counter = [0], [0]
    for site_id, *_ in SITES:
        doors = build_doors(site_id)
        locations = build_locations(site_id)
        site_factor = {s["sku"]: rng.lognormvariate(0, 0.3) for s in skus}

        slot_map = slot_skus(rng, site_id, skus, locations, base_weight, site_factor)
        inventory = build_inventory(rng, site_id, slot_map, locations, base_weight)
        picks = build_picks(rng, site_id, skus, slot_map, base_weight, trend,
                            site_factor, start, now, args.days, order_counter)
        trailers, moves, appts = build_yard(rng, site_id, doors, carrier_ids,
                                            start, now, args.days, trailer_counter)

        insert(conn, "dock_doors", doors)
        insert(conn, "locations", locations)
        insert(conn, "inventory", inventory)
        insert(conn, "pick_lines", picks)
        insert(conn, "trailers", trailers)
        insert(conn, "yard_moves", moves)
        insert(conn, "appointments", appts)

    conn.execute("INSERT INTO meta (key, value) VALUES ('seeded_at', ?), ('history_days', ?)",
                 (ts(now), str(args.days)))
    conn.commit()

    print(f"Seeded {db_path} (reference time {ts(now)} UTC, {args.days} days of history)\n")
    for table in ["sites", "dock_doors", "carriers", "trailers", "yard_moves",
                  "appointments", "skus", "locations", "inventory", "pick_lines"]:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table:<14} {count:>8,}")
    conn.close()


if __name__ == "__main__":
    main()
