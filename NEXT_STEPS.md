# VelocityCollector - Next Steps

## Current State (v0.4.0)

### ✅ Working Features

| Feature | CLI | GUI | Notes |
|---------|-----|-----|-------|
| Environment init | `vcollector init` | — | Creates dirs, DBs, config |
| Vault management | `vcollector vault *` | Vault view | Init, add, list, remove, export/import |
| Device CRUD | — | Devices view | Full CRUD with search/filters |
| Sites/Platforms/Roles | — | Tabbed views | Full CRUD |
| Job management | `vcollector jobs *` | Jobs view | List, show, history, CRUD |
| Single job execution | `vcollector run --job` | Run view | By slug, ID, or JSON file |
| Job history | `vcollector jobs history` | History view | Shared database |
| Output browser | — | Output view | File browser with content search |
| TextFSM tools | — | Tools menu | Database test, manual test, template manager |
| Coverage report | `python coverage_report.py` | — | HTML report generation |
| Theme support | — | Toolbar | Light/Dark/Cyber |
| **Credential discovery** | `vcollector creds *` | Devices view | Bulk test, per-device assignment |
| **Per-device creds** | Auto | Run view checkbox | Runner uses device.credential_id |

### 🔶 Partially Working

| Feature | Status | Issue |
|---------|--------|-------|
| `vcollector jobs create` | Stub | Parser exists, handler not implemented |
| `vcollector jobs migrate` | External | Works via `migrate_jobs.py`, not integrated |
| TextFSM validation | Exists | May be broken, needs testing |
| BatchRunner | Exists | Only works with JSON files, not database jobs |

---

## ~~Priority 1: Credential-Device Tracking~~ ✅ COMPLETE

### Implementation Summary

**Database Schema v2:**
```sql
-- Added to dcim_device table
credential_id INTEGER           -- FK to credentials table (working credential)
credential_tested_at TEXT       -- Last test timestamp  
credential_test_result TEXT     -- 'untested', 'success', 'failed'

-- Indexes
CREATE INDEX idx_dcim_device_cred_result ON dcim_device(credential_test_result);
CREATE INDEX idx_dcim_device_cred_tested ON dcim_device(credential_tested_at);
```

**CLI Commands:**
- `vcollector creds discover` — Bulk credential testing
- `vcollector creds test <device>` — Single device testing
- `vcollector creds status` — Coverage report

**GUI Features:**
- ✅ Cred Status column in Devices view (✓ OK / ✗ Failed / —)
- ✅ Credential Coverage stat card with percentage
- ✅ Credential status filter dropdown
- ✅ 🔑 Discover Creds button for bulk discovery
- ✅ Credentials tab in Device Detail dialog
- ✅ Credential dropdown in Device Edit dialog
- ✅ Test button for on-demand credential testing
- ✅ Per-device credentials checkbox in Run view
- ✅ Credential column in job results table

**Runner Integration:**
- JobRunner accepts `credential_resolver` parameter
- Devices with `credential_id` automatically use their assigned credential
- Falls back to job default if no per-device credential

---

## Priority 1 (NEW): Batch Job Execution

### Problem
The existing `BatchRunner` only works with JSON file paths. Need to:
1. Run multiple database jobs in sequence or parallel
2. Group jobs by tag/category for batch execution
3. Support job patterns like `vcollector run --jobs "arista-*"`

### Current State
- `job_tags` table exists but unused
- `BatchRunner` in `jobs/batch.py` expects file paths
- CLI `--jobs` argument parsed but not fully implemented

### Proposed Solution

**Extend JobRunner:**
```python
def run_jobs(
    self,
    job_slugs: List[str] = None,
    job_ids: List[int] = None,
    tag: str = None,
    pattern: str = None,
    parallel: bool = False,
    max_concurrent: int = 4,
) -> BatchResult:
    """Run multiple jobs from database."""
```

**CLI Support:**
```bash
# Pattern matching (already parsed)
vcollector run --jobs "arista-*" "cisco-*"

# By tag
vcollector run --tag daily-collection

# All enabled jobs
vcollector run --all-enabled

# Parallel execution
vcollector run --jobs "arista-*" --parallel --max-concurrent 4
```

**GUI Support:**
- Jobs view: Multi-select with "Run Selected" button
- Run view: Job queue with drag-and-drop ordering
- New "Batch Run" view or dialog

### Job Tagging
- Implement tag CRUD in GUI (Jobs view)
- Filter jobs by tag
- Color-coded tags in job list

---

## Priority 2: TextFSM Validation Fixes

### Problem
The validation system exists but may be broken. Core components:
- `core/tfsm_fire.py` - Auto-matching engine
- `core/tfsm_engine.py` - Template matching
- `validation/tfsm_engine.py` - Validation wrapper
- `tfsm_templates.db` - Template database

### Issues to Investigate
1. **Duplicate modules**: `core/tfsm_engine.py` vs `validation/tfsm_engine.py` - which is canonical?
2. **Template matching**: Does auto-detection work for all platforms?
3. **Score calculation**: Is the multi-factor scoring working correctly?
4. **Force save**: Does `--force-save` actually bypass validation?
5. **Validation failures**: Are they being recorded properly in job history?

### Testing Plan
```bash
# Test 1: Run with validation
vcollector run --job arista-arp-300 --debug

# Test 2: Check validation scores in output
# Should show: [score=0.85] or similar

# Test 3: Force save on validation failure
vcollector run --job arista-arp-300 --force-save

# Test 4: TextFSM tester tool
# Tools → TextFSM Tester → Database Test
```

### Fixes Needed
- Consolidate duplicate tfsm_engine modules
- Add validation score to history view
- Show validation failures in run results
- Add "revalidate" option to output browser

---

## Priority 3: Help System

### Problem
No integrated help system. Users must rely on:
- README.md
- CLI `--help` flags
- External documentation

### Proposed Solution

**In-App Help:**
1. **Tooltips**: Add tooltips to all buttons, fields, and options
2. **Context help**: F1 or "?" button opens relevant help
3. **Welcome screen**: First-run wizard or getting started guide
4. **Status bar hints**: Show hints based on current view

**Help Menu:**
```
Help
├── Getting Started...
├── User Guide...
├── CLI Reference...
├── Keyboard Shortcuts
├── ─────────────────
├── Check for Updates
├── Report Issue...
├── ─────────────────
└── About VelocityCollector
```

**Documentation:**
- Ship with bundled HTML help (from markdown)
- Link to online docs for latest
- Include example jobs and workflows

### About Dialog Improvements
```python
# Current: Basic info
# Needed:
- System info (Python version, PyQt version, OS)
- Database paths
- Credential status
- Device/job counts
- Link to GitHub, docs, issues
```

---

## Priority 4: Additional Improvements

### CLI Completions

**`vcollector jobs create`:**
```bash
vcollector jobs create \
  --name "Arista Config Backup" \
  --vendor arista \
  --type configs \
  --command "show running-config" \
  --paging-disable "terminal length 0" \
  --output-dir configs \
  --filter-site usa
```

**`vcollector jobs migrate`:**
```bash
# Integrate migrate_jobs.py into CLI
vcollector jobs migrate --dir jobs_v2/ --dry-run
vcollector jobs migrate --dir jobs_v2/
```

### Device Import

**CSV Import:**
```bash
vcollector devices import --csv devices.csv
vcollector devices import --csv devices.csv --dry-run
```

**NetBox Sync:**
```bash
vcollector devices sync --netbox https://netbox.example.com --token xxx
vcollector devices sync --netbox-config ~/.vcollector/netbox.yaml
```

### Scheduling (Future)

```bash
# Cron-like scheduling
vcollector schedule add --job arista-configs --cron "0 2 * * *"
vcollector schedule list
vcollector schedule remove <id>

# Or via config file
vcollector daemon start  # Runs scheduled jobs
```

---

## Technical Debt

### Code Cleanup
- [ ] Remove duplicate `tfsm_engine.py` modules
- [ ] Consolidate credential resolution logic
- [ ] Add type hints to all functions
- [ ] Add docstrings to all classes/methods
- [ ] Unit tests for core modules

### Database
- [ ] Add indexes for common queries
- [ ] Add foreign key constraints (some missing)
- [ ] Migration system for schema updates
- [ ] Backup/restore commands

### Error Handling
- [ ] Consistent error messages across CLI/GUI
- [ ] Better SSH error reporting (timeout vs auth vs connection refused)
- [ ] Graceful handling of database corruption
- [ ] Recovery from interrupted jobs

### Performance
- [ ] Connection pooling for frequent database access
- [ ] Lazy loading for large device lists
- [ ] Progress streaming for long-running jobs
- [ ] Memory optimization for large output files

---

## File Reference

### Files Delivered - Per-Device Credentials (v0.4)

| File | Destination | Purpose |
|------|-------------|---------|
| `cred_discovery.py` | `vcollector/core/cred_discovery.py` | Core discovery engine |
| `creds.py` | `vcollector/cli/creds.py` | CLI handler for creds command |
| `db_schema.py` | `vcollector/dcim/db_schema.py` | Schema v2 with migration |
| `dcim_repo.py` | `vcollector/dcim/dcim_repo.py` | Device dataclass + helper methods |
| `devices_view.py` | `vcollector/ui/widgets/devices_view.py` | Cred status column, discovery |
| `device_dialogs.py` | `vcollector/ui/widgets/device_dialogs.py` | Cred tab, dropdown, test button |
| `run_view.py` | `vcollector/ui/widgets/run_view.py` | Per-device creds checkbox |
| `executor.py` | `vcollector/ssh/executor.py` | Per-device credential support |
| `runner.py` | `vcollector/jobs/runner.py` | credential_resolver param |

### External Scripts (Keep Separate)
- `migrate_jobs.py` - JSON to database migration
- `import_from_velocitycmdb.py` - Device import from VelocityCMDB
- `coverage_report.py` - HTML coverage generation
- `db_doc.py` - Database documentation generator

---

## Version Roadmap

### v0.4.0 - Per-Device Credentials ✅
- [x] Device-credential tracking (schema v2)
- [x] Credential discovery CLI (`vcollector creds`)
- [x] Credential discovery GUI (Devices view)
- [x] Credential status column and filter
- [x] Credential dropdown in device edit
- [x] Test button in device dialogs
- [x] Per-device credential override in runner
- [x] Coverage stat card

### v0.5.0 - Batch Execution (Planned)
- [ ] Database-aware BatchRunner
- [ ] Job tagging and filtering
- [ ] Multi-select run in GUI
- [ ] Pattern matching for job slugs

### v0.6.0 - Polish
- [ ] Fix TextFSM validation
- [ ] Implement `vcollector jobs create`
- [ ] Integrate `vcollector jobs migrate`
- [ ] Add tooltips to GUI
- [ ] Update About dialog

### v0.7.0 - Integration
- [ ] CSV device import
- [ ] NetBox sync (read-only)
- [ ] Help system
- [ ] Keyboard shortcuts documentation

### v1.0.0 - Production Ready
- [ ] Full test coverage
- [ ] Performance optimization
- [ ] Complete documentation
- [ ] Scheduled execution
- [ ] Config diff detection