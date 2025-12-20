"""
VelocityCollector - Network data collection engine with encrypted credential vault.

Usage:
    vcollector vault init
    vcollector vault add lab --username admin
    vcollector run --job jobs/cisco-ios_configs.json
"""

__version__ = "0.3.0"
__author__ = "Scott Peterman"

from vcollector.core.config import Config, get_config
from vcollector.vault.resolver import CredentialResolver
from vcollector.vault.models import SSHCredentials
from vcollector.ssh.executor import SSHExecutorPool, ExecutorOptions, ExecutionResult
from vcollector.jobs.runner import JobRunner, JobResult
from vcollector.jobs.batch import BatchRunner, BatchResult

# Optional validation imports
try:
    from vcollector.validation import ValidationEngine, ValidationResult
    VALIDATION_AVAILABLE = True
except ImportError:
    VALIDATION_AVAILABLE = False
    ValidationEngine = None
    ValidationResult = None

__all__ = [
    # Version
    "__version__",
    # Config
    "Config",
    "get_config",
    # Vault
    "CredentialResolver",
    "SSHCredentials",
    # SSH
    "SSHExecutorPool",
    "ExecutorOptions",
    "ExecutionResult",
    # Jobs
    "JobRunner",
    "JobResult",
    "BatchRunner",
    "BatchResult",
    # Validation (optional)
    "ValidationEngine",
    "ValidationResult",
    "VALIDATION_AVAILABLE",
]
