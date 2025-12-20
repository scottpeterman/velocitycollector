"""
Batch Runner - Concurrent job execution.

Executes multiple collection jobs in parallel, each with its own
device-level concurrency.
"""

import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from vcollector.vault.models import SSHCredentials
from vcollector.jobs.runner import JobRunner, JobResult


# Thread-safe print
_print_lock = threading.Lock()


def safe_print(*args, **kwargs):
    """Thread-safe print."""
    with _print_lock:
        print(*args, **kwargs)


@dataclass
class BatchResult:
    """Result of batch job execution."""
    total_jobs: int
    successful_jobs: int
    failed_jobs: int
    total_devices: int
    total_success: int
    total_failed: int
    total_skipped: int
    total_captures: int
    duration_seconds: float
    job_results: List[JobResult] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.failed_jobs == 0


class BatchRunner:
    """
    Execute multiple jobs concurrently.
    
    Each job runs in its own thread, with device-level concurrency
    handled by the SSHExecutorPool within each job.
    
    Usage:
        runner = BatchRunner(
            credentials=creds,
            max_concurrent_jobs=4,
        )
        
        result = runner.run([
            Path("jobs/cisco-ios_configs.json"),
            Path("jobs/arista_configs.json"),
        ])
        
        print(f"Jobs: {result.successful_jobs}/{result.total_jobs}")
        print(f"Devices: {result.total_success} success")
    """

    def __init__(
        self,
        credentials: SSHCredentials,
        max_concurrent_jobs: int = 4,
        validate: bool = True,
        tfsm_db_path: Optional[str] = None,
        debug: bool = False,
        no_save: bool = False,
        force_save: bool = False,
        limit: Optional[int] = None,
        quiet: bool = False,
    ):
        """
        Initialize batch runner.

        Args:
            credentials: SSH credentials from vault.
            max_concurrent_jobs: Max jobs to run in parallel.
            validate: Enable TextFSM validation.
            tfsm_db_path: Path to TextFSM templates database.
            debug: Enable debug output.
            no_save: Don't save output files.
            force_save: Save output even if validation fails.
            limit: Limit devices per job.
            quiet: Minimal output.
        """
        self.credentials = credentials
        self.max_concurrent_jobs = max_concurrent_jobs
        self.validate = validate
        self.tfsm_db_path = tfsm_db_path
        self.debug = debug
        self.no_save = no_save
        self.force_save = force_save
        self.limit = limit
        self.quiet = quiet

    def run(
        self,
        job_files: List[Path],
        progress_callback: Optional[Callable[[int, int, JobResult], None]] = None,
    ) -> BatchResult:
        """
        Execute multiple job files concurrently.

        Args:
            job_files: List of job file paths.
            progress_callback: Optional callback(completed, total, result).

        Returns:
            BatchResult with aggregate statistics.
        """
        start_time = datetime.now()
        total_jobs = len(job_files)
        job_results: List[JobResult] = []

        with ThreadPoolExecutor(max_workers=self.max_concurrent_jobs) as executor:
            futures = {
                executor.submit(self._run_single_job, jf): jf
                for jf in job_files
            }

            completed = 0

            for future in as_completed(futures):
                job_file = futures[future]
                completed += 1

                try:
                    result = future.result()
                except Exception as e:
                    result = JobResult(
                        job_file=str(job_file),
                        job_id=job_file.stem,
                        error=str(e),
                    )

                job_results.append(result)

                if progress_callback:
                    progress_callback(completed, total_jobs, result)

        # Calculate aggregates
        duration_seconds = (datetime.now() - start_time).total_seconds()

        successful_jobs = sum(1 for r in job_results if r.success)
        failed_jobs = total_jobs - successful_jobs

        total_devices = sum(r.total_devices for r in job_results)
        total_success = sum(r.success_count for r in job_results)
        total_failed = sum(r.failed_count for r in job_results)
        total_skipped = sum(r.skipped_count for r in job_results)
        total_captures = sum(len(r.saved_files) for r in job_results)

        return BatchResult(
            total_jobs=total_jobs,
            successful_jobs=successful_jobs,
            failed_jobs=failed_jobs,
            total_devices=total_devices,
            total_success=total_success,
            total_failed=total_failed,
            total_skipped=total_skipped,
            total_captures=total_captures,
            duration_seconds=duration_seconds,
            job_results=job_results,
        )

    def _run_single_job(self, job_file: Path) -> JobResult:
        """Run a single job (called in thread pool)."""
        runner = JobRunner(
            credentials=self.credentials,
            validate=self.validate,
            tfsm_db_path=self.tfsm_db_path,
            debug=self.debug,
            no_save=self.no_save,
            force_save=self.force_save,
            limit=self.limit,
            quiet=self.quiet,
        )

        return runner.run(job_file)

