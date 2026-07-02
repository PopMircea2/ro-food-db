# ro-food-db

Romanian food database for on-device search, rebuilt weekly from the
[Open Food Facts](https://openfoodfacts.org) bulk CSV export.

The build keeps products sold in Romania that have a name and at least one
non-zero macro, and publishes to the fixed-tag
[`ro-food-db` release](../../releases/tag/ro-food-db):

- `ro-foods.sqlite.gz` — SQLite database with an FTS5 index
  (`unicode61 remove_diacritics 2`, so `branza` matches `brânză`)
- `manifest.json` — `{version, count, staples, sha256, bytes, url}` for update checks

The database also carries a `staples` table — curated generic foods
(piept de pui, orez fiert, sarmale) that OFF's packaged-goods data lacks.
It is edited as a Notion database, pulled into [`staples.csv`](staples.csv)
by `scripts/sync_staples.py` on every build, and searched first by the app.
Macro sources per row: USDA FoodData Central, CIQUAL, or reviewed estimates.

Build locally (stdlib-only, streams ~1 GB):

```sh
python3 scripts/build_ro_food_db.py
```

## License

Data © [Open Food Facts](https://openfoodfacts.org) contributors, available
under the [Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/1-0/).
