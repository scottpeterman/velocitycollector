# VelocityCollector: Per-Device Credential System

## Overview

Network environments often have fragmented credentials — legacy devices, acquisitions, different teams managing different equipment. The "one credential fits all" model breaks in production.

VelocityCollector now supports **per-device credential discovery and assignment**. The system:

1. Tests all vault credentials against each device
2. Stores the working credential per-device
3. Automatically uses the correct credential when running jobs

## Architecture

### Database Schema (v2)

New columns added to `dcim_device`:

```sql
credential_id INTEGER           -- FK to credentials table (working credential)
credential_tested_at TEXT       -- Last test timestamp
credential_test_result TEXT     -- 'untested', 'success', 'failed'
```

Indexes for efficient filtering:
- `idx_dcim_device_cred_result` on credential_test_result
- `idx_dcim_device_cred_tested` on credential_tested_at

### Credential Resolution Chain

When running a job, credentials resolve in this order:

1. **Device credential** (`device.credential_id`) — from discovery
2. **CLI credential** (`--credential <name>`) — explicit override
3. **Default credential** — vault default

If a device has `credential_id` set, that credential is used regardless of the job's default.

## CLI Commands

### Discovery (Bulk Testing)

```bash
# Test all active devices against all credentials
vcollector creds discover

# Filter by site/platform/role
vcollector creds discover --site dc1
vcollector creds discover --platform arista_eos
vcollector creds discover --role spine

# Limit credentials tested (avoid lockouts)
vcollector creds discover --credentials lab,legacy-core

# Test subset of devices
vcollector creds discover --limit 10
vcollector creds discover --search "spine"

# Skip devices that already have credential_id
vcollector creds discover --skip-configured

# Force re-test (ignore recent tests)
vcollector creds discover --force

# Dry run (show what would be tested)
vcollector creds discover --dry-run

# Non-interactive
vcollector creds discover --yes --quiet
```

### Single Device Testing

```bash
# Test all credentials against one device
vcollector creds test spine-1

# Test specific credential
vcollector creds test spine-1 --credential legacy-core

# Save result to database
vcollector creds test spine-1 --update
```

### Coverage Report

```bash
vcollector creds status
```

Output:
```
Credential Coverage Report
============================================================
Total active devices: 14
Configuration status:
  Configured (credential_id set): 14
  Unconfigured: 0
Test results:
  Success: 14
  Failed: 0
  Untested: 0
Coverage: 100.0%
Tested: 100.0%
```

## Files Modified/Added

### New Files

| File | Path | Purpose |
|------|------|---------|
| `cred_discovery.py` | `vcollector/core/cred_discovery.py` | Core discovery engine |
| `creds.py` | `vcollector/cli/creds.py` | CLI handler for creds command |

### Modified Files

| File | Path | Changes |
|------|------|---------|
| `db_schema.py` | `vcollector/dcim/db_schema.py` | Schema v2, migration method |
| `dcim_repo.py` | `vcollector/dcim/dcim_repo.py` | Device dataclass fields, helper methods |
| `main.py` | `vcollector/cli/main.py` | Added creds subparser |
| `executor.py` | `vcollector/ssh/executor.py` | Per-device credential support in `_execute_single()` |
| `runner.py` | `vcollector/jobs/runner.py` | `credential_resolver` param, credential cache |
| `run.py` | `vcollector/cli/run.py` | Pass resolver to runner |

## How Discovery Works

1. **Connect-only testing**: Discovery connects, detects prompt, disconnects. No commands executed.

2. **Smart ordering**: If device already has `credential_id`, that credential is tried first.

3. **Early exit on non-auth failures**: Timeout, connection refused, DNS failure → don't try other credentials.

4. **Parallel execution**: ThreadPoolExecutor with configurable workers (default: 8).

5. **Skip logic**: 
   - `--skip-configured`: Skip devices with credential_id already set
   - Default: Skip devices tested within 24 hours
   - `--force`: Override skip logic

6. **Database updates**: On success, sets `credential_id`, `credential_tested_at`, `credential_test_result`.

## How Jobs Use Per-Device Credentials

```python
# In runner.py
def _get_device_credentials(self, device):
    """Get credentials for a specific device."""
    if not self.credential_resolver:
        return None  # Fall back to job default
        
    credential_id = device.get('credential_id')
    if not credential_id:
        return None  # Fall back to job default
        
    # Load from cache (built on first call)
    return self._credential_cache.get(credential_id)
```

```python
# In executor.py
def _execute_single(self, host, command, extra_data=None):
    # Use per-device credentials if provided
    creds = self.credentials  # Pool default
    if extra_data and extra_data.get('credentials'):
        creds = extra_data['credentials']  # Per-device override
```

## Example Workflow

```bash
# 1. Initialize vault with credentials
vcollector vault init
vcollector vault add lab --username admin
vcollector vault add legacy --username netops

# 2. Import/add devices
vcollector gui  # or import from NetBox/CSV

# 3. Discover credentials
vcollector creds discover

# 4. Check coverage
vcollector creds status

# 5. Run jobs (credentials auto-selected)
vcollector run --job cisco-configs
```

## DCIMRepository Helper Methods

```python
# Get devices by credential status
devices = repo.get_devices_by_credential_status(
    test_result='failed',      # 'success', 'failed', 'untested'
    has_credential=False,      # Only devices without credential_id
)

# Get coverage statistics
stats = repo.get_credential_coverage_stats()
# Returns: {total_active, with_credential, without_credential, 
#           test_success, test_failed, test_untested}

# Update device credential test result
repo.update_device_credential_test(
    device_id=42,
    credential_id=1,           # Working credential (or None)
    test_result='success',     # 'success' or 'failed'
)

# Get devices needing testing
devices = repo.get_devices_needing_credential_test(
    hours_since_test=24,       # Re-test if older than this
    include_failed=True,       # Include previously failed
    limit=100,
)
```

## Migration from v1

For existing databases:

```bash
# Option 1: Run standalone migration script
python schema_migration_v2.py

# Option 2: Let db_schema.py handle it automatically
# (init_schema() detects v1 and runs migration)
```

Fresh installs automatically get v2 schema.

## Future Enhancements

- [ ] GUI: Credential status column in Devices view
- [ ] GUI: Credential dropdown in Device Edit dialog
- [ ] Scheduled discovery (cron-like periodic testing)
- [ ] Rate limiting to avoid account lockouts
- [ ] Multi-credential support (password + key for privilege escalation)
- [ ] BatchRunner integration for multi-job runs