# VelocityCollector - Next Steps

## Current State (v0.3.0)

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

### 🔶 Partially Working

| Feature | Status | Issue |
|---------|--------|-------|
| `vcollector jobs create` | Stub | Parser exists, handler not implemented |
| `vcollector jobs migrate` | External | Works via `migrate_jobs.py`, not integrated |
| TextFSM validation | Exists | May be broken, needs testing |
| BatchRunner | Exists | Only works with JSON files, not database jobs |

---

## Priority 1: Credential-Device Tracking

### Problem
Currently, credentials are global. When running jobs against devices, the runner tries the default credential. If a network has devices with different credentials (common in real environments), there's no way to:
1. Track which credential last worked on a specific device
2. Automatically try the "known good" credential first
3. Fall back to other credentials on failure

### Proposed Solution

**Database Schema Addition:**
```sql
-- Add to dcim.db or collector.db
CREATE TABLE device_credentials (
    id INTEGER PRIMARY KEY,
    device_id INTEGER NOT NULL,
    credential_id INTEGER NOT NULL,
    last_success_at TEXT,
    last_failure_at TEXT,
    failure_count INTEGER DEFAULT 0,
    is_preferred INTEGER DEFAULT 0,
    notes TEXT,
    FOREIGN KEY (device_id) REFERENCES dcim_device(id) ON DELETE CASCADE,
    UNIQUE(device_id, credential_id)
);
```

**Runner Logic:**
```python
def get_credentials_for_device(device_id: int) -> List[SSHCredentials]:
    """
    Returns credentials in priority order:
    1. Device's preferred credential (if set and recently successful)
    2. Other credentials that worked on this device
    3. Default credential
    4. All other credentials
    """
```

**On Success/Failure:**
```python
def record_credential_result(device_id, credential_id, success: bool):
    # Update device_credentials table
    # Set is_preferred = 1 if success
    # Increment failure_count if failure
```

### UI Changes
- Device edit dialog: "Credentials" tab showing credential history
- Run results: Show which credential was used per device
- Credentials view: Show device count per credential

---

## Priority 2: Batch Job Execution

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

## Priority 3: TextFSM Validation Fixes

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

## Priority 4: Help System

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

## Priority 5: Additional Improvements

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

### Files Delivered This Session

| File | Destination | Purpose |
|------|-------------|---------|
| `cli/main.py` | `vcollector/cli/main.py` | Updated CLI with GUI launch |
| `cli/init.py` | `vcollector/cli/init.py` | Environment bootstrap |
| `cli/jobs.py` | `vcollector/cli/jobs.py` | Database-first job commands |
| `cli/run.py` | `vcollector/cli/run.py` | Database-first job execution |
| `core/config.py` | `vcollector/core/config.py` | New config with dcim_db |
| `setup.py` | `setup.py` | Package installation |
| `MANIFEST.in` | `MANIFEST.in` | Source distribution |
| `requirements.txt` | `requirements.txt` | Dependencies |
| `LICENSE` | `LICENSE` | MIT license |
| `.gitignore` | `.gitignore` | Clean repository |
| `README.md` | `README.md` | Updated documentation |

### External Scripts (Keep Separate)
- `migrate_jobs.py` - JSON to database migration
- `import_from_velocitycmdb.py` - Device import from VelocityCMDB
- `coverage_report.py` - HTML coverage generation
- `db_doc.py` - Database documentation generator

---

## Version Roadmap

### v0.3.1 - Stability
- [ ] Fix TextFSM validation
- [ ] Implement `vcollector jobs create`
- [ ] Integrate `vcollector jobs migrate`
- [ ] Add tooltips to GUI
- [ ] Update About dialog

### v0.4.0 - Credentials
- [ ] Device-credential tracking
- [ ] Credential fallback logic in runner
- [ ] Credential history in device dialog
- [ ] Per-device credential override

### v0.5.0 - Batch Execution
- [ ] Database-aware BatchRunner
- [ ] Job tagging and filtering
- [ ] Multi-select run in GUI
- [ ] Pattern matching for job slugs

### v0.6.0 - Integration
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