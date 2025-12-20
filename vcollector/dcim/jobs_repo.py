"""
VelocityCollector Jobs Repository

Data access layer for job definitions and job history.
Works with collector.db to manage collection jobs.

Usage:
    from vcollector.db.jobs_repo import JobsRepository

    repo = JobsRepository()

    # Get all jobs
    jobs = repo.get_jobs()

    # Get job by ID or slug
    job = repo.get_job(job_id=1)
    job = repo.get_job(slug='arista-arp-300')

    # Create a new job
    job_id = repo.create_job(
        name="Cisco Config Backup",
        slug="cisco-config",
        capture_type="config",
        vendor="cisco",
        command="show running-config"
    )
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from enum import Enum


class CaptureType(str, Enum):
    """Common capture types for jobs."""
    ARP = "arp"
    MAC = "mac"
    CONFIG = "config"
    INVENTORY = "inventory"
    INTERFACES = "interfaces"
    ROUTES = "routes"
    BGP = "bgp"
    OSPF = "ospf"
    VLANS = "vlans"
    SPANNING = "spanning"
    CDP = "cdp"
    LLDP = "lldp"
    VERSION = "version"
    CUSTOM = "custom"


class JobStatus(str, Enum):
    """Job run status."""
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    RUNNING = "running"
    PENDING = "pending"


@dataclass
class Job:
    """Job definition model."""
    id: Optional[int] = None
    name: str = ""
    slug: str = ""
    description: Optional[str] = None

    # Classification
    capture_type: str = "custom"
    vendor: Optional[str] = None

    # Credentials
    credential_id: Optional[int] = None
    credential_fallback_env: Optional[str] = None

    # Connection
    protocol: str = "ssh"

    # Device Filtering
    device_filter_source: str = "database"
    device_filter_platform_id: Optional[int] = None
    device_filter_site_id: Optional[int] = None
    device_filter_role_id: Optional[int] = None
    device_filter_name_pattern: Optional[str] = None
    device_filter_status: str = "active"

    # Commands
    paging_disable_command: Optional[str] = None
    command: str = ""

    # Output
    output_directory: Optional[str] = None
    filename_pattern: str = "{device_name}.txt"

    # Validation
    use_textfsm: bool = False
    textfsm_template: Optional[str] = None
    validation_min_score: int = 0
    store_failures: bool = True

    # Execution
    max_workers: int = 10
    timeout_seconds: int = 60
    inter_command_delay: int = 1

    # Storage
    base_path: str = "~/.vcollector/collections"

    # Scheduling
    schedule_enabled: bool = False
    schedule_cron: Optional[str] = None

    # State
    is_enabled: bool = True
    last_run_at: Optional[str] = None
    last_run_status: Optional[str] = None

    # Legacy
    legacy_job_id: Optional[int] = None
    legacy_job_file: Optional[str] = None
    migrated_at: Optional[str] = None

    # Timestamps
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    # Joined fields (from queries)
    credential_name: Optional[str] = None
    run_count: Optional[int] = None


@dataclass
class JobHistory:
    """Job execution history model."""
    id: Optional[int] = None
    job_id: str = ""
    job_file: Optional[str] = None
    started_at: str = ""
    completed_at: Optional[str] = None
    total_devices: Optional[int] = None
    success_count: Optional[int] = None
    failed_count: Optional[int] = None
    status: Optional[str] = None
    error_message: Optional[str] = None

    # Joined fields
    job_name: Optional[str] = None
    capture_type: Optional[str] = None
    vendor: Optional[str] = None


class JobsRepository:
    """
    Data access layer for jobs and job history.
    """

    def __init__(self, db_path: Optional[Path] = None):
        """
        Initialize repository.

        Args:
            db_path: Path to collector.db. If None, uses default location.
        """
        if db_path is None:
            db_path = Path.home() / ".vcollector" / "collector.db"

        self.db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None

    @property
    def conn(self) -> sqlite3.Connection:
        """Get database connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
        return self._conn

    def close(self):
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def _row_to_job(self, row: sqlite3.Row) -> Job:
        """Convert a sqlite Row to a Job instance."""
        if row is None:
            return None

        data = dict(row)
        # Convert integer booleans
        for bool_field in ['use_textfsm', 'store_failures', 'schedule_enabled', 'is_enabled']:
            if bool_field in data:
                data[bool_field] = bool(data[bool_field])

        # Only include fields that exist in Job dataclass
        job_fields = {k: v for k, v in data.items() if k in Job.__dataclass_fields__}
        return Job(**job_fields)

    def _row_to_history(self, row: sqlite3.Row) -> JobHistory:
        """Convert a sqlite Row to a JobHistory instance."""
        if row is None:
            return None
        data = {k: row[k] for k in row.keys() if k in JobHistory.__dataclass_fields__}
        return JobHistory(**data)

    def _now(self) -> str:
        """Get current timestamp in ISO format."""
        return datetime.now().isoformat(sep=' ', timespec='seconds')

    # =========================================================================
    # Jobs CRUD
    # =========================================================================

    def create_job(self, name: str, slug: str, capture_type: str, command: str, **kwargs) -> int:
        """Create a new job definition."""
        fields = ['name', 'slug', 'capture_type', 'command', 'created_at', 'updated_at']
        values = [name, slug, capture_type, command, self._now(), self._now()]

        # Optional fields
        optional = [
            'description', 'vendor', 'credential_id', 'credential_fallback_env',
            'protocol', 'device_filter_source', 'device_filter_platform_id',
            'device_filter_site_id', 'device_filter_role_id', 'device_filter_name_pattern',
            'device_filter_status', 'paging_disable_command', 'output_directory',
            'filename_pattern', 'use_textfsm', 'textfsm_template', 'validation_min_score',
            'store_failures', 'max_workers', 'timeout_seconds', 'inter_command_delay',
            'base_path', 'schedule_enabled', 'schedule_cron', 'is_enabled',
            'legacy_job_id', 'legacy_job_file'
        ]

        for key in optional:
            if key in kwargs and kwargs[key] is not None:
                fields.append(key)
                value = kwargs[key]
                # Convert booleans to integers for SQLite
                if isinstance(value, bool):
                    value = 1 if value else 0
                values.append(value)

        placeholders = ', '.join(['?' for _ in fields])
        field_names = ', '.join(fields)

        cursor = self.conn.execute(
            f"INSERT INTO jobs ({field_names}) VALUES ({placeholders})",
            values
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_job(self, job_id: Optional[int] = None,
                slug: Optional[str] = None,
                legacy_job_id: Optional[int] = None) -> Optional[Job]:
        """Get a job by ID, slug, or legacy job ID."""
        query = """
            SELECT j.*, c.name as credential_name,
                   (SELECT COUNT(*) FROM job_history h 
                    WHERE h.job_id = j.slug OR h.job_id = CAST(j.legacy_job_id AS TEXT)) as run_count
            FROM jobs j
            LEFT JOIN credentials c ON j.credential_id = c.id
            WHERE j.{} = ?
        """

        if job_id:
            row = self.conn.execute(query.format('id'), (job_id,)).fetchone()
        elif slug:
            row = self.conn.execute(query.format('slug'), (slug,)).fetchone()
        elif legacy_job_id:
            row = self.conn.execute(query.format('legacy_job_id'), (legacy_job_id,)).fetchone()
        else:
            return None

        return self._row_to_job(row)

    def get_jobs(self,
                 capture_type: Optional[str] = None,
                 vendor: Optional[str] = None,
                 is_enabled: Optional[bool] = None,
                 search: Optional[str] = None) -> List[Job]:
        """Get all jobs with optional filtering."""
        query = """
            SELECT j.*, c.name as credential_name,
                   (SELECT COUNT(*) FROM job_history h 
                    WHERE h.job_id = j.slug OR h.job_id = CAST(j.legacy_job_id AS TEXT)) as run_count
            FROM jobs j
            LEFT JOIN credentials c ON j.credential_id = c.id
            WHERE 1=1
        """
        params = []

        if capture_type:
            query += " AND j.capture_type = ?"
            params.append(capture_type)

        if vendor:
            query += " AND j.vendor = ?"
            params.append(vendor)

        if is_enabled is not None:
            query += " AND j.is_enabled = ?"
            params.append(1 if is_enabled else 0)

        if search:
            query += " AND (j.name LIKE ? OR j.slug LIKE ? OR j.description LIKE ?)"
            pattern = f"%{search}%"
            params.extend([pattern, pattern, pattern])

        query += " ORDER BY j.name"

        rows = self.conn.execute(query, params).fetchall()
        return [self._row_to_job(row) for row in rows]

    def update_job(self, job_id: int, **kwargs) -> bool:
        """Update a job definition."""
        if not kwargs:
            return False

        kwargs['updated_at'] = self._now()

        # Convert booleans to integers
        for key, value in kwargs.items():
            if isinstance(value, bool):
                kwargs[key] = 1 if value else 0

        set_clause = ', '.join([f"{k} = ?" for k in kwargs.keys()])
        values = list(kwargs.values()) + [job_id]

        cursor = self.conn.execute(
            f"UPDATE jobs SET {set_clause} WHERE id = ?",
            values
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def delete_job(self, job_id: int) -> bool:
        """Delete a job definition."""
        cursor = self.conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        self.conn.commit()
        return cursor.rowcount > 0

    def duplicate_job(self, job_id: int, new_name: str, new_slug: str) -> Optional[int]:
        """Create a copy of an existing job with a new name/slug."""
        job = self.get_job(job_id=job_id)
        if not job:
            return None

        # Remove fields that shouldn't be copied
        exclude = ['id', 'created_at', 'updated_at', 'last_run_at', 'last_run_status',
                   'legacy_job_id', 'legacy_job_file', 'migrated_at', 'credential_name', 'run_count']

        kwargs = {k: v for k, v in job.__dict__.items()
                  if k not in exclude and v is not None}

        return self.create_job(
            name=new_name,
            slug=new_slug,
            capture_type=kwargs.pop('capture_type'),
            command=kwargs.pop('command'),
            **kwargs
        )

    def set_job_enabled(self, job_id: int, enabled: bool) -> bool:
        """Enable or disable a job."""
        return self.update_job(job_id, is_enabled=enabled)

    def update_job_last_run(self, job_id: int, status: str) -> bool:
        """Update job's last run timestamp and status."""
        return self.update_job(job_id, last_run_at=self._now(), last_run_status=status)

    # =========================================================================
    # Job History
    # =========================================================================

    def create_job_history(self, job_id: str, started_at: Optional[str] = None,
                           job_file: Optional[str] = None) -> int:
        """Create a new job history entry. Returns history ID."""
        started = started_at or self._now()

        cursor = self.conn.execute(
            """INSERT INTO job_history (job_id, job_file, started_at, status)
               VALUES (?, ?, ?, ?)""",
            (job_id, job_file, started, 'running')
        )
        self.conn.commit()
        return cursor.lastrowid

    def update_job_history(self, history_id: int, **kwargs) -> bool:
        """Update a job history entry."""
        if not kwargs:
            return False

        set_clause = ', '.join([f"{k} = ?" for k in kwargs.keys()])
        values = list(kwargs.values()) + [history_id]

        cursor = self.conn.execute(
            f"UPDATE job_history SET {set_clause} WHERE id = ?",
            values
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def complete_job_history(self, history_id: int, total_devices: int,
                             success_count: int, failed_count: int,
                             status: str = 'success', error_message: Optional[str] = None) -> bool:
        """Mark a job history entry as complete."""
        return self.update_job_history(
            history_id,
            completed_at=self._now(),
            total_devices=total_devices,
            success_count=success_count,
            failed_count=failed_count,
            status=status,
            error_message=error_message
        )

    def get_job_history(self, history_id: int) -> Optional[JobHistory]:
        """Get a specific job history entry."""
        row = self.conn.execute(
            """SELECT h.*, j.name as job_name, j.capture_type, j.vendor
               FROM job_history h
               LEFT JOIN jobs j ON h.job_id = j.slug OR h.job_id = CAST(j.legacy_job_id AS TEXT)
               WHERE h.id = ?""",
            (history_id,)
        ).fetchone()
        return self._row_to_history(row)

    def get_job_history_list(self,
                             job_slug: Optional[str] = None,
                             status: Optional[str] = None,
                             limit: int = 50,
                             offset: int = 0) -> List[JobHistory]:
        """Get job history entries with optional filtering."""
        query = """
            SELECT h.*, j.name as job_name, j.capture_type, j.vendor
            FROM job_history h
            LEFT JOIN jobs j ON h.job_id = j.slug OR h.job_id = CAST(j.legacy_job_id AS TEXT)
            WHERE 1=1
        """
        params = []

        if job_slug:
            query += " AND (h.job_id = ? OR h.job_id = (SELECT CAST(legacy_job_id AS TEXT) FROM jobs WHERE slug = ?))"
            params.extend([job_slug, job_slug])

        if status:
            query += " AND h.status = ?"
            params.append(status)

        query += " ORDER BY h.started_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = self.conn.execute(query, params).fetchall()
        return [self._row_to_history(row) for row in rows]

    # =========================================================================
    # Statistics
    # =========================================================================

    def get_stats(self) -> Dict[str, int]:
        """Get job statistics."""
        stats = {}

        stats['total_jobs'] = self.conn.execute(
            "SELECT COUNT(*) FROM jobs"
        ).fetchone()[0]

        stats['enabled_jobs'] = self.conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE is_enabled = 1"
        ).fetchone()[0]

        stats['total_runs'] = self.conn.execute(
            "SELECT COUNT(*) FROM job_history"
        ).fetchone()[0]

        stats['successful_runs'] = self.conn.execute(
            "SELECT COUNT(*) FROM job_history WHERE status = 'success'"
        ).fetchone()[0]

        stats['failed_runs'] = self.conn.execute(
            "SELECT COUNT(*) FROM job_history WHERE status = 'failed'"
        ).fetchone()[0]

        # Unique capture types
        stats['capture_types'] = self.conn.execute(
            "SELECT COUNT(DISTINCT capture_type) FROM jobs"
        ).fetchone()[0]

        # Unique vendors
        stats['vendors'] = self.conn.execute(
            "SELECT COUNT(DISTINCT vendor) FROM jobs WHERE vendor IS NOT NULL"
        ).fetchone()[0]

        return stats

    def get_capture_types(self) -> List[str]:
        """Get list of unique capture types in use."""
        rows = self.conn.execute(
            "SELECT DISTINCT capture_type FROM jobs ORDER BY capture_type"
        ).fetchall()
        return [row[0] for row in rows]

    def get_vendors(self) -> List[str]:
        """Get list of unique vendors in use."""
        rows = self.conn.execute(
            "SELECT DISTINCT vendor FROM jobs WHERE vendor IS NOT NULL ORDER BY vendor"
        ).fetchall()
        return [row[0] for row in rows]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


if __name__ == "__main__":
    # Quick test
    repo = JobsRepository()

    print("=== Jobs Repository Test ===\n")

    # Get stats
    stats = repo.get_stats()
    print("Statistics:")
    for key, value in stats.items():
        print(f"  {key}: {value}")

    # List jobs
    print("\nJobs:")
    jobs = repo.get_jobs()
    for job in jobs:
        print(f"  [{job.id}] {job.name} ({job.capture_type}/{job.vendor})")
        print(f"      Command: {job.command[:50]}..." if len(job.command) > 50 else f"      Command: {job.command}")
        print(f"      Runs: {job.run_count}, Last: {job.last_run_status or 'never'}")

    # List recent history
    print("\nRecent History:")
    history = repo.get_job_history_list(limit=5)
    for h in history:
        print(f"  [{h.id}] {h.job_name or h.job_id} - {h.status} ({h.started_at})")
        if h.total_devices:
            print(f"      Devices: {h.success_count}/{h.total_devices} success")

    repo.close()