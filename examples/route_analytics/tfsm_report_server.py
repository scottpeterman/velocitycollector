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
    python tfsm_report_server.py --json report.json       # Load pre-generated report (fast!)

Workflow for large datasets:
    # Generate report once (slow)
    python tfsm_coverage_analyzer.py --json report.json --extract
    
    # View report instantly (fast)
    python tfsm_report_server.py --json report.json
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
import csv

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
# JSON Report Loading
# ============================================================================

class LoadedFileResult:
    """Reconstructed FileResult from JSON."""
    def __init__(self, data: dict):
        self.filepath = data.get('filepath', '')
        self.device_name = data.get('device_name', '')
        self.capture_type = data.get('capture_type', '')
        self.vendor = data.get('vendor', '')
        self.platform = data.get('platform', '')
        self.file_size = data.get('file_size', 0)
        self.template_matched = data.get('template_matched')
        self.score = data.get('score', 0.0)
        self.score_bucket = data.get('score_bucket', '0')
        self.record_count = data.get('record_count', 0)
        self.parse_time_ms = data.get('parse_time_ms', 0.0)
        self.error = data.get('error')
        self.skipped = data.get('skipped', False)
        self.skip_reason = data.get('skip_reason')
        self.parsed_records = data.get('parsed_records')


class LoadedCaptureTypeStats:
    """Reconstructed CaptureTypeStats from JSON."""
    def __init__(self, data: dict):
        self.capture_type = data.get('capture_type', '')
        self.total_files = data.get('total_files', 0)
        self.matched_files = data.get('matched_files', 0)
        self.total_records = data.get('total_records', 0)
        self.avg_score = data.get('avg_score', 0.0)
        self.min_score = data.get('min_score', 0.0)
        self.max_score = data.get('max_score', 0.0)
        self.score_distribution = data.get('score_distribution', {})
        self.templates_used = data.get('templates_used', {})
        self.failed_devices = data.get('failed_devices', [])
    
    @property
    def parseable_files(self) -> int:
        return self.total_files - self.score_distribution.get("skipped", 0)
    
    @property
    def match_rate(self) -> float:
        return (self.matched_files / self.parseable_files * 100) if self.parseable_files > 0 else 0.0
    
    @property
    def is_skipped(self) -> bool:
        return self.score_distribution.get("skipped", 0) == self.total_files


class LoadedVendorStats:
    """Reconstructed VendorStats from JSON."""
    def __init__(self, data: dict):
        self.vendor = data.get('vendor', '')
        self.total_files = data.get('total_files', 0)
        self.matched_files = data.get('matched_files', 0)
        self.avg_score = data.get('avg_score', 0.0)


class LoadedReport:
    """Reconstructed AnalysisReport from JSON."""
    def __init__(self, data: dict):
        self.generated_at = data.get('generated_at', datetime.now().isoformat())
        self.analysis_time_seconds = data.get('analysis_time_seconds', 0.0)
        self.collections_dir = data.get('collections_dir', '')
        self.tfsm_db = data.get('tfsm_db', '')
        self.template_count = data.get('template_count', 0)
        
        # Summary stats
        self.total_files = data.get('total_files', 0)
        self.total_size_bytes = data.get('total_size_bytes', 0)
        self.total_matched = data.get('total_matched', 0)
        self.total_skipped = data.get('total_skipped', 0)
        self.total_records = data.get('total_records', 0)
        self.overall_match_rate = data.get('overall_match_rate', 0.0)
        self.overall_avg_score = data.get('overall_avg_score', 0.0)
        self.score_distribution = data.get('score_distribution', {})
        
        # File results
        self.file_results = [
            LoadedFileResult(fr) for fr in data.get('file_results', [])
        ]
        
        # Capture type stats - the JSON uses 'by_capture_type'
        by_ct_data = data.get('by_capture_type', data.get('capture_type_stats', {}))
        self.by_capture_type = {
            k: LoadedCaptureTypeStats(v) 
            for k, v in by_ct_data.items()
        }
        
        # Vendor stats - the JSON uses 'by_vendor'
        by_vendor_data = data.get('by_vendor', data.get('vendor_stats', {}))
        self.by_vendor = {
            k: LoadedVendorStats(v)
            for k, v in by_vendor_data.items()
        }


def load_report_from_json(json_path: Path) -> LoadedReport:
    """Load a pre-generated report from JSON file."""
    with open(json_path, 'r') as f:
        data = json.load(f)
    return LoadedReport(data)


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
            cursor: pointer;
        }}
        th:hover {{
            background: #e9ecef;
        }}
        tr:hover {{ background: #f8f9fa; }}
        .filter-row td {{
            background: #f0f4f8;
            padding: 8px;
        }}
        .filter-row input {{
            width: 100%;
            padding: 6px 8px;
            border: 1px solid #ddd;
            border-radius: 4px;
            font-size: 12px;
        }}
        .filter-row input:focus {{
            outline: none;
            border-color: #0066cc;
            box-shadow: 0 0 0 2px rgba(0,102,204,0.2);
        }}
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
            align-items: center;
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
        .btn {{
            background: #0066cc;
            color: white;
            border: none;
            padding: 8px 16px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 14px;
            text-decoration: none;
            display: inline-block;
        }}
        .btn:hover {{ background: #0052a3; }}
        .btn-secondary {{
            background: #6c757d;
        }}
        .btn-secondary:hover {{ background: #545b62; }}
        .btn-success {{
            background: #28a745;
        }}
        .btn-success:hover {{ background: #1e7e34; }}
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
        .table-controls {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 10px;
            flex-wrap: wrap;
            gap: 10px;
        }}
        .row-count {{
            color: #666;
            font-size: 0.9em;
        }}
        .export-buttons {{
            display: flex;
            gap: 8px;
        }}
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
        if (rows[i].classList.contains('filter-row')) continue;
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
    updateVisibleCount(tableId);
}

function sortTable(tableId, colIndex) {
    const table = document.getElementById(tableId);
    const tbody = table.tBodies[0];
    const rows = Array.from(tbody.querySelectorAll('tr:not(.filter-row)'));
    const isNumeric = rows.every(r => {
        const text = r.cells[colIndex]?.textContent?.replace('%', '') || '';
        return !isNaN(parseFloat(text)) || text === '-';
    });
    
    // Toggle sort direction
    const currentDir = table.dataset.sortDir === 'asc' ? 'desc' : 'asc';
    table.dataset.sortDir = currentDir;
    table.dataset.sortCol = colIndex;
    
    rows.sort((a, b) => {
        let aVal = a.cells[colIndex]?.textContent?.replace('%', '') || '';
        let bVal = b.cells[colIndex]?.textContent?.replace('%', '') || '';
        
        if (aVal === '-') aVal = '-999999';
        if (bVal === '-') bVal = '-999999';
        
        let result;
        if (isNumeric) {
            result = parseFloat(aVal) - parseFloat(bVal);
        } else {
            result = aVal.localeCompare(bVal);
        }
        return currentDir === 'asc' ? result : -result;
    });
    
    rows.forEach(row => tbody.appendChild(row));
    
    // Update header indicators
    table.querySelectorAll('th').forEach((th, idx) => {
        th.textContent = th.textContent.replace(/ [▲▼]$/, '');
        if (idx === colIndex) {
            th.textContent += currentDir === 'asc' ? ' ▲' : ' ▼';
        }
    });
}

// Column-specific filtering
function applyColumnFilters(tableId) {
    const table = document.getElementById(tableId);
    const filterRow = table.querySelector('.filter-row');
    if (!filterRow) return;
    
    const filters = filterRow.querySelectorAll('input');
    const tbody = table.tBodies[0];
    const rows = tbody.querySelectorAll('tr:not(.filter-row)');
    
    rows.forEach(row => {
        let visible = true;
        filters.forEach((filter, idx) => {
            const cell = row.cells[idx];
            if (!cell) return;
            const cellText = cell.textContent.toLowerCase();
            const filterVal = filter.value.toLowerCase().trim();
            
            if (filterVal && !cellText.includes(filterVal)) {
                visible = false;
            }
        });
        row.style.display = visible ? '' : 'none';
    });
    
    updateVisibleCount(tableId);
}

function updateVisibleCount(tableId) {
    const table = document.getElementById(tableId);
    const tbody = table.tBodies[0];
    const rows = tbody.querySelectorAll('tr:not(.filter-row)');
    const visible = Array.from(rows).filter(r => r.style.display !== 'none').length;
    const counter = document.getElementById(tableId + '-count');
    if (counter) {
        counter.textContent = visible + ' of ' + rows.length + ' rows';
    }
}

function clearColumnFilters(tableId) {
    const table = document.getElementById(tableId);
    const filterRow = table.querySelector('.filter-row');
    if (!filterRow) return;
    
    filterRow.querySelectorAll('input').forEach(f => {
        f.value = '';
    });
    applyColumnFilters(tableId);
}

function exportTableToCSV(tableId, filename) {
    const table = document.getElementById(tableId);
    const rows = table.querySelectorAll('tr');
    const csvRows = [];
    
    rows.forEach((row, rowIdx) => {
        // Skip filter row
        if (row.classList.contains('filter-row')) return;
        // Skip hidden rows (filtered out) except header
        if (rowIdx > 0 && row.style.display === 'none') return;
        
        const cols = row.querySelectorAll('th, td');
        const rowData = [];
        cols.forEach(col => {
            let text = col.textContent.trim();
            // Remove sort indicators from headers
            text = text.replace(/ [▲▼]$/, '').replace(' ▾', '');
            // Escape quotes and wrap in quotes if needed
            if (text.includes('"')) {
                text = text.replace(/"/g, '""');
            }
            if (text.includes(',') || text.includes('"') || text.includes('\\n')) {
                text = '"' + text + '"';
            }
            rowData.push(text);
        });
        csvRows.push(rowData.join(','));
    });
    
    const blob = new Blob([csvRows.join('\\n')], {type: 'text/csv;charset=utf-8;'});
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = filename || 'export.csv';
    link.click();
    URL.revokeObjectURL(link.href);
}

// Initialize column filters on page load
document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('table[id]').forEach(table => {
        updateVisibleCount(table.id);
    });
});
</script>
</body>
</html>
"""


# ============================================================================
# Page Generators
# ============================================================================

class ReportGenerator:
    """Generate HTML pages from analysis report."""
    
    def __init__(self, report):
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
            <div class="table-controls">
                <div class="row-count" id="ct-table-count"></div>
                <div class="export-buttons">
                    <button class="btn btn-secondary" onclick="clearColumnFilters('ct-table')">Clear Filters</button>
                    <button class="btn btn-success" onclick="exportTableToCSV('ct-table', 'capture_types.csv')">Export CSV</button>
                </div>
            </div>
            <table id="ct-table">
            <thead>
                <tr>
                    <th onclick="sortTable('ct-table', 0)">Type ▾</th>
                    <th onclick="sortTable('ct-table', 1)">Files ▾</th>
                    <th onclick="sortTable('ct-table', 2)">Match% ▾</th>
                    <th onclick="sortTable('ct-table', 3)">Avg Score ▾</th>
                    <th onclick="sortTable('ct-table', 4)">Records ▾</th>
                    <th onclick="sortTable('ct-table', 5)">Status ▾</th>
                    <th>Data</th>
                </tr>
                <tr class="filter-row">
                    <td><input type="text" placeholder="Filter..." oninput="applyColumnFilters('ct-table')"></td>
                    <td><input type="text" placeholder="Filter..." oninput="applyColumnFilters('ct-table')"></td>
                    <td><input type="text" placeholder="Filter..." oninput="applyColumnFilters('ct-table')"></td>
                    <td><input type="text" placeholder="Filter..." oninput="applyColumnFilters('ct-table')"></td>
                    <td><input type="text" placeholder="Filter..." oninput="applyColumnFilters('ct-table')"></td>
                    <td><input type="text" placeholder="Filter..." oninput="applyColumnFilters('ct-table')"></td>
                    <td></td>
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
                        <td>Skipped</td>
                        <td>-</td>
                    </tr>
                ''')
            else:
                status = "Excellent" if ct.avg_score >= 75 else "Good" if ct.avg_score >= 50 else "Fair" if ct.avg_score >= 25 else "Poor" if ct.avg_score > 0 else "No Match"
                badge = self.score_badge(ct.avg_score) if ct.match_rate > 0 else '<span class="badge badge-danger">No Match</span>'
                data_link = f'<a class="data-link" href="/data/{ct_name}">View →</a>' if ct.total_records > 0 else '-'
                html_parts.append(f'''
                    <tr>
                        <td><strong>{ct_name}</strong></td>
                        <td>{ct.total_files}</td>
                        <td class="{self.score_class(ct.match_rate)}">{ct.match_rate:.1f}%</td>
                        <td class="{self.score_class(ct.avg_score)}">{ct.avg_score:.1f}</td>
                        <td>{ct.total_records}</td>
                        <td>{status}</td>
                        <td>{data_link}</td>
                    </tr>
                ''')
        
        html_parts.append('</tbody></table></div>')
        
        # By vendor table
        html_parts.append('''
            <div class="card">
            <h2>By Vendor</h2>
            <div class="table-controls">
                <div class="row-count" id="vendor-table-count"></div>
                <div class="export-buttons">
                    <button class="btn btn-secondary" onclick="clearColumnFilters('vendor-table')">Clear Filters</button>
                    <button class="btn btn-success" onclick="exportTableToCSV('vendor-table', 'vendors.csv')">Export CSV</button>
                </div>
            </div>
            <table id="vendor-table">
            <thead>
                <tr>
                    <th onclick="sortTable('vendor-table', 0)">Vendor ▾</th>
                    <th onclick="sortTable('vendor-table', 1)">Files ▾</th>
                    <th onclick="sortTable('vendor-table', 2)">Match% ▾</th>
                    <th onclick="sortTable('vendor-table', 3)">Avg Score ▾</th>
                </tr>
                <tr class="filter-row">
                    <td><input type="text" placeholder="Filter..." oninput="applyColumnFilters('vendor-table')"></td>
                    <td><input type="text" placeholder="Filter..." oninput="applyColumnFilters('vendor-table')"></td>
                    <td><input type="text" placeholder="Filter..." oninput="applyColumnFilters('vendor-table')"></td>
                    <td><input type="text" placeholder="Filter..." oninput="applyColumnFilters('vendor-table')"></td>
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
        html_parts.append('<div class="card"><h2>Top Templates Matched</h2><table><thead><tr><th>Template</th><th>Count</th></tr></thead><tbody>')
        
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
                        <a class="data-link" href="/api/data/{ct_name}">JSON</a> |
                        <a class="data-link" href="/api/data/{ct_name}?format=csv">CSV</a>
                    </td>
                </tr>
            ''')
        
        if not types_with_data:
            html_parts.append('<tr><td colspan="4" class="empty-state">No extracted data available. Run with --extract flag.</td></tr>')
        
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
            f'<a href="/api/data/{capture_type}?format=csv">📥 CSV</a>',
            '</div>',
            f'<h1>{capture_type}</h1>',
            f'<p>{len(all_records)} records from {len(set(r["_device"] for r in all_records))} devices</p>',
            '<div class="card">',
            '<div class="table-controls">',
            f'<div class="row-count" id="data-table-count"></div>',
            '<div class="export-buttons">',
            '<button class="btn btn-secondary" onclick="clearColumnFilters(\'data-table\')">Clear Filters</button>',
            f'<button class="btn btn-success" onclick="exportTableToCSV(\'data-table\', \'{capture_type}.csv\')">Export CSV</button>',
            '</div>',
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
        for i, f in enumerate(fields):
            label = header_labels.get(f, f)
            html_parts.append(f'<th onclick="sortTable(\'data-table\', {i})">{html.escape(label)} ▾</th>')
        
        html_parts.append('</tr>')
        
        # Filter row
        html_parts.append('<tr class="filter-row">')
        for f in fields:
            html_parts.append('<td><input type="text" placeholder="Filter..." oninput="applyColumnFilters(\'data-table\')"></td>')
        html_parts.append('</tr>')
        
        html_parts.append('</thead><tbody>')
        
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
    
    def generate_api_data_csv(self, capture_type: str) -> str:
        """Generate CSV API response for capture type data."""
        r = self.report
        
        # Collect all records
        all_records = []
        all_fields = set()
        
        for result in r.file_results:
            if result.capture_type == capture_type and result.parsed_records:
                for record in result.parsed_records:
                    all_fields.update(record.keys())
                    all_records.append({
                        'Device': result.device_name,
                        'Vendor': result.vendor or '',
                        'Score': result.score,
                        **record
                    })
        
        if not all_records:
            return ""
        
        # Build field list with standard fields first
        fields = ['Device', 'Vendor', 'Score'] + sorted(f for f in all_fields)
        
        # Generate CSV
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        for record in all_records:
            writer.writerow(record)
        
        return output.getvalue()


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
    
    def send_csv(self, content: str, filename: str, status: int = 200):
        self.send_response(status)
        self.send_header('Content-Type', 'text/csv; charset=utf-8')
        self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        self.send_header('Content-Length', len(content.encode()))
        self.end_headers()
        self.wfile.write(content.encode())
    
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        
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
                fmt = query.get('format', ['json'])[0]
                if fmt == 'csv':
                    csv_data = self.generator.generate_api_data_csv(capture_type)
                    self.send_csv(csv_data, f'{capture_type}.csv')
                else:
                    self.send_json(self.generator.generate_api_data(capture_type))
            
            else:
                self.send_html('<h1>404 Not Found</h1>', 404)
        
        except Exception as e:
            self.send_html(f'<h1>500 Error</h1><pre>{html.escape(str(e))}</pre>', 500)


def run_server(report, port: int = 8080, open_browser: bool = True):
    """Run the HTTP server."""
    ReportHandler.generator = ReportGenerator(report)
    
    with socketserver.TCPServer(("", port), ReportHandler) as httpd:
        url = f"http://localhost:{port}"
        print(f"\n{'='*60}")
        print(f"TextFSM Coverage Report Server")
        print(f"{'='*60}")
        print(f"  URL: {url}")
        print(f"  Report: {url}/")
        print(f"  Data: {url}/data")
        print(f"  API: {url}/api/report")
        print(f"{'='*60}")
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
        description="Serve TextFSM coverage report as a web interface",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run fresh analysis and serve (slow for large datasets)
  %(prog)s --tfsm-db path/to/templates.db

  # Load pre-generated report (instant!)
  %(prog)s --json report.json

Workflow for large datasets:
  # Step 1: Generate report once
  python tfsm_coverage_analyzer.py --json report.json --extract
  
  # Step 2: View instantly, as many times as you want
  python tfsm_report_server.py --json report.json
        """
    )
    
    parser.add_argument(
        '--json', '-j',
        type=Path,
        help="Load pre-generated JSON report (skips analysis)"
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
    
    # Load from JSON or run fresh analysis
    if args.json:
        # Fast path: load pre-generated report
        if not args.json.exists():
            print(f"Error: JSON report not found: {args.json}")
            sys.exit(1)
        
        print(f"Loading report from {args.json}...")
        report = load_report_from_json(args.json)
        print(f"Loaded: {report.total_files} files, {report.total_matched} matched, {report.total_records} records")
    else:
        # Slow path: run fresh analysis
        tfsm_db = args.tfsm_db or find_tfsm_db()
        
        if not args.collections_dir.exists():
            print(f"Error: Collections directory not found: {args.collections_dir}")
            sys.exit(1)
        
        if not tfsm_db.exists():
            print(f"Error: TextFSM database not found: {tfsm_db}")
            sys.exit(1)
        
        print(f"Analyzing collections in {args.collections_dir}...")
        print(f"Using TextFSM DB: {tfsm_db}")
        print(f"(Tip: Use --json to load a pre-generated report for instant startup)")
        
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