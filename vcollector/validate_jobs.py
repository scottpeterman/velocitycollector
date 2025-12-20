#!/usr/bin/env python3
"""
Job File Validator for VelocityCollector v2 jobs.

Validates job files against the v2 schema and checks tfsm_filter
against the template database.

Usage:
    python validate_jobs.py jobs_v2/*.json
    python validate_jobs.py --tfsm-db ~/.vcollector/tfsm_templates.db jobs_v2/*.json
"""

import json
import sqlite3
import sys
import argparse
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any

# Schema definition
REQUIRED_FIELDS = ['version', 'job_id', 'capture_type', 'vendor', 'commands']
REQUIRED_COMMANDS_FIELDS = ['command', 'output_directory']
VALID_VENDORS = ['cisco', 'arista', 'juniper', 'paloalto', 'fortinet']
VALID_PROTOCOLS = ['ssh', 'telnet']

# Capture types that should skip validation (raw output, no structured parsing)
SKIP_VALIDATION_CAPTURE_TYPES = [
    'configs', 'config', 'running-config',
    'authentication', 'authorization', 'console',
    'radius', 'tacacs', 'syslog', 'logging'
]


class JobValidator:
    def __init__(
            self,
            tfsm_db_path: Optional[str] = None,
            warn_trailing_commas: bool = False,
            strict: bool = False,
    ):
        self.tfsm_db_path = tfsm_db_path
        self.warn_trailing_commas = warn_trailing_commas
        self.strict = strict  # Treat warnings as errors
        self._tfsm_conn = None
        self._seen_job_ids: Dict[int, str] = {}  # job_id -> filename

    @property
    def tfsm_conn(self):
        """Lazy-load TFSM database connection."""
        if self._tfsm_conn is None and self.tfsm_db_path:
            db_path = Path(self.tfsm_db_path).expanduser()
            if db_path.exists():
                self._tfsm_conn = sqlite3.connect(db_path)
            else:
                print(f"Warning: TFSM database not found: {db_path}")
        return self._tfsm_conn

    def validate_file(self, job_path: Path) -> Tuple[bool, List[str], List[str]]:
        """
        Validate a single job file.

        Returns:
            (is_valid, errors, warnings)
        """
        errors = []
        warnings = []

        # Check file exists
        if not job_path.exists():
            return False, [f"File not found: {job_path}"], []

        # Parse JSON
        try:
            with open(job_path) as f:
                job = json.load(f)
        except json.JSONDecodeError as e:
            return False, [f"Invalid JSON: {e}"], []

        # Validate structure
        self._validate_required_fields(job, errors)
        self._validate_version(job, errors, warnings)
        self._validate_job_id(job, job_path, errors, warnings)
        self._validate_vendor(job, errors, warnings)
        self._validate_protocol(job, warnings)
        self._validate_commands(job, errors, warnings)
        self._validate_device_filter(job, warnings)
        self._validate_validation_section(job, errors, warnings)
        self._validate_storage(job, errors, warnings)
        self._validate_execution(job, warnings)

        is_valid = len(errors) == 0
        if self.strict and warnings:
            is_valid = False

        return is_valid, errors, warnings

    def _validate_required_fields(self, job: Dict[str, Any], errors: List[str]):
        """Check required top-level fields."""
        for field in REQUIRED_FIELDS:
            if field not in job:
                errors.append(f"Missing required field: {field}")

    def _validate_version(self, job: Dict[str, Any], errors: List[str], warnings: List[str]):
        """Validate version field."""
        version = job.get('version')
        if version is None:
            errors.append("Missing 'version' field")
        elif version != '2.0':
            warnings.append(f"Non-standard version: {version} (expected '2.0')")

    def _validate_job_id(self, job: Dict[str, Any], job_path: Path, errors: List[str], warnings: List[str]):
        """Validate job_id and check for duplicates."""
        job_id = job.get('job_id')
        if job_id is None:
            errors.append("Missing 'job_id' field")
            return

        # Check for duplicate job_ids
        if job_id in self._seen_job_ids:
            warnings.append(f"Duplicate job_id {job_id} (also in {self._seen_job_ids[job_id]})")
        else:
            self._seen_job_ids[job_id] = job_path.name

    def _validate_vendor(self, job: Dict[str, Any], errors: List[str], warnings: List[str]):
        """Validate vendor field."""
        vendor = job.get('vendor', '').lower()
        if not vendor:
            errors.append("Missing or empty 'vendor' field")
        elif vendor not in VALID_VENDORS:
            warnings.append(f"Unknown vendor: '{vendor}' (known: {', '.join(VALID_VENDORS)})")

    def _validate_protocol(self, job: Dict[str, Any], warnings: List[str]):
        """Validate protocol field."""
        protocol = job.get('protocol', 'ssh').lower()
        if protocol not in VALID_PROTOCOLS:
            warnings.append(f"Unknown protocol: '{protocol}' (known: {', '.join(VALID_PROTOCOLS)})")

    def _validate_commands(self, job: Dict[str, Any], errors: List[str], warnings: List[str]):
        """Validate commands section."""
        commands = job.get('commands', {})

        # Check required fields
        if 'command' not in commands:
            errors.append("Missing commands.command")
        elif not commands.get('command'):
            errors.append("commands.command is empty or null")

        if 'output_directory' not in commands:
            errors.append("Missing commands.output_directory")

        # Validate output_directory is not a full path
        output_dir = commands.get('output_directory', '')
        if output_dir:
            if '~' in output_dir:
                errors.append(
                    f"output_directory contains '~': '{output_dir}' - "
                    "should be subdirectory name only (e.g., 'arp')"
                )
            elif output_dir.startswith('/'):
                errors.append(
                    f"output_directory is absolute path: '{output_dir}' - "
                    "should be subdirectory name only"
                )
            elif '/' in output_dir:
                warnings.append(
                    f"output_directory contains '/': '{output_dir}' - "
                    "should typically be a single subdirectory name"
                )

        # Check paging_disable (null is OK, just note it)
        paging_disable = commands.get('paging_disable')
        if paging_disable is None and 'paging_disable' in commands:
            # Explicitly set to null - that's fine but worth noting in debug
            pass

        # Check trailing commas
        command = commands.get('command', '')
        if self.warn_trailing_commas and command and not command.endswith(',,'):
            warnings.append(f"Command may need trailing commas: '{command}'")

    def _validate_device_filter(self, job: Dict[str, Any], warnings: List[str]):
        """Validate device_filter section."""
        device_filter = job.get('device_filter', {})

        valid_filter_fields = ['source', 'vendor', 'platform', 'site', 'role', 'name_pattern']
        for field in device_filter:
            if field not in valid_filter_fields:
                warnings.append(f"Unknown device_filter field: '{field}'")

    def _validate_validation_section(self, job: Dict[str, Any], errors: List[str], warnings: List[str]):
        """Validate the validation section and tfsm_filter."""
        validation = job.get('validation', {})
        capture_type = job.get('capture_type', '')

        # Check if validation should be skipped for this capture type
        if capture_type.lower() in SKIP_VALIDATION_CAPTURE_TYPES:
            if not validation.get('skip', False):
                warnings.append(
                    f"capture_type '{capture_type}' typically doesn't need validation - "
                    "consider adding 'skip: true'"
                )
            return

        # Check tfsm_filter
        tfsm_filter = validation.get('tfsm_filter', '')

        if not tfsm_filter:
            warnings.append("No tfsm_filter defined - output won't be validated")
            return

        # Check filter relevance to capture_type
        self._check_filter_relevance(tfsm_filter, capture_type, warnings)

        # Validate against database
        if self.tfsm_conn:
            match_count = self._check_tfsm_filter(tfsm_filter)
            if match_count == 0:
                errors.append(f"tfsm_filter '{tfsm_filter}' matches 0 templates")
                suggestion = self._suggest_filter(tfsm_filter)
                if suggestion:
                    errors.append(f"  → Suggested: '{suggestion[0]}' ({suggestion[1]} matches)")
            elif match_count > 10:
                warnings.append(
                    f"tfsm_filter '{tfsm_filter}' matches {match_count} templates - may be too broad"
                )

    def _check_filter_relevance(self, tfsm_filter: str, capture_type: str, warnings: List[str]):
        """Check if tfsm_filter seems relevant to capture_type."""
        filter_lower = tfsm_filter.lower()
        capture_lower = capture_type.lower().replace('-', '_')

        # Common mismatches
        if capture_lower == 'inventory' and filter_lower == 'enable':
            warnings.append(
                f"tfsm_filter '{tfsm_filter}' doesn't match capture_type '{capture_type}' - "
                "should probably be 'inventory' or 'show_inventory'"
            )
        elif capture_lower == 'version' and 'version' not in filter_lower:
            warnings.append(
                f"tfsm_filter '{tfsm_filter}' may not match capture_type '{capture_type}'"
            )
        elif capture_lower not in filter_lower and filter_lower not in capture_lower:
            # Generic check - filter should relate to capture type
            # Allow some common mappings
            mappings = {
                'int_status': ['interface', 'status'],
                'interface_status': ['interface', 'brief'],
                'bgp_neighbor': ['bgp', 'neighbor'],
                'bgp_summary': ['bgp', 'summary'],
                'bgp_table': ['bgp'],
                'bgp_table_detail': ['bgp'],
                'ospf_neighbor': ['ospf', 'neighbor'],
                'port_channel': ['etherchannel', 'port_channel', 'lacp'],
                'mac': ['mac_address'],
                'routes': ['route', 'ip_route'],
                'ntp_status': ['ntp', 'status'],
                'lldp': ['lldp'],
                'lldp_detail': ['lldp'],
                'snmp_server': ['snmp'],
                'ip_ssh': ['ssh'],
            }

            if capture_lower in mappings:
                if not any(m in filter_lower for m in mappings[capture_lower]):
                    warnings.append(
                        f"tfsm_filter '{tfsm_filter}' may not match capture_type '{capture_type}'"
                    )

    def _validate_storage(self, job: Dict[str, Any], errors: List[str], warnings: List[str]):
        """Validate storage section."""
        storage = job.get('storage', {})

        base_path = storage.get('base_path', '')
        if base_path:
            if not ('~' in base_path or base_path.startswith('/')):
                warnings.append(
                    f"storage.base_path may be incomplete: '{base_path}' - "
                    "expected full path like '~/.vcollector/collections'"
                )

        filename_pattern = storage.get('filename_pattern', '')
        if filename_pattern:
            valid_vars = ['{device_name}', '{device_id}', '{timestamp}', '{capture_type}']
            if not any(v in filename_pattern for v in valid_vars):
                warnings.append(
                    f"filename_pattern '{filename_pattern}' has no variables - "
                    f"available: {', '.join(valid_vars)}"
                )

    def _validate_execution(self, job: Dict[str, Any], warnings: List[str]):
        """Validate execution settings."""
        execution = job.get('execution', {})

        timeout = execution.get('timeout', 60)
        if timeout and timeout < 10:
            warnings.append(f"execution.timeout={timeout} may be too short")
        elif timeout and timeout > 300:
            warnings.append(f"execution.timeout={timeout} is very long (>5 min)")

        max_workers = execution.get('max_workers', 12)
        if max_workers and max_workers > 50:
            warnings.append(f"execution.max_workers={max_workers} is very high")

    def _check_tfsm_filter(self, tfsm_filter: str) -> int:
        """Check how many templates match the filter."""
        if not self.tfsm_conn:
            return -1

        cursor = self.tfsm_conn.cursor()

        for column in ['cli_command', 'template_name', 'name', 'template']:
            try:
                cursor.execute(
                    f"SELECT COUNT(*) FROM templates WHERE {column} LIKE ?",
                    (f"%{tfsm_filter}%",)
                )
                return cursor.fetchone()[0]
            except sqlite3.OperationalError:
                continue

        return -1

    def _suggest_filter(self, failed_filter: str) -> Optional[Tuple[str, int]]:
        """Suggest an alternative filter when the original fails."""
        if not self.tfsm_conn:
            return None

        prefixes = [
            'cisco_ios_', 'cisco_nxos_', 'cisco_',
            'arista_eos_', 'arista_',
            'juniper_junos_', 'juniper_',
        ]

        candidates = []
        remaining = failed_filter

        for prefix in prefixes:
            if failed_filter.startswith(prefix):
                remaining = failed_filter[len(prefix):]
                candidates.append(remaining)
                break

        parts = remaining.split('_')
        if len(parts) > 1:
            if parts[0] == 'show':
                candidates.append('_'.join(parts[1:]))
            candidates.append('_'.join(parts[-2:]))
            candidates.append(parts[-1])

        for candidate in candidates:
            if len(candidate) < 3:
                continue
            count = self._check_tfsm_filter(candidate)
            if count > 0:
                return (candidate, count)

        return None

    def list_matching_templates(self, tfsm_filter: str, limit: int = 10) -> List[str]:
        """List templates matching the filter."""
        if not self.tfsm_conn:
            return []

        cursor = self.tfsm_conn.cursor()

        for column in ['cli_command', 'template_name', 'name', 'template']:
            try:
                cursor.execute(
                    f"SELECT {column} FROM templates WHERE {column} LIKE ? LIMIT ?",
                    (f"%{tfsm_filter}%", limit)
                )
                return [row[0] for row in cursor.fetchall()]
            except sqlite3.OperationalError:
                continue

        return []


def main():
    parser = argparse.ArgumentParser(
        description='Validate VelocityCollector job files',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s jobs_v2/*.json
  %(prog)s --tfsm-db vcollector/core/tfsm_templates.db jobs_v2/*.json
  %(prog)s --errors-only --quiet jobs_v2/*.json
  %(prog)s --strict jobs_v2/*.json  # Treat warnings as errors
"""
    )
    parser.add_argument('files', nargs='+', help='Job files to validate')
    parser.add_argument('--tfsm-db', default='~/.vcollector/tfsm_templates.db',
                        help='Path to TextFSM templates database')
    parser.add_argument('--show-templates', action='store_true',
                        help='Show matching templates for each filter')
    parser.add_argument('--quiet', '-q', action='store_true',
                        help='Only show errors, not warnings')
    parser.add_argument('--errors-only', action='store_true',
                        help='Only show files with errors')
    parser.add_argument('--strict', action='store_true',
                        help='Treat warnings as errors')
    parser.add_argument('--warn-trailing-commas', action='store_true',
                        help='Warn about commands missing trailing commas')

    args = parser.parse_args()

    tfsm_db = Path(args.tfsm_db).expanduser() if args.tfsm_db else None

    validator = JobValidator(
        tfsm_db_path=str(tfsm_db) if tfsm_db else None,
        warn_trailing_commas=args.warn_trailing_commas,
        strict=args.strict,
    )

    total_files = 0
    valid_files = 0
    files_with_warnings = 0
    files_with_errors = 0

    for file_pattern in args.files:
        path = Path(file_pattern)
        if '*' in file_pattern:
            files = list(path.parent.glob(path.name))
        else:
            files = [path]

        for job_file in sorted(files):
            if not job_file.suffix == '.json':
                continue

            total_files += 1
            is_valid, errors, warnings = validator.validate_file(job_file)

            if is_valid:
                valid_files += 1
            else:
                files_with_errors += 1

            if warnings:
                files_with_warnings += 1

            if args.errors_only and is_valid:
                continue

            status = "✓" if is_valid else "✗"

            if errors or (warnings and not args.quiet):
                print(f"\n{status} {job_file.name}")

                for error in errors:
                    print(f"  ERROR: {error}")

                if not args.quiet:
                    for warning in warnings:
                        print(f"  WARN:  {warning}")

                if args.show_templates:
                    with open(job_file) as f:
                        job = json.load(f)
                    tfsm_filter = job.get('validation', {}).get('tfsm_filter', '')
                    if tfsm_filter:
                        templates = validator.list_matching_templates(tfsm_filter)
                        if templates:
                            print(f"  Templates matching '{tfsm_filter}':")
                            for t in templates:
                                print(f"    - {t}")
            elif not args.errors_only:
                print(f"{status} {job_file.name}")

    print(f"\n{'=' * 60}")
    print(f"Summary: {valid_files}/{total_files} valid")
    if files_with_errors:
        print(f"  Files with errors: {files_with_errors}")
    if files_with_warnings:
        print(f"  Files with warnings: {files_with_warnings}")

    return 0 if files_with_errors == 0 else 1


if __name__ == '__main__':
    sys.exit(main())