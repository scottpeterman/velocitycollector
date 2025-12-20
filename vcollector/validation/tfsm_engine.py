"""
TextFSM Validation Engine - Based on tfsm_fire.

Validates collected output against TextFSM templates.
Only output with score > 0 is considered valid.

Usage:
    engine = ValidationEngine(db_path="~/.vcollector/tfsm_templates.db")
    result = engine.validate(output, filter_string="cisco_ios_show_version")
    
    if result.is_valid:
        # Output matched a template with score > 0
        print(f"Template: {result.template}")
        print(f"Score: {result.score}")
        print(f"Records: {len(result.parsed_data)}")
    else:
        # Output failed validation - don't save
        print("Invalid output - no matching template")
"""

import sqlite3
import threading
import io
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

try:
    import textfsm
    TEXTFSM_AVAILABLE = True
except ImportError:
    TEXTFSM_AVAILABLE = False


@dataclass
class ValidationResult:
    """Result of output validation."""
    is_valid: bool
    template: Optional[str] = None
    parsed_data: Optional[List[Dict]] = None
    score: float = 0.0
    error: Optional[str] = None
    
    @property
    def record_count(self) -> int:
        """Number of records parsed."""
        return len(self.parsed_data) if self.parsed_data else 0


class ThreadSafeConnection:
    """Thread-local storage for SQLite connections."""

    def __init__(self, db_path: str, verbose: bool = False):
        self.db_path = db_path
        self.verbose = verbose
        self._local = threading.local()

    @contextmanager
    def get_connection(self):
        """Get a thread-local connection."""
        if not hasattr(self._local, 'connection'):
            self._local.connection = sqlite3.connect(self.db_path)
            self._local.connection.row_factory = sqlite3.Row

        try:
            yield self._local.connection
        except Exception as e:
            if hasattr(self._local, 'connection'):
                self._local.connection.close()
                delattr(self._local, 'connection')
            raise e

    def close_all(self):
        """Close connection if it exists for current thread."""
        if hasattr(self._local, 'connection'):
            self._local.connection.close()
            delattr(self._local, 'connection')


class ValidationEngine:
    """
    TextFSM-based output validation engine.
    
    Uses a SQLite database of TextFSM templates to validate
    collected device output. Output must achieve a score > 0
    to be considered valid.
    
    Attributes:
        db_path: Path to TextFSM templates database
        min_score: Minimum score for valid output (default: 0.01)
        verbose: Enable verbose logging
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        min_score: float = 0.01,
        verbose: bool = False,
    ):
        """
        Initialize validation engine.
        
        Args:
            db_path: Path to tfsm_templates.db. If None, uses default location.
            min_score: Minimum score to consider output valid.
            verbose: Enable verbose output.
        """
        if not TEXTFSM_AVAILABLE:
            raise ImportError(
                "textfsm not installed. Install with: pip install textfsm"
            )
        
        # Default database location
        if db_path is None:
            db_path = str(Path.home() / ".vcollector" / "tfsm_templates.db")
        
        self.db_path = db_path
        self.min_score = min_score
        self.verbose = verbose
        self.connection_manager = ThreadSafeConnection(db_path, verbose)
        
        # Verify database exists
        if not Path(db_path).exists():
            raise FileNotFoundError(
                f"TextFSM template database not found: {db_path}\n"
                "Download from: https://github.com/networktocode/ntc-templates"
            )

    def validate(
        self,
        device_output: str,
        filter_string: Optional[str] = None,
    ) -> ValidationResult:
        """
        Validate device output against TextFSM templates.
        
        Args:
            device_output: Raw CLI output from device.
            filter_string: Template filter (e.g., "cisco_ios_show_version").
            
        Returns:
            ValidationResult with validation status and parsed data.
        """
        if not device_output or not device_output.strip():
            return ValidationResult(
                is_valid=False,
                error="Empty output"
            )
        
        try:
            template, parsed_data, score = self.find_best_template(
                device_output, filter_string
            )
            
            is_valid = score >= self.min_score and parsed_data is not None
            
            return ValidationResult(
                is_valid=is_valid,
                template=template,
                parsed_data=parsed_data,
                score=score,
            )
            
        except Exception as e:
            return ValidationResult(
                is_valid=False,
                error=str(e)
            )

    def find_best_template(
        self,
        device_output: str,
        filter_string: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[List[Dict]], float]:
        """
        Try filtered templates against output and return best match.
        
        Args:
            device_output: Raw CLI output.
            filter_string: Template filter string.
            
        Returns:
            Tuple of (template_name, parsed_data, score).
        """
        best_template = None
        best_parsed_output = None
        best_score = 0.0

        with self.connection_manager.get_connection() as conn:
            templates = self._get_filtered_templates(conn, filter_string)
            
            if self.verbose:
                print(f"Found {len(templates)} templates for filter: {filter_string}")

            for template in templates:
                try:
                    fsm = textfsm.TextFSM(io.StringIO(template['textfsm_content']))
                    parsed = fsm.ParseText(device_output)
                    parsed_dicts = [dict(zip(fsm.header, row)) for row in parsed]
                    score = self._calculate_score(parsed_dicts, template, device_output)

                    if self.verbose:
                        print(f"  {template['cli_command']}: score={score:.2f}, records={len(parsed_dicts)}")

                    if score > best_score:
                        best_score = score
                        best_template = template['cli_command']
                        best_parsed_output = parsed_dicts
                        
                        # Early exit on high confidence match
                        if score >= 70:
                            break

                except Exception as e:
                    if self.verbose:
                        print(f"  {template['cli_command']}: failed - {e}")
                    continue

        return best_template, best_parsed_output, best_score

    def _get_filtered_templates(
        self,
        connection: sqlite3.Connection,
        filter_string: Optional[str] = None,
    ) -> List[sqlite3.Row]:
        """Get templates matching filter from database."""
        cursor = connection.cursor()
        
        if filter_string:
            # Split filter into terms and build query
            filter_terms = filter_string.replace('-', '_').split('_')
            query = "SELECT * FROM templates WHERE 1=1"
            params = []
            
            for term in filter_terms:
                if term and len(term) > 2:
                    query += " AND cli_command LIKE ?"
                    params.append(f"%{term}%")
            
            cursor.execute(query, params)
        else:
            cursor.execute("SELECT * FROM templates")
        
        return cursor.fetchall()

    def _calculate_score(
        self,
        parsed_data: List[Dict],
        template: sqlite3.Row,
        raw_output: str,
    ) -> float:
        """
        Calculate confidence score for template match.
        
        Scoring factors:
        - Number of records parsed (0-30 points)
        - Field population rate (0-25 points)
        - Output coverage (0-25 points)
        - Template specificity (0-20 points)
        """
        score = 0.0
        
        if not parsed_data:
            return score

        # Factor 1: Number of records parsed (0-30 points)
        num_records = len(parsed_data)
        if num_records > 0:
            cli_command = template['cli_command'].lower()
            
            # Version commands typically return single record
            if 'version' in cli_command:
                score += 30 if num_records == 1 else 15
            else:
                # More records = better match (up to 30 points)
                score += min(30, num_records * 10)

        # Factor 2: Field population (0-25 points)
        if parsed_data:
            sample = parsed_data[0]
            total_fields = len(sample)
            populated = sum(1 for v in sample.values() if v)
            
            if total_fields > 0:
                population_rate = populated / total_fields
                score += population_rate * 25

        # Factor 3: Output coverage (0-25 points)
        # How much of the output was captured by the template
        if parsed_data and raw_output:
            captured_chars = sum(
                len(str(v)) for record in parsed_data for v in record.values() if v
            )
            output_chars = len(raw_output)
            
            if output_chars > 0:
                coverage = min(1.0, captured_chars / output_chars)
                score += coverage * 25

        # Factor 4: Template specificity (0-20 points)
        # More specific templates (longer names) get bonus
        cli_command = template['cli_command']
        specificity = min(20, len(cli_command) / 3)
        score += specificity

        return score

    def list_templates(self, filter_string: Optional[str] = None) -> List[str]:
        """List available templates matching filter."""
        with self.connection_manager.get_connection() as conn:
            templates = self._get_filtered_templates(conn, filter_string)
            return [t['cli_command'] for t in templates]

    def __del__(self):
        """Clean up connections."""
        self.connection_manager.close_all()


# Convenience function for simple validation
def validate_output(
    output: str,
    filter_string: str,
    db_path: Optional[str] = None,
) -> ValidationResult:
    """
    Validate device output against TextFSM templates.
    
    Args:
        output: Raw CLI output from device.
        filter_string: Template filter (e.g., "cisco_ios_show_version").
        db_path: Path to template database (optional).
        
    Returns:
        ValidationResult with validation status.
    """
    engine = ValidationEngine(db_path=db_path)
    return engine.validate(output, filter_string)
