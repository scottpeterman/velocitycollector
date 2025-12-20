# VelocityCollector Runner Architecture

## Overview

VelocityCollector's job execution system collects data from network devices using SSH, validates the output with TextFSM templates, and stores the results. The architecture supports both CLI and GUI execution with real-time progress feedback.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           User Interface                                 │
├─────────────────────────────────┬───────────────────────────────────────┤
│           CLI                   │              GUI                       │
│    vcollector run --job ...     │         RunView (PyQt6)               │
│    vcollector run --jobs ...    │    JobExecutionThread (QThread)       │
└─────────────────────────────────┴───────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          Job Runner Layer                                │
├─────────────────────────────────┬───────────────────────────────────────┤
│         JobRunner               │           BatchRunner                  │
│   (single job execution)        │    (parallel job execution)           │
│                                 │                                        │
│  • run(job_file) - JSON         │  • run(job_files) - multiple jobs     │
│  • run_job(job_id) - Database   │  • ThreadPoolExecutor for jobs        │
└─────────────────────────────────┴───────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         SSH Execution Layer                              │
├─────────────────────────────────────────────────────────────────────────┤
│                        SSHExecutorPool                                   │
│              (concurrent device connections)                             │
│                                                                          │
│  • ThreadPoolExecutor for devices                                        │
│  • Netmiko SSH sessions                                                  │
│  • Progress callbacks                                                    │
└─────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        Validation Layer                                  │
├─────────────────────────────────────────────────────────────────────────┤
│                      ValidationEngine                                    │
│                  (TextFSM template matching)                             │
│                                                                          │
│  • Template database (tfsm_templates.db)                                 │
│  • Output parsing and scoring                                            │
│  • Quality validation                                                    │
└─────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         Storage Layer                                    │
├─────────────────────────────────┬───────────────────────────────────────┤
│      File Storage               │        Database Storage                │
│  ~/.vcollector/collections/     │                                        │
│    ├── arp/                     │  collector.db                          │
│    ├── mac/                     │    ├── job_history                     │
│    ├── config/                  │    ├── captures                        │
│    └── ...                      │    └── jobs                            │
└─────────────────────────────────┴───────────────────────────────────────┘
```

## Components

### 1. Job Sources

Jobs can be loaded from two sources:

#### JSON Files (Legacy)
```json
{
  "version": "2.0",
  "job_id": 300,
  "capture_type": "arp",
  "vendor": "arista",
  "commands": {
    "paging_disable": "terminal length 0",
    "command": "show ip arp",
    "output_directory": "arp"
  },
  "device_filter": {
    "source": "database",
    "vendor": "arista"
  },
  "validation": {
    "use_tfsm": true,
    "tfsm_filter": "arp",
    "min_score": 1
  },
  "execution": {
    "max_workers": 12,
    "timeout": 60
  }
}
```

#### Database (New)
```sql
-- Jobs stored in collector.db
SELECT id, name, slug, capture_type, vendor, command,
       max_workers, timeout_seconds, use_textfsm
FROM jobs
WHERE is_enabled = 1;
```

### 2. JobRunner

The core execution engine for single jobs.

```python
from vcollector.jobs.runner import JobRunner
from vcollector.vault.resolver import CredentialResolver

# Get credentials from vault
resolver = CredentialResolver()
resolver.unlock_vault("password")
creds = resolver.get_ssh_credentials()

# Create runner
runner = JobRunner(
    credentials=creds,
    validate=True,           # Enable TextFSM validation
    debug=False,             # Debug output
    no_save=False,           # Save output files
    force_save=False,        # Save even if validation fails
    limit=None,              # Device limit (None = all)
    quiet=False,             # Minimal output
    record_history=True,     # Record in job_history table
)

# Run from JSON file
result = runner.run(Path("jobs/arista_arp.json"))

# Run from database
result = runner.run_job(job_id=42)
result = runner.run_job(job_slug="arista-arp-300")
```

#### JobResult
```python
@dataclass
class JobResult:
    job_file: str              # Source (file path or "database:id")
    job_id: str                # Job identifier
    success_count: int         # Devices completed successfully
    failed_count: int          # Devices that failed (SSH error)
    skipped_count: int         # Devices skipped (validation failed)
    total_devices: int         # Total devices attempted
    duration_ms: float         # Total execution time
    error: Optional[str]       # Job-level error message
    saved_files: List[tuple]   # (device, path, size, score)
    validation_failures: List  # (device, host, score, reason)
    history_id: Optional[int]  # job_history record ID
```

### 3. BatchRunner

Executes multiple jobs concurrently.

```python
from vcollector.jobs.batch import BatchRunner

runner = BatchRunner(
    credentials=creds,
    max_concurrent_jobs=4,   # Jobs in parallel
    validate=True,
)

result = runner.run([
    Path("jobs/arista_arp.json"),
    Path("jobs/cisco_arp.json"),
    Path("jobs/juniper_arp.json"),
])

print(f"Jobs: {result.successful_jobs}/{result.total_jobs}")
print(f"Devices: {result.total_success} success, {result.total_failed} failed")
```

### 4. SSHExecutorPool

Manages concurrent SSH connections to devices.

```python
from vcollector.ssh.executor import SSHExecutorPool, ExecutorOptions

options = ExecutorOptions(
    timeout=60,              # SSH timeout per device
    inter_command_time=1,    # Delay between commands
    debug=False,
)

pool = SSHExecutorPool(
    credentials=creds,
    options=options,
    max_workers=12,          # Concurrent SSH sessions
)

# Execute on multiple devices
targets = [
    ("192.168.1.1", "show ip arp", {"name": "switch1"}),
    ("192.168.1.2", "show ip arp", {"name": "switch2"}),
]

results = pool.execute_batch(targets, progress_callback=on_progress)
```

### 5. Device Selection

Devices are queried from the DCIM database based on job filters.

```python
# Job filter configuration
device_filter = {
    "source": "database",
    "vendor": "arista",           # Match manufacturer name
    "platform_id": 5,             # Specific platform
    "site_id": 2,                 # Specific site
    "role_id": 3,                 # Specific role
    "name_pattern": "^core-.*",   # Regex pattern
    "status": "active",           # Device status
}
```

The runner queries `dcim.db`:
```sql
SELECT d.*, p.netmiko_device_type, m.name as manufacturer_name
FROM device d
LEFT JOIN platform p ON d.platform_id = p.id
LEFT JOIN manufacturer m ON p.manufacturer_id = m.id
WHERE d.status = 'active'
  AND d.primary_ip4 IS NOT NULL
  AND m.name LIKE '%arista%';
```

### 6. Validation Flow

```
Raw Output → Clean Output → TextFSM Parse → Score → Decision
     │            │              │           │         │
     │            │              │           │         ├─ score > 0: Save
     │            │              │           │         └─ score = 0: Skip
     │            │              │           │
     │            │              │           └─ Records found / expected
     │            │              │
     │            │              └─ Match template, extract structured data
     │            │
     │            └─ Strip command echo, prompts
     │
     └─ Raw SSH output with prompts
```

#### Validation Engine
```python
from vcollector.validation import ValidationEngine

engine = ValidationEngine(db_path="tfsm_templates.db")

# Validate output against template
result = engine.validate(
    output="Protocol  Address   Age  Hardware Addr...",
    filter_str="arista_eos_show_ip_arp"
)

print(f"Template: {result.template}")
print(f"Score: {result.score}")
print(f"Records: {result.record_count}")
print(f"Valid: {result.is_valid}")
```

### 7. Progress Callbacks

Both CLI and GUI receive real-time progress updates.

```python
def on_progress(completed: int, total: int, result: ExecutionResult):
    """Called after each device completes."""
    status = "✓" if result.success else "✗"
    print(f"[{completed}/{total}] {status} {result.host} - {result.duration_ms:.0f}ms")

result = runner.run(job_file, progress_callback=on_progress)
```

### 8. Job History

Execution history is recorded in `collector.db`.

```sql
CREATE TABLE job_history (
    id INTEGER PRIMARY KEY,
    job_id TEXT NOT NULL,           -- Job slug or legacy ID
    job_file TEXT,                  -- Source file or "database:id"
    started_at TEXT NOT NULL,
    completed_at TEXT,
    total_devices INTEGER,
    success_count INTEGER,
    failed_count INTEGER,
    status TEXT,                    -- 'running', 'success', 'partial', 'failed'
    error_message TEXT
);
```

## Execution Flow

### CLI Flow

```
$ vcollector run --job jobs/arista_arp.json --credential lab

1. Parse arguments
2. Collect job files
3. Prompt for vault password
4. Unlock vault, get credentials
5. For each job:
   a. Load job definition (JSON)
   b. Query matching devices (DCIM)
   c. Create job_history record
   d. Execute SSH commands (parallel)
   e. Validate output (TextFSM)
   f. Save valid output to files
   g. Update job_history
6. Print summary
7. Lock vault
```

### GUI Flow

```
User clicks "Run Job"
        │
        ▼
┌─────────────────────┐
│  Vault Password     │
│  Dialog             │
└─────────────────────┘
        │
        ▼
┌─────────────────────┐
│ JobExecutionThread  │ (QThread)
│   starts            │
└─────────────────────┘
        │
        ├──► log_message signal → Execution Log
        │
        ├──► progress signal → Results Table + Progress Bar
        │
        └──► finished_job signal → Summary Display
```

## File Locations

```
~/.vcollector/
├── config.yaml              # Configuration
├── collector.db             # Jobs, credentials, history
├── dcim.db                  # Devices, sites, platforms
├── jobs/                    # JSON job definitions (legacy)
│   ├── job_300_arista_arp.json
│   └── ...
└── collections/             # Captured output
    ├── arp/
    │   ├── switch1.txt
    │   └── switch2.txt
    ├── mac/
    ├── config/
    └── ...
```

## Database Schema

### collector.db

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│    jobs      │     │ job_history  │     │   captures   │
├──────────────┤     ├──────────────┤     ├──────────────┤
│ id           │◄────│ job_id       │     │ id           │
│ name         │     │ started_at   │◄────│ job_history_id│
│ slug         │     │ completed_at │     │ device_name  │
│ capture_type │     │ total_devices│     │ capture_type │
│ vendor       │     │ success_count│     │ filepath     │
│ command      │     │ status       │     │ file_size    │
│ ...          │     │ ...          │     │ ...          │
└──────────────┘     └──────────────┘     └──────────────┘

┌──────────────┐     ┌──────────────┐
│ credentials  │     │vault_metadata│
├──────────────┤     ├──────────────┤
│ id           │     │ key          │
│ name         │     │ value        │
│ username     │     │              │
│ password_enc │     │              │
│ ssh_key_enc  │     │              │
│ is_default   │     │              │
└──────────────┘     └──────────────┘
```

### dcim.db

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   device     │     │   platform   │     │ manufacturer │
├──────────────┤     ├──────────────┤     ├──────────────┤
│ id           │     │ id           │     │ id           │
│ name         │────►│ name         │────►│ name         │
│ primary_ip4  │     │ netmiko_type │     │ slug         │
│ platform_id  │     │manufacturer_id│    │              │
│ site_id      │     │              │     │              │
│ role_id      │     └──────────────┘     └──────────────┘
│ status       │
└──────────────┘
        │
        ▼
┌──────────────┐     ┌──────────────┐
│    site      │     │ device_role  │
├──────────────┤     ├──────────────┤
│ id           │     │ id           │
│ name         │     │ name         │
│ slug         │     │ slug         │
│ status       │     │ color        │
└──────────────┘     └──────────────┘
```

## Error Handling

### SSH Errors
- Connection timeout → device marked failed
- Authentication failure → device marked failed
- Command timeout → device marked failed

### Validation Errors
- No matching template → score = 0, device skipped
- Parse failure → score = 0, device skipped
- Low quality score → device skipped (unless force_save)

### Job Errors
- No devices match filter → job fails with error
- All devices fail → job marked failed
- Some devices fail → job marked partial

## Configuration

### config.yaml
```yaml
# Database paths
collector_db: ~/.vcollector/collector.db
dcim_db: ~/.vcollector/dcim.db
assets_db: ~/.vcollector/assets.db  # Legacy fallback

# Default paths
jobs_dir: ~/.vcollector/jobs
collections_dir: ~/.vcollector/collections

# TextFSM templates
tfsm_db: ~/.vcollector/tfsm_templates.db

# Execution defaults
default_timeout: 60
default_workers: 12
```

## Extending

### Adding New Capture Types

1. Add TextFSM template to `tfsm_templates.db`
2. Create job in GUI or JSON:
   ```json
   {
     "capture_type": "new_type",
     "commands": {
       "command": "show new command",
       "output_directory": "new_type"
     },
     "validation": {
       "tfsm_filter": "vendor_os_show_new_command"
     }
   }
   ```

### Adding New Vendors

1. Add manufacturer to DCIM
2. Add platform with `netmiko_device_type`
3. Add TextFSM templates for the vendor
4. Create jobs targeting the vendor

### Custom Validation

```python
# Extend ValidationEngine for custom scoring
class CustomValidationEngine(ValidationEngine):
    def validate(self, output, filter_str):
        result = super().validate(output, filter_str)
        
        # Custom scoring logic
        if "expected_pattern" in output:
            result.score += 0.5
            
        return result
```