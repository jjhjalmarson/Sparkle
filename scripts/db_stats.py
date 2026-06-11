"""Quick state-of-the-hound: stage counts, verdicts, samples, run history.

Usage: python scripts/db_stats.py [path-to-db]
"""

import sqlite3
import sys

db = sys.argv[1] if len(sys.argv) > 1 else "data/sketchhound.db"
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row

print("== stage counts ==")
for r in conn.execute("SELECT stage_reached, COUNT(*) n FROM listings GROUP BY 1 ORDER BY n DESC"):
    print(f"  {r['stage_reached']}: {r['n']}")

print("== haiku verdicts ==")
for r in conn.execute("SELECT haiku_verdict, COUNT(*) n FROM listings WHERE haiku_verdict IS NOT NULL GROUP BY 1"):
    print(f"  {r['haiku_verdict']}: {r['n']}")

print("== sample gate survivors ==")
for r in conn.execute("SELECT title FROM listings WHERE stage_reached='gate_survivor' LIMIT 8"):
    print(f"  - {r['title'][:90]}")

print("== sample negative-keyword rejects ==")
for r in conn.execute("SELECT title FROM listings WHERE stage_reached='negative_keyword_reject' LIMIT 5"):
    print(f"  - {r['title'][:90]}")

print("== vision-scored (top confidence) ==")
for r in conn.execute(
    "SELECT confidence, attributed_artist, title FROM listings "
    "WHERE stage_reached='vision_scored' ORDER BY confidence DESC LIMIT 10"
):
    print(f"  {r['confidence']:.2f}  {(r['attributed_artist'] or '?'):<14} {r['title'][:70]}")

print("== runs ==")
for r in conn.execute("SELECT id, started_at, fetched_count, new_count, vision_call_count, est_cost_usd, errors FROM runs"):
    import json

    errs = len(json.loads(r["errors"]))
    print(
        f"  #{r['id']} {r['started_at'][:16]} fetched={r['fetched_count']} new={r['new_count']} "
        f"vision={r['vision_call_count']} cost=${r['est_cost_usd']:.2f} errors={errs}"
    )
