# VelocityCollector Route Analytics

A web-based routing table analysis tool for exploring collected route data. Part of the VelocityCollector examples suite.

[![PyPI version](https://img.shields.io/pypi/v/velocitycollector.svg)](https://pypi.org/project/velocitycollector/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

## Overview

Route Analytics transforms raw routing table output from VelocityCollector into an interactive browser for:

- **Prefix Discovery** — Browse all unique prefixes across your network with filtering by classification, protocol, address family, and device
- **IP Lookup** — Find which devices have routes to any IP address, showing directly connected vs learned routes with next-hop details
- **Route Analysis** — Dashboard with statistics on prefix distribution, protocol breakdown, IPv4/IPv6 coverage, and per-site metrics

## Screenshots

### Dashboard
![Dashboard](https://raw.githubusercontent.com/scottpeterman/velocitycollector/main/screenshots/route_table_analysis.png)
*Route table analysis with prefix counts, protocol breakdown, and IPv4/IPv6 distribution*

### Prefix Browser
![Browser](https://raw.githubusercontent.com/scottpeterman/velocitycollector/main/screenshots/route_table_browser.png)
*Browse and filter prefixes by classification, protocol, address family, and device*

### IP Lookup
![Lookup](https://raw.githubusercontent.com/scottpeterman/velocitycollector/main/screenshots/route_table_lookup.png)
*Find all devices with routes to a specific IP, showing connected vs learned routes*

## Quick Start

### 1. Collect Route Data

First, collect routing tables from your devices using VelocityCollector:
```bash
# Create a job for route collection (GUI or CLI)
vcollector run --job cisco-routes
```

This saves raw output to `~/.vcollector/collections/routes/`

### 2. Extract & Parse Routes

Use the coverage analyzer to extract structured route data from collections:
```bash
python tfsm_coverage_analyzer.py \
    --extract \
    --json extracted/routes.json \
    --collections-dir ~/.vcollector/collections/
```

This parses all route outputs using TextFSM templates and produces a consolidated JSON file with:
- Per-device route tables
- Normalized prefix format (CIDR notation)
- Protocol classification (OSPF, BGP, Static, Connected, Local)
- Next-hop information

### 3. Start the Route Browser

Launch the Flask web server:
```bash
python route_report_server.py --data extracted/routes.json --port 8080
```

Open http://127.0.0.1:8080 in your browser.

## CLI Reference

### Extract Routes
```bash
python tfsm_coverage_analyzer.py \
    --extract \
    --json <output.json> \
    --collections-dir <path>

Options:
    --extract           Extract and parse collected data
    --json <file>       Output JSON file path
    --collections-dir   Path to vcollector collections (default: ~/.vcollector/collections/)
    --capture-type      Filter by capture type (default: routes)
```

### Run Server
```bash
python route_report_server.py --data <routes.json> [options]

Options:
    --data, -d <file>   Path to extracted routes JSON (required)
    --host <addr>       Bind address (default: 127.0.0.1)
    --port, -p <port>   Port number (default: 5000)
    --debug             Enable Flask debug mode
    --preindex          Build lookup index on startup (faster first query)
```

## API Endpoints

The Flask app exposes a REST API for programmatic access:

### Summary

| Endpoint | Description |
|----------|-------------|
| `GET /api/summary` | Global statistics with IPv4/IPv6 breakdowns |
| `GET /api/summary/ipv4` | IPv4-specific statistics |
| `GET /api/summary/ipv6` | IPv6-specific statistics |

### Prefixes

| Endpoint | Description |
|----------|-------------|
| `GET /api/prefixes` | List prefixes with optional filtering |
| `GET /api/prefixes/<prefix>` | Details for a specific prefix |
| `GET /api/classifications` | Prefix classifications with counts |
| `GET /api/protocols` | Protocol distribution |
| `GET /api/prefix-lengths` | Prefix length distribution |

**Query Parameters for `/api/prefixes`:**
- `classification` — Filter by type (private, public, cgn, ula, etc.)
- `protocol` — Filter by protocol (OSPF, BGP, Static, Connected, Local)
- `device` — Filter by device name (partial match)
- `family` — Filter by address family (ipv4, ipv6, v4, v6)
- `limit` — Max results (default: 100)
- `offset` — Pagination offset

### IP Lookup

| Endpoint | Description |
|----------|-------------|
| `GET /api/lookup/<ip>` | Find prefixes containing this IP |
| `POST /api/lookup` | Bulk IP lookup (JSON body: `{"ips": [...]}`) |

### Sites

| Endpoint | Description |
|----------|-------------|
| `GET /api/sites` | All sites with route counts |
| `GET /api/sites/<name>` | Details for a specific site |
| `GET /api/sites/compare` | Compare IPv6 coverage across sites |

### Path Tracing (if path_tracer.py available)

| Endpoint | Description |
|----------|-------------|
| `GET /api/trace?source=X&dest=Y` | Trace path between IPs |
| `GET /api/trace/device/<ip>` | Find device owning an IP |

## Example API Usage
```bash
# Get summary statistics
curl http://localhost:8080/api/summary

# Browse private prefixes
curl "http://localhost:8080/api/prefixes?classification=private&limit=50"

# Find OSPF-learned routes
curl "http://localhost:8080/api/prefixes?protocol=OSPF"

# Lookup an IP address
curl http://localhost:8080/api/lookup/172.16.100.5

# Bulk IP lookup
curl -X POST http://localhost:8080/api/lookup \
    -H "Content-Type: application/json" \
    -d '{"ips": ["172.16.100.1", "10.0.0.1", "192.168.1.1"]}'

# Compare IPv6 readiness across sites
curl http://localhost:8080/api/sites/compare
```

## Data Format

### Input (from tfsm_coverage_analyzer.py)

The extracted routes JSON follows this structure:
```json
{
  "capture_type": "routes",
  "extracted_at": "2025-12-23T04:09:04.672986",
  "total_devices": 6,
  "total_records": 88,
  "devices": {
    "usa-rtr-1": {
      "template": "cisco_ios_show_ip_route",
      "score": 79.87,
      "vendor": "Cisco",
      "platform": "Cisco IOS",
      "record_count": 23,
      "records": [
        {
          "PROTOCOL": "O",
          "NETWORK": "172.16.10.0/24",
          "DISTANCE": "110",
          "METRIC": "11",
          "NEXTHOPIP": "172.16.1.6",
          "NEXTHOPIF": "GigabitEthernet0/2"
        }
      ]
    }
  }
}
```

### Analyzed Output

After route_analyzer.py processes the data:
```json
{
  "summary": {
    "total_devices": 6,
    "total_sites": 1,
    "unique_prefixes": 36,
    "unique_ipv4_prefixes": 36,
    "unique_ipv6_prefixes": 0,
    "by_protocol": {"OSPF": 51, "Connected": 16, "Local": 16, "Static": 5},
    "by_classification": {"private": 82, "public": 6}
  },
  "prefixes": {
    "private": [
      {
        "prefix": "172.16.10.0/24",
        "address_family": "ipv4",
        "protocol": "Connected",
        "devices": ["usa-leaf-1", "usa-leaf-2", "usa-leaf-3"],
        "connected_devices": ["usa-leaf-1", "usa-leaf-2", "usa-leaf-3"],
        "next_hops": []
      }
    ]
  },
  "sites": {}
}
```

## Use Cases

### Network Documentation
Generate an accurate view of your routing topology by examining which devices have routes to which prefixes.

### Troubleshooting
Quickly answer "who has a route to X?" when diagnosing connectivity issues.

### IPv6 Migration Planning
Use the IPv4/IPv6 breakdown and site comparison features to track dual-stack deployment progress.

### Change Validation
Compare route tables before and after network changes by extracting data at different times.

### Compliance Auditing
Identify unexpected routes, rogue static entries, or missing redundancy paths.

## File Structure
```
examples/route_analytics/
├── README.md                  # This file
├── route_report_server.py     # Flask web application
├── route_analyzer.py          # Route data analyzer
├── path_tracer.py             # Virtual path tracing (optional)
├── extracted/                 # Generated data files
│   └── routes.json
├── templates/                 # Jinja2 HTML templates
│   ├── index.html             # Dashboard
│   ├── browse.html            # Prefix browser
│   ├── lookup.html            # IP lookup
│   ├── sites.html             # Sites view
│   └── trace.html             # Path tracer
└── screenshots/
    ├── route_table_analysis.png
    ├── route_table_browser.png
    └── route_table_lookup.png
```

## Requirements

- Python 3.10+
- Flask
- VelocityCollector (for data collection)
- TextFSM templates for route parsing (included in vcollector)
```bash
pip install flask
```

## Troubleshooting

### "No data loaded"
Ensure the JSON file path is correct and contains valid extracted route data.

### "Template not found"
Make sure you have TextFSM templates for your device types. Import NTC templates via vcollector's TextFSM Tester.

### Empty results
Check that your route collection job captured output. Browse `~/.vcollector/collections/routes/` to verify files exist.

### IPv4Network serialization error
Update to the latest route_report_server.py which includes the custom JSON encoder for ipaddress objects.

## Related Tools

- **VelocityCollector** — The parent tool for network data collection
- **VelocityMaps** — Network topology discovery via LLDP/CDP
- **Smart Export** — GUI-based TextFSM parsing in vcollector

## License

GPLv3 License — Part of the VelocityCollector project

## Author

Scott Peterman — Network Automation Tooling