#!/usr/bin/env python3
"""Build the on-device Romanian food database from Open Food Facts.

Streams the official OFF bulk CSV export (tab-separated, unthrottled static
host — the Hugging Face parquet 429s anonymous readers), keeps products sold
in Romania that have a name and at least one non-zero macro (the same
usability filter the app applies to live OFF results), and writes:

    dist/ro-foods.sqlite.gz   SQLite DB with an FTS5 index (see schema below)
    dist/manifest.json        {version, count, sha256, url} checked by the app

Run weekly by .github/workflows/build-ro-food-db.yml; stdlib-only, so run
locally with plain `python3 scripts/build_ro_food_db.py`.

Data is © Open Food Facts contributors, ODbL — the app shows attribution.
"""

import argparse
import csv
import gzip
import hashlib
import json
import sqlite3
import sys
import urllib.request
from datetime import date
from pathlib import Path

CSV_URL = ("https://static.openfoodfacts.org/data/"
           "en.openfoodfacts.org.products.csv.gz")

# Column names verified against the export header on 2026-07-02. The export
# is flat TSV, no quoting, one product per line; countries_tags is
# comma-separated. Order here must match the products table columns.
COLUMNS = ["code", "product_name", "brands", "quantity", "serving_quantity",
           "serving_size", "energy-kcal_100g", "proteins_100g",
           "carbohydrates_100g", "fat_100g"]


def _num(s):
    try:
        return float(s)
    except ValueError:
        return None


def romanian_rows(source):
    """Stream the export, yielding COLUMNS values for products sold in Romania."""
    if source.startswith(("http://", "https://")):
        req = urllib.request.Request(source, headers={
            "User-Agent": "WorkoutJournal-ro-food-db "
                          "(github.com/PopMircea2/WorkoutJournal)"})
        raw = urllib.request.urlopen(req)
    else:
        raw = open(source, "rb")
    with raw, gzip.open(raw, "rt", encoding="utf-8", newline="\n") as text:
        header = next(text).rstrip("\n").split("\t")
        take = [header.index(c) for c in COLUMNS]
        country = header.index("countries_tags")
        for line in text:
            if "en:romania" not in line:  # cheap prefilter; exact check below
                continue
            fields = line.rstrip("\n").split("\t")
            if (len(fields) <= country or not fields[take[0]]
                    or "en:romania" not in fields[country].split(",")):
                continue
            row = [fields[i] if i < len(fields) else "" for i in take]
            yield row[:6] + [_num(v) for v in row[6:]]


SCHEMA = """
    CREATE TABLE products(
      code TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      brands TEXT,
      quantity TEXT,
      serving_quantity TEXT,
      serving_size TEXT,
      kcal_100g REAL,
      protein_100g REAL,
      carbs_100g REAL,
      fat_100g REAL
    );
    -- remove_diacritics 2 folds ă/â/î/ș/ț so "branza" matches "brânză".
    -- Must match the tokenizer LocalFoodDatabase.swift queries with.
    CREATE VIRTUAL TABLE products_fts USING fts5(
      name, brands, content='products', content_rowid='rowid',
      tokenize = "unicode61 remove_diacritics 2"
    );
    -- Curated generic staples (piept de pui, orez fiert, sarmale) that OFF's
    -- packaged-goods data lacks — synced from Notion into staples.csv by
    -- scripts/sync_staples.py. The app searches this tier first.
    CREATE TABLE staples(
      slug TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      aliases TEXT,
      category TEXT,
      kcal_100g REAL NOT NULL,
      protein_100g REAL NOT NULL,
      carbs_100g REAL NOT NULL,
      fat_100g REAL NOT NULL,
      portion_g REAL,
      portion_label TEXT
    );
    CREATE VIRTUAL TABLE staples_fts USING fts5(
      name, aliases, content='staples', content_rowid='rowid',
      tokenize = "unicode61 remove_diacritics 2"
    );
"""


def staple_rows(path):
    """Rows of staples.csv in staples-table column order."""
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            yield (r["slug"], r["name"], r["aliases"] or None,
                   r["category"] or None,
                   float(r["kcal_100g"]), float(r["protein_100g"]),
                   float(r["carbs_100g"]), float(r["fat_100g"]),
                   float(r["portion_g"]) if r["portion_g"] else None,
                   r["portion_label"] or None)


def usable(row) -> bool:
    """Mirror of the live-result filter in FoodSearchService.performSearch."""
    name = (row[1] or "").strip()
    macros = [v or 0 for v in row[6:10]]
    return bool(name) and name != "Unknown" and any(v > 0 for v in macros)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="dist", help="output directory")
    ap.add_argument("--min-count", type=int, default=1000,
                    help="fail if fewer usable products (bad upstream dump guard)")
    ap.add_argument("--url", default="https://github.com/PopMircea2/ro-food-db/"
                    "releases/download/ro-food-db/ro-foods.sqlite.gz")
    ap.add_argument("--csv", default=CSV_URL,
                    help="export URL or local .csv.gz path (for testing)")
    ap.add_argument("--staples", default=str(Path(__file__).resolve().parent.parent
                    / "staples.csv"), help="curated staples CSV (see sync_staples.py)")
    args = ap.parse_args()

    staples = list(staple_rows(args.staples))

    print(f"streaming {args.csv} ...", flush=True)
    total, kept = 0, []
    for r in romanian_rows(args.csv):
        total += 1
        if usable(r):
            kept.append(r)
    print(f"{total} Romanian products, {len(kept)} usable")
    if len(kept) < args.min_count:
        print(f"FAIL: only {len(kept)} usable products (min {args.min_count}); "
              "refusing to publish a suspicious dataset", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    db_path = out / "ro-foods.sqlite"
    db_path.unlink(missing_ok=True)

    db = sqlite3.connect(db_path)
    db.executescript(SCHEMA)
    # INSERT OR REPLACE: the dump occasionally repeats a barcode; last wins.
    # Empty TSV fields become NULL, matching what the parquet build stored.
    db.executemany(
        "INSERT OR REPLACE INTO products VALUES (?,?,?,?,?,?,?,?,?,?)",
        ((r[0], r[1].strip(), *(v or None for v in r[2:6]), *r[6:]) for r in kept),
    )
    db.execute("INSERT INTO products_fts(products_fts) VALUES ('rebuild')")
    db.execute("INSERT INTO products_fts(products_fts) VALUES ('optimize')")
    db.executemany("INSERT INTO staples VALUES (?,?,?,?,?,?,?,?,?,?)", staples)
    db.execute("INSERT INTO staples_fts(staples_fts) VALUES ('rebuild')")
    db.execute("INSERT INTO staples_fts(staples_fts) VALUES ('optimize')")
    db.commit()
    count = db.execute("SELECT count(*) FROM products").fetchone()[0]
    db.execute("VACUUM")
    db.close()

    gz_path = out / "ro-foods.sqlite.gz"
    with open(db_path, "rb") as src, gzip.open(gz_path, "wb", compresslevel=9) as dst:
        dst.write(src.read())
    sha = hashlib.sha256(gz_path.read_bytes()).hexdigest()

    manifest = {
        "version": date.today().isoformat(),
        "count": count,
        "staples": len(staples),
        "sha256": sha,
        # uncompressed size: lets the app inflate with a single preallocated buffer
        "bytes": db_path.stat().st_size,
        "url": args.url,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {db_path} ({db_path.stat().st_size:,} B), "
          f"{gz_path} ({gz_path.stat().st_size:,} B), "
          f"{count} products + {len(staples)} staples")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
