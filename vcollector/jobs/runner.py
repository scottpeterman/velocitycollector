"""
Job Runner - Single job execution with validation.

Path: vcollector/jobs/runner.py

Executes collection jobs against network devices, validates output
using TextFSM templates, and only saves valid (score > 0) output.

Supports loading jobs from:
- JSON files (legacy/backward compatible)
- Database (jobs table in collector.db)

Supports per-device credentials via credential_resolver parameter.
Enhanced with comprehensive error trapping and logging.
"""

import json
import logging
import re
import sqlite3
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable, Union

from vcollector.core.config import get_config
from vcollector.vault.models import SSHCredentials
from vcollector.vault.resolver import CredentialResolver
from vcollector.ssh.executor import (
    SSHExecutorPool,
    ExecutorOptions,
    ExecutionResult,
    SSHErrorCategory,
    BatchExecutionSummary,
)


# Module logger
logger = logging.getLogger(__name__)


@dataclass
class DeviceError:
    """Detailed error information for a device failure."""
    device_name: str
    host: str
    error: str
    category: SSHErrorCategory = SSHErrorCategory.UNKNOWN
    traceback: Optional[str] = None
    duration_ms: float = 0

    def __repr__(self) -> str:
        return f"DeviceError({self.device_name}: {self.category.value} - {self.error})"


@dataclass
class JobResult:
    """Result of job execution."""
    job_file: str
    job_id: str
    success_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0  # Failed validation
    total_devices: int = 0
    duration_ms: float = 0
    error: Optional[str] = None
    error_traceback: Optional[str] = None  # NEW: Full traceback for job-level errors
    saved_files: List[tuple] = field(default_factory=list)
    validation_failures: List[tuple] = field(default_factory=list)
    device_errors: List[DeviceError] = field(default_factory=list)  # NEW: Detailed device failures
    history_id: Optional[int] = None  # job_history record ID
    execution_summary: Optional[BatchExecutionSummary] = None  # NEW: Executor summary

    @property
    def success(self) -> bool:
        return self.error is None and self.failed_count == 0

    def get_error_summary(self) -> Dict[str, int]:
        """Get count of errors by category."""
        summary = {}
        for err in self.device_errors:
            cat = err.category.value
            summary[cat] = summary.get(cat, 0) + 1
        return summary

    def format_error_report(self) -> str:
        """Format a human-readable error report."""
        lines = []

        if self.error:
            lines.append(f"Job Error: {self.error}")
            if self.error_traceback:
                lines.append(f"Traceback:\n{self.error_traceback}")

        if self.device_errors:
            lines.append(f"\nDevice Errors ({len(self.device_errors)}):")

            # Group by category
            by_category = {}
            for err in self.device_errors:
                cat = err.category.value
                if cat not in by_category:
                    by_category[cat] = []
                by_category[cat].append(err)

            for cat, errors in sorted(by_category.items()):
                lines.append(f"\n  [{cat}] ({len(errors)} devices):")
                for err in errors[:10]:  # Limit to first 10 per category
                    lines.append(f"    - {err.device_name} ({err.host}): {err.error}")
                if len(errors) > 10:
                    lines.append(f"    ... and {len(errors) - 10} more")

        if self.validation_failures:
            lines.append(f"\nValidation Failures ({len(self.validation_failures)}):")
            for name, host, score, reason in self.validation_failures[:10]:
                lines.append(f"  - {name} ({host}): score={score:.2f}, {reason}")
            if len(self.validation_failures) > 10:
                lines.append(f"  ... and {len(self.validation_failures) - 10} more")

        return "\n".join(lines) if lines else "No errors"


@dataclass
class DeviceResult:
    """Result for a single device."""
    device_name: str
    host: str
    success: bool
    output: str = ""
    error: Optional[str] = None
    validated: bool = False
    validation_score: float = 0.0
    validation_template: Optional[str] = None
    filepath: Optional[Path] = None


class JobRunner:
    """
    Execute a single collection job with validation.

    Flow:
    1. Load job definition from JSON or database
    2. Query matching devices from dcim.db
    3. Execute SSH commands concurrently
    4. Validate output using TextFSM (if enabled)
    5. Save only validated output (score > 0)
    6. Record execution in job_history

    Usage:
        runner = JobRunner(
            credentials=creds,
            validate=True,
        )

        # Run from JSON file
        result = runner.run(Path("jobs/cisco-ios_configs.json"))

        # Run from database job
        result = runner.run_job(job_id=42)

        # Check for errors
        if not result.success:
            print(result.format_error_report())
    """

    def __init__(
        self,
        credentials: SSHCredentials,
        validate: bool = True,
        tfsm_db_path: Optional[str] = None,
        debug: bool = False,
        no_save: bool = False,
        force_save: bool = False,
        limit: Optional[int] = None,
        quiet: bool = False,
        record_history: bool = True,
        capture_traceback: bool = True,  # NEW: Capture full tracebacks
        credential_resolver: Optional[CredentialResolver] = None,  # For per-device credentials
    ):
        """
        Initialize job runner.

        Args:
            credentials: SSH credentials from vault (default/fallback).
            validate: Enable TextFSM validation (default: True).
            tfsm_db_path: Path to TextFSM templates database.
            debug: Enable debug output.
            no_save: Don't save output files.
            force_save: Save output even if validation fails.
            limit: Limit number of devices.
            quiet: Minimal output.
            record_history: Record execution in job_history table.
            capture_traceback: Capture full tracebacks for errors.
            credential_resolver: Unlocked resolver for per-device credential lookup.
        """
        self.credentials = credentials
        self.validate = validate
        self.tfsm_db_path = tfsm_db_path
        self.debug = debug
        self.no_save = no_save
        self.force_save = force_save
        self.limit = limit
        self.quiet = quiet
        self.record_history = record_history
        self.capture_traceback = capture_traceback
        self.credential_resolver = credential_resolver

        self.config = get_config()
        self._validation_engine = None
        self._jobs_repo = None
        self._dcim_repo = None
        self._credential_cache: Dict[int, SSHCredentials] = {}  # Cache for per-device creds

        # Configure logging based on debug flag
        if debug:
            logger.setLevel(logging.DEBUG)
        elif quiet:
            logger.setLevel(logging.WARNING)
        else:
            logger.setLevel(logging.INFO)

    @property
    def validation_engine(self):
        """Lazy-load validation engine."""
        if self._validation_engine is None and self.validate:
            try:
                from vcollector.validation import ValidationEngine
                self._validation_engine = ValidationEngine(
                    db_path=self.tfsm_db_path,
                    verbose=self.debug,
                )
                logger.debug("Validation engine loaded successfully")
            except ImportError as e:
                logger.warning(f"textfsm not installed, validation disabled: {e}")
                self.validate = False
            except FileNotFoundError as e:
                logger.warning(f"Validation database not found: {e}")
                self.validate = False
            except Exception as e:
                logger.error(f"Failed to load validation engine: {e}", exc_info=self.debug)
                self.validate = False
        return self._validation_engine

    @property
    def jobs_repo(self):
        """Lazy-load jobs repository."""
        if self._jobs_repo is None:
            try:
                from vcollector.dcim.jobs_repo import JobsRepository
                self._jobs_repo = JobsRepository()
                logger.debug("Jobs repository loaded")
            except Exception as e:
                logger.error(f"Failed to load jobs repository: {e}", exc_info=self.debug)
                raise
        return self._jobs_repo

    @property
    def dcim_repo(self):
        """Lazy-load DCIM repository."""
        if self._dcim_repo is None:
            try:
                from vcollector.dcim.dcim_repo import DCIMRepository
                self._dcim_repo = DCIMRepository()
                logger.debug("DCIM repository loaded")
            except Exception as e:
                logger.error(f"Failed to load DCIM repository: {e}", exc_info=self.debug)
                raise
        return self._dcim_repo

    def _get_device_credentials(self, device: Dict[str, Any]) -> Optional[SSHCredentials]:
        """
        Get credentials for a specific device.

        Uses per-device credential_id if set, otherwise returns None
        to fall back to job/default credentials.

        Args:
            device: Device dict with 'credential_id' key

        Returns:
            SSHCredentials if device has specific credential, None otherwise
        """
        if not self.credential_resolver:
            return None

        credential_id = device.get('credential_id')
        if not credential_id:
            return None

        # Check cache first
        if credential_id in self._credential_cache:
            return self._credential_cache[credential_id]

        # Build cache from all credentials if not yet done
        if not self._credential_cache:
            try:
                all_creds = self.credential_resolver.list_credentials()
                for cred in all_creds:
                    ssh_creds = self.credential_resolver.get_ssh_credentials(credential_name=cred.name)
                    if ssh_creds:
                        self._credential_cache[cred.id] = ssh_creds
                logger.debug(f"Loaded {len(self._credential_cache)} credentials into cache")
            except Exception as e:
                logger.warning(f"Failed to load credentials: {e}")
                return None

        # Look up from cache
        if credential_id in self._credential_cache:
            device_name = device.get('name', device.get('primary_ip4', '?'))
            logger.debug(f"{device_name}: Using credential_id={credential_id}")
            return self._credential_cache[credential_id]

        return None

    def run(
        self,
        job_file: Path,
        progress_callback: Optional[Callable] = None,
    ) -> JobResult:
        """
        Execute job from JSON file (legacy interface).

        Args:
            job_file: Path to job JSON file.
            progress_callback: Optional callback(completed, total, result).

        Returns:
            JobResult with execution details.
        """
        start_time = datetime.now()
        job_id = job_file.stem

        logger.info(f"Running job from file: {job_file}")

        try:
            # Load job definition from JSON
            job_dict = self._load_job_file(job_file)
            job_id = str(job_dict.get('job_id', job_file.stem))

            return self._execute_job(
                job_dict=job_dict,
                job_id=job_id,
                job_source=str(job_file),
                start_time=start_time,
                progress_callback=progress_callback,
            )

        except json.JSONDecodeError as e:
            error_msg = f"Invalid JSON in {job_file}: {e}"
            logger.error(error_msg)
            return JobResult(
                job_file=str(job_file),
                job_id=job_id,
                duration_ms=self._elapsed_ms(start_time),
                error=error_msg,
                error_traceback=traceback.format_exc() if self.capture_traceback else None,
            )
        except FileNotFoundError as e:
            error_msg = f"Job file not found: {job_file}"
            logger.error(error_msg)
            return JobResult(
                job_file=str(job_file),
                job_id=job_id,
                duration_ms=self._elapsed_ms(start_time),
                error=error_msg,
            )
        except Exception as e:
            error_msg = f"Failed to run job {job_file}: {e}"
            logger.error(error_msg, exc_info=self.debug)
            return JobResult(
                job_file=str(job_file),
                job_id=job_id,
                duration_ms=self._elapsed_ms(start_time),
                error=error_msg,
                error_traceback=traceback.format_exc() if self.capture_traceback else None,
            )

    def run_job(
        self,
        job_id: Optional[int] = None,
        job_slug: Optional[str] = None,
        progress_callback: Optional[Callable] = None,
    ) -> JobResult:
        """
        Execute job from database.

        Args:
            job_id: Job ID in database.
            job_slug: Job slug (alternative to ID).
            progress_callback: Optional callback(completed, total, result).

        Returns:
            JobResult with execution details.
        """
        start_time = datetime.now()

        logger.info(f"Running job from database: id={job_id}, slug={job_slug}")

        try:
            # Load job from database
            job = self.jobs_repo.get_job(job_id=job_id, slug=job_slug)

            if not job:
                error_msg = f"Job not found: {job_id or job_slug}"
                logger.error(error_msg)
                return JobResult(
                    job_file="database",
                    job_id=str(job_id or job_slug),
                    duration_ms=self._elapsed_ms(start_time),
                    error=error_msg,
                )

            if not job.is_enabled:
                error_msg = f"Job is disabled: {job.name}"
                logger.warning(error_msg)
                return JobResult(
                    job_file="database",
                    job_id=job.slug,
                    duration_ms=self._elapsed_ms(start_time),
                    error=error_msg,
                )

            # Convert Job dataclass to dict for unified processing
            job_dict = self._job_to_dict(job)

            return self._execute_job(
                job_dict=job_dict,
                job_id=job.slug,
                job_source=f"database:{job.id}",
                start_time=start_time,
                progress_callback=progress_callback,
                db_job=job,
            )

        except Exception as e:
            error_msg = f"Failed to run database job: {e}"
            logger.error(error_msg, exc_info=self.debug)
            return JobResult(
                job_file="database",
                job_id=str(job_id or job_slug),
                duration_ms=self._elapsed_ms(start_time),
                error=error_msg,
                error_traceback=traceback.format_exc() if self.capture_traceback else None,
            )

    def _job_to_dict(self, job) -> Dict[str, Any]:
        """Convert Job dataclass to dict matching JSON structure."""
        return {
            'job_id': job.slug,
            'capture_type': job.capture_type,
            'vendor': job.vendor,
            'commands': {
                'paging_disable': job.paging_disable_command,
                'command': job.command,
                'output_directory': job.output_directory,
            },
            'device_filter': {
                'source': job.device_filter_source,
                'vendor': job.vendor,
                'platform_id': job.device_filter_platform_id,
                'site_id': job.device_filter_site_id,
                'role_id': job.device_filter_role_id,
                'name_pattern': job.device_filter_name_pattern,
                'status': job.device_filter_status,
            },
            'validation': {
                'use_tfsm': job.use_textfsm,
                'tfsm_filter': job.textfsm_template,
                'min_score': job.validation_min_score,
                'store_failures': job.store_failures,
            },
            'execution': {
                'max_workers': job.max_workers,
                'timeout': job.timeout_seconds,
                'inter_command_time': job.inter_command_delay,
            },
            'storage': {
                'base_path': job.base_path,
                'filename_pattern': job.filename_pattern,
            },
        }

    def _execute_job(
        self,
        job_dict: Dict[str, Any],
        job_id: str,
        job_source: str,
        start_time: datetime,
        progress_callback: Optional[Callable] = None,
        db_job=None,
    ) -> JobResult:
        """
        Core job execution logic.

        Args:
            job_dict: Job configuration as dict.
            job_id: Job identifier for logging.
            job_source: Source description (file path or "database:id").
            start_time: Execution start time.
            progress_callback: Optional progress callback.
            db_job: Original Job object if from database (for history).

        Returns:
            JobResult with execution details.
        """
        history_id = None

        try:
            logger.info(f"[{job_id}] Loading job: {job_dict.get('capture_type', 'unknown')}")

            # Record job start in history
            if self.record_history:
                try:
                    history_id = self.jobs_repo.create_job_history(
                        job_id=job_id,
                        job_file=job_source,
                    )
                    logger.debug(f"[{job_id}] Created history record: {history_id}")
                except Exception as hist_err:
                    logger.warning(f"[{job_id}] Failed to create job history: {hist_err}")
                    # Continue anyway - history is not critical

            # Query matching devices
            logger.debug(f"[{job_id}] Querying devices...")
            devices = self._get_devices(job_dict)

            if self.limit:
                original_count = len(devices)
                devices = devices[:self.limit]
                logger.info(f"[{job_id}] Limited devices from {original_count} to {len(devices)}")

            if not devices:
                error_msg = "No devices match filter"
                logger.warning(f"[{job_id}] {error_msg}")
                result = JobResult(
                    job_file=job_source,
                    job_id=job_id,
                    total_devices=0,
                    duration_ms=self._elapsed_ms(start_time),
                    error=error_msg,
                    history_id=history_id,
                )
                self._complete_history(history_id, result)
                return result

            logger.info(f"[{job_id}] Found {len(devices)} devices")

            # Build command string from job
            commands_config = job_dict['commands']
            command_parts = []

            paging_disable = commands_config.get('paging_disable')
            if paging_disable:
                command_parts.append(paging_disable)

            main_command = commands_config.get('command')
            if main_command:
                command_parts.append(main_command)

            command_string = ','.join(command_parts)
            logger.debug(f"[{job_id}] Command string: {command_string}")

            # Build execution targets with per-device credentials
            targets = []
            for d in devices:
                extra_data = dict(d)  # Copy device data

                # Add per-device credentials if available
                device_creds = self._get_device_credentials(d)
                if device_creds:
                    extra_data['credentials'] = device_creds

                targets.append((d['primary_ip4'], command_string, extra_data))

            # Execute SSH commands
            exec_config = job_dict.get('execution', {})
            options = ExecutorOptions(
                timeout=exec_config.get('timeout', 60),
                inter_command_time=exec_config.get('inter_command_time', 1),
                debug=self.debug,
                capture_traceback=self.capture_traceback,
            )

            pool = SSHExecutorPool(
                credentials=self.credentials,
                options=options,
                max_workers=exec_config.get('max_workers', 12),
            )

            logger.info(f"[{job_id}] Executing SSH commands on {len(targets)} devices...")
            ssh_results, exec_summary = pool.execute_batch(targets, progress_callback)

            # Process results with validation
            result = self._process_results(
                job=job_dict,
                job_source=job_source,
                devices=devices,
                ssh_results=ssh_results,
                start_time=start_time,
                main_command=main_command,
                history_id=history_id,
                execution_summary=exec_summary,
            )

            # Update job last_run if from database
            if db_job:
                try:
                    status = 'success' if result.success else ('partial' if result.success_count > 0 else 'failed')
                    self.jobs_repo.update_job_last_run(db_job.id, status)
                except Exception as update_err:
                    logger.warning(f"[{job_id}] Failed to update job last_run: {update_err}")

            # Complete history record
            self._complete_history(history_id, result)

            return result

        except Exception as e:
            error_msg = f"Job execution failed: {e}"
            logger.error(f"[{job_id}] {error_msg}", exc_info=True)

            result = JobResult(
                job_file=job_source,
                job_id=job_id,
                duration_ms=self._elapsed_ms(start_time),
                error=error_msg,
                error_traceback=traceback.format_exc() if self.capture_traceback else None,
                history_id=history_id,
            )
            self._complete_history(history_id, result)
            return result

    def _complete_history(self, history_id: Optional[int], result: JobResult):
        """Complete the job history record."""
        if not self.record_history or not history_id:
            return

        try:
            # Calculate total failures (SSH errors + validation skips)
            total_failed = result.failed_count + result.skipped_count

            # Determine status based on success/failure mix
            if result.error:
                # Job-level error (no devices matched, etc)
                status = 'failed'
            elif result.success_count == 0:
                # No successes at all
                status = 'failed'
            elif total_failed == 0:
                # All devices succeeded
                status = 'success'
            else:
                # Some succeeded, some failed
                status = 'partial'

            self.jobs_repo.complete_job_history(
                history_id=history_id,
                total_devices=result.total_devices,
                success_count=result.success_count,
                failed_count=result.failed_count + result.skipped_count,
                status=status,
                error_message=result.error,
            )
            logger.debug(f"Completed history record {history_id}: {status}")
        except Exception as e:
            # Log the full error, not just in debug mode
            logger.warning(f"Failed to update job history {history_id}: {e}")
            if self.debug:
                logger.debug(f"History update traceback:\n{traceback.format_exc()}")

    def _load_job_file(self, job_file: Path) -> Dict[str, Any]:
        """Load and validate job definition from JSON file."""
        logger.debug(f"Loading job file: {job_file}")

        with open(job_file) as f:
            job = json.load(f)

        # Validate required fields
        if 'commands' not in job or 'command' not in job['commands']:
            raise ValueError("Job missing required 'commands.command' field")

        logger.debug(f"Job loaded: capture_type={job.get('capture_type')}")
        return job

    def _get_devices(self, job: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Query devices from dcim.db based on job filter.

        Falls back to legacy assets.db if dcim.db query fails.
        """
        device_filter = job.get('device_filter', {})

        # Try DCIM database first (new system)
        try:
            devices = self._get_devices_from_dcim(device_filter)
            logger.debug(f"DCIM query returned {len(devices)} devices")
            return devices
        except Exception as e:
            logger.info(f"DCIM query failed, trying legacy assets.db: {e}")
            if self.debug:
                logger.debug(f"DCIM error traceback:\n{traceback.format_exc()}")

        # Fall back to legacy assets.db
        try:
            devices = self._get_devices_from_assets(job)
            logger.debug(f"Legacy assets query returned {len(devices)} devices")
            return devices
        except Exception as e:
            logger.error(f"Both device queries failed: {e}", exc_info=self.debug)
            raise

    def _get_devices_from_dcim(self, device_filter: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Query devices from dcim.db (new system)."""
        # Build filter kwargs for get_devices()
        kwargs = {}

        # Site filter (by ID)
        site_id = device_filter.get('site_id')
        if site_id:
            kwargs['site_id'] = site_id

        # Platform filter (by ID)
        platform_id = device_filter.get('platform_id')
        if platform_id:
            kwargs['platform_id'] = platform_id

        # Role filter (by ID)
        role_id = device_filter.get('role_id')
        if role_id:
            kwargs['role_id'] = role_id

        # Status filter
        status = device_filter.get('status')
        if status and status != 'any':
            kwargs['status'] = status

        # Name pattern (search)
        name_pattern = device_filter.get('name_pattern')
        if name_pattern:
            # Convert glob pattern to search string
            search = name_pattern.replace('*', '').replace('^', '').replace('$', '')
            if search:
                kwargs['search'] = search

        logger.debug(f"DCIM query filters: {kwargs}")

        # Query devices
        devices = self.dcim_repo.get_devices(**kwargs)

        # Filter by vendor if specified (match platform's manufacturer)
        vendor = device_filter.get('vendor')
        if vendor:
            vendor_lower = vendor.lower()
            original_count = len(devices)
            devices = [
                d for d in devices
                if d.manufacturer_name and vendor_lower in d.manufacturer_name.lower()
            ]
            logger.debug(f"Vendor filter '{vendor}' reduced devices from {original_count} to {len(devices)}")

        # Convert Device dataclass to dict for compatibility
        result = []
        skipped_no_ip = 0
        for d in devices:
            if not d.primary_ip4:
                skipped_no_ip += 1
                continue  # Skip devices without IP

            result.append({
                'id': d.id,
                'name': d.name,
                'normalized_name': d.name,  # Use name directly
                'primary_ip4': d.primary_ip4,
                'management_ip': d.primary_ip4,  # Alias for compatibility
                'model': None,  # Not in our schema
                'vendor_name': d.manufacturer_name,
                'site_code': d.site_slug,
                'site_name': d.site_name,
                'role_name': d.role_name,
                'platform_name': d.platform_name,
                'netmiko_device_type': d.netmiko_device_type,
            })

        if skipped_no_ip:
            logger.debug(f"Skipped {skipped_no_ip} devices without primary IP")

        return result

    def _get_devices_from_assets(self, job: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Query devices from legacy assets.db."""
        assets_db = self.config.assets_db

        if not assets_db.exists():
            raise FileNotFoundError(
                f"Assets database not found: {assets_db}\n"
                "Configure assets_db in ~/.vcollector/config.yaml or use DCIM database"
            )

        logger.debug(f"Querying legacy assets.db: {assets_db}")

        conn = sqlite3.connect(assets_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        try:
            # Build query from device_filter
            device_filter = job.get('device_filter', {})
            conditions = []
            params = []

            # Vendor filter
            vendor = device_filter.get('vendor')
            if vendor:
                conditions.append("LOWER(vendor_name) LIKE ?")
                params.append(f"%{vendor.lower()}%")

            # Platform filter
            platform = device_filter.get('platform')
            if platform:
                conditions.append("LOWER(platform) LIKE ?")
                params.append(f"%{platform.lower()}%")

            # Site filter
            site = device_filter.get('site')
            if site:
                conditions.append("site_code = ?")
                params.append(site)

            # Role filter
            role = device_filter.get('role')
            if role:
                conditions.append("LOWER(role_name) LIKE ?")
                params.append(f"%{role.lower()}%")

            # Name pattern
            name_pattern = device_filter.get('name_pattern')
            if name_pattern:
                conditions.append("name LIKE ?")
                params.append(name_pattern.replace('*', '%'))

            # Must have management IP
            conditions.append("management_ip IS NOT NULL")

            where_clause = " AND ".join(conditions) if conditions else "1=1"

            # Try v_device_status view first, fall back to devices table
            try:
                query = f"""
                    SELECT id, name, normalized_name, management_ip,
                           model, vendor_name, site_code, role_name
                    FROM v_device_status
                    WHERE {where_clause}
                    ORDER BY name
                """
                cursor.execute(query, params)
                logger.debug("Using v_device_status view")
            except sqlite3.OperationalError as e:
                # View doesn't exist, try devices table
                logger.debug(f"v_device_status view not found ({e}), using devices table")
                query = f"""
                    SELECT id, name, name as normalized_name, management_ip,
                           model, vendor as vendor_name, site as site_code, 
                           role as role_name
                    FROM devices
                    WHERE {where_clause}
                    ORDER BY name
                """
                cursor.execute(query, params)

            devices = []
            for row in cursor.fetchall():
                d = dict(row)
                d['primary_ip4'] = d.get('management_ip')  # Alias
                devices.append(d)

            return devices

        finally:
            conn.close()

    def _clean_output(self, raw_output: str, command: Optional[str] = None) -> str:
        """
        Clean raw CLI output for TextFSM parsing.

        Strips everything up to and including the command echo line,
        and removes trailing prompts.

        Args:
            raw_output: Raw output from SSH session
            command: The command that was run (e.g., "show ip arp")

        Returns:
            Cleaned output suitable for TextFSM parsing
        """
        lines = raw_output.split('\n')

        # Find the line containing the command echo
        if command:
            # Get the actual show/display command
            cmd_parts = [c.strip() for c in command.split(',') if c.strip()]
            main_cmd = None
            for part in cmd_parts:
                if part.lower().startswith(('show ', 'display ', 'get ')):
                    main_cmd = part
                    break

            if main_cmd:
                # Find line containing this command (case-insensitive)
                cmd_pattern = re.escape(main_cmd)
                start_idx = 0
                for i, line in enumerate(lines):
                    if re.search(cmd_pattern, line, re.IGNORECASE):
                        start_idx = i + 1  # Start after the command line
                        break

                # Get lines after command, before trailing prompts
                cleaned_lines = []
                prompt_pattern = r'^[\w\-\.]+[\#\>\$\)]\s*$'

                for line in lines[start_idx:]:
                    # Skip trailing prompts
                    if re.match(prompt_pattern, line.strip()):
                        continue
                    cleaned_lines.append(line)

                # Remove trailing empty lines
                while cleaned_lines and not cleaned_lines[-1].strip():
                    cleaned_lines.pop()

                return '\n'.join(cleaned_lines)

        # Fallback: return as-is if we can't find the command
        return raw_output

    def _process_results(
        self,
        job: Dict[str, Any],
        job_source: str,
        devices: List[Dict],
        ssh_results: List[ExecutionResult],
        start_time: datetime,
        main_command: Optional[str] = None,
        history_id: Optional[int] = None,
        execution_summary: Optional[BatchExecutionSummary] = None,
    ) -> JobResult:
        """Process SSH results, validate, and save."""
        job_id = job.get('job_id', Path(job_source).stem if '/' in job_source else job_source)
        capture_type = job.get('capture_type', 'unknown')
        output_dir = job['commands'].get('output_directory', capture_type)

        # Get validation filter from job
        validation_config = job.get('validation', {})
        validation_filter = validation_config.get('tfsm_filter')
        skip_validation = validation_config.get('skip', False)
        use_tfsm = validation_config.get('use_tfsm', True)

        success_count = 0
        failed_count = 0
        skipped_count = 0
        saved_files = []
        validation_failures = []
        device_errors = []  # NEW: Track detailed device errors

        for i, ssh_result in enumerate(ssh_results):
            device = devices[i]
            device_name = device.get('normalized_name') or device.get('name')

            if not ssh_result.success:
                failed_count += 1
                # NEW: Capture detailed error information
                device_errors.append(DeviceError(
                    device_name=device_name,
                    host=ssh_result.host,
                    error=ssh_result.error or "Unknown error",
                    category=ssh_result.error_category,
                    traceback=ssh_result.error_traceback,
                    duration_ms=ssh_result.duration_ms,
                ))
                logger.debug(f"[{job_id}] {device_name}: SSH failed - {ssh_result.error_category.value}: {ssh_result.error}")
                continue

            output = ssh_result.output

            # Clean output for validation (strip command echo and prompts)
            cleaned_output = self._clean_output(output, main_command)

            logger.debug(f"[{job_id}] {device_name}: raw={len(output)} chars, cleaned={len(cleaned_output)} chars")

            # Track validation result for scoring
            validation_result = None
            validation_failed = False

            # Validate output (if enabled and not skipped for this job)
            if self.validate and use_tfsm and not skip_validation and self.validation_engine:
                # Build filter string from job or device info
                filter_str = validation_filter
                if not filter_str:
                    # Auto-build filter from vendor + capture type
                    vendor = device.get('vendor_name', '').lower().replace(' ', '_')
                    # Simplify common vendor names for template matching
                    vendor_map = {
                        'cisco_systems': 'cisco_ios',
                        'cisco_systems,_inc.': 'cisco_ios',
                        'arista_networks': 'arista_eos',
                        'juniper_networks': 'juniper_junos',
                    }
                    vendor = vendor_map.get(vendor, vendor)
                    filter_str = f"{vendor}_{capture_type}"

                logger.debug(f"[{job_id}] {device_name}: validation filter='{filter_str}'")

                try:
                    validation_result = self.validation_engine.validate(cleaned_output, filter_str)

                    logger.debug(f"[{job_id}] {device_name}: template='{validation_result.template}', "
                              f"score={validation_result.score:.2f}, "
                              f"records={validation_result.record_count}")

                    if not validation_result.is_valid:
                        validation_failed = True
                        validation_failures.append((
                            device_name,
                            ssh_result.host,
                            validation_result.score,
                            validation_result.error or f"Score below threshold (filter={filter_str})",
                        ))

                        if self.force_save:
                            logger.debug(f"[{job_id}] {device_name}: validation failed but force_save enabled "
                                      f"(score={validation_result.score:.2f})")
                        else:
                            skipped_count += 1
                            logger.debug(f"[{job_id}] {device_name}: skipped - validation failed "
                                      f"(score={validation_result.score:.2f})")
                            continue

                    if not validation_failed:
                        logger.debug(f"[{job_id}] {device_name}: validated OK "
                                  f"(template={validation_result.template}, "
                                  f"score={validation_result.score:.2f})")

                except Exception as val_err:
                    logger.warning(f"[{job_id}] {device_name}: validation error: {val_err}")
                    if self.debug:
                        logger.debug(f"Validation traceback:\n{traceback.format_exc()}")
                    # Continue without validation rather than failing

            # Save output (use cleaned output for consistency with validation)
            if not self.no_save:
                try:
                    filepath = self._save_output(device, cleaned_output, job)
                    # Get the score (0 if validation was skipped or failed)
                    score = 0.0
                    if validation_result and not validation_failed:
                        score = validation_result.score
                    elif validation_result and validation_failed:
                        score = validation_result.score  # Still record the score even on failure
                    saved_files.append((
                        device_name,
                        str(filepath),
                        len(cleaned_output),
                        score,
                    ))
                except Exception as save_err:
                    logger.error(f"[{job_id}] {device_name}: failed to save output: {save_err}")
                    if self.debug:
                        logger.debug(f"Save error traceback:\n{traceback.format_exc()}")

            success_count += 1

        duration_ms = self._elapsed_ms(start_time)

        # Log summary
        status = "✓" if failed_count == 0 and skipped_count == 0 else "✗"
        logger.info(f"[{job_id}] {status} Complete: "
              f"{success_count}/{len(devices)} success, "
              f"{skipped_count} skipped (validation), "
              f"{failed_count} failed "
              f"in {duration_ms:.0f}ms")

        # Log error breakdown if there were failures
        if device_errors:
            error_summary = {}
            for err in device_errors:
                cat = err.category.value
                error_summary[cat] = error_summary.get(cat, 0) + 1
            logger.info(f"[{job_id}] Error breakdown: {error_summary}")

        return JobResult(
            job_file=job_source,
            job_id=str(job_id),
            success_count=success_count,
            failed_count=failed_count,
            skipped_count=skipped_count,
            total_devices=len(devices),
            duration_ms=duration_ms,
            saved_files=saved_files,
            validation_failures=validation_failures,
            device_errors=device_errors,
            history_id=history_id,
            execution_summary=execution_summary,
        )

    def _save_output(
        self,
        device: Dict[str, Any],
        output: str,
        job: Dict[str, Any],
    ) -> Path:
        """Save capture output to file."""
        # Get base path from job or config
        storage_config = job.get('storage', {})
        base_path = storage_config.get('base_path')

        if base_path:
            # Expand ~ and make absolute
            collections_base = Path(base_path).expanduser()
        else:
            collections_base = self.config.collections_dir

        # Add output subdirectory (e.g., "arp", "configs")
        output_dir = job['commands'].get('output_directory', job.get('capture_type', 'collections'))
        collections_dir = collections_base / output_dir
        collections_dir.mkdir(parents=True, exist_ok=True)

        # Build filename from pattern
        pattern = storage_config.get('filename_pattern', '{device_name}.txt')
        filename = pattern.format(
            device_name=device.get('normalized_name') or device.get('name'),
            device_id=device.get('id', 0),
            timestamp=datetime.now().strftime('%Y%m%d_%H%M%S'),
            capture_type=job.get('capture_type', 'unknown'),
        )

        filepath = collections_dir / filename
        filepath.write_text(output)

        logger.debug(f"Saved: {filepath}")

        return filepath

    def _elapsed_ms(self, start_time: datetime) -> float:
        """Calculate elapsed milliseconds."""
        return (datetime.now() - start_time).total_seconds() * 1000


def configure_logging(
    level: int = logging.INFO,
    format_string: Optional[str] = None,
    handler: Optional[logging.Handler] = None,
):
    """
    Configure logging for the job runner module.

    Call this at application startup to enable logging.

    Args:
        level: Logging level (default: INFO).
        format_string: Optional custom format string.
        handler: Optional custom handler (default: StreamHandler).

    Example:
        from vcollector.ssh.runner import configure_logging
        configure_logging(level=logging.DEBUG)
    """
    if format_string is None:
        format_string = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    if handler is None:
        handler = logging.StreamHandler()

    handler.setFormatter(logging.Formatter(format_string))
    logger.addHandler(handler)
    logger.setLevel(level)