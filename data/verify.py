"""
Sanity checks for the seeded database. Run from the project root:
    python data/verify.py

Each query is a preview of a question the MCP tools will answer later.
"""

import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent / "warehouse.db"

CHECKS = [
    ("Trailers currently on site, by status", """
        SELECT site_id, status, COUNT(*) AS trailers
        FROM trailers WHERE status != 'departed'
        GROUP BY site_id, status ORDER BY site_id, status
    """),
    ("Long-dwell trailers (in yard > 48 hours)", """
        SELECT t.site_id, t.trailer_id, c.scac, t.direction, t.load_status,
               t.current_location,
               ROUND((julianday((SELECT value FROM meta WHERE key = 'seeded_at'))
                      - julianday(t.checked_in_at)) * 24, 1) AS dwell_hours
        FROM trailers t JOIN carriers c USING (carrier_id)
        WHERE t.status = 'in_yard'
          AND (julianday((SELECT value FROM meta WHERE key = 'seeded_at'))
               - julianday(t.checked_in_at)) * 24 > 48
        ORDER BY dwell_hours DESC LIMIT 12
    """),
    ("Dock door status", """
        SELECT site_id, door_type, status, COUNT(*) AS doors
        FROM dock_doors GROUP BY site_id, door_type, status
        ORDER BY site_id, door_type, status
    """),
    ("Appointments by status", """
        SELECT site_id, status, COUNT(*) AS appts
        FROM appointments GROUP BY site_id, status ORDER BY site_id, status
    """),
    ("Top 15 SKUs by picks (last 30 days, CHI1) and where they sit", """
        SELECT p.sku, COUNT(*) AS pick_lines, l.location_id,
               l.travel_distance_ft AS dist_ft, l.is_golden_zone AS golden
        FROM pick_lines p JOIN locations l ON l.location_id = p.location_id
        WHERE p.site_id = 'CHI1'
          AND p.picked_at >= datetime((SELECT value FROM meta WHERE key = 'seeded_at'), '-30 days')
        GROUP BY p.sku ORDER BY pick_lines DESC LIMIT 15
    """),
    ("Pick volume: share of lines from top 20% of SKUs (should be ~70-80%)", """
        WITH ranked AS (
            SELECT sku, COUNT(*) AS n,
                   ROW_NUMBER() OVER (ORDER BY COUNT(*) DESC) AS rk
            FROM pick_lines GROUP BY sku
        )
        SELECT ROUND(100.0 * SUM(CASE WHEN rk <= (SELECT COUNT(*) FROM ranked) * 0.2
                                      THEN n END) / SUM(n), 1) AS pct_top_20
        FROM ranked
    """),
]


def print_table(cur):
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    widths = [max(len(str(c)), *(len(str(r[i])) for r in rows)) if rows else len(c)
              for i, c in enumerate(cols)]
    print("  " + "  ".join(str(c).ljust(w) for c, w in zip(cols, widths)))
    print("  " + "  ".join("-" * w for w in widths))
    for r in rows:
        print("  " + "  ".join(str(v).ljust(w) for v, w in zip(r, widths)))
    if not rows:
        print("  (no rows)")


def main():
    if not DB.exists():
        raise SystemExit(f"{DB} not found. Run: python data/seed.py")
    conn = sqlite3.connect(DB)
    for title, sql in CHECKS:
        print(f"\n=== {title} ===")
        print_table(conn.execute(sql))
    conn.close()


if __name__ == "__main__":
    main()
