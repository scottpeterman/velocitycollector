"""Output validation using TextFSM templates."""

try:
    from vcollector.validation.tfsm_engine import (
        ValidationEngine,
        ValidationResult,
        validate_output,
    )
    VALIDATION_AVAILABLE = True
except ImportError as e:
    VALIDATION_AVAILABLE = False
    ValidationEngine = None
    ValidationResult = None
    validate_output = None
    _import_error = str(e)

__all__ = [
    "ValidationEngine",
    "ValidationResult",
    "validate_output",
    "VALIDATION_AVAILABLE",
]