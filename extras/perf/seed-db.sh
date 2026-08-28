#!/usr/bin/env bash
# Put a plausible day of detections in the station's database.
#
# An empty station renders the empty-nest card, which exercises almost none of
# what makes a render expensive - no packing, no label planning, no
# illustrations. The interesting path needs birds, and it needs the SAME birds
# every time or two runs are not comparable.
#
#   docker compose exec -u bird pi perf-seed [hours-span]
#
# Counts have the shape of a real day: one loud bird and a long tail, which is
# what the packer's count-to-area exponent was tuned against. The species are
# ones the bundled illustration set actually has, so a render fetches real
# cutouts rather than 404ing and drawing nothing.
#
# SQLite generates the rows from a recursive CTE rather than the shell looping.
# A thousand `date` subprocesses on one throttled core takes longer than the
# render being measured.
set -euo pipefail

DB=${DB:-$HOME/BirdNET-Pi/scripts/birds.db}
SPAN=${1:-24}

[ -f "$DB" ] || { echo "no database at $DB" >&2; exit 1; }

# sci|common|count|hours-ago-of-most-recent
BIRDS="Cardinalis cardinalis|Northern Cardinal|420|0.08
Cyanocitta cristata|Blue Jay|260|0.40
Spinus tristis|American Goldfinch|150|3
Poecile atricapillus|Black-capped Chickadee|88|6
Sitta carolinensis|White-breasted Nuthatch|61|9
Corvus brachyrhynchos|American Crow|40|12
Quiscalus quiscula|Common Grackle|22|15
Passer domesticus|House Sparrow|14|18
Dryobates villosus|Hairy Woodpecker|6|20
Melanerpes carolinus|Red-bellied Woodpecker|3|22"

sql=$(mktemp); trap 'rm -f "$sql"' EXIT
echo "BEGIN;" > "$sql"
echo "DELETE FROM detections;" >> "$sql"
printf '%s\n' "$BIRDS" | while IFS='|' read -r sci com n ago; do
  [ -n "${sci:-}" ] || continue
  cat >> "$sql" <<SQL
WITH RECURSIVE seq(i) AS (SELECT 0 UNION ALL SELECT i+1 FROM seq WHERE i+1 < $n)
INSERT INTO detections
SELECT date(dt), time(dt), '$sci', '$com', 0.9, 0.0, 0.0, 0.7,
       CAST(strftime('%W','now') AS INT), 1.25, 0.0, 'seed.mp3'
FROM (SELECT datetime('now', '-' ||
        ($ago * 3600.0 + ($SPAN - $ago) * 3600.0 * i / MAX($n - 1, 1)) ||
        ' seconds') AS dt FROM seq);
SQL
done
echo "COMMIT;" >> "$sql"

sqlite3 "$DB" < "$sql"
echo "seeded $(sqlite3 "$DB" 'SELECT count(*) FROM detections;') detections across $(sqlite3 "$DB" 'SELECT count(DISTINCT Sci_Name) FROM detections;') species"
sqlite3 "$DB" "SELECT Com_Name, count(*), max(Date||' '||Time) FROM detections GROUP BY Com_Name ORDER BY 2 DESC LIMIT 3;"
