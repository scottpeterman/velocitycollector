#!/usr/bin/env python3
"""
TextFSM Coverage Report Server

Lightweight HTTP server for browsing coverage reports and extracted data.
No external dependencies beyond stdlib + textfsm.

Usage:
    python tfsm_report_server.py                          # Analyze and serve on :8080
    python tfsm_report_server.py --port 9000              # Custom port
    python tfsm_report_server.py --tfsm-db path/to/db     # Custom template DB
    python tfsm_report_server.py --no-browser             # Don't auto-open browser
"""

import argparse
import html
import http.server
import json
import socketserver
import sys
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
from urllib.parse import parse_qs, urlparse
import io

# Import the analyzer (assumes it's in the same directory or installed)
try:
    from tfsm_coverage_analyzer import (
        CoverageAnalyzer, AnalysisReport, FileResult,
        DEFAULT_COLLECTIONS_DIR, DEFAULT_DCIM_DB, find_tfsm_db
    )
except ImportError:
    # Inline minimal version if analyzer not found
    print("Warning: tfsm_coverage_analyzer not found, using inline version")
    from pathlib import Path

    DEFAULT_COLLECTIONS_DIR = Path.home() / ".vcollector" / "collections"
    DEFAULT_DCIM_DB = Path.home() / ".vcollector" / "dcim.db"


    def find_tfsm_db():
        return Path.home() / ".vcollector" / "tfsm_templates.db"

# ============================================================================
# HTML Templates
# ============================================================================

HTML_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        * {{ box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            margin: 0;
            padding: 20px;
            background: #f5f5f5;
            color: #333;
            line-height: 1.5;
        }}
        .container {{
            max-width: 1400px;
            margin: 0 auto;
        }}
        h1 {{
            color: #1a1a1a;
            border-bottom: 3px solid #0066cc;
            padding-bottom: 10px;
            margin-bottom: 20px;
        }}
        h2 {{
            color: #333;
            margin-top: 30px;
            border-bottom: 1px solid #ddd;
            padding-bottom: 8px;
        }}
        .nav {{
            background: #fff;
            padding: 15px;
            border-radius: 8px;
            margin-bottom: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }}
        .nav a {{
            color: #0066cc;
            text-decoration: none;
            margin-right: 20px;
        }}
        .nav a:hover {{ text-decoration: underline; }}
        .card {{
            background: #fff;
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }}
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }}
        .stat-card {{
            background: #fff;
            border-radius: 8px;
            padding: 15px;
            text-align: center;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }}
        .stat-value {{
            font-size: 2em;
            font-weight: bold;
            color: #0066cc;
        }}
        .stat-label {{
            color: #666;
            font-size: 0.9em;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            background: #fff;
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }}
        th, td {{
            padding: 12px 15px;
            text-align: left;
            border-bottom: 1px solid #eee;
        }}
        th {{
            background: #f8f9fa;
            font-weight: 600;
            color: #333;
            position: sticky;
            top: 0;
        }}
        tr:hover {{ background: #f8f9fa; }}
        .score-excellent {{ color: #28a745; font-weight: bold; }}
        .score-good {{ color: #5cb85c; }}
        .score-fair {{ color: #f0ad4e; }}
        .score-poor {{ color: #d9534f; }}
        .score-zero {{ color: #999; }}
        .badge {{
            display: inline-block;
            padding: 3px 8px;
            border-radius: 12px;
            font-size: 0.85em;
            font-weight: 500;
        }}
        .badge-success {{ background: #d4edda; color: #155724; }}
        .badge-warning {{ background: #fff3cd; color: #856404; }}
        .badge-danger {{ background: #f8d7da; color: #721c24; }}
        .badge-secondary {{ background: #e9ecef; color: #495057; }}
        .progress-bar {{
            height: 20px;
            background: #e9ecef;
            border-radius: 4px;
            overflow: hidden;
        }}
        .progress-fill {{
            height: 100%;
            background: #0066cc;
            transition: width 0.3s;
        }}
        .filter-bar {{
            margin-bottom: 15px;
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
        }}
        .filter-bar input, .filter-bar select {{
            padding: 8px 12px;
            border: 1px solid #ddd;
            border-radius: 4px;
            font-size: 14px;
        }}
        .filter-bar input {{ flex: 1; min-width: 200px; }}
        .json-view {{
            background: #f8f9fa;
            border: 1px solid #e9ecef;
            border-radius: 4px;
            padding: 15px;
            overflow-x: auto;
            font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
            font-size: 13px;
            white-space: pre-wrap;
        }}
        .refresh-btn {{
            background: #0066cc;
            color: white;
            border: none;
            padding: 10px 20px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 14px;
        }}
        .refresh-btn:hover {{ background: #0052a3; }}
        .timestamp {{
            color: #666;
            font-size: 0.9em;
        }}
        .empty-state {{
            text-align: center;
            padding: 40px;
            color: #666;
        }}
        a.data-link {{
            color: #0066cc;
            text-decoration: none;
        }}
        a.data-link:hover {{ text-decoration: underline; }}
    </style>
</head>
<body>
<div class="container">
"""

HTML_FOOT = """
</div>
<script>
function filterTable(inputId, tableId) {
    const filter = document.getElementById(inputId).value.toLowerCase();
    const table = document.getElementById(tableId);
    const rows = table.getElementsByTagName('tr');

    for (let i = 1; i < rows.length; i++) {
        const cells = rows[i].getElementsByTagName('td');
        let match = false;
        for (let j = 0; j < cells.length; j++) {
            if (cells[j].textContent.toLowerCase().includes(filter)) {
                match = true;
                break;
            }
        }
        rows[i].style.display = match ? '' : 'none';
    }
}

function sortTable(tableId, colIndex) {
    const table = document.getElementById(tableId);
    const rows = Array.from(table.rows).slice(1);
    const isNumeric = rows.every(r => !isNaN(parseFloat(r.cells[colIndex]?.textContent)));

    rows.sort((a, b) => {
        const aVal = a.cells[colIndex]?.textContent || '';
        const bVal = b.cells[colIndex]?.textContent || '';
        if (isNumeric) return parseFloat(bVal) - parseFloat(aVal);
        return aVal.localeCompare(bVal);
    });

    rows.forEach(row => table.tBodies[0].appendChild(row));
}
</script>
</body>
</html>
"""


# ============================================================================
# Page Generators
# ============================================================================

class ReportGenerator:
    """Generate HTML pages from analysis report."""

    def __init__(self, report: AnalysisReport):
        self.report = report

    def score_class(self, score: float) -> str:
        if score >= 75:
            return "score-excellent"
        elif score >= 50:
            return "score-good"
        elif score >= 25:
            return "score-fair"
        elif score > 0:
            return "score-poor"
        return "score-zero"

    def score_badge(self, score: float) -> str:
        if score >= 75:
            return '<span class="badge badge-success">Excellent</span>'
        elif score >= 50:
            return '<span class="badge badge-success">Good</span>'
        elif score >= 25:
            return '<span class="badge badge-warning">Fair</span>'
        elif score > 0:
            return '<span class="badge badge-danger">Poor</span>'
        return '<span class="badge badge-secondary">No Match</span>'

    def generate_index(self) -> str:
        """Generate main report page."""
        r = self.report
        skipped = r.score_distribution.get("skipped", 0)
        parseable = r.total_files - skipped

        html_parts = [
            HTML_HEAD.format(title="TextFSM Coverage Report"),
            '<div class="nav">',
            '<a href="/">📊 Report</a>',
            '<a href="/data">📁 Browse Data</a>',
            '<a href="/api/report">📄 JSON API</a>',
            f'<span class="timestamp">Generated: {r.generated_at}</span>',
            '</div>',

            '<h1>TextFSM Coverage Report</h1>',

            # Stats cards
            '<div class="stats-grid">',
            f'<div class="stat-card"><div class="stat-value">{r.total_files}</div><div class="stat-label">Total Files</div></div>',
            f'<div class="stat-card"><div class="stat-value">{parseable}</div><div class="stat-label">Parseable</div></div>',
            f'<div class="stat-card"><div class="stat-value">{r.total_matched}</div><div class="stat-label">Matched</div></div>',
            f'<div class="stat-card"><div class="stat-value">{r.overall_match_rate:.1f}%</div><div class="stat-label">Match Rate</div></div>',
            f'<div class="stat-card"><div class="stat-value">{r.overall_avg_score:.1f}</div><div class="stat-label">Avg Score</div></div>',
            f'<div class="stat-card"><div class="stat-value">{r.total_records}</div><div class="stat-label">Records</div></div>',
            '</div>',

            # Score distribution
            '<div class="card">',
            '<h2>Score Distribution</h2>',
        ]

        for bucket, count in r.score_distribution.items():
            if bucket == "skipped":
                continue
            pct = (count / parseable * 100) if parseable > 0 else 0
            html_parts.append(f'''
                <div style="margin-bottom: 10px;">
                    <div style="display: flex; justify-content: space-between; margin-bottom: 3px;">
                        <span>{bucket}</span>
                        <span>{count} ({pct:.1f}%)</span>
                    </div>
                    <div class="progress-bar">
                        <div class="progress-fill" style="width: {pct}%"></div>
                    </div>
                </div>
            ''')

        html_parts.append('</div>')

        # By capture type table
        html_parts.append('''
            <div class="card">
            <h2>By Capture Type</h2>
            <div class="filter-bar">
                <input type="text" id="ct-filter" placeholder="Filter..." onkeyup="filterTable('ct-filter', 'ct-table')">
            </div>
            <table id="ct-table">
            <thead>
                <tr>
                    <th onclick="sortTable('ct-table', 0)" style="cursor:pointer">Type ▾</th>
                    <th onclick="sortTable('ct-table', 1)" style="cursor:pointer">Files ▾</th>
                    <th onclick="sortTable('ct-table', 2)" style="cursor:pointer">Match% ▾</th>
                    <th onclick="sortTable('ct-table', 3)" style="cursor:pointer">Avg Score ▾</th>
                    <th onclick="sortTable('ct-table', 4)" style="cursor:pointer">Records ▾</th>
                    <th>Status</th>
                    <th>Data</th>
                </tr>
            </thead>
            <tbody>
        ''')

        for ct_name in sorted(r.by_capture_type.keys()):
            ct = r.by_capture_type[ct_name]
            if ct.is_skipped:
                html_parts.append(f'''
                    <tr>
                        <td>{ct_name}</td>
                        <td>{ct.total_files}</td>
                        <td>-</td>
                        <td>-</td>
                        <td>-</td>
                        <td><span class="badge badge-secondary">Skipped</span></td>
                        <td>-</td>
                    </tr>
                ''')
            else:
                badge = self.score_badge(
                    ct.avg_score) if ct.match_rate > 0 else '<span class="badge badge-danger">No Match</span>'
                data_link = f'<a class="data-link" href="/data/{ct_name}">View →</a>' if ct.total_records > 0 else '-'
                html_parts.append(f'''
                    <tr>
                        <td><strong>{ct_name}</strong></td>
                        <td>{ct.total_files}</td>
                        <td class="{self.score_class(ct.match_rate)}">{ct.match_rate:.1f}%</td>
                        <td class="{self.score_class(ct.avg_score)}">{ct.avg_score:.1f}</td>
                        <td>{ct.total_records}</td>
                        <td>{badge}</td>
                        <td>{data_link}</td>
                    </tr>
                ''')

        html_parts.append('</tbody></table></div>')

        # By vendor table
        html_parts.append('''
            <div class="card">
            <h2>By Vendor</h2>
            <table>
            <thead>
                <tr>
                    <th>Vendor</th>
                    <th>Files</th>
                    <th>Match%</th>
                    <th>Avg Score</th>
                </tr>
            </thead>
            <tbody>
        ''')

        for v_name in sorted(r.by_vendor.keys()):
            v = r.by_vendor[v_name]
            match_pct = v.matched_files / v.total_files * 100 if v.total_files > 0 else 0
            html_parts.append(f'''
                <tr>
                    <td><strong>{v_name}</strong></td>
                    <td>{v.total_files}</td>
                    <td class="{self.score_class(match_pct)}">{match_pct:.1f}%</td>
                    <td class="{self.score_class(v.avg_score)}">{v.avg_score:.1f}</td>
                </tr>
            ''')

        html_parts.append('</tbody></table></div>')

        # Top templates
        html_parts.append(
            '<div class="card"><h2>Top Templates Matched</h2><table><thead><tr><th>Template</th><th>Count</th></tr></thead><tbody>')

        from collections import defaultdict
        all_templates = defaultdict(int)
        for ct in r.by_capture_type.values():
            for tpl, count in ct.templates_used.items():
                all_templates[tpl] += count

        for tpl, count in sorted(all_templates.items(), key=lambda x: -x[1])[:15]:
            html_parts.append(f'<tr><td><code>{tpl}</code></td><td>{count}</td></tr>')

        html_parts.append('</tbody></table></div>')

        html_parts.append(HTML_FOOT)
        return ''.join(html_parts)

    def generate_data_index(self) -> str:
        """Generate data browsing index page."""
        r = self.report

        # Collect capture types with data
        types_with_data = []
        for result in r.file_results:
            if result.parsed_records and result.capture_type not in [t[0] for t in types_with_data]:
                ct = r.by_capture_type.get(result.capture_type)
                if ct:
                    types_with_data.append((result.capture_type, ct.total_records, ct.matched_files))

        html_parts = [
            HTML_HEAD.format(title="Browse Extracted Data"),
            '<div class="nav">',
            '<a href="/">📊 Report</a>',
            '<a href="/data">📁 Browse Data</a>',
            '</div>',
            '<h1>Browse Extracted Data</h1>',
            '<div class="card">',
            '<table>',
            '<thead><tr><th>Capture Type</th><th>Devices</th><th>Records</th><th>Actions</th></tr></thead>',
            '<tbody>'
        ]

        for ct_name, records, devices in sorted(types_with_data):
            html_parts.append(f'''
                <tr>
                    <td><strong>{ct_name}</strong></td>
                    <td>{devices}</td>
                    <td>{records}</td>
                    <td>
                        <a class="data-link" href="/data/{ct_name}">Table View</a> |
                        <a class="data-link" href="/api/data/{ct_name}">JSON</a>
                    </td>
                </tr>
            ''')

        if not types_with_data:
            html_parts.append(
                '<tr><td colspan="4" class="empty-state">No extracted data available. Run with --extract flag.</td></tr>')

        html_parts.append('</tbody></table></div>')
        html_parts.append(HTML_FOOT)
        return ''.join(html_parts)

    def generate_data_table(self, capture_type: str) -> str:
        """Generate table view for a specific capture type."""
        r = self.report

        # Collect all records for this capture type
        all_records = []
        all_fields = set()

        for result in r.file_results:
            if result.capture_type == capture_type and result.parsed_records:
                for record in result.parsed_records:
                    all_fields.update(record.keys())
                    all_records.append({
                        '_device': result.device_name,
                        '_vendor': result.vendor or '',
                        '_score': result.score,
                        **record
                    })

        if not all_records:
            return self._generate_empty_data_page(capture_type)

        # Sort fields for consistent display
        fields = ['_device', '_vendor', '_score'] + sorted(f for f in all_fields if not f.startswith('_'))

        html_parts = [
            HTML_HEAD.format(title=f"{capture_type} Data"),
            '<div class="nav">',
            '<a href="/">📊 Report</a>',
            '<a href="/data">📁 Browse Data</a>',
            f'<a href="/api/data/{capture_type}">📄 JSON</a>',
            '</div>',
            f'<h1>{capture_type}</h1>',
            f'<p>{len(all_records)} records from {len(set(r["_device"] for r in all_records))} devices</p>',
            '<div class="card">',
            '<div class="filter-bar">',
            f'<input type="text" id="data-filter" placeholder="Filter records..." onkeyup="filterTable(\'data-filter\', \'data-table\')">',
            '</div>',
            '<div style="overflow-x: auto;">',
            '<table id="data-table">',
            '<thead><tr>'
        ]

        # Headers
        header_labels = {
            '_device': 'Device',
            '_vendor': 'Vendor',
            '_score': 'Score'
        }
        for f in fields:
            label = header_labels.get(f, f)
            html_parts.append(f'<th>{html.escape(label)}</th>')

        html_parts.append('</tr></thead><tbody>')

        # Rows
        for record in all_records:
            html_parts.append('<tr>')
            for f in fields:
                val = record.get(f, '')
                if f == '_score':
                    val = f'{val:.1f}'
                html_parts.append(f'<td>{html.escape(str(val))}</td>')
            html_parts.append('</tr>')

        html_parts.append('</tbody></table></div></div>')
        html_parts.append(HTML_FOOT)
        return ''.join(html_parts)

    def _generate_empty_data_page(self, capture_type: str) -> str:
        return HTML_HEAD.format(title=f"{capture_type} Data") + f'''
            <div class="nav">
            <a href="/">📊 Report</a>
            <a href="/data">📁 Browse Data</a>
            </div>
            <h1>{capture_type}</h1>
            <div class="card empty-state">
                <p>No extracted data for this capture type.</p>
                <p>This could mean:</p>
                <ul style="text-align:left; display:inline-block;">
                    <li>No templates matched with score ≥ 50</li>
                    <li>The capture type is not parseable</li>
                    <li>Run the analyzer with --extract flag</li>
                </ul>
            </div>
        ''' + HTML_FOOT

    def generate_api_report(self) -> str:
        """Generate JSON API response for report."""
        r = self.report
        return json.dumps({
            "generated_at": r.generated_at,
            "total_files": r.total_files,
            "total_matched": r.total_matched,
            "match_rate": r.overall_match_rate,
            "avg_score": r.overall_avg_score,
            "total_records": r.total_records,
            "score_distribution": r.score_distribution,
            "by_capture_type": {
                name: {
                    "files": ct.total_files,
                    "matched": ct.matched_files,
                    "match_rate": ct.match_rate,
                    "avg_score": ct.avg_score,
                    "records": ct.total_records,
                    "templates": ct.templates_used
                }
                for name, ct in r.by_capture_type.items()
            },
            "by_vendor": {
                name: {
                    "files": v.total_files,
                    "matched": v.matched_files,
                    "avg_score": v.avg_score
                }
                for name, v in r.by_vendor.items()
            }
        }, indent=2)

    def generate_api_data(self, capture_type: str) -> str:
        """Generate JSON API response for capture type data."""
        r = self.report

        devices = {}
        for result in r.file_results:
            if result.capture_type == capture_type and result.parsed_records:
                devices[result.device_name] = {
                    "template": result.template_matched,
                    "score": result.score,
                    "vendor": result.vendor,
                    "platform": result.platform,
                    "records": result.parsed_records
                }

        return json.dumps({
            "capture_type": capture_type,
            "total_devices": len(devices),
            "total_records": sum(len(d["records"]) for d in devices.values()),
            "devices": devices
        }, indent=2)


# ============================================================================
# HTTP Server
# ============================================================================

class ReportHandler(http.server.BaseHTTPRequestHandler):
    """HTTP request handler for report server."""

    generator: ReportGenerator = None  # Set by server

    def log_message(self, format, *args):
        # Quieter logging
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {args[0]}")

    def send_html(self, content: str, status: int = 200):
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', len(content.encode()))
        self.end_headers()
        self.wfile.write(content.encode())

    def send_json(self, content: str, status: int = 200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', len(content.encode()))
        self.end_headers()
        self.wfile.write(content.encode())

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            if path == '/' or path == '/index.html':
                self.send_html(self.generator.generate_index())

            elif path == '/data' or path == '/data/':
                self.send_html(self.generator.generate_data_index())

            elif path.startswith('/data/'):
                capture_type = path[6:]  # Remove '/data/'
                self.send_html(self.generator.generate_data_table(capture_type))

            elif path == '/api/report':
                self.send_json(self.generator.generate_api_report())

            elif path.startswith('/api/data/'):
                capture_type = path[10:]  # Remove '/api/data/'
                self.send_json(self.generator.generate_api_data(capture_type))

            else:
                self.send_html('<h1>404 Not Found</h1>', 404)

        except Exception as e:
            self.send_html(f'<h1>500 Error</h1><pre>{html.escape(str(e))}</pre>', 500)


def run_server(report: AnalysisReport, port: int = 8080, open_browser: bool = True):
    """Run the HTTP server."""
    ReportHandler.generator = ReportGenerator(report)

    with socketserver.TCPServer(("", port), ReportHandler) as httpd:
        url = f"http://localhost:{port}"
        print(f"\n{'=' * 60}")
        print(f"TextFSM Coverage Report Server")
        print(f"{'=' * 60}")
        print(f"  URL: {url}")
        print(f"  Report: {url}/")
        print(f"  Data: {url}/data")
        print(f"  API: {url}/api/report")
        print(f"{'=' * 60}")
        print("Press Ctrl+C to stop\n")

        if open_browser:
            threading.Timer(0.5, lambda: webbrowser.open(url)).start()

        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down...")


# ============================================================================
# Main
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Serve TextFSM coverage report as a web interface"
    )

    parser.add_argument(
        '--port', '-p',
        type=int,
        default=8080,
        help="Port to serve on (default: 8080)"
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
        default=None,
        help="TextFSM templates database"
    )

    parser.add_argument(
        '--dcim-db',
        type=Path,
        default=DEFAULT_DCIM_DB,
        help=f"DCIM database (default: {DEFAULT_DCIM_DB})"
    )

    parser.add_argument(
        '--min-score',
        type=float,
        default=50.0,
        help="Minimum score for extraction (default: 50)"
    )

    parser.add_argument(
        '--no-browser',
        action='store_true',
        help="Don't auto-open browser"
    )

    args = parser.parse_args()

    # Find template DB
    tfsm_db = args.tfsm_db or find_tfsm_db()

    if not args.collections_dir.exists():
        print(f"Error: Collections directory not found: {args.collections_dir}")
        sys.exit(1)

    if not tfsm_db.exists():
        print(f"Error: TextFSM database not found: {tfsm_db}")
        sys.exit(1)

    print(f"Analyzing collections in {args.collections_dir}...")
    print(f"Using TextFSM DB: {tfsm_db}")

    # Run analysis with extraction
    analyzer = CoverageAnalyzer(
        collections_dir=args.collections_dir,
        tfsm_db=tfsm_db,
        dcim_db=args.dcim_db,
        verbose=False
    )

    report = analyzer.analyze_all(
        extract=True,
        min_score_for_extract=args.min_score
    )

    print(f"Analysis complete: {report.total_files} files, {report.total_matched} matched")

    # Start server
    run_server(report, port=args.port, open_browser=not args.no_browser)


if __name__ == '__main__':
    main()