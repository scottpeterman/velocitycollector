#!/usr/bin/env python3
"""
Auto-fix job files with failing tfsm_filter values.

Reads job files, finds those with filters that match 0 templates,
and replaces them with a working alternative based on capture_type.

Usage:
    python fix_jobs.py --tfsm-db vcollector/core/tfsm_templates.db jobs_v2/*.json
    python fix_jobs.py --tfsm-db vcollector/core/tfsm_templates.db --dry-run jobs_v2/*.json
"""

import json
import sqlite3
import sys
import argparse
from pathlib import Path
from typing import Optional, Tuple, List

# Mapping of capture_type to likely tfsm_filter candidates
# Order matters - more specific first
CAPTURE_TYPE_FILTERS = {
    'arp': ['show_ip_arp', 'show_arp', 'arp'],
    'bgp-neighbor': ['bgp_neighbor', 'show_ip_bgp_neighbors', 'bgp_neighbors'],
    'bgp-summary': ['bgp_summary', 'show_ip_bgp_summary'],
    'bgp-table': ['show_ip_bgp', 'bgp'],
    'bgp-table-detail': ['bgp_detail', 'show_ip_bgp'],
    'configs': [],  # Skip validation
    'console': [],  # Skip validation
    'int-status': ['interfaces_status', 'interface_status', 'show_interfaces_status'],
    'interface-status': ['interface_brief', 'show_ip_interface_brief'],
    'inventory': ['inventory', 'show_inventory'],
    'ip_ssh': ['ip_ssh', 'ssh'],
    'lldp': ['lldp_neighbors', 'show_lldp_neighbors'],
    'lldp-detail': ['lldp_neighbors_detail', 'show_lldp_neighbors_detail'],
    'mac': ['mac_address_table', 'show_mac_address_table'],
    'ntp_status': ['ntp_status', 'show_ntp_status', 'ntp'],
    'ospf-neighbor': ['ospf_neighbor', 'show_ip_ospf_neighbor'],
    'port-channel': ['etherchannel_summary', 'port_channel_summary', 'port_channel'],
    'routes': ['ip_route', 'show_ip_route', 'show_route'],
    'snmp_server': ['snmp', 'show_snmp'],
    'version': ['version', 'show_version'],
    'authentication': [],  # Skip validation
    'authorization': [],  # Skip validation
    'radius': [],  # Skip validation
    'tacacs': [],  # Skip validation
    'syslog': [],  # Skip validation
}


class JobFixer:
    def __init__(self, tfsm_db_path: str):
        self.tfsm_db_path = tfsm_db_path
        self._conn = None

    @property
    def conn(self):
        if self._conn is None:
            db_path = Path(self.tfsm_db_path).expanduser()
            if not db_path.exists():
                raise FileNotFoundError(f"TFSM database not found: {db_path}")
            self._conn = sqlite3.connect(db_path)
        return self._conn

    def check_filter(self, tfsm_filter: str) -> int:
        """Return count of matching templates."""
        cursor = self.conn.cursor()
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

    def suggest_filter_by_capture_type(self, capture_type: str) -> Optional[Tuple[str, int]]:
        """Suggest a filter based on capture_type."""
        capture_key = capture_type.lower()

        candidates = CAPTURE_TYPE_FILTERS.get(capture_key, [])

        for candidate in candidates:
            count = self.check_filter(candidate)
            if count > 0:
                return (candidate, count)

        return None

    def suggest_filter_by_stripping(self, failed_filter: str) -> Optional[Tuple[str, int]]:
        """Suggest a filter by stripping vendor prefixes."""
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
            count = self.check_filter(candidate)
            if count > 0:
                return (candidate, count)

        return None

    def fix_file(self, job_path: Path, dry_run: bool = False) -> Tuple[bool, str]:
        """
        Fix a job file if needed.

        Returns:
            (was_fixed, message)
        """
        try:
            with open(job_path) as f:
                job = json.load(f)
        except json.JSONDecodeError as e:
            return False, f"Invalid JSON: {e}"

        validation = job.get('validation', {})
        tfsm_filter = validation.get('tfsm_filter', '')
        capture_type = job.get('capture_type', '')

        # Skip if no filter or validation section
        if not validation:
            return False, "No validation section"

        # Skip if validation is explicitly skipped
        if validation.get('skip', False):
            return False, "Validation skipped for this job"

        # Check if filter works
        if tfsm_filter:
            match_count = self.check_filter(tfsm_filter)
            if match_count > 0:
                return False, f"Filter OK: '{tfsm_filter}' ({match_count} matches)"

        # Try to find a better filter
        # First, try based on capture_type (more reliable)
        suggestion = self.suggest_filter_by_capture_type(capture_type)

        # Fall back to stripping prefixes if capture_type doesn't help
        if not suggestion and tfsm_filter:
            suggestion = self.suggest_filter_by_stripping(tfsm_filter)

        if not suggestion:
            if capture_type.lower() in ['configs', 'authentication', 'authorization',
                                        'console', 'radius', 'tacacs', 'syslog']:
                # These should probably skip validation
                if dry_run:
                    return True, f"Would set skip=true (capture_type '{capture_type}' doesn't need validation)"

                job['validation']['skip'] = True
                if 'tfsm_filter' in job['validation']:
                    del job['validation']['tfsm_filter']

                with open(job_path, 'w') as f:
                    json.dump(job, f, indent=2)
                    f.write('\n')

                return True, f"Set skip=true (capture_type '{capture_type}' doesn't need validation)"

            return False, f"No suggestion for capture_type '{capture_type}', filter '{tfsm_filter}'"

        new_filter, new_count = suggestion

        if dry_run:
            return True, f"Would fix: '{tfsm_filter}' → '{new_filter}' ({new_count} matches)"

        # Apply fix
        job['validation']['tfsm_filter'] = new_filter

        with open(job_path, 'w') as f:
            json.dump(job, f, indent=2)
            f.write('\n')

        return True, f"Fixed: '{tfsm_filter}' → '{new_filter}' ({new_count} matches)"


def main():
    parser = argparse.ArgumentParser(
        description='Auto-fix job tfsm_filter values',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --tfsm-db vcollector/core/tfsm_templates.db --dry-run jobs_v2/*.json
  %(prog)s --tfsm-db vcollector/core/tfsm_templates.db jobs_v2/*.json
"""
    )
    parser.add_argument('files', nargs='+', help='Job files to fix')
    parser.add_argument('--tfsm-db', required=True,
                        help='Path to TextFSM templates database')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be fixed without making changes')

    args = parser.parse_args()

    fixer = JobFixer(args.tfsm_db)

    fixed_count = 0
    skipped_count = 0
    error_count = 0

    for file_pattern in args.files:
        path = Path(file_pattern)
        if '*' in file_pattern:
            files = list(path.parent.glob(path.name))
        else:
            files = [path]

        for job_file in sorted(files):
            if not job_file.suffix == '.json':
                continue

            was_fixed, message = fixer.fix_file(job_file, dry_run=args.dry_run)

            if was_fixed:
                print(f"✓ {job_file.name}: {message}")
                fixed_count += 1
            elif "Filter OK" in message or "skipped" in message.lower() or "No validation" in message:
                skipped_count += 1
            else:
                print(f"✗ {job_file.name}: {message}")
                error_count += 1

    print(f"\n{'=' * 60}")
    action = "Would fix" if args.dry_run else "Fixed"
    print(f"{action}: {fixed_count} files")
    print(f"Skipped (already OK): {skipped_count} files")
    if error_count:
        print(f"Errors (no suggestion): {error_count} files")

    if args.dry_run and fixed_count > 0:
        print(f"\nRun without --dry-run to apply fixes")

    return 0


if __name__ == '__main__':
    sys.exit(main())