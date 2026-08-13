"""sync_bq.py — sync the local SQLite ground truth to BigQuery.

Replicates the star-schema tables and the v_cell_flat serving view to the 
BigQuery dataset `findociq`.

Usage:
    python3 findociq/pipeline/ingest/sync_bq.py [--db PATH]
"""
from __future__ import annotations

import argparse
import sqlite3
import pandas as pd
from google.cloud import bigquery
from pathlib import Path

PROJECT = "igc2026-team08-6311"
DATASET = "findociq"
REPO = Path(__file__).resolve().parents[3]
DEFAULT_DB = REPO / "findociq" / "db" / "compiled_fs.db"

# SQLite tables/views -> BigQuery tables
# Note: v_cell_flat is a VIEW in SQLite but is materialised as a TABLE in BQ --
# `pd.read_sql_query(SELECT * FROM <name>, ...)` doesn't care whether the source
# is a table or a view, and BQ load always writes a table, so no special-casing
# is needed beyond listing the name here.
#
# The concept-layer entries (concept_map, concept_resolution_log, fact_metric,
# v_fact_metric_serving) were REMOVED 2026-08-12 with pipeline/concept/: none of
# them is declared in schema_v7.sql, fact_metric/v_fact_metric_serving are absent
# from the built DB, and concept_resolution_log was empty. Listing a name that no
# longer exists makes sync fail on `SELECT * FROM <missing>`, so this list must
# track the schema.
TABLES_TO_SYNC = [
    "document",
    "section",
    "table_t",
    "segment_dim",             # was "dim_segment" (wrong name -> no such table)
    "geo_dim",                 # was "dim_geo"
    "industry_dim",            # industry-of-exposure axis (mirrors segment_dim)
    "v_cell_flat",
    "ingest_status",
]

def sync(db_path: str | Path):
    client = bigquery.Client(project=PROJECT)
    dataset_ref = client.dataset(DATASET)
    
    conn = sqlite3.connect(str(db_path))
    
    print(f"Syncing {db_path} to BigQuery dataset {PROJECT}.{DATASET}...")
    
    for table in TABLES_TO_SYNC:
        print(f"  -> {table}...", end="", flush=True)
        try:
            # Read from SQLite
            df = pd.read_sql_query(f"SELECT * FROM {table}", conn)
            
            if df.empty:
                print(" skipped (empty)")
                continue
                
            # Job configuration: write_truncate to replace the table
            job_config = bigquery.LoadJobConfig(
                write_disposition="WRITE_TRUNCATE",
            )
            
            # Load to BigQuery
            table_id = f"{PROJECT}.{DATASET}.{table}"
            job = client.load_table_from_dataframe(df, table_id, job_config=job_config)
            job.result()  # Wait for completion
            
            print(f" done ({len(df)} rows)")
        except Exception as e:
            print(f" FAILED: {e}")
            
    conn.close()

def main():
    ap = argparse.ArgumentParser(description="Sync SQLite to BigQuery.")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()
    
    sync(args.db)

if __name__ == "__main__":
    main()
