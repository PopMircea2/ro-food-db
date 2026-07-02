#!/usr/bin/env python3
"""Sync staples.csv from the Notion staples database.

The curated staples table (generic Romanian foods — piept de pui, orez fiert,
sarmale — that OFF's packaged-goods data doesn't cover) is edited in Notion
and mirrored here. This script pulls every row via the Notion API, validates
it, and rewrites staples.csv at the repo root; the weekly workflow commits the
result and build_ro_food_db.py compiles it into the published SQLite.

Needs NOTION_TOKEN (internal integration secret, read-only, shared with the
staples database only). Stdlib-only, like the build script:

    NOTION_TOKEN=ntn_... python3 scripts/sync_staples.py

Rows are skipped silently only when they are clearly not foods (no slug and
no macros — e.g. stray sub-pages). A row that looks like a food but fails
validation aborts the sync with a message naming it, so a typo in Notion
can't silently drop a food from the app.
"""

import csv
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

DATABASE_ID = "45546799a7c842f8969697926876660a"
API = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
NOTION_VERSION = "2022-06-28"

FIELDNAMES = ["slug", "name", "aliases", "category",
              "kcal_100g", "protein_100g", "carbs_100g", "fat_100g",
              "portion_g", "portion_label", "source", "reviewed"]

SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def fetch_pages(token):
    """Yield every page of the database, following pagination."""
    cursor = None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        req = urllib.request.Request(API, method="POST", data=json.dumps(body).encode(), headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        })
        with urllib.request.urlopen(req) as resp:
            data = json.load(resp)
        yield from data["results"]
        if not data.get("has_more"):
            return
        cursor = data["next_cursor"]


def _plain(prop):
    """Concatenated plain text of a title / rich_text property."""
    return "".join(t["plain_text"] for t in prop.get(prop["type"], [])).strip()


def row_from_page(page):
    """Map a Notion page's properties onto a staples.csv row dict."""
    p = page["properties"]

    def num(name):
        return p[name]["number"] if name in p else None

    def select(name):
        opt = p[name]["select"] if name in p else None
        return opt["name"] if opt else ""

    return {
        "slug": _plain(p["Slug"]),
        "name": _plain(p["Name (RO)"]),
        "aliases": _plain(p["Aliases"]),
        "category": select("Category"),
        "kcal_100g": num("Kcal/100g"),
        "protein_100g": num("Protein/100g"),
        "carbs_100g": num("Carbs/100g"),
        "fat_100g": num("Fat/100g"),
        "portion_g": num("Portion (g)"),
        "portion_label": _plain(p["Portion label"]),
        "source": select("Source"),
        "reviewed": 1 if p["Reviewed"]["checkbox"] else 0,
    }


MACROS = ["kcal_100g", "protein_100g", "carbs_100g", "fat_100g"]


def validate(rows):
    """Return (valid_rows, errors). Rows with no slug and no macros are dropped."""
    valid, errors, seen = [], [], {}
    for row in rows:
        label = row["slug"] or row["name"] or "<untitled>"
        if not row["slug"] and all(row[m] is None for m in MACROS):
            print(f"skipping non-food row: {label!r}")
            continue
        if not SLUG_RE.fullmatch(row["slug"] or ""):
            errors.append(f"{label!r}: missing or invalid slug (want kebab-case)")
            continue
        if row["slug"] in seen:
            errors.append(f"{label!r}: duplicate slug (also {seen[row['slug']]!r})")
            continue
        seen[row["slug"]] = row["name"]
        if not row["name"]:
            errors.append(f"{label!r}: missing name")
        missing = [m for m in MACROS if row[m] is None]
        if missing:
            errors.append(f"{label!r}: missing {', '.join(missing)}")
            continue
        if not 0 <= row["kcal_100g"] <= 900:
            errors.append(f"{label!r}: kcal_100g {row['kcal_100g']} outside 0–900")
        for m in MACROS[1:]:
            if not 0 <= row[m] <= 100:
                errors.append(f"{label!r}: {m} {row[m]} outside 0–100 g")
        if row["portion_g"] is not None and not 0 < row["portion_g"] <= 1000:
            errors.append(f"{label!r}: portion_g {row['portion_g']} outside 0–1000 g")
        # Atwater cross-check is advisory only — recipes with alcohol/fibre drift
        computed = 4 * row["protein_100g"] + 4 * row["carbs_100g"] + 9 * row["fat_100g"]
        if abs(computed - row["kcal_100g"]) > max(35, 0.25 * row["kcal_100g"]):
            print(f"warning: {label!r} kcal {row['kcal_100g']} vs "
                  f"{computed:.0f} computed from macros")
        valid.append(row)
    return valid, errors


def _fmt(v):
    if isinstance(v, float) and v == int(v):
        return int(v)
    return v


def write_csv(rows, path):
    """Write rows sorted by slug, atomically (never leaves a partial file)."""
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        writer = csv.DictWriter(f, FIELDNAMES, lineterminator="\n")
        writer.writeheader()
        for row in sorted(rows, key=lambda r: r["slug"]):
            writer.writerow({k: _fmt(v) for k, v in row.items()})
    tmp.replace(path)


def main() -> int:
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        print("FAIL: NOTION_TOKEN is not set", file=sys.stderr)
        return 1

    rows = [row_from_page(page) for page in fetch_pages(token)]
    valid, errors = validate(rows)
    if errors:
        for e in errors:
            print(f"FAIL: {e}", file=sys.stderr)
        return 1
    if len(valid) < 50:
        print(f"FAIL: only {len(valid)} staples (expected ≥50) — "
              "refusing to shrink the table this much", file=sys.stderr)
        return 1

    out = Path(__file__).resolve().parent.parent / "staples.csv"
    write_csv(valid, out)
    unreviewed = sum(1 for r in valid if not r["reviewed"] and r["source"] == "Estimate — verify")
    print(f"wrote {out} — {len(valid)} staples ({unreviewed} unreviewed estimates)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
