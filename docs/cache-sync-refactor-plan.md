# Implementation Plan: Shared Entry Cache Refactor

**Date:** 2026-04-11
**Status:** Proposed
**Priority:** High

## Summary

This plan replaces the app's feature-specific raw Toggl entry caches with one shared entry-cache layer so that:

- dashboard refresh, CSV export, and Stripe draft invoices stay in sync
- capped `last_billed_date` billing flows continue to honor `cap_fill_date`
- carryover calculations can be refreshed from the same source of truth
- manual refresh behavior becomes range-based instead of cache-family-specific

This is a design and migration plan only. It does not describe shipped behavior yet and should not be copied into `docs/SOT.md` until implementation lands.

## Why This Exists

The current app caches the same raw Toggl entry data in multiple overlapping namespaces:

- `daily_*`
- `weekly_*`
- `monthly_*`
- `lbd_*`
- `export_*`

That split was reasonable for API minimization, but the invalidation model drifted. Manual refresh currently clears the dashboard-facing caches and `lbd_*`, but not `export_*`. As a result, the dashboard can show refreshed data while CSV export and Stripe invoice creation still use stale entry payloads.

The latest commit (`c11e787`, "Trim unbilled export range to cap fill date") already moved product behavior toward agreement between dashboard and billing outputs by reusing `cap_fill_date`. This refactor finishes that job at the cache layer.

## Goals

- Create one shared raw-entry cache used by dashboard, export, invoice, and capped-range calculations
- Preserve low Toggl API usage
- Preserve the current `cap_fill_date` export/invoice semantics
- Make manual refresh semantics correct and understandable
- Add tests that lock the sync behavior before implementation
- Document the shipped behavior clearly in `docs/SOT.md` once complete

## Non-Goals

- No user-facing UI redesign
- No change to the separate project metadata cache (`projects.json`)
- No change to billing rules, export CSV format, or Stripe invoice workflow
- No speculative cache optimization beyond what is needed for correctness and maintainability

## Current State

### Confirmed Inputs Reviewed

- Current `docs/SOT.md`
- Last commit `c11e78741c0fddbc2539fdcbd7052c688b548575`

### Current Problems

1. Manual refresh clears `daily_*`, active `weekly_*`, active `monthly_*`, and active `lbd_*` caches, but not `export_*`.
2. Export and Stripe invoice creation read `export_{start}_to_{end}` caches independently of the dashboard refresh path.
3. Carryover is persisted as derived state and is not automatically recomputed when old Toggl entries are edited later.
4. Sync depends on remembering every cache namespace in invalidation code instead of having one shared raw-entry source.

## Target Architecture

### Core Rule

Raw Toggl time entries are cached once, then every feature reads through the same range API.

### Shared Entry Cache Contract

Introduce a shared entry-cache layer with a small public API:

- `get_entries_for_range(start_dt, end_dt, force_refresh=False)`
- `refresh_entry_ranges(ranges)`
- `invalidate_entry_days(days)`
- `partition_entries_by_local_start_day(entries)`
- `merge_ranges(ranges)`

### Storage Model

Cache raw entries by local start day, for example:

- `~/Library/Caches/TogglMenuBar/entries/by_day/2026-04-11.json`

Each day shard should contain:

- `version`
- `day`
- `fetched_at`
- `entries`

### Consumers

These paths should stop owning their own raw-entry cache namespaces and instead call the shared range API:

- daily / weekly / monthly dashboard calculations
- `last_billed_date` range reads
- CSV export
- Stripe draft invoice creation
- cap-fill-date computation
- auto carryover recomputation

### Refresh Model

Manual refresh should become range-based:

- refresh the ranges needed by the visible dashboard periods
- refresh every active `last_billed_date + 1 -> today` capped-project range
- refresh any prior-month range needed for current carryover recomputation

Those ranges should be merged before fetching to avoid redundant Toggl calls.

## Design Decisions

### 1. Shared raw-entry cache, not shared aggregate caches

Do not keep `weekly_*`, `monthly_*`, `lbd_*`, or `export_*` raw-entry snapshots once the refactor is complete. Week/month/export/invoice should be views over shared daily shards.

### 2. Day-sharded storage over feature-shaped storage

If one day changes in Toggl, every feature that spans that day should see the new data automatically. Day sharding makes that true without hardcoding every dependent cache family.

### 3. Carryover must distinguish manual from auto-derived values

Carryover persistence should support:

- `source: manual | auto`
- `updated_at`
- `hours`

Rules:

- manual values are never overwritten by automatic recomputation
- auto values are recomputed when refresh covers the relevant prior-month range

### 4. Project metadata remains separate

`projects.json` still has its own TTL and refresh path. This refactor is only about raw time-entry consistency.

## TDD Plan

Implementation should start with failing tests.

### Phase 1: Lock the Existing Bug

Add tests covering the current stale-export failure:

- `test_refresh_then_export_uses_updated_entry_data`
- `test_refresh_then_stripe_invoice_uses_updated_entry_data`

Intent:

- seed stale cached entry data for an exportable range
- simulate Toggl returning updated data on manual refresh
- assert dashboard-derived calculations and export/invoice reads agree after refresh

### Phase 2: Lock Shared Cache Behavior

Add tests for the new cache layer:

- `test_get_entries_for_range_populates_day_shards_from_single_fetch`
- `test_get_entries_for_range_reuses_cached_day_shards`
- `test_get_entries_for_range_force_refresh_replaces_existing_day_shards`
- `test_overlapping_requested_ranges_are_merged_before_fetch`

### Phase 3: Lock Feature Parity

Add tests ensuring the same raw entries feed all downstream consumers:

- `test_dashboard_period_reads_use_shared_entry_cache`
- `test_export_reads_use_shared_entry_cache`
- `test_invoice_reads_use_shared_entry_cache`
- `test_compute_lbd_cap_fill_date_uses_shared_entry_cache`

### Phase 4: Lock Carryover Rules

Add tests for recomputation behavior:

- `test_auto_carryover_recomputes_when_prior_month_range_is_refreshed`
- `test_manual_carryover_override_is_preserved`
- `test_previous_month_edit_updates_current_month_auto_carryover`

## Migration Sequence

### Phase A: Introduce Shared Cache Layer

- add the new shared range/day-shard helpers
- keep all current feature behavior intact
- do not remove old cache readers yet

### Phase B: Move Export and Invoice First

- migrate CSV export to `get_entries_for_range(...)`
- migrate Stripe invoice creation to the same path
- preserve `cap_fill_date` trimming behavior from `c11e787`

Reason: this is the highest-value fix because it closes the reported bug directly.

### Phase C: Move Dashboard Period Reads

- migrate daily/weekly/monthly reads to shared range reads
- keep existing totals, projections, and `last_billed_date` logic unchanged

### Phase D: Replace Manual Refresh

- replace cache-family deletion with range-based refresh
- merge overlapping refresh spans before fetching
- ensure refreshed day shards are the only raw-entry source

### Phase E: Add Carryover Source Metadata and Recompute Rules

- extend carryover persistence format
- recompute `auto` carryover from refreshed shared entry data
- preserve manual overrides

### Phase F: Remove Legacy Raw-Entry Cache Families

- stop writing `weekly_*`, `monthly_*`, `lbd_*`, and `export_*`
- remove old invalidation logic
- add one cleanup step for orphaned legacy cache files if needed

## User Confirmation Checkpoints

These are the points where user validation is useful during implementation.

### Checkpoint 1: After First Failing Test

Purpose:

- confirm the exact intended behavior contract in TDD terms

What to review:

- a failing test showing that refresh updates dashboard calculations but export still uses stale data

### Checkpoint 2: After Export/Invoice Move to Shared Cache

Purpose:

- confirm the reported bug is actually fixed before broader migration

What to verify:

- edit an entry in Toggl
- click `Refresh`
- export CSV
- create Stripe draft invoice
- confirm both match the refreshed dashboard data

### Checkpoint 3: After Dashboard Reads and Manual Refresh Are Migrated

Purpose:

- confirm the app still feels correct in normal daily use

What to verify:

- `Today`, `This Week`, and `This Month` values match expectations
- capped `last_billed_date` flows still trim to `cap_fill_date`
- refresh still feels acceptably fast
- API usage remains in an acceptable range

### Checkpoint 4: After Carryover Recompute Work

Purpose:

- confirm previous-month edits propagate the way the user expects

What to verify:

- edit a previous-month entry in Toggl
- click `Refresh`
- confirm current-month carryover/cap behavior updates correctly

## SOT Update Plan

Only after implementation ships, update `docs/SOT.md` to reflect the new cache model.

Planned `Smart Caching` wording should say, at a high level:

- raw Toggl time entries are cached once in shared day-based shards
- dashboard, CSV export, Stripe draft invoices, capped unbilled calculations, and carryover all read from the same shared entry cache
- manual refresh refreshes visible dashboard ranges plus active billing-cycle ranges so dashboard and billing outputs stay in sync after Toggl edits
- historical day shards remain cached until explicitly refreshed; week/month/export views are derived rather than stored separately
- project metadata caching remains a separate refresh path

## Risks

### API Call Regression

If range merging is poorly implemented, the refactor could increase Toggl calls. Tests should verify overlap merging and cache reuse.

### Time Zone Boundaries

Day sharding depends on local-day interpretation. Tests must cover entries near midnight and UTC/local conversions.

### Carryover Drift During Migration

Changing carryover semantics without distinguishing `manual` vs `auto` would risk overwriting intentional user values.

### Partial Migration Complexity

During migration, old and new cache paths may coexist temporarily. Tests must assert which path is authoritative at each phase.

## Implementation Notes

- Start with a new test module for cache and sync behavior; the current suite does not cover this area
- Prefer preserving public function signatures where possible, then swap internals underneath
- Keep `docs/SOT.md` untouched until behavior is shipped
- Update `README.md` only when the shipped cache behavior changes

## Definition of Done

- refresh/export/invoice all agree on entry data after a Toggl edit
- `cap_fill_date` export/invoice trimming still works
- manual refresh is range-based, not cache-family-based
- carryover recomputation is correct and manual overrides are preserved
- feature-specific raw-entry caches are removed
- TDD coverage exists for sync, refresh, export, invoice, and carryover behavior
- `docs/SOT.md` and `README.md` reflect the shipped behavior
