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
import os
import sqlite3
import sys
import urllib.request
from pathlib import Path

BATCH = 500


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


def food_rows(db):
    for (code, name, brands, quantity, serving_quantity, serving_size,
         kcal, protein, carbs, fat) in db.execute(
            "SELECT code, name, brands, quantity, serving_quantity, "
            "serving_size, kcal_100g, protein_100g, carbs_100g, fat_100g "
            "FROM products"):
        try:
            serving = float(serving_quantity) if serving_quantity else None
        except ValueError:
            serving = None
        yield {
            "barcode": code,
            "name": name,
            "brand": brands,
            "package_qty": quantity,
            "serving_size": serving,        # numeric, normalised to g/ml
            "serving_text": serving_size,   # display text, e.g. "30 g"
            # OFF products may report only some macros (usable() requires just
            # one non-zero); the foods columns are NOT NULL, and the app reads
            # missing macros as 0 anyway.
            "kcal_100g": kcal or 0,
            "protein_100g": protein or 0,
            "carbs_100g": carbs or 0,
            "fat_100g": fat or 0,
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
