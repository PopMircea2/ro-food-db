# ro-food-db

Weekly pipeline that fills the WorkoutJournal Supabase backend with Romanian
food data from the [Open Food Facts](https://openfoodfacts.org) bulk CSV
export.

Every Monday the Action streams the export, keeps products sold in Romania
that have a name and at least one non-zero macro, and upserts them into the
Supabase `foods` table (keyed on barcode). The app searches Supabase
directly — nothing is published here.

Only `foods` is machine-owned: the `staples` and `recipes` tables are
authored directly in Supabase's table editor and are never touched by this
pipeline.

Build the staging database locally (stdlib-only, streams ~1 GB):

```sh
python3 scripts/build_ro_food_db.py
```

## License

Data © [Open Food Facts](https://openfoodfacts.org) contributors, available
under the [Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/1-0/).
