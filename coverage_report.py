#!/usr/bin/env python3
"""
Collection Coverage Report Generator for VelocityCollector.

Generates an HTML report showing:
- Summary by capture type (device counts, file sizes, timestamps)
- Device × Capture Type matrix for gap analysis
- Per-site breakdowns

Usage:
    python coverage_report.py --assets-db ~/.velocitycmdb/data/assets.db \
                              --collections ~/.vcollector/collections \
                              --output coverage.html
"""

import sqlite3
import argparse
import os
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Set, Tuple, Optional
from collections import defaultdict
import json


class CoverageReport:
    def __init__(
            self,
            assets_db: Path,
            collections_dir: Path,
    ):
        self.assets_db = assets_db
        self.collections_dir = collections_dir
        self.devices: Dict[str, dict] = {}
        self.captures: Dict[str, Dict[str, dict]] = defaultdict(dict)  # capture_type -> {device_name: info}
        self.capture_types: Set[str] = set()

    def load_devices(self):
        """Load devices from assets.db."""
        conn = sqlite3.connect(self.assets_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Try v_device_status view first
        try:
            cursor.execute("""
                SELECT id, name, normalized_name, management_ip,
                       vendor_name, device_type_name, site_code, site_name, role_name
                FROM v_device_status
                ORDER BY site_code, name
            """)
        except sqlite3.OperationalError:
            # Fall back to devices table
            cursor.execute("""
                SELECT d.id, d.name, d.normalized_name, d.management_ip,
                       v.name as vendor_name, dt.name as device_type_name,
                       d.site_code, s.name as site_name, dr.name as role_name
                FROM devices d
                LEFT JOIN vendors v ON d.vendor_id = v.id
                LEFT JOIN device_types dt ON d.device_type_id = dt.id
                LEFT JOIN sites s ON d.site_code = s.code
                LEFT JOIN device_roles dr ON d.role_id = dr.id
                ORDER BY d.site_code, d.name
            """)

        for row in cursor.fetchall():
            device_name = row['normalized_name'] or row['name']
            self.devices[device_name] = {
                'id': row['id'],
                'name': row['name'],
                'normalized_name': device_name,
                'management_ip': row['management_ip'],
                'vendor': row['vendor_name'] or 'Unknown',
                'device_type': row['device_type_name'] or 'Unknown',
                'site_code': row['site_code'] or 'Unknown',
                'site_name': row['site_name'] or row['site_code'] or 'Unknown',
                'role': row['role_name'] or 'Unknown',
            }

        conn.close()
        print(f"Loaded {len(self.devices)} devices from assets.db")

    def scan_collections(self):
        """Scan collections directory for captured files."""
        if not self.collections_dir.exists():
            print(f"Warning: Collections directory not found: {self.collections_dir}")
            return

        for capture_dir in self.collections_dir.iterdir():
            if not capture_dir.is_dir():
                continue
            if capture_dir.name.startswith('.') or capture_dir.name == '~':
                continue

            capture_type = capture_dir.name
            self.capture_types.add(capture_type)

            for capture_file in capture_dir.glob('*.txt'):
                device_name = capture_file.stem
                stat = capture_file.stat()

                self.captures[capture_type][device_name] = {
                    'filepath': str(capture_file),
                    'filename': capture_file.name,
                    'size': stat.st_size,
                    'mtime': datetime.fromtimestamp(stat.st_mtime),
                }

        print(f"Found {len(self.capture_types)} capture types: {sorted(self.capture_types)}")
        total_captures = sum(len(v) for v in self.captures.values())
        print(f"Found {total_captures} capture files")

    def generate_report(self, output_path: Path):
        """Generate HTML coverage report."""
        self.load_devices()
        self.scan_collections()

        # Build summary stats
        summary = self._build_summary()

        # Build device matrix
        matrix = self._build_matrix()

        # Build site breakdown
        site_stats = self._build_site_stats()

        # Generate HTML
        html = self._render_html(summary, matrix, site_stats)

        output_path.write_text(html)
        print(f"Report written to: {output_path}")

    def _build_summary(self) -> List[dict]:
        """Build capture type summary stats."""
        summary = []

        for capture_type in sorted(self.capture_types):
            captures = self.captures[capture_type]

            # Count devices with this capture
            device_count = len(captures)

            # Devices in assets that have this capture
            matched = sum(1 for d in captures.keys() if d in self.devices)

            # Total and avg file size
            total_size = sum(c['size'] for c in captures.values())
            avg_size = total_size / device_count if device_count > 0 else 0

            # Latest capture time
            if captures:
                latest = max(c['mtime'] for c in captures.values())
            else:
                latest = None

            # Coverage percentage
            coverage_pct = (device_count / len(self.devices) * 100) if self.devices else 0

            summary.append({
                'capture_type': capture_type,
                'device_count': device_count,
                'matched_devices': matched,
                'total_devices': len(self.devices),
                'coverage_pct': coverage_pct,
                'total_size': total_size,
                'avg_size': avg_size,
                'latest': latest,
            })

        return summary

    def _build_matrix(self) -> List[dict]:
        """Build device × capture type matrix."""
        matrix = []

        for device_name, device_info in sorted(self.devices.items()):
            row = {
                'device': device_name,
                'site': device_info['site_code'],
                'vendor': device_info['vendor'],
                'mgmt_ip': device_info['management_ip'] or '',
                'model': device_info['device_type'],
                'captures': {},
            }

            for capture_type in sorted(self.capture_types):
                if device_name in self.captures[capture_type]:
                    capture = self.captures[capture_type][device_name]
                    row['captures'][capture_type] = {
                        'has': True,
                        'size': capture['size'],
                        'mtime': capture['mtime'],
                    }
                else:
                    row['captures'][capture_type] = {
                        'has': False,
                    }

            matrix.append(row)

        return matrix

    def _build_site_stats(self) -> Dict[str, dict]:
        """Build per-site statistics."""
        site_stats = defaultdict(lambda: {
            'device_count': 0,
            'capture_counts': defaultdict(int),
        })

        for device_name, device_info in self.devices.items():
            site = device_info['site_code']
            site_stats[site]['device_count'] += 1
            site_stats[site]['site_name'] = device_info['site_name']

            for capture_type in self.capture_types:
                if device_name in self.captures[capture_type]:
                    site_stats[site]['capture_counts'][capture_type] += 1

        return dict(site_stats)

    def _render_html(self, summary: List[dict], matrix: List[dict], site_stats: dict) -> str:
        """Render HTML report."""
        capture_types_sorted = sorted(self.capture_types)

        # Build summary table rows
        summary_rows = []
        for s in summary:
            latest_str = s['latest'].strftime('%Y-%m-%d %H:%M') if s['latest'] else 'Never'
            coverage_class = 'high' if s['coverage_pct'] >= 80 else 'medium' if s['coverage_pct'] >= 50 else 'low'

            summary_rows.append(f"""
                <tr>
                    <td><strong>{s['capture_type']}</strong></td>
                    <td>{s['device_count']}</td>
                    <td>{s['total_devices']}</td>
                    <td class="coverage-{coverage_class}">{s['coverage_pct']:.1f}%</td>
                    <td>{self._format_size(s['total_size'])}</td>
                    <td>{self._format_size(int(s['avg_size']))}</td>
                    <td>{latest_str}</td>
                </tr>
            """)

        # Build matrix header
        matrix_header = '<th class="sticky-col">Device</th><th>Site</th><th>Vendor</th><th>Mgmt IP</th><th>Model</th>'
        for ct in capture_types_sorted:
            # Abbreviate long capture type names
            abbrev = ct[:12] + '…' if len(ct) > 12 else ct
            matrix_header += f'<th class="capture-col" title="{ct}">{abbrev}</th>'

        # Build matrix rows
        matrix_rows = []
        for row in matrix:
            cells = f"""
                <td class="sticky-col"><strong>{row['device']}</strong></td>
                <td>{row['site']}</td>
                <td>{row['vendor']}</td>
                <td>{row['mgmt_ip']}</td>
                <td>{row['model']}</td>
            """

            for ct in capture_types_sorted:
                capture = row['captures'].get(ct, {'has': False})
                if capture['has']:
                    size_str = self._format_size(capture['size'])
                    mtime_str = capture['mtime'].strftime('%m/%d %H:%M')
                    cells += f'<td class="has-capture" title="{size_str} - {mtime_str}">✓</td>'
                else:
                    cells += '<td class="no-capture">—</td>'

            matrix_rows.append(f'<tr>{cells}</tr>')

        # Build site summary
        site_rows = []
        for site_code in sorted(site_stats.keys()):
            stats = site_stats[site_code]
            site_cells = f"""
                <td><strong>{site_code}</strong></td>
                <td>{stats.get('site_name', site_code)}</td>
                <td>{stats['device_count']}</td>
            """

            for ct in capture_types_sorted:
                count = stats['capture_counts'].get(ct, 0)
                total = stats['device_count']
                pct = (count / total * 100) if total > 0 else 0

                if pct >= 80:
                    cell_class = 'high'
                elif pct >= 50:
                    cell_class = 'medium'
                elif pct > 0:
                    cell_class = 'low'
                else:
                    cell_class = 'none'

                site_cells += f'<td class="coverage-{cell_class}">{count}/{total}</td>'

            site_rows.append(f'<tr>{site_cells}</tr>')

        # Site header
        site_header = '<th>Site Code</th><th>Site Name</th><th>Devices</th>'
        for ct in capture_types_sorted:
            abbrev = ct[:10] + '…' if len(ct) > 10 else ct
            site_header += f'<th class="capture-col" title="{ct}">{abbrev}</th>'

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>VelocityCollector Coverage Report</title>
    <style>
        :root {{
            --bg-color: #f5f7fa;
            --card-bg: #ffffff;
            --text-color: #2d3748;
            --text-muted: #718096;
            --border-color: #e2e8f0;
            --accent-blue: #3182ce;
            --accent-green: #38a169;
            --accent-yellow: #d69e2e;
            --accent-red: #e53e3e;
        }}

        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}

        body {{
            font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-color);
            line-height: 1.6;
            padding: 20px;
        }}

        .container {{
            max-width: 1800px;
            margin: 0 auto;
        }}

        h1 {{
            color: var(--accent-blue);
            margin-bottom: 10px;
            font-size: 2em;
        }}

        h2 {{
            color: var(--text-color);
            margin: 30px 0 15px 0;
            padding-bottom: 10px;
            border-bottom: 2px solid var(--border-color);
        }}

        .timestamp {{
            color: var(--text-muted);
            margin-bottom: 20px;
        }}

        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-bottom: 30px;
        }}

        .stat-card {{
            background: var(--card-bg);
            border-radius: 8px;
            padding: 20px;
            border: 1px solid var(--border-color);
        }}

        .stat-card .label {{
            color: var(--text-muted);
            font-size: 0.9em;
            text-transform: uppercase;
        }}

        .stat-card .value {{
            font-size: 2em;
            font-weight: bold;
            color: var(--accent-blue);
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            background: var(--card-bg);
            border-radius: 8px;
            overflow: hidden;
            margin-bottom: 30px;
        }}

        th, td {{
            padding: 10px 12px;
            text-align: left;
            border-bottom: 1px solid var(--border-color);
        }}

        th {{
            background: rgba(49, 130, 206, 0.1);
            color: var(--accent-blue);
            font-weight: 600;
            position: sticky;
            top: 0;
            z-index: 10;
        }}

        tr:hover {{
            background: rgba(0, 0, 0, 0.03);
        }}

        .coverage-high {{
            color: var(--accent-green);
            font-weight: bold;
        }}

        .coverage-medium {{
            color: var(--accent-yellow);
        }}

        .coverage-low {{
            color: var(--accent-red);
        }}

        .coverage-none {{
            color: var(--text-muted);
        }}

        .has-capture {{
            color: var(--accent-green);
            text-align: center;
            font-weight: bold;
        }}

        .no-capture {{
            color: var(--text-muted);
            text-align: center;
        }}

        .matrix-container {{
            overflow-x: auto;
            max-height: 600px;
            overflow-y: auto;
            border: 1px solid var(--border-color);
            border-radius: 8px;
        }}

        .matrix-container table {{
            margin-bottom: 0;
        }}

        .matrix-container th {{
            position: sticky;
            top: 0;
            background: var(--card-bg);
        }}

        .sticky-col {{
            position: sticky;
            left: 0;
            background: var(--card-bg);
            z-index: 5;
        }}

        th.sticky-col {{
            z-index: 15;
        }}

        .capture-col {{
            writing-mode: vertical-lr;
            text-orientation: mixed;
            transform: rotate(180deg);
            max-width: 40px;
            padding: 8px 4px;
            font-size: 0.85em;
        }}

        .filter-bar {{
            margin-bottom: 15px;
            display: flex;
            gap: 15px;
            flex-wrap: wrap;
        }}

        .filter-bar input, .filter-bar select {{
            padding: 8px 12px;
            border-radius: 4px;
            border: 1px solid var(--border-color);
            background: var(--card-bg);
            color: var(--text-color);
            font-size: 0.95em;
        }}

        .filter-bar input:focus, .filter-bar select:focus {{
            outline: none;
            border-color: var(--accent-blue);
        }}

        .legend {{
            display: flex;
            gap: 20px;
            margin-bottom: 15px;
            font-size: 0.9em;
        }}

        .legend-item {{
            display: flex;
            align-items: center;
            gap: 5px;
        }}

        .legend-dot {{
            width: 12px;
            height: 12px;
            border-radius: 50%;
        }}

        .legend-dot.green {{ background: var(--accent-green); }}
        .legend-dot.yellow {{ background: var(--accent-yellow); }}
        .legend-dot.red {{ background: var(--accent-red); }}
        .legend-dot.gray {{ background: var(--text-muted); }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 VelocityCollector Coverage Report</h1>
        <p class="timestamp">Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>

        <div class="stats-grid">
            <div class="stat-card">
                <div class="label">Total Devices</div>
                <div class="value">{len(self.devices)}</div>
            </div>
            <div class="stat-card">
                <div class="label">Capture Types</div>
                <div class="value">{len(self.capture_types)}</div>
            </div>
            <div class="stat-card">
                <div class="label">Total Captures</div>
                <div class="value">{sum(len(v) for v in self.captures.values())}</div>
            </div>
            <div class="stat-card">
                <div class="label">Sites</div>
                <div class="value">{len(site_stats)}</div>
            </div>
        </div>

        <h2>📋 Capture Type Summary</h2>
        <table>
            <thead>
                <tr>
                    <th>Capture Type</th>
                    <th>Devices</th>
                    <th>Total</th>
                    <th>Coverage</th>
                    <th>Total Size</th>
                    <th>Avg Size</th>
                    <th>Latest</th>
                </tr>
            </thead>
            <tbody>
                {''.join(summary_rows)}
            </tbody>
        </table>

        <h2>🏢 Site Coverage</h2>
        <table>
            <thead>
                <tr>{site_header}</tr>
            </thead>
            <tbody>
                {''.join(site_rows)}
            </tbody>
        </table>

        <h2>📱 Device × Capture Matrix</h2>

        <div class="legend">
            <div class="legend-item"><span class="legend-dot green"></span> Has capture</div>
            <div class="legend-item"><span class="legend-dot gray"></span> No capture (may be expected)</div>
        </div>

        <div class="filter-bar">
            <input type="text" id="deviceFilter" placeholder="Filter by device name..." onkeyup="filterMatrix()">
            <select id="siteFilter" onchange="filterMatrix()">
                <option value="">All Sites</option>
                {self._render_site_options(site_stats)}
            </select>
            <select id="vendorFilter" onchange="filterMatrix()">
                <option value="">All Vendors</option>
                {self._render_vendor_options()}
            </select>
        </div>

        <div class="matrix-container">
            <table id="matrixTable">
                <thead>
                    <tr>{matrix_header}</tr>
                </thead>
                <tbody>
                    {''.join(matrix_rows)}
                </tbody>
            </table>
        </div>
    </div>

    <script>
        function filterMatrix() {{
            const deviceFilter = document.getElementById('deviceFilter').value.toLowerCase();
            const siteFilter = document.getElementById('siteFilter').value;
            const vendorFilter = document.getElementById('vendorFilter').value;

            const rows = document.querySelectorAll('#matrixTable tbody tr');

            rows.forEach(row => {{
                const device = row.cells[0].textContent.toLowerCase();
                const site = row.cells[1].textContent;
                const vendor = row.cells[2].textContent;

                const matchDevice = device.includes(deviceFilter);
                const matchSite = !siteFilter || site === siteFilter;
                const matchVendor = !vendorFilter || vendor === vendorFilter;

                row.style.display = (matchDevice && matchSite && matchVendor) ? '' : 'none';
            }});
        }}
    </script>
</body>
</html>"""

        return html

    def _format_size(self, size: int) -> str:
        """Format file size for display."""
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        else:
            return f"{size / (1024 * 1024):.1f} MB"

    def _render_site_options(self, site_stats: dict) -> str:
        """Render site filter options."""
        options = []
        for site_code in sorted(site_stats.keys()):
            options.append(f'<option value="{site_code}">{site_code}</option>')
        return '\n'.join(options)

    def _render_vendor_options(self) -> str:
        """Render vendor filter options."""
        vendors = set(d['vendor'] for d in self.devices.values())
        options = []
        for vendor in sorted(vendors):
            options.append(f'<option value="{vendor}">{vendor}</option>')
        return '\n'.join(options)


def main():
    parser = argparse.ArgumentParser(
        description='Generate VelocityCollector coverage report',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --output coverage.html
  %(prog)s --assets-db ~/.velocitycmdb/data/assets.db --collections ~/.vcollector/collections
"""
    )
    parser.add_argument('--assets-db', default='~/.velocitycmdb/data/assets.db',
                        help='Path to VelocityCMDB assets database')
    parser.add_argument('--collections', default='~/.vcollector/collections',
                        help='Path to collections directory')
    parser.add_argument('--output', '-o', default='coverage_report.html',
                        help='Output HTML file path')

    args = parser.parse_args()

    assets_db = Path(args.assets_db).expanduser()
    collections_dir = Path(args.collections).expanduser()
    output_path = Path(args.output).expanduser()

    if not assets_db.exists():
        print(f"Error: Assets database not found: {assets_db}")
        return 1

    report = CoverageReport(
        assets_db=assets_db,
        collections_dir=collections_dir,
    )

    report.generate_report(output_path)
    return 0


if __name__ == '__main__':
    exit(main())