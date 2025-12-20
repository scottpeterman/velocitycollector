#!/usr/bin/env python3
"""
VelocityCollector Job Migration Script

Migrates job definitions from JSON files to the new jobs table in collector.db.

Usage:
    python migrate_jobs.py [--jobs-dir ~/.vcollector/jobs] [--db ~/.vcollector/collector.db]
"""

import json
import sqlite3
import re
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List
import argparse


def slugify(text: str) -> str:
    """Convert text to URL-friendly slug."""
    slug = text.lower().strip()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    slug = slug.strip('-')
    slug = re.sub(r'-+', '-', slug)
    return slug


def parse_job_json(filepath: Path) -> Optional[Dict[str, Any]]:
    """Parse a job JSON file and extract relevant fields."""
    try:
        with open(filepath) as f:
            data = json.load(f)

        # Handle both v1 and v2 formats
        version = data.get('version', '1.0')

        job = {
            'legacy_job_id': data.get('job_id'),
            'legacy_job_file': filepath.name,
            'capture_type': data.get('capture_type', 'custom'),
            'vendor': data.get('vendor'),
        }

        # Extract commands section
        commands = data.get('commands', {})
        if isinstance(commands, dict):
            job['paging_disable_command'] = commands.get('paging_disable')
            job['command'] = commands.get('command', '')
            job['output_directory'] = commands.get('output_directory')
        elif isinstance(commands, str):
            # v1 format: comma-separated commands
            job['command'] = commands

        # Device filter
        device_filter = data.get('device_filter', {})
        job['device_filter_source'] = device_filter.get('source', 'database')
        job['device_filter_name_pattern'] = device_filter.get('name_pattern')
        # Note: site/role filters would need ID lookups against dcim.db

        # Validation
        validation = data.get('validation', {})
        job['use_textfsm'] = 1 if validation.get('use_tfsm') else 0
        job['textfsm_template'] = validation.get('tfsm_filter')
        job['validation_min_score'] = validation.get('min_score', 0)
        job['store_failures'] = 1 if validation.get('store_failures', True) else 0

        # Execution
        execution = data.get('execution', {})
        job['max_workers'] = execution.get('max_workers', 10)
        job['timeout_seconds'] = execution.get('timeout', 60)
        job['inter_command_delay'] = execution.get('inter_command_time', 1)

        # Storage
        storage = data.get('storage', {})
        job['base_path'] = storage.get('base_path', '~/.vcollector/collections')
        job['filename_pattern'] = storage.get('filename_pattern', '{device_name}.txt')

        # Credentials
        creds = data.get('credentials', {})
        job['credential_fallback_env'] = creds.get('fallback_env')
        # Note: credential_id lookup would need to match credential_set name

        # Protocol
        job['protocol'] = data.get('protocol', 'ssh')

        # Generate name and slug
        vendor_name = (job['vendor'] or 'multi').title()
        capture_name = (job['capture_type'] or 'custom').upper()
        job['name'] = f"{vendor_name} {capture_name} Collection"
        job['slug'] = slugify(f"{job['vendor'] or 'multi'}-{job['capture_type'] or 'custom'}")

        # Ensure unique slug by appending job_id if needed
        if job['legacy_job_id']:
            job['slug'] = f"{job['slug']}-{job['legacy_job_id']}"

        return job

    except (json.JSONDecodeError, KeyError) as e:
        print(f"Error parsing {filepath}: {e}")
        return None


def init_jobs_schema(conn: sqlite3.Connection):
    """Initialize the jobs schema if it doesn't exist."""
    schema_sql = """
    CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        slug TEXT UNIQUE NOT NULL,
        description TEXT,
        capture_type TEXT NOT NULL,
        vendor TEXT,
        credential_id INTEGER,
        credential_fallback_env TEXT,
        protocol TEXT NOT NULL DEFAULT 'ssh',
        device_filter_source TEXT DEFAULT 'database',
        device_filter_platform_id INTEGER,
        device_filter_site_id INTEGER,
        device_filter_role_id INTEGER,
        device_filter_name_pattern TEXT,
        device_filter_status TEXT DEFAULT 'active',
        paging_disable_command TEXT,
        command TEXT NOT NULL,
        output_directory TEXT,
        filename_pattern TEXT DEFAULT '{device_name}.txt',
        use_textfsm INTEGER DEFAULT 0,
        textfsm_template TEXT,
        validation_min_score INTEGER DEFAULT 0,
        store_failures INTEGER DEFAULT 1,
        max_workers INTEGER DEFAULT 10,
        timeout_seconds INTEGER DEFAULT 60,
        inter_command_delay INTEGER DEFAULT 1,
        base_path TEXT DEFAULT '~/.vcollector/collections',
        schedule_enabled INTEGER DEFAULT 0,
        schedule_cron TEXT,
        is_enabled INTEGER DEFAULT 1,
        last_run_at TEXT,
        last_run_status TEXT,
        legacy_job_id INTEGER,
        legacy_job_file TEXT,
        migrated_at TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (credential_id) REFERENCES credentials(id) ON DELETE SET NULL
    );

    CREATE INDEX IF NOT EXISTS idx_jobs_capture_type ON jobs(capture_type);
    CREATE INDEX IF NOT EXISTS idx_jobs_vendor ON jobs(vendor);
    CREATE INDEX IF NOT EXISTS idx_jobs_enabled ON jobs(is_enabled);
    CREATE INDEX IF NOT EXISTS idx_jobs_legacy_id ON jobs(legacy_job_id);
    """
    conn.executescript(schema_sql)
    conn.commit()


def migrate_job(conn: sqlite3.Connection, job: Dict[str, Any]) -> Optional[int]:
    """Insert a job into the database. Returns job ID or None if failed."""

    # Check if already migrated
    existing = conn.execute(
        "SELECT id FROM jobs WHERE legacy_job_id = ? OR slug = ?",
        (job['legacy_job_id'], job['slug'])
    ).fetchone()

    if existing:
        print(f"  Skipping {job['slug']} - already exists (ID: {existing[0]})")
        return existing[0]

    fields = [
        'name', 'slug', 'capture_type', 'vendor', 'credential_fallback_env',
        'protocol', 'device_filter_source', 'device_filter_name_pattern',
        'paging_disable_command', 'command', 'output_directory', 'filename_pattern',
        'use_textfsm', 'textfsm_template', 'validation_min_score', 'store_failures',
        'max_workers', 'timeout_seconds', 'inter_command_delay', 'base_path',
        'legacy_job_id', 'legacy_job_file', 'migrated_at'
    ]

    job['migrated_at'] = datetime.now().isoformat()

    placeholders = ', '.join(['?' for _ in fields])
    field_names = ', '.join(fields)
    values = [job.get(f) for f in fields]

    try:
        cursor = conn.execute(
            f"INSERT INTO jobs ({field_names}) VALUES ({placeholders})",
            values
        )
        conn.commit()
        return cursor.lastrowid
    except sqlite3.IntegrityError as e:
        print(f"  Error inserting {job['slug']}: {e}")
        return None


def find_job_files(jobs_dir: Path) -> List[Path]:
    """Find all job JSON files in the directory."""
    patterns = ['job_*.json', 'job*.json']
    files = []
    for pattern in patterns:
        files.extend(jobs_dir.glob(pattern))
    return sorted(set(files))


def main():
    parser = argparse.ArgumentParser(description='Migrate job JSON files to database')
    parser.add_argument('--jobs-dir', type=Path,
                        default=Path.home() / '.vcollector' / 'jobs',
                        help='Directory containing job JSON files')
    parser.add_argument('--db', type=Path,
                        default=Path.home() / '.vcollector' / 'collector.db',
                        help='Path to collector.db')
    parser.add_argument('--dry-run', action='store_true',
                        help='Parse and display jobs without inserting')

    args = parser.parse_args()

    print(f"VelocityCollector Job Migration")
    print(f"=" * 40)
    print(f"Jobs directory: {args.jobs_dir}")
    print(f"Database: {args.db}")
    print()

    # Find job files
    job_files = find_job_files(args.jobs_dir)
    if not job_files:
        print(f"No job files found in {args.jobs_dir}")
        print("Looking for files matching: job_*.json, job*.json")
        return

    print(f"Found {len(job_files)} job file(s):")
    for f in job_files:
        print(f"  - {f.name}")
    print()

    # Parse all jobs
    jobs = []
    for filepath in job_files:
        print(f"Parsing: {filepath.name}")
        job = parse_job_json(filepath)
        if job:
            jobs.append(job)
            print(f"  Name: {job['name']}")
            print(f"  Slug: {job['slug']}")
            print(f"  Type: {job['capture_type']}")
            print(f"  Vendor: {job['vendor']}")
            print(f"  Command: {job['command'][:50]}..." if len(
                job.get('command', '')) > 50 else f"  Command: {job.get('command')}")
        print()

    if args.dry_run:
        print("Dry run - no changes made")
        return

    # Connect to database and migrate
    print(f"Connecting to database: {args.db}")
    conn = sqlite3.connect(str(args.db))

    print("Initializing jobs schema...")
    init_jobs_schema(conn)

    print(f"\nMigrating {len(jobs)} job(s)...")
    migrated = 0
    for job in jobs:
        job_id = migrate_job(conn, job)
        if job_id:
            print(f"  Migrated: {job['name']} -> ID {job_id}")
            migrated += 1

    conn.close()

    print()
    print(f"Migration complete: {migrated}/{len(jobs)} jobs migrated")


if __name__ == '__main__':
    main()