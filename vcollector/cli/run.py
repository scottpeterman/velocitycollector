"""
Run CLI handler - Refactored for database-first architecture.

Path: vcollector/cli/run.py

Handles: vcollector run [options]

Executes collection jobs against network devices with validation.
Supports per-device credentials via credential discovery.

Job sources (in order of precedence):
1. Database job by slug: --job arista-arp
2. Database job by ID: --job 42
3. Legacy JSON file: --job jobs/arista_arp.json
4. Multiple jobs: --jobs arista-* (matches slugs or files)

Device sources:
- Database (dcim.db) via DCIMRepository
- Filtered by job's device_filter_* fields
"""

import os
import sys
import re
import getpass
import glob
import json
from pathlib import Path
from typing import List, Optional, Union, Tuple
from dataclasses import dataclass

from vcollector.vault.resolver import CredentialResolver
from vcollector.dcim.jobs_repo import JobsRepository, Job
from vcollector.dcim.dcim_repo import DCIMRepository, Device


@dataclass
class JobRef:
    """Reference to a job - either database or legacy file."""
    slug: Optional[str] = None
    job_id: Optional[int] = None
    file_path: Optional[Path] = None
    job: Optional[Job] = None  # Loaded job object

    @property
    def display_name(self) -> str:
        if self.slug:
            return self.slug
        if self.file_path:
            return self.file_path.name
        return f"job-{self.job_id}"

    @property
    def is_database(self) -> bool:
        return self.slug is not None or self.job_id is not None


def handle_run(args) -> int:
    """Handle run subcommand."""

    # Collect job references
    job_refs = _collect_job_refs(args)

    if not job_refs:
        print("Error: No jobs found")
        print("Specify --job <slug|id|file> or --jobs <pattern>")
        return 1

    # Load and validate jobs
    loaded_jobs = _load_jobs(job_refs)
    if not loaded_jobs:
        print("Error: No valid jobs to run")
        return 1

    # Show jobs to run
    print(f"Jobs to run ({len(loaded_jobs)}):")
    for ref in loaded_jobs:
        source = "db" if ref.is_database else "file"
        print(f"  - {ref.display_name} [{source}]")
    print()

    if args.dry_run:
        return _dry_run(loaded_jobs, args)

    # Confirm
    if not args.yes:
        confirm = input(f"Run {len(loaded_jobs)} job(s)? [y/N]: ")
        if confirm.lower() != 'y':
            print("Aborted")
            return 0

    # Get vault password
    vault_pass = args.vault_pass or os.environ.get('VCOLLECTOR_VAULT_PASS')
    if not vault_pass:
        vault_pass = getpass.getpass("Vault password: ")

    # Unlock vault
    resolver = CredentialResolver()

    if not resolver.is_initialized():
        print("Error: Vault not initialized. Run 'vcollector vault init' first.")
        return 1

    if not resolver.unlock_vault(vault_pass):
        print("Error: Invalid vault password")
        return 1

    try:
        # Get credentials
        cred_name = args.credential
        if cred_name:
            creds = resolver.get_ssh_credentials(credential_name=cred_name)
            if not creds:
                print(f"Error: Credential '{cred_name}' not found")
                return 1
        else:
            creds = resolver.get_ssh_credentials()
            if not creds:
                available = resolver.list_credentials()
                if available:
                    print("Error: No default credential. Available:")
                    for c in available:
                        print(f"  - {c.name}{' (default)' if c.is_default else ''}")
                    print("\nUse --credential <name> to specify one")
                else:
                    print("Error: No credentials in vault")
                return 1

            # Find default name for display
            available = resolver.list_credentials()
            default_cred = next((c for c in available if c.is_default), None)
            cred_name = default_cred.name if default_cred else "default"

        print(f"Using credentials: {cred_name} (user: {creds.username})")
        print("=" * 60)

        # Execute job(s)
        if len(loaded_jobs) == 1:
            result = _run_single_job(loaded_jobs[0], creds, resolver, args)
            return 0 if result.success else 1
        else:
            result = _run_batch_jobs(loaded_jobs, creds, resolver, args)
            return 0 if result.success else 1

    finally:
        resolver.lock_vault()


def _collect_job_refs(args) -> List[JobRef]:
    """Collect job references from arguments."""
    job_refs = []

    if args.job:
        ref = _resolve_job_ref(args.job)
        if ref:
            job_refs.append(ref)

    elif args.jobs:
        for pattern in args.jobs:
            refs = _resolve_job_pattern(pattern)
            job_refs.extend(refs)

    elif args.jobs_dir:
        jobs_dir = Path(args.jobs_dir)
        if jobs_dir.is_dir():
            for jf in jobs_dir.glob('*.json'):
                job_refs.append(JobRef(file_path=jf))

    # Deduplicate by display_name
    seen = set()
    unique = []
    for ref in job_refs:
        if ref.display_name not in seen:
            seen.add(ref.display_name)
            unique.append(ref)

    return unique


def _resolve_job_ref(job_str: str) -> Optional[JobRef]:
    """
    Resolve a job string to a JobRef.

    Tries in order:
    1. Database job by slug
    2. Database job by numeric ID
    3. File path
    """
    # Try database first
    with JobsRepository() as repo:
        # Try as slug
        job = repo.get_job(slug=job_str)
        if job:
            return JobRef(slug=job_str, job=job)

        # Try as numeric ID
        if job_str.isdigit():
            job = repo.get_job(job_id=int(job_str))
            if job:
                return JobRef(job_id=int(job_str), slug=job.slug, job=job)

    # Try as file path
    path = Path(job_str)
    if path.exists() and path.suffix == '.json':
        return JobRef(file_path=path)

    # Try with .json extension
    if not path.suffix:
        json_path = path.with_suffix('.json')
        if json_path.exists():
            return JobRef(file_path=json_path)

    return None


def _resolve_job_pattern(pattern: str) -> List[JobRef]:
    """
    Resolve a pattern to multiple JobRefs.

    Supports:
    - Glob patterns for files: jobs/*.json
    - Wildcard patterns for slugs: arista-*
    """
    refs = []

    # Check if it's a file glob pattern
    if '*' in pattern or '?' in pattern:
        # First try as file glob
        for path_str in glob.glob(pattern):
            path = Path(path_str)
            if path.suffix == '.json':
                refs.append(JobRef(file_path=path))

        # Also try as slug pattern against database
        slug_pattern = pattern.replace('*', '%').replace('?', '_')
        if not slug_pattern.endswith('.json'):
            with JobsRepository() as repo:
                jobs = repo.get_jobs(search=pattern.replace('*', '').replace('?', ''))
                for job in jobs:
                    # Check if slug matches pattern
                    regex = pattern.replace('*', '.*').replace('?', '.')
                    if re.match(regex, job.slug):
                        refs.append(JobRef(slug=job.slug, job=job))
    else:
        # Single job reference
        ref = _resolve_job_ref(pattern)
        if ref:
            refs.append(ref)

    return refs


def _load_jobs(job_refs: List[JobRef]) -> List[JobRef]:
    """Load and validate all job references. Returns only valid jobs."""
    loaded = []

    for ref in job_refs:
        if ref.job:
            # Already loaded from database - fetch commands if available
            if ref.is_database and ref.job.id:
                with JobsRepository() as repo:
                    # Try to get full job with commands (if method exists)
                    if hasattr(repo, 'get_job_full'):
                        full_job = repo.get_job_full(job_id=ref.job.id)
                        if full_job:
                            ref.job = full_job
                    elif hasattr(repo, 'get_job_commands'):
                        ref.job.commands = repo.get_job_commands(ref.job.id)
            loaded.append(ref)
        elif ref.file_path:
            # Load from JSON file
            try:
                with open(ref.file_path) as f:
                    job_data = json.load(f)
                # Convert to Job object for consistent handling
                ref.job = _json_to_job(job_data, ref.file_path)
                loaded.append(ref)
            except (json.JSONDecodeError, KeyError) as e:
                print(f"Warning: Skipping invalid job file {ref.file_path}: {e}")
        else:
            print(f"Warning: Could not load job {ref.display_name}")

    return loaded


def _json_to_job(job_data: dict, file_path: Path) -> Job:
    """Convert legacy JSON job format to Job object."""
    commands = job_data.get('commands', {})
    device_filter = job_data.get('device_filter', {})
    validation = job_data.get('validation', {})
    execution = job_data.get('execution', {})
    storage = job_data.get('storage', {})

    # Handle command - could be string or list
    command_value = commands.get('command', '')
    if isinstance(command_value, list):
        command_value = ','.join(command_value)

    job = Job(
        id=None,  # Not in database
        name=file_path.stem,
        slug=file_path.stem.lower().replace(' ', '-'),
        capture_type=job_data.get('capture_type', 'custom'),
        vendor=device_filter.get('vendor'),
        paging_disable_command=commands.get('paging_disable'),
        command=command_value,
        output_directory=commands.get('output_directory'),
        use_textfsm=validation.get('use_tfsm', False),
        textfsm_template=validation.get('tfsm_filter'),
        validation_min_score=validation.get('min_score', 0),
        max_workers=execution.get('max_workers', 10),
        timeout_seconds=execution.get('timeout', 60),
        base_path=storage.get('base_path', '~/.vcollector/collections'),
        device_filter_status=device_filter.get('status', 'active'),
        legacy_job_id=job_data.get('job_id'),
        legacy_job_file=str(file_path),
    )

    return job


def _dry_run(job_refs: List[JobRef], args) -> int:
    """Show what would be executed without actually running."""
    print("DRY RUN - No changes will be made\n")

    with DCIMRepository() as dcim:
        for ref in job_refs:
            job = ref.job
            print(f"Job: {ref.display_name}")
            print(f"  Type: {job.capture_type}")

            # Handle multi-command jobs
            commands = job.get_all_commands() if hasattr(job, 'get_all_commands') else [job.command]
            if len(commands) > 1:
                print(f"  Commands ({len(commands)}):")
                for i, cmd in enumerate(commands, 1):
                    print(f"    {i}. {cmd}")
            else:
                print(f"  Command: {commands[0] if commands else job.command}")

            # Show devices that would be targeted
            devices = _get_devices_for_job(dcim, job, limit=args.limit)
            print(f"  Devices: {len(devices)} matched")

            if devices:
                for d in devices[:10]:
                    print(f"    - {d.name} ({d.primary_ip4}) [{d.platform_name or '?'}]")
                if len(devices) > 10:
                    print(f"    ... and {len(devices) - 10} more")

            # Show output path
            output_dir = job.output_directory or job.capture_type
            full_path = Path(job.base_path).expanduser() / output_dir
            print(f"  Output: {full_path}")
            print()

    return 0


def _get_devices_for_job(dcim: DCIMRepository, job: Job, limit: Optional[int] = None) -> List[Device]:
    """
    Get devices matching job's filter criteria.

    Uses DCIMRepository to query dcim.db based on job's device_filter_* fields.
    """
    # Map job vendor to platform slug pattern
    platform_slug = None
    if job.vendor and not job.device_filter_platform_id:
        # Try to find platform by vendor name
        platforms = dcim.get_platforms()
        for p in platforms:
            if p.slug and job.vendor.lower() in p.slug.lower():
                platform_slug = p.slug
                break

    devices = dcim.get_devices(
        site_id=job.device_filter_site_id,
        platform_id=job.device_filter_platform_id,
        platform_slug=platform_slug,
        role_id=job.device_filter_role_id,
        status=job.device_filter_status,
        limit=limit,
    )

    # Apply name pattern filter if specified
    if job.device_filter_name_pattern and devices:
        try:
            pattern = re.compile(job.device_filter_name_pattern, re.IGNORECASE)
            devices = [d for d in devices if pattern.search(d.name)]
        except re.error:
            pass  # Invalid regex - skip filtering

    return devices


def _run_single_job(job_ref: JobRef, creds, resolver, args):
    """Run a single job."""
    from vcollector.jobs.runner import JobRunner, JobResult

    job = job_ref.job

    # Show job configuration
    output_dir = job.output_directory or job.capture_type
    full_path = Path(job.base_path).expanduser() / output_dir

    print(f"Job: {job.capture_type} ({job.vendor or 'multi-vendor'})")
    print(f"Output: {full_path}")

    if job.use_textfsm:
        print(f"Validation: textfsm='{job.textfsm_template}', min_score={job.validation_min_score}")

    if getattr(args, 'force_save', False):
        print(f"Force save: enabled")
    print()

    # Create runner with job config and resolver for per-device credentials
    runner = JobRunner(
        credentials=creds,
        validate=True,
        debug=args.debug,
        no_save=args.no_save,
        force_save=getattr(args, 'force_save', False),
        limit=args.limit,
        quiet=args.quiet,
        credential_resolver=resolver,  # Enable per-device credentials
    )

    def progress(completed, total, result):
        status = "✓" if result.success else "✗"
        duration = f"{result.duration_ms:.0f}ms" if result.duration_ms else "?"
        print(f"  [{completed}/{total}] {status} {result.host} - {duration}")

    # Run with either database job or legacy file
    if job_ref.file_path:
        result = runner.run(job_ref.file_path, progress_callback=progress if not args.quiet else None)
    else:
        # For database jobs, pass the slug - runner will load and query devices itself
        result = runner.run_job(job_slug=job.slug, progress_callback=progress if not args.quiet else None)

    # Print summary
    print()
    print("=" * 60)
    _print_job_result(result)

    # Update job's last run status in database
    if job_ref.is_database and job.id:
        with JobsRepository() as repo:
            status = 'success' if result.success else 'failed'
            if result.skipped_count > 0 and result.success_count > 0:
                status = 'partial'
            repo.update_job_last_run(job.id, status)

    return result


def _run_batch_jobs(job_refs: List[JobRef], creds, resolver, args):
    """Run multiple jobs."""
    from vcollector.jobs.batch import BatchRunner, BatchResult

    # Separate database jobs from legacy files
    db_jobs = [(ref, ref.job) for ref in job_refs if ref.is_database]
    file_jobs = [ref.file_path for ref in job_refs if ref.file_path]

    print("Job configurations:")
    output_dirs = set()

    for ref in job_refs[:5]:
        job = ref.job
        output_dir = job.output_directory or job.capture_type
        full_path = Path(job.base_path).expanduser() / output_dir
        output_dirs.add(str(Path(job.base_path).expanduser()))
        print(f"  {job.capture_type}: {full_path}")

    if len(job_refs) > 5:
        print(f"  ... and {len(job_refs) - 5} more jobs")

    print(f"\nBase directories: {', '.join(sorted(output_dirs))}")
    if getattr(args, 'force_save', False):
        print(f"Force save: enabled")
    print()

    # Note: BatchRunner may need updating to pass credential_resolver
    runner = BatchRunner(
        credentials=creds,
        max_concurrent_jobs=args.max_concurrent_jobs,
        validate=True,
        debug=args.debug,
        no_save=args.no_save,
        force_save=getattr(args, 'force_save', False),
        limit=args.limit,
        quiet=args.quiet,
    )

    # Run file-based jobs (legacy support)
    # Note: BatchRunner may need updating to support Job objects directly
    result = runner.run(file_jobs) if file_jobs else BatchResult()

    # Print summary
    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Jobs: {result.successful_jobs}/{result.total_jobs} successful")
    print(f"Devices: {result.total_success} success, "
          f"{result.total_skipped} skipped (validation), "
          f"{result.total_failed} failed")
    print(f"Collections saved: {result.total_captures}")
    print(f"Total time: {result.duration_seconds:.1f}s")

    # Per-job breakdown
    print(f"\nPer-job results:")
    for r in sorted(result.job_results, key=lambda x: x.job_id):
        status = "✓" if r.success else "✗"
        if r.error:
            print(f"  {status} {r.job_id}: {r.error}")
        else:
            print(f"  {status} {r.job_id}: "
                  f"{r.success_count}/{r.total_devices} devices, "
                  f"{r.skipped_count} skipped, "
                  f"{r.duration_ms:.0f}ms")

    # Show validation failures
    all_failures = []
    for r in result.job_results:
        for failure in r.validation_failures:
            all_failures.append((r.job_id, *failure))

    if all_failures and not args.quiet:
        print(f"\nValidation failures ({len(all_failures)}):")
        for job_id, device, host, score, reason in all_failures[:10]:
            print(f"  - [{job_id}] {device} ({host}): {reason}")
        if len(all_failures) > 10:
            print(f"  ... and {len(all_failures) - 10} more")

    # Update database job statuses
    with JobsRepository() as repo:
        for ref, job in db_jobs:
            if job.id:
                # Find result for this job
                job_result = next((r for r in result.job_results
                                   if r.job_id == ref.display_name), None)
                if job_result:
                    status = 'success' if job_result.success else 'failed'
                    if job_result.skipped_count > 0 and job_result.success_count > 0:
                        status = 'partial'
                    repo.update_job_last_run(job.id, status)

    return result


def _print_job_result(result):
    """Print single job result."""
    print(f"Results: {result.success_count} success, "
          f"{result.skipped_count} skipped (validation), "
          f"{result.failed_count} failed")
    
    # Show saved files
    if result.saved_files:
        print(f"\nSaved {len(result.saved_files)} collections:")
        for item in result.saved_files[:10]:
            if len(item) == 4:
                name, path, size, score = item
                print(f"  - {name}: {Path(path).name} ({size:,} bytes) [score={score:.2f}]")
            else:
                name, path, size = item
                print(f"  - {name}: {Path(path).name} ({size:,} bytes)")
        if len(result.saved_files) > 10:
            print(f"  ... and {len(result.saved_files) - 10} more")

    # Show validation failures
    if result.validation_failures:
        print(f"\nValidation failures ({len(result.validation_failures)}):")
        for device, host, score, reason in result.validation_failures[:10]:
            print(f"  - {device} ({host}): {reason} (score={score:.2f})")
        if len(result.validation_failures) > 10:
            print(f"  ... and {len(result.validation_failures) - 10} more")

    # Show errors
    if result.error:
        print(f"\nError: {result.error}")