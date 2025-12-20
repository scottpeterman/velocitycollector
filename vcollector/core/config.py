"""
Configuration management for VelocityCollector.

Handles loading config from ~/.vcollector/config.yaml and providing
default values for all settings.

v2 Changes:
- Replaced assets_db with dcim_db (NetBox-compatible schema)
- jobs_dir renamed to legacy_jobs_dir (jobs now in database)
- Added tfsm_templates_db for TextFSM template storage
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


# Default paths
DEFAULT_BASE_DIR = Path.home() / ".vcollector"
DEFAULT_CONFIG_FILE = DEFAULT_BASE_DIR / "config.yaml"

# Database paths
DEFAULT_DCIM_DB = DEFAULT_BASE_DIR / "dcim.db"
DEFAULT_COLLECTOR_DB = DEFAULT_BASE_DIR / "collector.db"
DEFAULT_TFSM_TEMPLATES_DB = DEFAULT_BASE_DIR / "tfsm_templates.db"

# Storage paths
DEFAULT_COLLECTIONS_DIR = DEFAULT_BASE_DIR / "collections"
DEFAULT_LEGACY_JOBS_DIR = DEFAULT_BASE_DIR / "jobs"
DEFAULT_LOG_DIR = DEFAULT_BASE_DIR / "logs"


@dataclass
class ExecutionConfig:
    """Default execution settings (can be overridden per-job)."""

    max_workers: int = 12
    timeout: int = 60
    inter_command_delay: float = 1.0


@dataclass
class LoggingConfig:
    """Logging settings."""

    level: str = "INFO"
    file: Optional[Path] = None


@dataclass
class Config:
    """Main configuration container."""

    # Base directory
    base_dir: Path = DEFAULT_BASE_DIR
    config_file: Path = DEFAULT_CONFIG_FILE

    # Databases
    dcim_db: Path = DEFAULT_DCIM_DB              # Device inventory (NetBox-compatible)
    collector_db: Path = DEFAULT_COLLECTOR_DB     # Jobs, credentials, history
    tfsm_templates_db: Path = DEFAULT_TFSM_TEMPLATES_DB  # TextFSM templates

    # Storage
    collections_dir: Path = DEFAULT_COLLECTIONS_DIR
    legacy_jobs_dir: Path = DEFAULT_LEGACY_JOBS_DIR  # For JSON job files (backward compat)
    log_dir: Path = DEFAULT_LOG_DIR

    # Execution defaults (used when job doesn't specify)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)

    # Logging
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    # Deprecated - for migration warnings only
    _has_legacy_assets_db: bool = field(default=False, repr=False)

    @classmethod
    def load(cls, config_path: Optional[Path] = None) -> "Config":
        """
        Load configuration from YAML file.

        Args:
            config_path: Path to config file. If None, uses default location.
                         Can also be set via VCOLLECTOR_CONFIG env var.

        Returns:
            Config instance with values from file merged with defaults.
        """
        # Determine config path
        if config_path is None:
            config_path = Path(
                os.environ.get("VCOLLECTOR_CONFIG", str(DEFAULT_CONFIG_FILE))
            )

        config = cls()
        config.config_file = config_path

        # If config file doesn't exist, return defaults
        if not config_path.exists():
            return config

        # Load YAML
        try:
            with open(config_path) as f:
                data = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid config YAML: {e}")

        # Database paths
        if "dcim_db" in data:
            config.dcim_db = Path(data["dcim_db"]).expanduser()

        if "collector_db" in data:
            config.collector_db = Path(data["collector_db"]).expanduser()

        if "tfsm_templates_db" in data:
            config.tfsm_templates_db = Path(data["tfsm_templates_db"]).expanduser()

        # Storage paths
        if "collections_dir" in data:
            config.collections_dir = Path(data["collections_dir"]).expanduser()

        if "legacy_jobs_dir" in data:
            config.legacy_jobs_dir = Path(data["legacy_jobs_dir"]).expanduser()
        elif "jobs_dir" in data:
            # Backward compatibility: old key name
            config.legacy_jobs_dir = Path(data["jobs_dir"]).expanduser()

        # Execution settings
        if "execution" in data:
            exec_data = data["execution"]
            config.execution = ExecutionConfig(
                max_workers=exec_data.get("max_workers", 12),
                timeout=exec_data.get("timeout", 60),
                inter_command_delay=exec_data.get(
                    "inter_command_delay",
                    exec_data.get("inter_command_time", 1.0)  # Old key name
                ),
            )

        # Logging settings
        if "logging" in data:
            log_data = data["logging"]
            log_file = log_data.get("file")
            config.logging = LoggingConfig(
                level=log_data.get("level", "INFO"),
                file=Path(log_file).expanduser() if log_file else None,
            )

        # Check for deprecated assets_db
        if "assets_db" in data:
            config._has_legacy_assets_db = True

        return config

    def check_migration_warnings(self) -> list:
        """
        Check for configuration issues that need attention.

        Returns:
            List of warning messages.
        """
        warnings = []

        if self._has_legacy_assets_db:
            warnings.append(
                "Config contains deprecated 'assets_db' key. "
                "Device inventory is now in 'dcim_db'. "
                "Run 'vcollector migrate assets' to migrate data."
            )

        if not self.dcim_db.exists():
            warnings.append(
                f"DCIM database not found: {self.dcim_db}. "
                "Run 'vcollector init' to create it."
            )

        if not self.collector_db.exists():
            warnings.append(
                f"Collector database not found: {self.collector_db}. "
                "Run 'vcollector vault init' to create it."
            )

        return warnings

    def ensure_directories(self):
        """Create required directories if they don't exist."""
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.collections_dir.mkdir(parents=True, exist_ok=True)
        self.legacy_jobs_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def save_default_config(self):
        """Save a default config file if one doesn't exist."""
        if self.config_file.exists():
            return

        self.ensure_directories()

        default_config = f"""\
# VelocityCollector Configuration
# https://github.com/yourrepo/vcollector

# =============================================================================
# Database Paths
# =============================================================================

# Device inventory database (NetBox-compatible schema)
dcim_db: {self.dcim_db}

# Collector database (jobs, credentials, history)
collector_db: {self.collector_db}

# TextFSM template database
tfsm_templates_db: {self.tfsm_templates_db}

# =============================================================================
# Storage Paths
# =============================================================================

# Where captured output is stored
collections_dir: {self.collections_dir}

# Legacy JSON job files (for backward compatibility)
legacy_jobs_dir: {self.legacy_jobs_dir}

# =============================================================================
# Default Execution Settings
# =============================================================================
# These are used when a job doesn't specify its own values

execution:
  max_workers: 12          # Concurrent SSH connections per job
  timeout: 60              # SSH timeout in seconds
  inter_command_delay: 1   # Seconds between commands

# =============================================================================
# Logging
# =============================================================================

logging:
  level: INFO              # DEBUG, INFO, WARNING, ERROR
  file: {self.log_dir / 'vcollector.log'}
"""

        with open(self.config_file, "w") as f:
            f.write(default_config)


# Singleton instance
_config: Optional[Config] = None


def get_config(reload: bool = False) -> Config:
    """
    Get the global configuration instance.

    Args:
        reload: Force reload from file.

    Returns:
        Config instance.
    """
    global _config

    if _config is None or reload:
        _config = Config.load()

    return _config


def get_dcim_db_path() -> Path:
    """Convenience function for DCIMRepository."""
    return get_config().dcim_db


def get_collector_db_path() -> Path:
    """Convenience function for JobsRepository and CredentialResolver."""
    return get_config().collector_db