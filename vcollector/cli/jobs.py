"""
Jobs CLI handler - Refactored for database-first architecture.

Handles: vcollector jobs <command>

Uses JobsRepository to query collector.db instead of scanning JSON files.
Legacy JSON files are still supported via --legacy flag where applicable.
"""

import json
from pathlib import Path
from typing import Optional

from vcollector.core.config import get_config
from vcollector.dcim.jobs_repo import JobsRepository, Job


def handle_jobs(args) -> int:
    """Handle jobs subcommand."""

    if not args.jobs_command:
        print("Usage: vcollector jobs <command>")
        print("Commands: list, show, validate, create, history")
        return 1

    if args.jobs_command == "list":
        return _jobs_list(args)
    elif args.jobs_command == "show":
        return _jobs_show(args)
    elif args.jobs_command == "validate":
        return _jobs_validate(args)
    elif args.jobs_command == "create":
        return _jobs_create(args)
    elif args.jobs_command == "history":
        return _jobs_history(args)
    else:
        print(f"Unknown jobs command: {args.jobs_command}")
        return 1


def _jobs_list(args) -> int:
    """List available jobs from database."""

    # Check for legacy mode (scan JSON files)
    if getattr(args, 'legacy', False):
        return _jobs_list_legacy(args)

    with JobsRepository() as repo:
        # Apply filters from args
        vendor = getattr(args, 'vendor', None)
        capture_type = getattr(args, 'type', None)
        enabled_only = getattr(args, 'enabled', None)
        search = getattr(args, 'search', None)

        jobs = repo.get_jobs(
            vendor=vendor,
            capture_type=capture_type,
            is_enabled=enabled_only,
            search=search
        )

        if not jobs:
            print("No jobs found in database")
            print("\nCreate jobs with: vcollector jobs create")
            print("Or import legacy JSON: vcollector jobs migrate --dir ~/.vcollector/jobs")
            return 0

        # Get stats for header
        stats = repo.get_stats()
        print(f"Jobs ({stats['enabled_jobs']} enabled / {stats['total_jobs']} total):\n")

        # Group by vendor for cleaner output
        by_vendor = {}
        for job in jobs:
            v = job.vendor or "multi-vendor"
            if v not in by_vendor:
                by_vendor[v] = []
            by_vendor[v].append(job)

        for vendor_name in sorted(by_vendor.keys()):
            print(f"  [{vendor_name}]")
            for job in by_vendor[vendor_name]:
                status = "✓" if job.is_enabled else "○"
                run_info = ""
                if job.last_run_status:
                    run_info = f" (last: {job.last_run_status})"
                print(f"    {status} {job.slug}")
                print(f"        {job.name} [{job.capture_type}]{run_info}")
            print()

        # Summary
        capture_types = repo.get_capture_types()
        vendors = repo.get_vendors()
        print(f"Capture types: {', '.join(capture_types)}")
        print(f"Vendors: {', '.join(vendors)}")

    return 0


def _jobs_list_legacy(args) -> int:
    """List jobs from legacy JSON files (backward compatibility)."""
    config = get_config()
    jobs_dir = Path(args.dir) if getattr(args, 'dir', None) else config.legacy_jobs_dir

    if not jobs_dir.exists():
        print(f"Jobs directory not found: {jobs_dir}")
        return 1

    job_files = sorted(jobs_dir.glob('*.json'))

    if not job_files:
        print(f"No job files found in: {jobs_dir}")
        return 0

    print(f"Legacy jobs in {jobs_dir}:\n")

    for jf in job_files:
        try:
            with open(jf) as f:
                job = json.load(f)
            job_id = job.get('job_id', '?')
            capture_type = job.get('capture_type', '?')
            vendor = job.get('device_filter', {}).get('vendor', 'any')
            print(f"  {jf.name}")
            print(f"    ID: {job_id}, Type: {capture_type}, Vendor: {vendor}")
        except Exception as e:
            print(f"  {jf.name}")
            print(f"    Error: {e}")
        print()

    print(f"Total: {len(job_files)} legacy jobs")
    print("\nTo migrate to database: vcollector jobs migrate --dir", jobs_dir)
    return 0


def _jobs_show(args) -> int:
    """Show job details."""
    job_ref = args.job_file  # Can be slug, ID, or file path

    # Try database first
    with JobsRepository() as repo:
        job = None

        # Try as slug
        job = repo.get_job(slug=job_ref)

        # Try as numeric ID
        if not job and job_ref.isdigit():
            job = repo.get_job(job_id=int(job_ref))

        if job:
            _print_job_detail(job)

            # Show recent history
            history = repo.get_job_history_list(job_slug=job.slug, limit=5)
            if history:
                print("\nRecent runs:")
                for h in history:
                    status_icon = "✓" if h.status == "success" else "✗" if h.status == "failed" else "◐"
                    devices = f"{h.success_count}/{h.total_devices}" if h.total_devices else "?"
                    print(f"  {status_icon} {h.started_at[:16]} - {h.status} ({devices} devices)")

            return 0

    # Fall back to file path (legacy JSON)
    job_path = Path(job_ref)
    if job_path.exists():
        try:
            with open(job_path) as f:
                job_data = json.load(f)
            print(f"Legacy job: {job_path.name}\n")
            print(json.dumps(job_data, indent=2))
            return 0
        except json.JSONDecodeError as e:
            print(f"Invalid JSON: {e}")
            return 1

    print(f"Job not found: {job_ref}")
    print("Specify a job slug, ID, or path to legacy JSON file")
    return 1


def _print_job_detail(job: Job):
    """Print formatted job details."""
    print(f"Job: {job.name}")
    print(f"  Slug: {job.slug}")
    print(f"  ID: {job.id}")
    if job.description:
        print(f"  Description: {job.description}")
    print()

    print("Classification:")
    print(f"  Capture type: {job.capture_type}")
    print(f"  Vendor: {job.vendor or 'any'}")
    print(f"  Enabled: {'yes' if job.is_enabled else 'no'}")
    print()

    print("Commands:")
    if job.paging_disable_command:
        print(f"  Paging disable: {job.paging_disable_command}")
    print(f"  Command: {job.command}")
    print()

    print("Output:")
    print(f"  Directory: {job.output_directory or job.capture_type}")
    print(f"  Filename: {job.filename_pattern}")
    print(f"  Base path: {job.base_path}")
    print()

    print("Device filters:")
    print(f"  Source: {job.device_filter_source}")
    print(f"  Status: {job.device_filter_status}")
    if job.device_filter_platform_id:
        print(f"  Platform ID: {job.device_filter_platform_id}")
    if job.device_filter_site_id:
        print(f"  Site ID: {job.device_filter_site_id}")
    if job.device_filter_role_id:
        print(f"  Role ID: {job.device_filter_role_id}")
    if job.device_filter_name_pattern:
        print(f"  Name pattern: {job.device_filter_name_pattern}")
    print()

    print("Execution:")
    print(f"  Protocol: {job.protocol}")
    print(f"  Max workers: {job.max_workers}")
    print(f"  Timeout: {job.timeout_seconds}s")
    print()

    if job.use_textfsm:
        print("Validation:")
        print(f"  TextFSM: enabled")
        if job.textfsm_template:
            print(f"  Template: {job.textfsm_template}")
        print(f"  Min score: {job.validation_min_score}")
        print(f"  Store failures: {'yes' if job.store_failures else 'no'}")
        print()

    if job.legacy_job_id:
        print("Legacy:")
        print(f"  Original ID: {job.legacy_job_id}")
        if job.legacy_job_file:
            print(f"  Original file: {job.legacy_job_file}")
        if job.migrated_at:
            print(f"  Migrated: {job.migrated_at}")


def _jobs_validate(args) -> int:
    """Validate job (database or file)."""
    job_ref = args.job_file

    # Try database first
    with JobsRepository() as repo:
        job = repo.get_job(slug=job_ref)
        if not job and job_ref.isdigit():
            job = repo.get_job(job_id=int(job_ref))

        if job:
            errors = _validate_job(job)
            if errors:
                print(f"✗ Validation failed for {job.slug}:\n")
                for e in errors:
                    print(f"  - {e}")
                return 1
            else:
                print(f"✓ {job.slug} is valid")
                return 0

    # Fall back to legacy JSON
    job_path = Path(job_ref)
    if not job_path.exists():
        print(f"Job not found: {job_ref}")
        return 1

    return _validate_legacy_file(job_path)


def _validate_job(job: Job) -> list:
    """Validate a Job object. Returns list of errors."""
    errors = []

    if not job.command:
        errors.append("Missing command")

    if not job.capture_type:
        errors.append("Missing capture_type")

    # Warn about potentially problematic configurations
    if not job.device_filter_platform_id and not job.vendor:
        errors.append("Warning: No platform or vendor filter - job will match all devices")

    return errors


def _validate_legacy_file(job_path: Path) -> int:
    """Validate legacy JSON job file."""
    errors = []

    try:
        with open(job_path) as f:
            job = json.load(f)
    except json.JSONDecodeError as e:
        print(f"✗ Invalid JSON: {e}")
        return 1

    # Check required fields
    required = ['job_id', 'commands']
    for field in required:
        if field not in job:
            errors.append(f"Missing required field: {field}")

    if 'commands' in job:
        if 'command' not in job['commands']:
            errors.append("Missing commands.command")

    if 'device_filter' not in job:
        errors.append("Warning: Missing device_filter (job will match all devices)")

    if errors:
        print(f"✗ Validation failed for {job_path.name}:\n")
        for e in errors:
            print(f"  - {e}")
        return 1
    else:
        print(f"✓ {job_path.name} is valid")
        return 0


def _jobs_create(args) -> int:
    """Create new job in database."""
    from vcollector.dcim.dcim_repo import DCIMRepository

    vendor = args.vendor
    capture_type = args.type

    # Generate slug
    slug = f"{vendor}-{capture_type}".lower().replace(' ', '-')
    name = f"{vendor.title()} {capture_type.upper()}"

    # Get platform info for paging command
    paging_cmd = None
    with DCIMRepository() as dcim:
        platforms = dcim.get_platforms()
        for p in platforms:
            if p.slug and vendor.lower() in p.slug.lower():
                paging_cmd = p.paging_disable_command
                break

    # Default commands by capture type
    default_commands = {
        'config': 'show running-config',
        'arp': 'show ip arp',
        'mac': 'show mac address-table',
        'interfaces': 'show interfaces',
        'inventory': 'show inventory',
        'version': 'show version',
        'routes': 'show ip route',
        'bgp': 'show ip bgp summary',
        'ospf': 'show ip ospf neighbor',
        'vlans': 'show vlan',
        'lldp': 'show lldp neighbors',
        'cdp': 'show cdp neighbors',
    }

    command = default_commands.get(capture_type.lower(), f'show {capture_type}')

    with JobsRepository() as repo:
        # Check if slug exists
        existing = repo.get_job(slug=slug)
        if existing:
            print(f"Error: Job with slug '{slug}' already exists")
            return 1

        job_id = repo.create_job(
            name=name,
            slug=slug,
            capture_type=capture_type.lower(),
            command=command,
            vendor=vendor.lower(),
            paging_disable_command=paging_cmd,
            output_directory=capture_type.lower(),
            device_filter_status='active',
        )

        print(f"✓ Created job '{name}' (id={job_id}, slug={slug})")
        print(f"\nEdit with GUI or update via:")
        print(f"  vcollector jobs show {slug}")

        if args.output:
            # Export to JSON for reference
            job = repo.get_job(job_id=job_id)
            _export_job_json(job, Path(args.output))
            print(f"\nExported to: {args.output}")

        return 0


def _export_job_json(job: Job, output_path: Path):
    """Export job to legacy JSON format."""
    job_dict = {
        "job_id": job.legacy_job_id or job.id,
        "capture_type": job.capture_type,
        "commands": {
            "paging_disable": job.paging_disable_command,
            "command": job.command,
            "output_directory": job.output_directory or job.capture_type,
        },
        "device_filter": {
            "vendor": job.vendor,
            "status": job.device_filter_status,
        },
        "validation": {
            "use_tfsm": job.use_textfsm,
            "tfsm_filter": job.textfsm_template,
            "min_score": job.validation_min_score,
        },
        "execution": {
            "max_workers": job.max_workers,
            "timeout": job.timeout_seconds,
        },
        "storage": {
            "base_path": job.base_path,
        }
    }

    with open(output_path, 'w') as f:
        json.dump(job_dict, f, indent=2)


def _jobs_history(args) -> int:
    """Show job execution history."""
    limit = getattr(args, 'limit', 20)
    job_slug = getattr(args, 'job', None)

    with JobsRepository() as repo:
        history = repo.get_job_history_list(
            job_slug=job_slug,
            limit=limit
        )

        if not history:
            print("No job history found")
            return 0

        print(f"Job History (last {len(history)} runs):\n")

        for h in history:
            status_icon = "✓" if h.status == "success" else "✗" if h.status == "failed" else "◐"
            job_name = h.job_name or h.job_id

            # Format device counts
            if h.total_devices:
                devices = f"{h.success_count}/{h.total_devices} devices"
                if h.failed_count:
                    devices += f" ({h.failed_count} failed)"
            else:
                devices = "? devices"

            # Format duration
            duration = ""
            if h.started_at and h.completed_at:
                # Simple duration calc would go here
                pass

            print(f"  {status_icon} [{h.id}] {h.started_at[:16]}")
            print(f"      Job: {job_name} ({h.capture_type or '?'})")
            print(f"      Result: {h.status} - {devices}")
            if h.error_message:
                print(f"      Error: {h.error_message[:60]}...")
            print()

        # Summary stats
        stats = repo.get_stats()
        print(f"Total runs: {stats['total_runs']} ({stats['successful_runs']} success, {stats['failed_runs']} failed)")

    return 0