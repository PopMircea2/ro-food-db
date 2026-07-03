#!/usr/bin/env python3
"""Mirror the built dataset into Supabase — the app queries it directly.

Reads dist/ro-foods.sqlite (produced by build_ro_food_db.py, so the same
filters and dedup already applied) and upserts every row into the Supabase
`foods` table via PostgREST, keyed on barcode.

Only `foods` is machine-owned. The `staples` and `recipes` tables are
authored directly in Supabase's table editor and must never be written here.

Env (GitHub Action secrets):
    SUPABASE_URL         https://<project>.supabase.co
    SUPABASE_SECRET_KEY  sb_secret_... — service key, never ships in the app

Stdlib-only, like the rest of the pipeline.
"""

import json
import math
import os
import re
import sqlite3
import sys
import urllib.request
from pathlib import Path

BATCH = 500

# Unit inside the serving display text, e.g. "30 g" or "2 biscuits (25 ml)".
# The bulk CSV export has no serving_quantity_unit column (checked 2026-07-03),
# so this is the only unit source available to the pipeline.
SERVING_UNIT_RE = re.compile(r"\d\s*(g|ml)\b", re.IGNORECASE)


def upsert(base, key, table, conflict, rows):
    """POST rows in batches with PostgREST merge-duplicates semantics."""
    for start in range(0, len(rows), BATCH):
        chunk = rows[start:start + BATCH]
        req = urllib.request.Request(
            f"{base}/rest/v1/{table}?on_conflict={conflict}",
            data=json.dumps(chunk).encode(),
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
            method="POST")
        with urllib.request.urlopen(req) as resp:
            if resp.status not in (200, 201, 204):
                raise RuntimeError(f"{table} upsert: HTTP {resp.status}")
    print(f"upserted {len(rows)} rows into {table}")


def _finite(v, default=None):
    """Non-finite floats serialise as Infinity/NaN — invalid JSON that fails
    the whole PostgREST batch. build's _num filters them, but guard here too:
    this is the boundary where a stray value actually breaks the sync."""
    return v if v is not None and math.isfinite(v) else default


def food_rows(db):
    for (code, name, brands, quantity, serving_quantity, serving_size,
         kcal, protein, carbs, fat) in db.execute(
            "SELECT code, name, brands, quantity, serving_quantity, "
            "serving_size, kcal_100g, protein_100g, carbs_100g, fat_100g "
            "FROM products"):
        try:
            serving = _finite(float(serving_quantity)) if serving_quantity else None
        except ValueError:
            serving = None
        unit = SERVING_UNIT_RE.search(serving_size or "")
        yield {
            "barcode": code,
            "name": name,
            "brand": brands,
            "package_qty": quantity,
            "serving_size": serving,        # numeric, normalised to g/ml
            "serving_unit": unit.group(1).lower() if unit else None,
            "serving_text": serving_size,   # display text, e.g. "30 g"
            # OFF products may report only some macros (usable() requires just
            # one non-zero); the foods columns are NOT NULL, and the app reads
            # missing macros as 0 anyway.
            "kcal_100g": _finite(kcal, 0),
            "protein_100g": _finite(protein, 0),
            "carbs_100g": _finite(carbs, 0),
            "fat_100g": _finite(fat, 0),
            "source": "off",
            "country": "ro",
        }


def main() -> int:
    base = os.environ["SUPABASE_URL"].rstrip("/")
    secret = os.environ["SUPABASE_SECRET_KEY"]

    db_path = Path(__file__).resolve().parent.parent / "dist" / "ro-foods.sqlite"
    if not db_path.exists():
        print(f"FAIL: {db_path} missing — run build_ro_food_db.py first",
              file=sys.stderr)
        return 1

    db = sqlite3.connect(db_path)
    upsert(base, secret, "foods", "barcode", list(food_rows(db)))
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
