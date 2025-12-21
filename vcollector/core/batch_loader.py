"""
Batch Job Loader - YAML-based batch job definitions.

Path: vcollector/core/batch_loader.py

Manages batch job files in ~/.vcollector/batches/
A batch is simply a named list of job slugs - nothing more.

Example batch file (cisco-collection.yaml):
    name: Cisco Collection
    jobs:
      - cisco-ios-arp
      - cisco-ios-mac
      - cisco-ios-config
"""

import yaml
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from vcollector.core.config import get_config

logger = logging.getLogger(__name__)


@dataclass
class BatchDefinition:
    """A batch job definition."""
    name: str
    filename: str
    jobs: List[str]  # Job slugs
    description: str = ""

    # Populated during validation
    job_count: int = 0
    valid_jobs: List[str] = field(default_factory=list)
    invalid_jobs: List[str] = field(default_factory=list)


class BatchLoader:
    """
    Load, save, and validate batch job definitions.

    Usage:
        loader = BatchLoader()
        batches = loader.list_batches()

        # Create new batch
        loader.save_batch(BatchDefinition(
            name="Cisco Collection",
            filename="cisco-collection.yaml",
            jobs=["cisco-ios-arp", "cisco-ios-mac"],
        ))

        # Load and validate
        batch = loader.load_batch("cisco-collection.yaml")
        if batch.invalid_jobs:
            print(f"Warning: {batch.invalid_jobs} not found")
    """

    def __init__(self, batches_dir: Optional[Path] = None, jobs_repo=None):
        """
        Initialize batch loader.

        Args:
            batches_dir: Override batch files directory.
            jobs_repo: Optional JobsRepository instance (avoids creating new connection).
        """
        config = get_config()
        self.batches_dir = batches_dir or (config.base_dir / "batches")
        self.batches_dir.mkdir(parents=True, exist_ok=True)
        self._jobs_repo = jobs_repo
        self._owns_repo = False

    @property
    def jobs_repo(self):
        """Lazy-load jobs repository."""
        if self._jobs_repo is None:
            from vcollector.dcim.jobs_repo import JobsRepository
            self._jobs_repo = JobsRepository()
            self._owns_repo = True
        return self._jobs_repo

    def list_batches(self, validate: bool = True) -> List[BatchDefinition]:
        """
        List all batch definitions.

        Args:
            validate: Check job slugs exist in database.

        Returns:
            List of BatchDefinition objects.
        """
        batches = []
        for path in sorted(self.batches_dir.glob("*.yaml")):
            try:
                batch = self.load_batch(path.name, validate=validate)
                batches.append(batch)
            except Exception as e:
                logger.warning(f"Failed to load batch {path.name}: {e}")
        return batches

    def load_batch(self, filename: str, validate: bool = True) -> BatchDefinition:
        """
        Load a batch definition from YAML.

        Args:
            filename: Name of batch file (e.g., "cisco-collection.yaml").
            validate: Check job slugs exist in database.

        Returns:
            BatchDefinition with jobs list populated.
        """
        path = self.batches_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Batch file not found: {filename}")

        with open(path) as f:
            data = yaml.safe_load(f) or {}

        batch = BatchDefinition(
            name=data.get("name", path.stem),
            filename=filename,
            jobs=data.get("jobs", []),
            description=data.get("description", ""),
        )

        if validate:
            self._validate_jobs(batch)
        else:
            batch.valid_jobs = batch.jobs[:]
            batch.job_count = len(batch.jobs)

        return batch

    def _validate_jobs(self, batch: BatchDefinition):
        """Check which job slugs exist in database."""
        existing_slugs = {j.slug for j in self.jobs_repo.get_jobs()}

        batch.valid_jobs = [s for s in batch.jobs if s in existing_slugs]
        batch.invalid_jobs = [s for s in batch.jobs if s not in existing_slugs]
        batch.job_count = len(batch.valid_jobs)

    def save_batch(self, batch: BatchDefinition) -> Path:
        """
        Save batch definition to YAML.

        Args:
            batch: BatchDefinition to save.

        Returns:
            Path to saved file.
        """
        path = self.batches_dir / batch.filename

        data = {
            "name": batch.name,
            "jobs": batch.jobs,
        }

        if batch.description:
            data["description"] = batch.description

        with open(path, 'w') as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

        logger.info(f"Saved batch: {path}")
        return path

    def delete_batch(self, filename: str) -> bool:
        """
        Delete a batch file.

        Args:
            filename: Name of batch file to delete.

        Returns:
            True if deleted, False if not found.
        """
        path = self.batches_dir / filename
        if path.exists():
            path.unlink()
            logger.info(f"Deleted batch: {filename}")
            return True
        return False

    def get_batch_path(self, filename: str) -> Path:
        """Get full path to a batch file."""
        return self.batches_dir / filename

    def close(self):
        """Close resources."""
        if self._owns_repo and self._jobs_repo:
            self._jobs_repo.close()
            self._jobs_repo = None


def create_example_batches(batches_dir: Optional[Path] = None):
    """
    Create example batch files for new installations.

    Called by 'vcollector init' if batches directory is empty.
    """
    loader = BatchLoader(batches_dir)

    if list(loader.batches_dir.glob("*.yaml")):
        logger.debug("Batch files already exist, skipping examples")
        return

    examples = [
        BatchDefinition(
            name="Example - ARP Collection",
            filename="example-arp.yaml",
            jobs=["cisco-ios-arp", "arista-eos-arp", "juniper-junos-arp"],
            description="Collect ARP tables from all vendors",
        ),
        BatchDefinition(
            name="Example - Config Backup",
            filename="example-configs.yaml",
            jobs=["cisco-ios-config", "arista-eos-config", "juniper-junos-config"],
            description="Backup running configurations",
        ),
    ]

    for batch in examples:
        try:
            loader.save_batch(batch)
            logger.info(f"Created example batch: {batch.filename}")
        except Exception as e:
            logger.warning(f"Failed to create example batch {batch.filename}: {e}")

    loader.close()