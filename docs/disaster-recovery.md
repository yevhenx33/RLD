# RLD Disaster Recovery Targets

This document defines baseline recovery objectives and validation procedures for production operations.

## Recovery Objectives

| Domain | RPO target | RTO target | Source of truth |
| --- | --- | --- | --- |
| Simulation PostgreSQL (`rld_indexer`) | <= 24 hours | <= 60 minutes | `backups/<date>/rld_indexer.sql.gz` |
| Analytics schema (ClickHouse) | <= 24 hours | <= 90 minutes | `backups/<date>/clickhouse_schema.sql.gz` |
| Chain runtime state (Reth datadir snapshots) | <= 24 hours | <= 120 minutes | host-level snapshot + restart flow |

## Validation Contract

- Daily backup: `docker/scripts/backup-databases.sh`
- Daily restore drill: `docker/scripts/validate-backup-restore.sh`
- Status surfaces:
  - `backups/last_backup.json`
  - `backups/last_restore_check.json`
  - `docker/dashboard/status.json` (`backups` + `restore_checks`)

## Alerting Contract

- `docker/scripts/emit-alerts.py` sends change-based alerts when:
  - stack gates are degraded/critical
  - backup status is `failed` or `partial`
  - restore check status is `failed`
- Recovery notifications are emitted when status returns healthy.

## Manual Restore Drill (Operator)

1. Confirm latest `last_backup.json` status is `success`.
2. Run `bash docker/scripts/validate-backup-restore.sh`.
3. Confirm `last_restore_check.json` has:
   - `"status": "success"`
   - `"tables_restored" > 0`
4. Record any failures and open an incident if objective windows are at risk.
