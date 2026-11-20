#!/bin/sh
# Application roles: none owns tables, none can bypass row-level security.
set -e
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<EOSQL
CREATE ROLE tl_ingest LOGIN PASSWORD '${PG_INGEST_PASSWORD}' NOBYPASSRLS;
CREATE ROLE tl_worker LOGIN PASSWORD '${PG_WORKER_PASSWORD}' NOBYPASSRLS;
CREATE ROLE tl_api    LOGIN PASSWORD '${PG_API_PASSWORD}'    NOBYPASSRLS;
EOSQL
