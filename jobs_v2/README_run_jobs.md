# VCollector Job Runners

Two command-line tools for executing collection jobs against network devices.

## Overview

| Tool | Purpose | Use Case |
|------|---------|----------|
| `run_job` | Execute a single job file | Quick captures, testing, single vendor collections |
| `run_jobs_concurrent` | Execute multiple job files in parallel | Scheduled collection runs, multi-vendor sweeps |

Both tools query devices from `assets.db`, authenticate via encrypted vault credentials, execute SSH commands concurrently against matched devices, and save output to capture files.

---

## run_job

Executes a single v2.0 collection job.

### Usage

```bash
python -m vcollector.run_job --job <job_file> [options]
```

### Options

| Option | Description |
|--------|-------------|
| `--job` | **(Required)** Path to v2.0 job JSON file |
| `--credential` | Credential set name from vault (default: vault's default credential) |
| `--vault-pass` | Vault password (or set `VELOCITYCMDB_VAULT_PASS` env var) |
| `--dry-run` | Show matched devices without executing |
| `--limit N` | Process only first N matched devices |
| `--debug` | Enable SSH debug output |
| `--no-save` | Execute but don't save captures to files |
| `--yes`, `-y` | Skip confirmation prompt |

### Examples

```bash
# Basic execution (prompts for vault password)
python -m vcollector.run_job --job jobs_v2/job_329_cisco-ios_configs.json

# Specify credential set
python -m vcollector.run_job --job jobs_v2/job_329_cisco-ios_configs.json --credential lab

# Preview which devices match without executing
python -m vcollector.run_job --job jobs_v2/job_329_cisco-ios_configs.json --dry-run

# Test against 2 devices with debug output
python -m vcollector.run_job --job jobs_v2/job_329_cisco-ios_configs.json --limit 2 --debug

# Non-interactive with env var
VELOCITYCMDB_VAULT_PASS=secret python -m vcollector.run_job --job jobs_v2/job_329_cisco-ios_configs.json -y
```

### Output

```
Job: 329 - cisco-ios_configs
Vendor filter: cisco
Command: terminal length 0,show running-config
Output dir: /home/user/.velocitycmdb/data/capture/configs

Found 9 matching devices:
  - eng-leaf-1 (172.16.11.41) - C9300-48P
  - eng-leaf-2 (172.16.11.42) - C9300-48P
  ...

Run job against 9 devices? [y/N]: y
Vault password: 
Using credentials: lab (user: admin)

Executing against 9 devices...
  [1/9] ✓ 172.16.11.41 - 5234ms
  [2/9] ✓ 172.16.11.42 - 5312ms
  ...

============================================================
Results: 9 success, 0 failed

Saved 9 captures:
  - eng-leaf-1: eng-leaf-1.txt (3,987 bytes)
  - eng-leaf-2: eng-leaf-2.txt (4,029 bytes)
  ...
```

---

## run_jobs_concurrent

Executes multiple job files in parallel, with each job running its own concurrent device pool.

### Usage

```bash
python -m vcollector.run_jobs_concurrent --jobs <job_files...> [options]
python -m vcollector.run_jobs_concurrent --jobs-dir <directory> [options]
```

### Options

| Option | Description |
|--------|-------------|
| `--jobs` | Job file(s) to run (space-separated, supports glob patterns) |
| `--jobs-dir` | Directory containing job files (runs all `*.json` files) |
| `--credential` | Credential set name from vault |
| `--vault-pass` | Vault password (or set `VELOCITYCMDB_VAULT_PASS` env var) |
| `--max-concurrent-jobs N` | Max jobs to run in parallel (default: 4) |
| `--dry-run` | Show jobs and device counts without executing |
| `--limit N` | Limit devices per job |
| `--debug` | Enable SSH debug output |
| `--no-save` | Execute but don't save captures |
| `--yes`, `-y` | Skip confirmation prompt |
| `--quiet`, `-q` | Minimal output during execution |

### Examples

```bash
# Run two specific jobs
python -m vcollector.run_jobs_concurrent --jobs jobs_v2/job_329_cisco-ios_configs.json jobs_v2/job_328_arista_configs.json --credential lab

# Run all jobs in a directory
python -m vcollector.run_jobs_concurrent --jobs-dir jobs_v2/ --credential lab

# Use glob pattern
python -m vcollector.run_jobs_concurrent --jobs jobs_v2/job_*.json --credential lab

# Increase parallelism (8 concurrent jobs)
python -m vcollector.run_jobs_concurrent --jobs jobs_v2/*.json --max-concurrent-jobs 8 --credential lab

# Preview without executing
python -m vcollector.run_jobs_concurrent --jobs jobs_v2/*.json --dry-run

# Quiet mode for cron/scheduled runs
python -m vcollector.run_jobs_concurrent --jobs jobs_v2/*.json --credential lab -y -q
```

### Output

```
Jobs to run (2):
  - job_328_arista_configs.json: 328 (3 devices)
  - job_329_cisco-ios_configs.json: 329 (9 devices)

Total: 2 jobs, 12 device executions
Max concurrent jobs: 4

Run 2 jobs? [y/N]: y
Vault password: 
Using credentials: lab (user: admin)
============================================================
[328] Starting - arista_configs
[329] Starting - cisco-ios_configs
[328] Found 3 devices
[329] Found 9 devices
[328] ✓ Complete: 3/3 success in 12852ms
[329] ✓ Complete: 9/9 success in 14854ms

============================================================
SUMMARY
============================================================
Jobs: 2/2 successful
Devices: 12 success, 0 failed
Captures saved: 12
Total time: 14.9s

Per-job results:
  ✓ 328: 3/3 devices, 12852ms
  ✓ 329: 9/9 devices, 14854ms
```

---

## Job File Format (v2.0)

Jobs are JSON files defining what to collect and from which devices.

```json
{
  "job_id": "329",
  "capture_type": "cisco-ios_configs",
  "device_filter": {
    "vendor": "cisco",
    "site": null,
    "role": null,
    "name_pattern": null
  },
  "commands": {
    "command": "terminal length 0,show running-config",
    "output_directory": "configs"
  },
  "execution": {
    "timeout": 60,
    "inter_command_time": 1,
    "max_workers": 12
  },
  "storage": {
    "filename_pattern": "{device_name}.txt"
  }
}
```

### Fields

| Field | Description |
|-------|-------------|
| `job_id` | Unique identifier for the job |
| `capture_type` | Descriptive name for the collection type |
| `device_filter.vendor` | Filter by vendor name (case-insensitive partial match) |
| `device_filter.site` | Filter by site code (exact match) |
| `device_filter.role` | Filter by role name (case-insensitive partial match) |
| `device_filter.name_pattern` | Filter by device name (`*` wildcards supported) |
| `commands.command` | Comma-separated commands to execute |
| `commands.output_directory` | Subdirectory under capture base for output |
| `execution.timeout` | SSH connection timeout in seconds |
| `execution.inter_command_time` | Delay between commands in seconds |
| `execution.max_workers` | Max concurrent device connections per job |
| `storage.filename_pattern` | Output filename template |

### Filename Pattern Variables

- `{device_name}` — Normalized device name
- `{device_id}` — Device ID from assets.db
- `{timestamp}` — Current timestamp (YYYYMMDD_HHMMSS)

---

## Concurrency Model

### run_job

```
┌─────────────────────────────────────────┐
│              run_job                    │
│                                         │
│   ┌─────────────────────────────────┐   │
│   │      SSHExecutorPool            │   │
│   │      (max_workers=12)           │   │
│   │                                 │   │
│   │  ┌───┐ ┌───┐ ┌───┐     ┌───┐   │   │
│   │  │D1 │ │D2 │ │D3 │ ... │D9 │   │   │
│   │  └───┘ └───┘ └───┘     └───┘   │   │
│   │    ▼     ▼     ▼         ▼     │   │
│   │   SSH   SSH   SSH       SSH    │   │
│   └─────────────────────────────────┘   │
└─────────────────────────────────────────┘
```

Single job, multiple devices connected concurrently via thread pool.

### run_jobs_concurrent

```
┌─────────────────────────────────────────────────────────────┐
│                  run_jobs_concurrent                        │
│                  (max_concurrent_jobs=4)                    │
│                                                             │
│  ┌─────────────────────┐    ┌─────────────────────┐        │
│  │       Job 328       │    │       Job 329       │        │
│  │  ┌───────────────┐  │    │  ┌───────────────┐  │        │
│  │  │ SSHExecPool   │  │    │  │ SSHExecPool   │  │        │
│  │  │ (workers=12)  │  │    │  │ (workers=12)  │  │        │
│  │  │ ┌──┐┌──┐┌──┐  │  │    │  │ ┌──┐┌──┐...   │  │        │
│  │  │ │D1││D2││D3│  │  │    │  │ │D1││D2│      │  │        │
│  │  │ └──┘└──┘└──┘  │  │    │  │ └──┘└──┘      │  │        │
│  │  └───────────────┘  │    │  └───────────────┘  │        │
│  └─────────────────────┘    └─────────────────────┘        │
│            ▼                          ▼                    │
│     3 devices @ 12.8s            9 devices @ 14.8s         │
│                                                             │
│              Total wall time: 14.9s (parallel)              │
│              vs ~27.6s sequential                           │
└─────────────────────────────────────────────────────────────┘
```

Multiple jobs run in parallel, each with its own concurrent device pool.

---

## Credential Management

Both tools use the same vault-based credential system:

1. **Vault unlocked once** at startup with password
2. **Credentials retrieved** by name (`--credential lab`) or vault default
3. **Vault locked** after execution completes

### Credential Priority

1. `--credential <name>` — explicit credential set
2. Vault default (`is_default=1`) — if no `--credential` specified
3. Error with available options — if no default exists

### Environment Variable

Set `VELOCITYCMDB_VAULT_PASS` to skip interactive password prompt:

```bash
export VELOCITYCMDB_VAULT_PASS=mysecret
python -m vcollector.run_job --job jobs_v2/job_329.json -y
```

---

## File Paths

| Path | Description |
|------|-------------|
| `~/.velocitycmdb/data/assets.db` | Device inventory database |
| `~/.vcollector/collections/<output_dir>/` | Capture output files |
| `jobs_v2/*.json` | Job definition files (convention) |

---

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | All devices/jobs succeeded |
| 1 | One or more failures (auth, connection, job error) |

---

## Tips

### Testing a New Job

```bash
# Check device filter matches expected devices
python -m vcollector.run_job --job jobs_v2/new_job.json --dry-run

# Test against one device with debug
python -m vcollector.run_job --job jobs_v2/new_job.json --limit 1 --debug --credential lab
```

### Scheduled Runs

```bash
# Cron-friendly: env var for password, -y to skip prompt, -q for minimal output
VELOCITYCMDB_VAULT_PASS=secret python -m vcollector.run_jobs_concurrent \
    --jobs-dir jobs_v2/ --credential lab -y -q
```

### Tuning Concurrency

- **Per-job device concurrency**: Set `execution.max_workers` in job JSON (default: 12)
- **Cross-job concurrency**: Use `--max-concurrent-jobs N` with `run_jobs_concurrent` (default: 4)

For 5 jobs × 12 workers = up to 60 concurrent SSH sessions. Adjust based on your control machine's resources and target device limits.