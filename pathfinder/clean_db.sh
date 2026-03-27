#!/bin/bash
# Clear seen jobs so the next run re-scores everything.
# Run from the repo root: bash pathfinder/clean_db.sh

DB="pathfinder/data/tracker.db"

if [ ! -f "$DB" ]; then
    echo "Database not found at $DB"
    exit 1
fi

BEFORE=$(sqlite3 "$DB" "SELECT COUNT(*) FROM seen_jobs;")
sqlite3 "$DB" "DELETE FROM seen_jobs;"
echo "Cleared $BEFORE seen job IDs. Next run will score everything fresh."
