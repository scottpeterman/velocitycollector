#!/usr/bin/env python3
"""
TextFSM Coverage Analyzer

Analyzes collected device outputs against the TextFSM template database
to measure parsing coverage and identify gaps. Can also extract parsed
data into JSON, SQLite, or NDJSON formats, and purge files that fail parsing.

Usage:
    # Analysis only
    python tfsm_coverage_analyzer.py                    # Full analysis, console output
    python tfsm_coverage_analyzer.py --json report.json # Export detailed JSON
    python tfsm_coverage_analyzer.py --csv report.csv   # Export CSV for spreadsheets
    python tfsm_coverage_analyzer.py --type arp         # Analyze specific capture type
    python tfsm_coverage_analyzer.py --verbose          # Show per-file details

    # Data extraction
    python tfsm_coverage_analyzer.py --extract                      # Extract to ./extracted/
    python tfsm_coverage_analyzer.py --extract-dir ./parsed         # Custom output dir
    python tfsm_coverage_analyzer.py --extract-db network.db        # SQLite database
    python tfsm_coverage_analyzer.py --extract-ndjson all.ndjson    # NDJSON for jq
    python tfsm_coverage_analyzer.py --extract --min-score 75       # Only high-confidence

    # Purge failed files (cleanup for test iteration)
    python tfsm_coverage_analyzer.py --purge-dry-run                # Preview what would be deleted
    python tfsm_coverage_analyzer.py --purge                        # Delete score=0 files
    python tfsm_coverage_analyzer.py --purge --purge-below 25       # Delete score<=25 files

    # Query NDJSON with jq
    cat all.ndjson | jq 'select(._meta.capture_type == "arp")'
    cat all.ndjson | jq 'select(.ADDRESS == "10.0.0.1")'
"""

import argparse
import json
import csv
import sqlite3
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import io

try:
    import textfsm

    TEXTFSM_AVAILABLE = True
except ImportError:
    TEXTFSM_AVAILABLE = False

# ============================================================================
# Configuration
# ============================================================================

DEFAULT_VCOLLECTOR_DIR = Path.home() / ".vcollector"
DEFAULT_COLLECTIONS_DIR = DEFAULT_VCOLLECTOR_DIR / "collections"
DEFAULT_DCIM_DB = DEFAULT_VCOLLECTOR_DIR / "dcim.db"
DEFAULT_COLLECTOR_DB = DEFAULT_VCOLLECTOR_DIR / "collector.db"

# Search paths for TextFSM database (in order of preference)
TFSM_DB_SEARCH_PATHS = [
    # Bundled in vcollector package (relative to script or installed)
    Path(__file__).parent / "vcollector" / "core" / "tfsm_templates.db",
    # Common development layout
    Path.cwd() / "vcollector" / "core" / "tfsm_templates.db",
    # Installed package location
    Path(__file__).parent / "core" / "tfsm_templates.db",
    # User's vcollector directory (may be empty from init)
    DEFAULT_VCOLLECTOR_DIR / "tfsm_templates.db",
]


def find_tfsm_db() -> Path:
    """Find the best TextFSM database with actual templates."""
    for path in TFSM_DB_SEARCH_PATHS:
        if path.exists():
            try:
                conn = sqlite3.connect(str(path))
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM templates")
                count = cursor.fetchone()[0]
                conn.close()
                if count > 0:
                    return path
            except Exception:
                continue

    # Fallback to user dir even if empty
    return DEFAULT_VCOLLECTOR_DIR / "tfsm_templates.db"


DEFAULT_TFSM_DB = find_tfsm_db()


# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class FileResult:
    """Result for a single file analysis."""
    filepath: str
    device_name: str
    capture_type: str
    vendor: Optional[str]
    platform: Optional[str]
    file_size: int
    template_matched: Optional[str]
    score: float
    record_count: int
    parse_time_ms: float
    error: Optional[str] = None
    skipped: bool = False  # True if capture type not parseable
    skip_reason: Optional[str] = None
    parsed_records: Optional[List[Dict]] = None  # The actual extracted data

    @property
    def score_bucket(self) -> str:
        if self.skipped:
            return "skipped"
        if self.score == 0:
            return "0 (no match)"
        elif self.score < 25:
            return "1-24 (poor)"
        elif self.score < 50:
            return "25-49 (fair)"
        elif self.score < 75:
            return "50-74 (good)"
        else:
            return "75-100 (excellent)"


@dataclass
class CaptureTypeStats:
    """Aggregated stats for a capture type."""
    capture_type: str
    total_files: int = 0
    matched_files: int = 0
    total_records: int = 0
    total_size_bytes: int = 0
    avg_score: float = 0.0
    min_score: float = 0.0
    max_score: float = 0.0
    score_distribution: Dict[str, int] = field(default_factory=dict)
    templates_used: Dict[str, int] = field(default_factory=dict)
    failed_devices: List[str] = field(default_factory=list)

    @property
    def parseable_files(self) -> int:
        return self.total_files - self.score_distribution.get("skipped", 0)

    @property
    def match_rate(self) -> float:
        return (self.matched_files / self.parseable_files * 100) if self.parseable_files > 0 else 0.0

    @property
    def is_skipped(self) -> bool:
        return self.score_distribution.get("skipped", 0) == self.total_files


@dataclass
class VendorStats:
    """Aggregated stats by vendor."""
    vendor: str
    total_files: int = 0
    matched_files: int = 0
    avg_score: float = 0.0
    capture_types: Dict[str, int] = field(default_factory=dict)


@dataclass
class AnalysisReport:
    """Complete analysis report."""
    generated_at: str
    collections_dir: str
    tfsm_db: str
    total_files: int
    total_matched: int
    total_records: int
    total_size_bytes: int
    overall_match_rate: float
    overall_avg_score: float
    analysis_time_seconds: float
    by_capture_type: Dict[str, CaptureTypeStats] = field(default_factory=dict)
    by_vendor: Dict[str, VendorStats] = field(default_factory=dict)
    score_distribution: Dict[str, int] = field(default_factory=dict)
    file_results: List[FileResult] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


# ============================================================================
# TextFSM Engine (embedded from tfsm_fire)
# ============================================================================

class TextFSMAnalyzer:
    """Lightweight TextFSM analyzer for coverage testing."""

    def __init__(self, db_path: str, verbose: bool = False):
        self.db_path = db_path
        self.verbose = verbose
        self._conn = None

    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def get_filtered_templates(self, filter_string: Optional[str] = None) -> List[sqlite3.Row]:
        """Get templates matching filter."""
        conn = self._get_connection()
        cursor = conn.cursor()

        if filter_string:
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

    def analyze_output(
            self,
            device_output: str,
            filter_string: Optional[str] = None
    ) -> Tuple[Optional[str], List[Dict], float]:
        """
        Analyze output against templates.

        Returns: (template_name, parsed_records, score)
        """
        best_template = None
        best_parsed = []
        best_score = 0.0

        templates = self.get_filtered_templates(filter_string)

        for template in templates:
            try:
                fsm = textfsm.TextFSM(io.StringIO(template['textfsm_content']))
                parsed = fsm.ParseText(device_output)
                parsed_dicts = [dict(zip(fsm.header, row)) for row in parsed]
                score = self._calculate_score(parsed_dicts, template, device_output)

                if score > best_score:
                    best_score = score
                    best_template = template['cli_command']
                    best_parsed = parsed_dicts

                    # Early exit on high confidence
                    if score >= 75:
                        break

            except Exception:
                continue

        return best_template, best_parsed, best_score

    def _calculate_score(
            self,
            parsed_data: List[Dict],
            template: sqlite3.Row,
            raw_output: str
    ) -> float:
        """Calculate match quality score (0-100)."""
        if not parsed_data:
            return 0.0

        num_records = len(parsed_data)
        num_fields = len(parsed_data[0].keys()) if parsed_data else 0
        is_version_cmd = 'version' in template['cli_command'].lower()

        # Record count score (0-30)
        if is_version_cmd:
            record_score = 30.0 if num_records == 1 else max(0, 15 - (num_records - 1) * 5)
        else:
            if num_records >= 10:
                record_score = 30.0
            elif num_records >= 3:
                record_score = 20.0 + (num_records - 3) * (10.0 / 7.0)
            else:
                record_score = num_records * 10.0

        # Field richness (0-30)
        if num_fields >= 10:
            field_score = 30.0
        elif num_fields >= 6:
            field_score = 20.0 + (num_fields - 6) * 2.5
        elif num_fields >= 3:
            field_score = 10.0 + (num_fields - 3) * (10.0 / 3.0)
        else:
            field_score = num_fields * 5.0

        # Population rate (0-25)
        total_cells = num_records * num_fields
        populated = sum(
            1 for record in parsed_data
            for v in record.values()
            if v is not None and str(v).strip()
        )
        population_rate = populated / total_cells if total_cells > 0 else 0
        population_score = population_rate * 25.0

        # Consistency (0-15)
        if num_records > 1:
            field_counts = {k: 0 for k in parsed_data[0].keys()}
            for record in parsed_data:
                for k, v in record.items():
                    if v is not None and str(v).strip():
                        field_counts[k] += 1
            consistent = sum(
                1 for c in field_counts.values()
                if c == 0 or c == num_records
            )
            consistency_rate = consistent / num_fields if num_fields > 0 else 0
            consistency_score = consistency_rate * 15.0
        else:
            consistency_score = 15.0

        return record_score + field_score + population_score + consistency_score

    def get_template_count(self) -> int:
        """Get total template count."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM templates")
        return cursor.fetchone()[0]


# ============================================================================
# Device Info Lookup
# ============================================================================

class DeviceInfoLookup:
    """Lookup device info from DCIM database."""

    def __init__(self, dcim_db: str):
        self.dcim_db = dcim_db
        self._cache: Dict[str, Dict] = {}
        self._loaded = False

    def _load_cache(self):
        if self._loaded:
            return

        if not Path(self.dcim_db).exists():
            self._loaded = True
            return

        try:
            conn = sqlite3.connect(self.dcim_db)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT 
                    d.name,
                    d.primary_ip4,
                    p.name as platform,
                    p.netmiko_device_type,
                    m.name as manufacturer
                FROM dcim_device d
                LEFT JOIN dcim_platform p ON d.platform_id = p.id
                LEFT JOIN dcim_manufacturer m ON p.manufacturer_id = m.id
            """)

            for row in cursor.fetchall():
                self._cache[row['name']] = {
                    'platform': row['platform'],
                    'netmiko_type': row['netmiko_device_type'],
                    'vendor': row['manufacturer']
                }

            conn.close()
        except Exception:
            pass

        self._loaded = True

    def get_device_info(self, device_name: str) -> Dict:
        self._load_cache()
        return self._cache.get(device_name, {
            'platform': None,
            'netmiko_type': None,
            'vendor': None
        })


# ============================================================================
# Coverage Analyzer
# ============================================================================

class CoverageAnalyzer:
    """Main analyzer class."""

    def __init__(
            self,
            collections_dir: Path,
            tfsm_db: Path,
            dcim_db: Path,
            verbose: bool = False
    ):
        self.collections_dir = collections_dir
        self.tfsm_db = tfsm_db
        self.verbose = verbose
        self.analyzer = TextFSMAnalyzer(str(tfsm_db), verbose)
        self.device_lookup = DeviceInfoLookup(str(dcim_db))

    def build_filter_string(self, capture_type: str, device_info: Dict) -> str:
        """Build TextFSM filter string from capture type and device info."""
        parts = []

        # Add vendor/platform prefix if available
        netmiko_type = device_info.get('netmiko_type')
        if netmiko_type:
            # Extract vendor from netmiko type (e.g., "arista_eos" -> "arista")
            vendor_part = netmiko_type.split('_')[0]
            parts.append(vendor_part)

        # Map capture types to likely command patterns
        # These should match ntc-templates naming conventions
        type_command_map = {
            # Layer 2/3 tables
            'arp': 'arp',
            'mac': 'mac-address-table',
            'routes': 'ip_route',
            'interfaces': 'interfaces',
            'interface-status': 'interfaces_status',

            # Discovery protocols
            'lldp': 'lldp_neighbors',
            'lldp-detail': 'lldp_neighbors_detail',
            'cdp': 'cdp_neighbors',
            'cdp-detail': 'cdp_neighbors_detail',

            # Routing protocols
            'bgp-summary': 'bgp_summary',
            'bgp-neighbor': 'bgp_neighbors',
            'bgp-table': 'bgp_table',
            'bgp-table-detail': 'bgp_table',
            'ospf-neighbor': 'ospf_neighbor',

            # Device info
            'version': 'version',
            'inventory': 'inventory',

            # VLANs and port-channels
            'vlan': 'vlan',
            'port-channel': 'etherchannel_summary',

            # Services - these map to specific show commands
            'ntp_status': 'ntp_status',
            'snmp_server': 'snmp',
            'syslog': 'logging',

            # AAA - typically config output, not show commands
            # But try common show patterns
            'authentication': 'aaa',
            'authorization': 'aaa',
            'tacacs': 'tacacs',
            'radius': 'radius',

            # SSH/console
            'ip_ssh': 'ssh',
            'console': 'line',

            # Config output - no TextFSM for these
            'configs': None,
            'config': None,
        }

        cmd_hint = type_command_map.get(capture_type.lower())

        # None means this capture type shouldn't be parsed
        if cmd_hint is None:
            return ""

        # Default to capture type name if not mapped
        if not cmd_hint:
            cmd_hint = capture_type.replace('-', '_')

        parts.append(cmd_hint)

        return '_'.join(parts)

    def analyze_file(self, filepath: Path, capture_type: str, extract: bool = False) -> FileResult:
        """Analyze a single file."""
        device_name = filepath.stem  # filename without extension
        device_info = self.device_lookup.get_device_info(device_name)

        try:
            content = filepath.read_text(errors='replace')
            file_size = filepath.stat().st_size
        except Exception as e:
            return FileResult(
                filepath=str(filepath),
                device_name=device_name,
                capture_type=capture_type,
                vendor=device_info.get('vendor'),
                platform=device_info.get('platform'),
                file_size=0,
                template_matched=None,
                score=0.0,
                record_count=0,
                parse_time_ms=0.0,
                error=str(e)
            )

        # Build filter and check if parseable
        filter_string = self.build_filter_string(capture_type, device_info)

        # Empty filter means this capture type isn't parseable
        if not filter_string:
            return FileResult(
                filepath=str(filepath),
                device_name=device_name,
                capture_type=capture_type,
                vendor=device_info.get('vendor'),
                platform=device_info.get('platform'),
                file_size=file_size,
                template_matched=None,
                score=0.0,
                record_count=0,
                parse_time_ms=0.0,
                skipped=True,
                skip_reason="Not parseable (config/non-tabular output)"
            )

        start = time.perf_counter()
        template, records, score = self.analyzer.analyze_output(content, filter_string)
        elapsed_ms = (time.perf_counter() - start) * 1000

        return FileResult(
            filepath=str(filepath),
            device_name=device_name,
            capture_type=capture_type,
            vendor=device_info.get('vendor'),
            platform=device_info.get('platform'),
            file_size=file_size,
            template_matched=template,
            score=score,
            record_count=len(records),
            parse_time_ms=elapsed_ms,
            parsed_records=records if extract and records else None
        )

    def analyze_all(
            self,
            capture_type_filter: Optional[str] = None,
            limit: Optional[int] = None,
            extract: bool = False,
            min_score_for_extract: float = 50.0
    ) -> AnalysisReport:
        """Run full analysis.

        Args:
            capture_type_filter: Only analyze this capture type
            limit: Limit to N files
            extract: If True, store parsed records in results
            min_score_for_extract: Only extract data if score >= this threshold
        """
        start_time = time.time()

        report = AnalysisReport(
            generated_at=datetime.now().isoformat(),
            collections_dir=str(self.collections_dir),
            tfsm_db=str(self.tfsm_db),
            total_files=0,
            total_matched=0,
            total_records=0,
            total_size_bytes=0,
            overall_match_rate=0.0,
            overall_avg_score=0.0,
            analysis_time_seconds=0.0,
            score_distribution={
                "skipped": 0,
                "0 (no match)": 0,
                "1-24 (poor)": 0,
                "25-49 (fair)": 0,
                "50-74 (good)": 0,
                "75-100 (excellent)": 0
            }
        )

        # Collect files to analyze
        files_to_analyze = []

        for capture_dir in self.collections_dir.iterdir():
            if not capture_dir.is_dir():
                continue

            capture_type = capture_dir.name

            if capture_type_filter and capture_type != capture_type_filter:
                continue

            for f in capture_dir.glob("*.txt"):
                files_to_analyze.append((f, capture_type))

        if limit:
            files_to_analyze = files_to_analyze[:limit]

        total = len(files_to_analyze)
        print(f"\nAnalyzing {total} files across {self.collections_dir}...")
        print(f"TextFSM DB: {self.tfsm_db} ({self.analyzer.get_template_count()} templates)")
        print("-" * 60)

        scores_sum = 0.0

        for idx, (filepath, capture_type) in enumerate(files_to_analyze, 1):
            if self.verbose:
                print(f"[{idx}/{total}] {capture_type}/{filepath.name}...", end=" ")
            elif idx % 50 == 0 or idx == total:
                print(f"  Progress: {idx}/{total} ({idx / total * 100:.0f}%)")

            result = self.analyze_file(filepath, capture_type, extract=extract)

            # Only keep parsed records if score meets threshold
            if extract and result.parsed_records and result.score < min_score_for_extract:
                result.parsed_records = None

            report.file_results.append(result)

            if self.verbose:
                if result.template_matched:
                    print(f"✓ {result.score:.1f} ({result.record_count} records)")
                else:
                    print("✗ no match")

            # Aggregate stats
            report.total_files += 1
            report.total_size_bytes += result.file_size
            report.score_distribution[result.score_bucket] += 1

            # Skip files don't count toward match calculations
            if result.skipped:
                continue

            report.total_records += result.record_count
            scores_sum += result.score

            if result.score > 0:
                report.total_matched += 1

            # By capture type
            if capture_type not in report.by_capture_type:
                report.by_capture_type[capture_type] = CaptureTypeStats(
                    capture_type=capture_type,
                    score_distribution={
                        "skipped": 0, "0 (no match)": 0, "1-24 (poor)": 0,
                        "25-49 (fair)": 0, "50-74 (good)": 0,
                        "75-100 (excellent)": 0
                    }
                )

            ct_stats = report.by_capture_type[capture_type]
            ct_stats.total_files += 1
            ct_stats.total_size_bytes += result.file_size
            ct_stats.score_distribution[result.score_bucket] += 1

            # Skip files don't count toward match rate
            if not result.skipped:
                ct_stats.total_records += result.record_count

                if result.score > 0:
                    ct_stats.matched_files += 1
                    if result.template_matched:
                        ct_stats.templates_used[result.template_matched] = \
                            ct_stats.templates_used.get(result.template_matched, 0) + 1
                else:
                    ct_stats.failed_devices.append(result.device_name)

            # By vendor
            vendor = result.vendor or "unknown"
            if vendor not in report.by_vendor:
                report.by_vendor[vendor] = VendorStats(vendor=vendor)

            v_stats = report.by_vendor[vendor]
            v_stats.total_files += 1
            if not result.skipped and result.score > 0:
                v_stats.matched_files += 1
            v_stats.capture_types[capture_type] = v_stats.capture_types.get(capture_type, 0) + 1

            if result.error:
                report.errors.append(f"{filepath}: {result.error}")

        # Calculate averages (excluding skipped files)
        parseable_files = report.total_files - report.score_distribution.get("skipped", 0)
        if parseable_files > 0:
            report.overall_match_rate = report.total_matched / parseable_files * 100
            report.overall_avg_score = scores_sum / parseable_files

        for ct_stats in report.by_capture_type.values():
            parseable = ct_stats.total_files - ct_stats.score_distribution.get("skipped", 0)
            if parseable > 0:
                ct_scores = [
                    r.score for r in report.file_results
                    if r.capture_type == ct_stats.capture_type and not r.skipped
                ]
                if ct_scores:
                    ct_stats.avg_score = sum(ct_scores) / len(ct_scores)
                    ct_stats.min_score = min(ct_scores)
                    ct_stats.max_score = max(ct_scores)

        for v_stats in report.by_vendor.values():
            v_scores = [
                r.score for r in report.file_results
                if (r.vendor or "unknown") == v_stats.vendor and not r.skipped
            ]
            v_stats.avg_score = sum(v_scores) / len(v_scores) if v_scores else 0

        report.analysis_time_seconds = time.time() - start_time

        self.analyzer.close()
        return report

    def print_report(self, report: AnalysisReport):
        """Print formatted console report."""
        print("\n" + "=" * 60)
        print("TEXTFSM COVERAGE ANALYSIS REPORT")
        print("=" * 60)

        # Summary
        print(f"\nGenerated: {report.generated_at}")
        print(f"Analysis Time: {report.analysis_time_seconds:.1f}s")
        print(f"\n{'SUMMARY':^60}")
        print("-" * 60)

        skipped = report.score_distribution.get("skipped", 0)
        parseable = report.total_files - skipped

        print(f"  Total Files:     {report.total_files:>8}")
        print(f"  Parseable:       {parseable:>8}")
        print(f"  Skipped:         {skipped:>8} (configs/non-tabular)")
        print(f"  Matched Files:   {report.total_matched:>8} ({report.overall_match_rate:.1f}% of parseable)")
        print(f"  Total Records:   {report.total_records:>8}")
        print(f"  Total Size:      {report.total_size_bytes / 1024 / 1024:.1f} MB")
        print(f"  Average Score:   {report.overall_avg_score:>8.1f}")

        # Score distribution
        print(f"\n{'SCORE DISTRIBUTION':^60}")
        print("-" * 60)
        for bucket, count in report.score_distribution.items():
            pct = count / report.total_files * 100 if report.total_files > 0 else 0
            bar = "█" * int(pct / 2)
            print(f"  {bucket:20} {count:>5} ({pct:>5.1f}%) {bar}")

        # By capture type
        print(f"\n{'BY CAPTURE TYPE':^60}")
        print("-" * 60)
        print(f"  {'Type':<15} {'Files':>6} {'Match%':>8} {'AvgScore':>10} {'Records':>8}")
        print("  " + "-" * 55)

        # Separate skipped and parseable
        skipped_types = []
        parseable_types = []
        for ct_name in sorted(report.by_capture_type.keys()):
            ct = report.by_capture_type[ct_name]
            if ct.is_skipped:
                skipped_types.append(ct_name)
            else:
                parseable_types.append(ct_name)

        for ct_name in parseable_types:
            ct = report.by_capture_type[ct_name]
            print(
                f"  {ct_name:<15} {ct.total_files:>6} {ct.match_rate:>7.1f}% {ct.avg_score:>10.1f} {ct.total_records:>8}")

        if skipped_types:
            print("  " + "-" * 55)
            print("  (Skipped - not parseable with TextFSM):")
            for ct_name in skipped_types:
                ct = report.by_capture_type[ct_name]
                print(f"  {ct_name:<15} {ct.total_files:>6}       -          -        -")

        # By vendor
        if report.by_vendor:
            print(f"\n{'BY VENDOR':^60}")
            print("-" * 60)
            print(f"  {'Vendor':<20} {'Files':>8} {'Match%':>10} {'AvgScore':>10}")
            print("  " + "-" * 50)

            for v_name in sorted(report.by_vendor.keys()):
                v = report.by_vendor[v_name]
                match_pct = v.matched_files / v.total_files * 100 if v.total_files > 0 else 0
                print(f"  {v_name:<20} {v.total_files:>8} {match_pct:>9.1f}% {v.avg_score:>10.1f}")

        # Template usage (top 10)
        print(f"\n{'TOP TEMPLATES MATCHED':^60}")
        print("-" * 60)

        all_templates = defaultdict(int)
        for ct in report.by_capture_type.values():
            for tpl, count in ct.templates_used.items():
                all_templates[tpl] += count

        for tpl, count in sorted(all_templates.items(), key=lambda x: -x[1])[:10]:
            print(f"  {tpl:<45} {count:>5}")

        # Coverage gaps (exclude skipped)
        gaps = [ct for ct in report.by_capture_type.values()
                if not ct.is_skipped and ct.match_rate < 50]
        if gaps:
            print(f"\n{'COVERAGE GAPS (< 50% match rate)':^60}")
            print("-" * 60)
            for ct in sorted(gaps, key=lambda x: x.match_rate):
                failed_sample = ct.failed_devices[:3]
                print(f"  {ct.capture_type}: {ct.match_rate:.1f}% match rate")
                print(f"    Failed devices: {', '.join(failed_sample)}{'...' if len(ct.failed_devices) > 3 else ''}")

        if report.errors:
            print(f"\n{'ERRORS ({len(report.errors)})':^60}")
            print("-" * 60)
            for err in report.errors[:5]:
                print(f"  {err}")
            if len(report.errors) > 5:
                print(f"  ... and {len(report.errors) - 5} more")

        print("\n" + "=" * 60)


# ============================================================================
# Export Functions
# ============================================================================

def export_json(report: AnalysisReport, output_path: Path):
    """Export report to JSON."""

    def convert(obj):
        if hasattr(obj, '__dict__'):
            return {k: convert(v) for k, v in obj.__dict__.items()}
        elif isinstance(obj, dict):
            return {k: convert(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [convert(i) for i in obj]
        elif isinstance(obj, Path):
            return str(obj)
        return obj

    data = convert(report)

    with open(output_path, 'w') as f:
        json.dump(data, f, indent=2)

    print(f"\nJSON report saved to: {output_path}")


def export_csv(report: AnalysisReport, output_path: Path):
    """Export file-level results to CSV."""
    with open(output_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            'filepath', 'device_name', 'capture_type', 'vendor', 'platform',
            'file_size', 'template_matched', 'score', 'score_bucket',
            'record_count', 'parse_time_ms', 'skipped', 'skip_reason', 'error'
        ])

        for r in report.file_results:
            writer.writerow([
                r.filepath, r.device_name, r.capture_type, r.vendor, r.platform,
                r.file_size, r.template_matched, f"{r.score:.2f}", r.score_bucket,
                r.record_count, f"{r.parse_time_ms:.2f}", r.skipped,
                r.skip_reason or '', r.error or ''
            ])

    print(f"\nCSV report saved to: {output_path}")


def export_extracted_json(report: AnalysisReport, output_dir: Path):
    """Export extracted data as JSON files per capture type.

    Creates:
        output_dir/
            arp.json
            routes.json
            version.json
            ...

    Each JSON file contains:
    {
        "capture_type": "arp",
        "extracted_at": "2025-12-22T...",
        "total_devices": 12,
        "total_records": 88,
        "devices": {
            "usa-leaf-3": {
                "template": "cisco_ios_show_arp",
                "score": 84.1,
                "records": [...]
            },
            ...
        }
    }
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Group by capture type
    by_type: Dict[str, Dict] = {}

    for result in report.file_results:
        if not result.parsed_records:
            continue

        ct = result.capture_type
        if ct not in by_type:
            by_type[ct] = {
                "capture_type": ct,
                "extracted_at": report.generated_at,
                "total_devices": 0,
                "total_records": 0,
                "devices": {}
            }

        by_type[ct]["devices"][result.device_name] = {
            "template": result.template_matched,
            "score": round(result.score, 2),
            "vendor": result.vendor,
            "platform": result.platform,
            "record_count": result.record_count,
            "records": result.parsed_records
        }
        by_type[ct]["total_devices"] += 1
        by_type[ct]["total_records"] += result.record_count

    # Write files
    files_written = []
    for ct, data in by_type.items():
        output_file = output_dir / f"{ct}.json"
        with open(output_file, 'w') as f:
            json.dump(data, f, indent=2)
        files_written.append((ct, data["total_devices"], data["total_records"]))

    print(f"\nExtracted data saved to: {output_dir}/")
    print(f"  {'Capture Type':<20} {'Devices':>8} {'Records':>10}")
    print(f"  {'-' * 42}")
    for ct, devices, records in sorted(files_written):
        print(f"  {ct:<20} {devices:>8} {records:>10}")


def export_extracted_sqlite(report: AnalysisReport, db_path: Path):
    """Export extracted data to SQLite database.

    Creates tables dynamically based on capture types:
        - extraction_meta (run metadata)
        - arp (device, template, score, + parsed fields)
        - routes (device, template, score, + parsed fields)
        - etc.
    """
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    # Meta table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS extraction_meta (
            id INTEGER PRIMARY KEY,
            extracted_at TEXT,
            collections_dir TEXT,
            tfsm_db TEXT,
            total_files INTEGER,
            total_matched INTEGER
        )
    """)

    cursor.execute("""
        INSERT INTO extraction_meta (extracted_at, collections_dir, tfsm_db, total_files, total_matched)
        VALUES (?, ?, ?, ?, ?)
    """, (report.generated_at, report.collections_dir, report.tfsm_db,
          report.total_files, report.total_matched))

    extraction_id = cursor.lastrowid

    # Group records by capture type and collect all field names
    by_type: Dict[str, List[Tuple[FileResult, Dict]]] = {}
    fields_by_type: Dict[str, set] = {}

    for result in report.file_results:
        if not result.parsed_records:
            continue

        ct = result.capture_type
        if ct not in by_type:
            by_type[ct] = []
            fields_by_type[ct] = set()

        for record in result.parsed_records:
            by_type[ct].append((result, record))
            fields_by_type[ct].update(record.keys())

    # Create tables and insert data
    tables_created = []

    for ct, records in by_type.items():
        # Sanitize table name
        table_name = f"extracted_{ct.replace('-', '_')}"
        fields = sorted(fields_by_type[ct])

        # Create table with dynamic columns
        field_defs = ", ".join(f'"{f}" TEXT' for f in fields)
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS "{table_name}" (
                id INTEGER PRIMARY KEY,
                extraction_id INTEGER,
                device_name TEXT,
                vendor TEXT,
                platform TEXT,
                template TEXT,
                score REAL,
                {field_defs},
                FOREIGN KEY (extraction_id) REFERENCES extraction_meta(id)
            )
        """)

        # Insert records
        for result, record in records:
            columns = ['extraction_id', 'device_name', 'vendor', 'platform', 'template', 'score'] + fields
            placeholders = ', '.join(['?'] * len(columns))

            # Serialize any complex values (lists, dicts) to JSON strings
            def serialize_value(v):
                if v is None:
                    return ''
                if isinstance(v, (list, dict)):
                    return json.dumps(v)
                return str(v)

            values = [
                         extraction_id, result.device_name, result.vendor,
                         result.platform, result.template_matched, result.score
                     ] + [serialize_value(record.get(f, '')) for f in fields]

            cursor.execute(f"""
                INSERT INTO "{table_name}" ({', '.join(f'"{c}"' for c in columns)})
                VALUES ({placeholders})
            """, values)

        tables_created.append((table_name, len(records)))

    conn.commit()
    conn.close()

    print(f"\nExtracted data saved to: {db_path}")
    print(f"  {'Table':<30} {'Records':>10}")
    print(f"  {'-' * 42}")
    for table, count in sorted(tables_created):
        print(f"  {table:<30} {count:>10}")


def export_extracted_ndjson(report: AnalysisReport, output_path: Path):
    """Export all extracted records as newline-delimited JSON.

    One JSON object per line, queryable with jq:
        cat extracted.ndjson | jq 'select(.capture_type == "arp")'
    """
    with open(output_path, 'w') as f:
        for result in report.file_results:
            if not result.parsed_records:
                continue

            for record in result.parsed_records:
                obj = {
                    "_meta": {
                        "device": result.device_name,
                        "capture_type": result.capture_type,
                        "vendor": result.vendor,
                        "platform": result.platform,
                        "template": result.template_matched,
                        "score": round(result.score, 2)
                    },
                    **record
                }
                f.write(json.dumps(obj) + "\n")

    total_records = sum(r.record_count for r in report.file_results if r.parsed_records)
    print(f"\nNDJSON export saved to: {output_path} ({total_records} records)")


def purge_failed_files(
        report: AnalysisReport,
        score_threshold: float = 0.0,
        dry_run: bool = True,
        collector_db: Optional[Path] = None
) -> Dict[str, Any]:
    """
    Purge files that failed to parse.

    Args:
        report: Analysis report with file results
        score_threshold: Delete files with score <= this value (default 0 = no match only)
        dry_run: If True, just report what would be deleted
        collector_db: Path to collector.db for cleaning up captures table

    Returns:
        Dict with 'deleted' and 'skipped' lists
    """
    to_delete = []
    skipped_unparseable = []
    skipped_above_threshold = []

    for result in report.file_results:
        # Skip files that are in non-parseable categories
        if result.skipped:
            skipped_unparseable.append(result.filepath)
            continue

        # Skip files above the threshold
        if result.score > score_threshold:
            skipped_above_threshold.append(result.filepath)
            continue

        # This file failed to parse and should be purged
        to_delete.append(result)

    # Group by capture type for reporting
    by_type: Dict[str, List[FileResult]] = {}
    for result in to_delete:
        ct = result.capture_type
        if ct not in by_type:
            by_type[ct] = []
        by_type[ct].append(result)

    # Report
    print(f"\n{'=' * 60}")
    print(f"PURGE {'(DRY RUN)' if dry_run else 'RESULTS'}")
    print(f"{'=' * 60}")
    print(f"  Score threshold: <= {score_threshold}")
    print(f"  Files to purge: {len(to_delete)}")
    print(f"  Skipped (non-parseable type): {len(skipped_unparseable)}")
    print(f"  Skipped (above threshold): {len(skipped_above_threshold)}")

    # Check if collector.db exists for DB cleanup
    db_cleanup_available = collector_db and collector_db.exists()
    if db_cleanup_available:
        print(f"  Database cleanup: {collector_db}")
    else:
        print(f"  Database cleanup: Not available (collector.db not found)")

    if to_delete:
        print(f"\n  {'Capture Type':<20} {'Files':>8} {'Size':>12}")
        print(f"  {'-' * 44}")

        total_size = 0
        for ct in sorted(by_type.keys()):
            files = by_type[ct]
            size = sum(r.file_size for r in files)
            total_size += size
            print(f"  {ct:<20} {len(files):>8} {size / 1024:>10.1f} KB")

        print(f"  {'-' * 44}")
        print(f"  {'TOTAL':<20} {len(to_delete):>8} {total_size / 1024:>10.1f} KB")

        if not dry_run:
            # Actually delete the files
            deleted_files = []
            deleted_db_records = 0
            errors = []

            for result in to_delete:
                try:
                    Path(result.filepath).unlink()
                    deleted_files.append(result.filepath)
                except Exception as e:
                    errors.append(f"{result.filepath}: {e}")

            print(f"\n  ✓ Deleted {len(deleted_files)} files")

            # Clean up database records
            if db_cleanup_available and deleted_files:
                try:
                    conn = sqlite3.connect(str(collector_db))
                    cursor = conn.cursor()

                    # Try matching by filepath first
                    for filepath in deleted_files:
                        cursor.execute(
                            "DELETE FROM captures WHERE filepath = ?",
                            (filepath,)
                        )
                        deleted_db_records += cursor.rowcount

                    # If no matches by filepath, try by device_name + capture_type
                    if deleted_db_records == 0:
                        for result in to_delete:
                            if result.filepath in deleted_files:
                                cursor.execute(
                                    "DELETE FROM captures WHERE device_name = ? AND capture_type = ?",
                                    (result.device_name, result.capture_type)
                                )
                                deleted_db_records += cursor.rowcount

                    conn.commit()
                    conn.close()

                    if deleted_db_records > 0:
                        print(f"  ✓ Removed {deleted_db_records} records from captures table")
                    else:
                        print(f"  ℹ No matching records found in captures table")
                except Exception as e:
                    errors.append(f"Database cleanup failed: {e}")

            if errors:
                print(f"  ✗ {len(errors)} errors:")
                for err in errors[:5]:
                    print(f"    {err}")
                if len(errors) > 5:
                    print(f"    ... and {len(errors) - 5} more")

            return {'deleted': deleted_files, 'db_records_removed': deleted_db_records, 'errors': errors}
        else:
            print(f"\n  (Dry run - no files deleted. Use --purge without --purge-dry-run to delete)")

            # Show sample of what would be deleted
            print(f"\n  Sample files to delete:")
            for result in to_delete[:10]:
                print(f"    {result.capture_type}/{Path(result.filepath).name} (score: {result.score:.1f})")
            if len(to_delete) > 10:
                print(f"    ... and {len(to_delete) - 10} more")

            # Preview DB cleanup - match by device_name and capture_type for more reliable matching
            if db_cleanup_available:
                try:
                    conn = sqlite3.connect(str(collector_db))
                    cursor = conn.cursor()

                    # First check what's in captures table
                    cursor.execute("SELECT COUNT(*) FROM captures")
                    total_captures = cursor.fetchone()[0]

                    # Try matching by filepath first
                    filepaths = [r.filepath for r in to_delete]
                    placeholders = ','.join(['?' for _ in filepaths])
                    cursor.execute(
                        f"SELECT COUNT(*) FROM captures WHERE filepath IN ({placeholders})",
                        filepaths
                    )
                    db_count_by_path = cursor.fetchone()[0]

                    # Also try matching by device_name + capture_type
                    db_count_by_device = 0
                    for result in to_delete:
                        cursor.execute(
                            "SELECT COUNT(*) FROM captures WHERE device_name = ? AND capture_type = ?",
                            (result.device_name, result.capture_type)
                        )
                        db_count_by_device += cursor.fetchone()[0]

                    conn.close()

                    print(f"\n  Database status:")
                    print(f"    Total captures records: {total_captures}")
                    print(f"    Matches by filepath: {db_count_by_path}")
                    print(f"    Matches by device+type: {db_count_by_device}")

                    if total_captures == 0:
                        print(f"    (captures table is empty - collection may not have saved records)")
                except Exception as e:
                    print(f"\n  Could not preview DB cleanup: {e}")
    else:
        print(f"\n  No files to purge.")

    print(f"{'=' * 60}")

    return {'would_delete': [r.filepath for r in to_delete], 'skipped': skipped_unparseable}


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Analyze TextFSM template coverage across collected device outputs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Analysis
  %(prog)s                          # Full analysis with console output
  %(prog)s --json report.json       # Export detailed JSON report
  %(prog)s --csv results.csv        # Export per-file CSV
  %(prog)s --type arp               # Analyze only ARP captures
  %(prog)s --type config --verbose  # Verbose analysis of configs
  %(prog)s --limit 100              # Quick test with first 100 files

  # Extraction
  %(prog)s --extract                      # Extract to ./extracted/*.json
  %(prog)s --extract-dir ./parsed         # Custom output directory
  %(prog)s --extract-db network.db        # SQLite with tables per capture type
  %(prog)s --extract-ndjson all.ndjson    # One JSON object per line
  %(prog)s --extract --min-score 75       # Only high-confidence matches

  # Purge failed files
  %(prog)s --purge-dry-run                # Preview what would be deleted
  %(prog)s --purge                        # Delete all score=0 files
  %(prog)s --purge --purge-below 25       # Delete files with score <= 25

  # Query extracted NDJSON
  cat all.ndjson | jq 'select(._meta.capture_type == "arp")'
  cat all.ndjson | jq 'select(.ADDRESS | startswith("10."))'
        """
    )

    parser.add_argument(
        '--collections-dir', '-d',
        type=Path,
        default=DEFAULT_COLLECTIONS_DIR,
        help=f"Collections directory (default: {DEFAULT_COLLECTIONS_DIR})"
    )

    parser.add_argument(
        '--tfsm-db', '-t',
        type=Path,
        default=DEFAULT_TFSM_DB,
        help=f"TextFSM templates database (default: {DEFAULT_TFSM_DB})"
    )

    parser.add_argument(
        '--dcim-db',
        type=Path,
        default=DEFAULT_DCIM_DB,
        help=f"DCIM database for device info (default: {DEFAULT_DCIM_DB})"
    )

    parser.add_argument(
        '--collector-db',
        type=Path,
        default=DEFAULT_COLLECTOR_DB,
        help=f"Collector database for captures cleanup (default: {DEFAULT_COLLECTOR_DB})"
    )

    parser.add_argument(
        '--type', '-T',
        dest='capture_type',
        help="Analyze only this capture type (e.g., arp, mac, config)"
    )

    parser.add_argument(
        '--json', '-j',
        type=Path,
        dest='json_output',
        help="Export detailed report to JSON file"
    )

    parser.add_argument(
        '--csv', '-c',
        type=Path,
        dest='csv_output',
        help="Export per-file results to CSV"
    )

    parser.add_argument(
        '--limit', '-l',
        type=int,
        help="Limit analysis to N files (for testing)"
    )

    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help="Show per-file analysis details"
    )

    parser.add_argument(
        '--quiet', '-q',
        action='store_true',
        help="Minimal output (summary only)"
    )

    # Extraction options
    parser.add_argument(
        '--extract', '-e',
        action='store_true',
        help="Extract parsed data from matched templates"
    )

    parser.add_argument(
        '--extract-dir',
        type=Path,
        help="Output directory for extracted JSON files (default: ./extracted/)"
    )

    parser.add_argument(
        '--extract-db',
        type=Path,
        help="Export extracted data to SQLite database"
    )

    parser.add_argument(
        '--extract-ndjson',
        type=Path,
        help="Export all records as newline-delimited JSON"
    )

    parser.add_argument(
        '--min-score',
        type=float,
        default=50.0,
        help="Minimum score to include in extraction (default: 50)"
    )

    # Purge options
    parser.add_argument(
        '--purge',
        action='store_true',
        help="Delete files that failed to parse (score=0), excluding known non-parseable types"
    )

    parser.add_argument(
        '--purge-below',
        type=float,
        default=0.0,
        help="Purge files with score below this threshold (default: 0 = only no-match)"
    )

    parser.add_argument(
        '--purge-dry-run',
        action='store_true',
        help="Show what would be purged without actually deleting"
    )

    args = parser.parse_args()

    # Validate paths
    if not args.collections_dir.exists():
        print(f"Error: Collections directory not found: {args.collections_dir}")
        sys.exit(1)

    if not args.tfsm_db.exists():
        print(f"Error: TextFSM database not found: {args.tfsm_db}")
        sys.exit(1)

    if not TEXTFSM_AVAILABLE:
        print("Error: textfsm not installed. Run: pip install textfsm")
        sys.exit(1)

    # Run analysis
    analyzer = CoverageAnalyzer(
        collections_dir=args.collections_dir,
        tfsm_db=args.tfsm_db,
        dcim_db=args.dcim_db,
        verbose=args.verbose
    )

    # Enable extraction if any extract option is set
    do_extract = args.extract or args.extract_dir or args.extract_db or args.extract_ndjson

    report = analyzer.analyze_all(
        capture_type_filter=args.capture_type,
        limit=args.limit,
        extract=do_extract,
        min_score_for_extract=args.min_score
    )

    # Output
    if not args.quiet:
        analyzer.print_report(report)
    else:
        print(
            f"\nFiles: {report.total_files}, Matched: {report.total_matched} ({report.overall_match_rate:.1f}%), Avg Score: {report.overall_avg_score:.1f}")

    if args.json_output:
        export_json(report, args.json_output)

    if args.csv_output:
        export_csv(report, args.csv_output)

    # Extraction exports
    if do_extract:
        extracted_count = sum(1 for r in report.file_results if r.parsed_records)
        total_records = sum(r.record_count for r in report.file_results if r.parsed_records)
        print(f"\nExtraction: {extracted_count} files, {total_records} records (min_score >= {args.min_score})")

        if args.extract_dir:
            export_extracted_json(report, args.extract_dir)
        elif args.extract and not args.extract_db and not args.extract_ndjson:
            # Default to ./extracted/ if just --extract
            export_extracted_json(report, Path("./extracted"))

        if args.extract_db:
            export_extracted_sqlite(report, args.extract_db)

        if args.extract_ndjson:
            export_extracted_ndjson(report, args.extract_ndjson)

    # Purge failed files if requested
    if args.purge or args.purge_dry_run:
        purge_failed_files(
            report,
            score_threshold=args.purge_below,
            dry_run=args.purge_dry_run or not args.purge,
            collector_db=args.collector_db
        )

    # Exit code: 0 if >50% match rate, 1 otherwise
    sys.exit(0 if report.overall_match_rate >= 50 else 1)


if __name__ == '__main__':
    main()