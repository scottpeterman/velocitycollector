#!/usr/bin/env python3
"""
TextFSM Coverage Report Generator

Scans collected data, looks up device vendors from assets.db,
and reports parsing success/failure rates across all collections.

Usage:
    python tfsm_coverage_report.py [--collections-dir PATH] [--assets-db PATH]
    python tfsm_coverage_report.py --help
"""

import argparse
import sqlite3
import io
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime

try:
    import textfsm

    TEXTFSM_AVAILABLE = True
except ImportError:
    TEXTFSM_AVAILABLE = False
    print("WARNING: textfsm not installed. Install with: pip install textfsm")

# ============================================================================
# Configuration
# ============================================================================

DEFAULT_COLLECTIONS_DIR = Path.home() / ".vcollector" / "collections"
DEFAULT_ASSETS_DB = Path.home() / ".velocitycmdb" / "data" / "assets.db"
DEFAULT_TFSM_DB = Path.home() / ".vcollector" / "tfsm_templates.db"

# Map folder names to TextFSM filter hints
# Format: folder_name -> (filter_terms, expected_command_hint)
FOLDER_TO_FILTER_MAP = {
    "arp": ("arp", "show ip arp"),
    "bgp-neighbor": ("bgp_neighbor", "show bgp neighbor"),
    "bgp-summary": ("bgp_summary", "show bgp summary"),
    "bgp-table": ("bgp", "show bgp"),  # broader filter
    "bgp-table-detail": ("bgp", "show bgp"),
    "configs": ("running", "show running-config"),
    "console": ("line", "show line"),
    "interface-status": ("interface", "show interfaces status"),
    "inventory": ("inventory", "show inventory"),
    "lldp": ("lldp", "show lldp neighbor"),
    "lldp-detail": ("lldp", "show lldp neighbor detail"),
    "mac": ("mac", "show mac address-table"),
    "ntp_status": ("ntp", "show ntp"),
    "ospf-neighbor": ("ospf", "show ip ospf neighbor"),
    "radius": ("radius", "show radius"),
    "routes": ("route", "show ip route"),
    "snmp_server": ("snmp", "show snmp"),
    "syslog": ("logging", "show logging"),
    "tacacs": ("tacacs", "show tacacs"),
    "version": ("version", "show version"),
    "authentication": ("aaa", "show aaa"),  # might be aaa related
    "authorization": ("aaa", "show aaa"),
    "ip_ssh": ("ssh", "show ip ssh"),
}

# Vendor normalization map
VENDOR_NORMALIZE = {
    "cisco systems": "cisco",
    "cisco systems, inc.": "cisco",
    "cisco": "cisco",
    "arista networks": "arista",
    "arista": "arista",
    "juniper networks": "juniper",
    "juniper": "juniper",
    "hewlett packard": "hp",
    "hp": "hp",
    "palo alto networks": "paloalto",
    "paloalto": "paloalto",
}


# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class ParseResult:
    """Result of parsing a single file."""
    device_name: str
    vendor: str
    collection_type: str
    filepath: Path
    file_size: int
    success: bool
    score: float = 0.0
    template: Optional[str] = None
    record_count: int = 0
    error: Optional[str] = None
    fields: List[str] = field(default_factory=list)


@dataclass
class CollectionStats:
    """Statistics for a collection folder."""
    name: str
    total_files: int = 0
    parsed_ok: int = 0
    parsed_fail: int = 0
    no_vendor: int = 0
    total_records: int = 0
    avg_score: float = 0.0
    templates_used: Dict[str, int] = field(default_factory=dict)
    failures: List[ParseResult] = field(default_factory=list)
    vendor_breakdown: Dict[str, Dict[str, int]] = field(default_factory=dict)


@dataclass
class DeviceInfo:
    """Device information from assets.db."""
    device_id: int
    name: str
    vendor: str
    platform: str
    site: Optional[str] = None


# ============================================================================
# Database Access
# ============================================================================

class AssetsDatabase:
    """Read-only access to VelocityCMDB assets database."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._device_cache: Dict[str, DeviceInfo] = {}
        self._load_devices()

    def _load_devices(self):
        """Load all devices into cache for fast lookup."""
        if not self.db_path.exists():
            print(f"WARNING: Assets database not found: {self.db_path}")
            return

        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Try common schema patterns
        try:
            cursor.execute("""
                SELECT id, device_name, vendor, platform, site 
                FROM devices
            """)
        except sqlite3.OperationalError:
            try:
                cursor.execute("""
                    SELECT id, name as device_name, vendor, platform, site 
                    FROM assets
                """)
            except sqlite3.OperationalError:
                # List tables to help debug
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [r[0] for r in cursor.fetchall()]
                print(f"WARNING: Could not find devices table. Available: {tables}")
                conn.close()
                return

        for row in cursor.fetchall():
            name = row['device_name'].lower().strip()
            # Also index without domain suffix
            name_base = name.split('.')[0]

            info = DeviceInfo(
                device_id=row['id'],
                name=row['device_name'],
                vendor=row['vendor'] or '',
                platform=row['platform'] or '',
                site=row.get('site', '')
            )
            self._device_cache[name] = info
            if name_base != name:
                self._device_cache[name_base] = info

        conn.close()
        print(f"Loaded {len(self._device_cache)} device entries from assets.db")

    def get_device(self, device_name: str) -> Optional[DeviceInfo]:
        """Look up device by name."""
        name = device_name.lower().strip()
        # Remove .txt extension if present
        if name.endswith('.txt'):
            name = name[:-4]

        # Try exact match first
        if name in self._device_cache:
            return self._device_cache[name]

        # Try without domain
        name_base = name.split('.')[0]
        if name_base in self._device_cache:
            return self._device_cache[name_base]

        return None

    def normalize_vendor(self, vendor: str) -> str:
        """Normalize vendor string to standard form."""
        if not vendor:
            return "unknown"
        v = vendor.lower().strip()
        return VENDOR_NORMALIZE.get(v, v.split()[0] if v else "unknown")


# ============================================================================
# TextFSM Engine (simplified from tfsm_fire)
# ============================================================================

class TextFSMEngine:
    """TextFSM template matching engine."""

    def __init__(self, db_path: Path, verbose: bool = False):
        self.db_path = db_path
        self.verbose = verbose
        self._conn: Optional[sqlite3.Connection] = None

    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            if not self.db_path.exists():
                raise FileNotFoundError(f"TextFSM database not found: {self.db_path}")
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def find_best_template(
            self,
            content: str,
            capture_type: str
    ) -> Tuple[Optional[str], List[Dict], float, List[str]]:
        """
        Find best matching template for content.

        Uses folder/capture_type as the primary hint for template matching.
        TextFSM scoring determines which vendor-specific template matches best.

        Returns: (template_name, parsed_data, score, field_names)
        """
        if not TEXTFSM_AVAILABLE:
            return None, [], 0.0, []

        conn = self._get_connection()
        cursor = conn.cursor()

        # Build filter based on capture type (folder name)
        filter_hint = FOLDER_TO_FILTER_MAP.get(capture_type, (capture_type, ""))
        filter_term = filter_hint[0] if isinstance(filter_hint, tuple) else filter_hint

        # Query ALL templates matching the capture type - let scoring pick the best
        cursor.execute(
            "SELECT * FROM templates WHERE cli_command LIKE ?",
            (f"%{filter_term}%",)
        )
        templates = cursor.fetchall()

        if self.verbose and templates:
            print(f"  Found {len(templates)} candidate templates for {vendor}/{capture_type}")

        best_template = None
        best_parsed = []
        best_score = 0.0
        best_fields = []

        for template in templates:
            try:
                fsm = textfsm.TextFSM(io.StringIO(template['textfsm_content']))
                parsed = fsm.ParseText(content)
                parsed_dicts = [dict(zip(fsm.header, row)) for row in parsed]

                score = self._calculate_score(parsed_dicts, template, content)

                if score > best_score:
                    best_score = score
                    best_template = template['cli_command']
                    best_parsed = parsed_dicts
                    best_fields = list(fsm.header)

                    # Early exit on high confidence
                    if score >= 70:
                        break

            except Exception as e:
                if self.verbose:
                    print(f"    Template {template['cli_command']} failed: {e}")
                continue

        return best_template, best_parsed, best_score, best_fields

    def _calculate_score(
            self,
            parsed_data: List[Dict],
            template: sqlite3.Row,
            raw_output: str
    ) -> float:
        """Calculate confidence score for template match."""
        if not parsed_data:
            return 0.0

        score = 0.0
        num_records = len(parsed_data)
        cli_command = template['cli_command'].lower()

        # Factor 1: Number of records (0-30 points)
        if 'version' in cli_command or 'inventory' in cli_command:
            score += 30 if num_records >= 1 else 0
        else:
            score += min(30, num_records * 3)

        # Factor 2: Field population (0-25 points)
        sample = parsed_data[0]
        total_fields = len(sample)
        populated = sum(1 for v in sample.values() if v)
        if total_fields > 0:
            score += (populated / total_fields) * 25

        # Factor 3: Output coverage (0-25 points)
        captured_chars = sum(
            len(str(v)) for record in parsed_data for v in record.values() if v
        )
        if len(raw_output) > 0:
            coverage = min(1.0, captured_chars / len(raw_output))
            score += coverage * 25

        # Factor 4: Template specificity (0-20 points)
        specificity = min(20, len(cli_command) / 3)
        score += specificity

        return score

    def list_templates(self, filter_term: str = "") -> List[str]:
        """List available templates matching filter."""
        conn = self._get_connection()
        cursor = conn.cursor()

        if filter_term:
            cursor.execute(
                "SELECT cli_command FROM templates WHERE cli_command LIKE ?",
                (f"%{filter_term}%",)
            )
        else:
            cursor.execute("SELECT cli_command FROM templates")

        return [row['cli_command'] for row in cursor.fetchall()]

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None


# ============================================================================
# Report Generator
# ============================================================================

class CoverageReportGenerator:
    """Generate TextFSM parsing coverage reports."""

    def __init__(
            self,
            collections_dir: Path,
            assets_db: Path,
            tfsm_db: Path,
            verbose: bool = False,
            limit: Optional[int] = None
    ):
        self.collections_dir = collections_dir
        self.verbose = verbose
        self.limit = limit

        self.assets = AssetsDatabase(assets_db)
        self.tfsm = TextFSMEngine(tfsm_db, verbose=verbose)

        self.results: List[ParseResult] = []
        self.stats: Dict[str, CollectionStats] = {}

    def scan_all_collections(self) -> Dict[str, CollectionStats]:
        """Scan all collection folders and generate stats."""
        if not self.collections_dir.exists():
            print(f"ERROR: Collections directory not found: {self.collections_dir}")
            return {}

        folders = sorted([
            d for d in self.collections_dir.iterdir()
            if d.is_dir() and not d.name.startswith('.')
        ])

        print(f"\nScanning {len(folders)} collection folders in {self.collections_dir}\n")
        print("=" * 70)

        for folder in folders:
            self._scan_collection(folder)

        return self.stats

    def _scan_collection(self, folder: Path):
        """Scan a single collection folder."""
        collection_name = folder.name
        files = list(folder.glob("*.txt"))

        if not files:
            print(f"  {collection_name}: (empty)")
            return

        stats = CollectionStats(name=collection_name, total_files=len(files))
        stats.vendor_breakdown = defaultdict(lambda: {"ok": 0, "fail": 0})

        print(f"\n{collection_name}/ ({len(files)} files)")
        print("-" * 50)

        scores = []
        processed = 0

        for filepath in sorted(files):
            if self.limit and processed >= self.limit:
                print(f"  (limited to {self.limit} files)")
                break

            result = self._parse_file(filepath, collection_name)
            self.results.append(result)

            if result.vendor == "unknown":
                stats.no_vendor += 1

            if result.success:
                stats.parsed_ok += 1
                stats.total_records += result.record_count
                scores.append(result.score)

                # Track template usage
                if result.template:
                    stats.templates_used[result.template] = \
                        stats.templates_used.get(result.template, 0) + 1

                stats.vendor_breakdown[result.vendor]["ok"] += 1
            else:
                stats.parsed_fail += 1
                stats.failures.append(result)
                stats.vendor_breakdown[result.vendor]["fail"] += 1

            processed += 1

        # Calculate averages
        if scores:
            stats.avg_score = sum(scores) / len(scores)

        # Print summary for this collection
        ok_pct = (stats.parsed_ok / stats.total_files * 100) if stats.total_files else 0
        print(f"  ✓ Parsed: {stats.parsed_ok}/{stats.total_files} ({ok_pct:.1f}%)")
        print(f"  ✗ Failed: {stats.parsed_fail}")
        if stats.no_vendor:
            print(f"  ? No vendor: {stats.no_vendor}")
        if scores:
            print(f"  Avg score: {stats.avg_score:.1f}")

        # Show vendor breakdown
        if stats.vendor_breakdown:
            print(f"  By vendor:")
            for vendor, counts in sorted(stats.vendor_breakdown.items()):
                total = counts["ok"] + counts["fail"]
                pct = counts["ok"] / total * 100 if total else 0
                print(f"    {vendor}: {counts['ok']}/{total} ({pct:.0f}%)")

        # Show top templates used
        if stats.templates_used:
            top_templates = sorted(
                stats.templates_used.items(),
                key=lambda x: x[1],
                reverse=True
            )[:3]
            print(f"  Templates: {', '.join(t[0] for t in top_templates)}")

        self.stats[collection_name] = stats

    def _infer_vendor_from_template(self, template_name: str) -> str:
        """Infer vendor from template name pattern."""
        t = template_name.lower()

        if t.startswith('arista_eos') or 'arista' in t:
            return 'arista'
        elif t.startswith('cisco_ios') and 'xr' not in t:
            return 'cisco_ios'
        elif t.startswith('cisco_nxos') or 'nxos' in t:
            return 'cisco_nxos'
        elif t.startswith('cisco_xr') or 'iosxr' in t:
            return 'cisco_xr'
        elif t.startswith('juniper') or 'junos' in t:
            return 'juniper'
        elif t.startswith('hp_') or 'procurve' in t or 'comware' in t:
            return 'hp'
        elif t.startswith('paloalto') or 'panos' in t:
            return 'paloalto'
        elif 'fortinet' in t or 'fortios' in t:
            return 'fortinet'
        elif 'vmware' in t or 'nsx' in t:
            return 'vmware'
        elif 'huawei' in t:
            return 'huawei'
        elif 'dell' in t or 'force10' in t:
            return 'dell'
        else:
            # Try to extract first word as vendor
            parts = t.replace('-', '_').split('_')
            if parts and len(parts[0]) > 2:
                return parts[0]
            return 'generic'

    def _parse_file(self, filepath: Path, collection_type: str) -> ParseResult:
        """Parse a single file and return result."""
        device_name = filepath.stem

        # Look up device vendor
        device_info = self.assets.get_device(device_name)
        if device_info:
            vendor = self.assets.normalize_vendor(device_info.vendor)
        else:
            vendor = "unknown"

        # Read file content
        try:
            content = filepath.read_text(encoding='utf-8', errors='replace')
            file_size = filepath.stat().st_size
        except Exception as e:
            return ParseResult(
                device_name=device_name,
                vendor=vendor,
                collection_type=collection_type,
                filepath=filepath,
                file_size=0,
                success=False,
                error=str(e)
            )

        # Skip empty files
        if not content.strip():
            return ParseResult(
                device_name=device_name,
                vendor=vendor,
                collection_type=collection_type,
                filepath=filepath,
                file_size=file_size,
                success=False,
                error="Empty file"
            )

        # Try to parse with TextFSM - uses folder name as hint
        template, parsed_data, score, fields = self.tfsm.find_best_template(
            content, collection_type
        )

        success = score > 0 and parsed_data is not None and len(parsed_data) > 0

        # Infer vendor from matched template name if we got one
        if template:
            vendor = self._infer_vendor_from_template(template)

        if self.verbose:
            status = "✓" if success else "✗"
            print(f"  {status} {device_name} ({vendor}): score={score:.1f}, template={template}")

        return ParseResult(
            device_name=device_name,
            vendor=vendor,
            collection_type=collection_type,
            filepath=filepath,
            file_size=file_size,
            success=success,
            score=score,
            template=template,
            record_count=len(parsed_data) if parsed_data else 0,
            fields=fields,
            error=None if success else f"Score {score:.1f}, no matching template"
        )

    def generate_report(self) -> str:
        """Generate final summary report."""
        lines = []
        lines.append("\n" + "=" * 70)
        lines.append("TEXTFSM COVERAGE REPORT")
        lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("=" * 70)

        # Overall statistics
        total_files = sum(s.total_files for s in self.stats.values())
        total_ok = sum(s.parsed_ok for s in self.stats.values())
        total_fail = sum(s.parsed_fail for s in self.stats.values())
        total_records = sum(s.total_records for s in self.stats.values())

        lines.append(f"\nOVERALL SUMMARY")
        lines.append("-" * 40)
        lines.append(f"Total files scanned:  {total_files:,}")
        lines.append(f"Successfully parsed:  {total_ok:,} ({total_ok / total_files * 100:.1f}%)" if total_files else "")
        lines.append(
            f"Failed to parse:      {total_fail:,} ({total_fail / total_files * 100:.1f}%)" if total_files else "")
        lines.append(f"Total records parsed: {total_records:,}")

        # Collection breakdown
        lines.append(f"\nCOLLECTION BREAKDOWN")
        lines.append("-" * 40)
        lines.append(f"{'Collection':<25} {'OK':>6} {'Fail':>6} {'%':>6} {'Avg Score':>10}")
        lines.append("-" * 55)

        for name, stats in sorted(self.stats.items(), key=lambda x: x[1].parsed_ok, reverse=True):
            pct = stats.parsed_ok / stats.total_files * 100 if stats.total_files else 0
            lines.append(
                f"{name:<25} {stats.parsed_ok:>6} {stats.parsed_fail:>6} "
                f"{pct:>5.1f}% {stats.avg_score:>10.1f}"
            )

        # Problem areas (collections with >20% failure)
        problem_collections = [
            (name, stats) for name, stats in self.stats.items()
            if stats.total_files > 0 and stats.parsed_fail / stats.total_files > 0.2
        ]

        if problem_collections:
            lines.append(f"\nPROBLEM AREAS (>20% failure rate)")
            lines.append("-" * 40)
            for name, stats in sorted(problem_collections, key=lambda x: x[1].parsed_fail, reverse=True):
                fail_pct = stats.parsed_fail / stats.total_files * 100
                lines.append(f"\n{name}/ - {stats.parsed_fail} failures ({fail_pct:.0f}%)")

                # Show available templates for this collection type
                filter_hint = FOLDER_TO_FILTER_MAP.get(name, (name, ""))
                filter_term = filter_hint[0] if isinstance(filter_hint, tuple) else filter_hint
                available = self.tfsm.list_templates(filter_term)
                if available:
                    lines.append(f"  Available templates ({len(available)}): {', '.join(available[:5])}" +
                                 (f" ..." if len(available) > 5 else ""))
                else:
                    lines.append(f"  ⚠ NO TEMPLATES found for filter '{filter_term}'")

                # Group failures by vendor
                vendor_failures = defaultdict(list)
                for f in stats.failures[:10]:  # Show first 10
                    vendor_failures[f.vendor].append(f.device_name)

                for vendor, devices in vendor_failures.items():
                    lines.append(f"  {vendor}: {', '.join(devices[:5])}" +
                                 (f" (+{len(devices) - 5} more)" if len(devices) > 5 else ""))

        # Vendor summary
        vendor_totals = defaultdict(lambda: {"ok": 0, "fail": 0})
        for stats in self.stats.values():
            for vendor, counts in stats.vendor_breakdown.items():
                vendor_totals[vendor]["ok"] += counts["ok"]
                vendor_totals[vendor]["fail"] += counts["fail"]

        lines.append(f"\nVENDOR SUMMARY")
        lines.append("-" * 40)
        lines.append(f"{'Vendor':<20} {'OK':>8} {'Fail':>8} {'Success %':>10}")
        lines.append("-" * 48)

        for vendor, counts in sorted(vendor_totals.items(), key=lambda x: x[1]["ok"], reverse=True):
            total = counts["ok"] + counts["fail"]
            pct = counts["ok"] / total * 100 if total else 0
            lines.append(f"{vendor:<20} {counts['ok']:>8} {counts['fail']:>8} {pct:>9.1f}%")

        # Template usage summary
        all_templates = defaultdict(int)
        for stats in self.stats.values():
            for template, count in stats.templates_used.items():
                all_templates[template] += count

        if all_templates:
            lines.append(f"\nTOP TEMPLATES USED")
            lines.append("-" * 40)
            for template, count in sorted(all_templates.items(), key=lambda x: x[1], reverse=True)[:15]:
                lines.append(f"  {count:>5}x  {template}")

        lines.append("\n" + "=" * 70)

        return "\n".join(lines)

    def export_failures_csv(self, output_path: Path):
        """Export failures to CSV for further analysis."""
        import csv

        failures = [r for r in self.results if not r.success]

        with open(output_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                'collection', 'device', 'vendor', 'file_size',
                'score', 'template', 'error'
            ])

            for r in failures:
                writer.writerow([
                    r.collection_type, r.device_name, r.vendor, r.file_size,
                    r.score, r.template or '', r.error or ''
                ])

        print(f"\nExported {len(failures)} failures to {output_path}")


# ============================================================================
# Main
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Analyze TextFSM parsing coverage across collected data"
    )
    parser.add_argument(
        "--collections-dir", "-c",
        type=Path,
        default=DEFAULT_COLLECTIONS_DIR,
        help=f"Collections directory (default: {DEFAULT_COLLECTIONS_DIR})"
    )
    parser.add_argument(
        "--assets-db", "-a",
        type=Path,
        default=DEFAULT_ASSETS_DB,
        help=f"Assets database path (default: {DEFAULT_ASSETS_DB})"
    )
    parser.add_argument(
        "--tfsm-db", "-t",
        type=Path,
        default=DEFAULT_TFSM_DB,
        help=f"TextFSM templates database (default: {DEFAULT_TFSM_DB})"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show per-file parsing results"
    )
    parser.add_argument(
        "--limit", "-l",
        type=int,
        help="Limit files per collection (for testing)"
    )
    parser.add_argument(
        "--export-failures",
        type=Path,
        help="Export failures to CSV file"
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        help="Write report to file instead of stdout"
    )
    parser.add_argument(
        "--collection",
        help="Scan only this specific collection folder"
    )
    parser.add_argument(
        "--list-templates",
        metavar="FILTER",
        help="List available templates matching filter (e.g., 'arp', 'bgp', 'version')"
    )

    args = parser.parse_args()

    # Handle --list-templates mode
    if args.list_templates:
        if not args.tfsm_db.exists():
            print(f"ERROR: TextFSM database not found: {args.tfsm_db}")
            sys.exit(1)

        engine = TextFSMEngine(args.tfsm_db)
        templates = engine.list_templates(args.list_templates)

        if templates:
            print(f"\nTemplates matching '{args.list_templates}' ({len(templates)}):")
            print("-" * 50)
            for t in sorted(templates):
                print(f"  {t}")
        else:
            print(f"\nNo templates found matching '{args.list_templates}'")

        engine.close()
        sys.exit(0)

    # Validate paths
    if not args.collections_dir.exists():
        print(f"ERROR: Collections directory not found: {args.collections_dir}")
        sys.exit(1)

    if not args.tfsm_db.exists():
        print(f"ERROR: TextFSM database not found: {args.tfsm_db}")
        print("This is required for template matching.")
        sys.exit(1)

    # Run analysis
    generator = CoverageReportGenerator(
        collections_dir=args.collections_dir,
        assets_db=args.assets_db,
        tfsm_db=args.tfsm_db,
        verbose=args.verbose,
        limit=args.limit
    )

    # If specific collection requested, filter
    if args.collection:
        target = args.collections_dir / args.collection
        if target.exists():
            generator._scan_collection(target)
        else:
            print(f"ERROR: Collection not found: {target}")
            sys.exit(1)
    else:
        generator.scan_all_collections()

    # Generate report
    report = generator.generate_report()

    if args.output:
        args.output.write_text(report)
        print(f"\nReport written to {args.output}")
    else:
        print(report)

    # Export failures if requested
    if args.export_failures:
        generator.export_failures_csv(args.export_failures)

    generator.tfsm.close()


if __name__ == "__main__":
    main()