#!/usr/bin/env python3
"""
VCollector Route Browser (IPv6 Enhanced)

Flask app that serves pre-generated route analysis from route_analyzer.py
with full IPv4/IPv6 filtering and visibility for migration planning.

Usage:
    # First, generate the analysis:
    python route_analyzer.py ./extracted/routes.json --json analysis.json

    # Then run the app:
    python app.py --data analysis.json
    python app.py --data analysis.json --port 5001
"""

import argparse
import ipaddress
import json
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any

from flask import Flask, jsonify, request, render_template, abort
from flask.json.provider import DefaultJSONProvider


# Custom JSON provider to handle ipaddress objects
class NetworkJSONProvider(DefaultJSONProvider):
    """JSON provider that handles IPv4Network/IPv6Network objects."""

    def default(self, obj):
        if isinstance(obj, (ipaddress.IPv4Network, ipaddress.IPv6Network,
                            ipaddress.IPv4Address, ipaddress.IPv6Address)):
            return str(obj)
        return super().default(obj)


app = Flask(__name__)
app.json = NetworkJSONProvider(app)  # Use custom JSON provider

# Global data store - loaded once at startup
DATA = {
    'summary': {},
    'sites': {},
    'prefixes': {},
    'lookup_index': None,  # Built on first lookup or startup
    'tracer': None,  # PathTracer instance
}


# ============================================================================
# Data Loading & Indexing
# ============================================================================

def load_analysis(filepath: Path):
    """Load route data - handles both raw vcollector output and pre-analyzed JSON."""
    from route_analyzer import RouteAnalyzer

    with open(filepath) as f:
        data = json.load(f)

    # Detect format
    if 'devices' in data:
        # Raw vcollector extracted format - analyze it
        print(f"Detected raw vcollector format, analyzing...")
        analyzer = RouteAnalyzer(store_prefixes=True)
        analyzer.analyze_file(filepath)
        data = analyzer.to_dict()
        print(f"Analysis complete.")
    elif 'summary' not in data:
        print(f"Error: Unrecognized format in {filepath}")
        return

    DATA['summary'] = data.get('summary', {})
    DATA['sites'] = data.get('sites', {})
    DATA['prefixes'] = data.get('prefixes', {})
    DATA['filepath'] = str(filepath)
    DATA['lookup_index'] = None  # Reset index

    # Print summary with v4/v6 breakdown
    unique_v4 = DATA['summary'].get('unique_ipv4_prefixes', 0)
    unique_v6 = DATA['summary'].get('unique_ipv6_prefixes', 0)
    print(f"Loaded: {DATA['summary'].get('unique_prefixes', 0):,} unique prefixes")
    print(f"        IPv4: {unique_v4:,}  IPv6: {unique_v6:,}")
    print(f"        {DATA['summary'].get('total_devices', 0)} devices")
    print(f"        {DATA['summary'].get('total_sites', 0)} sites")

    # Initialize path tracer
    try:
        from path_tracer import PathTracer
        DATA['tracer'] = PathTracer(data)
        print(f"        Path tracer initialized")
    except ImportError:
        print(f"        Path tracer not available (path_tracer.py not found)")


def build_lookup_index():
    """
    Build prefix lookup index for IP searches.
    Groups prefixes by first octet (v4) or first 16 bits (v6) for faster lookup.

    NOTE: Uses tuple storage to avoid mutating source data (which breaks JSON serialization).
    """
    if DATA['lookup_index'] is not None:
        return DATA['lookup_index']

    print("Building lookup index...")
    index = {
        'v4': {},  # first_octet -> [(network, prefix_data, classification), ...]
        'v6': {},  # first_16bits -> [(network, prefix_data, classification), ...]
    }

    for classification, prefixes in DATA['prefixes'].items():
        for p in prefixes:
            prefix_str = p['prefix']
            try:
                net = ipaddress.ip_network(prefix_str, strict=False)

                # Store as tuple instead of mutating the prefix dict
                # This keeps DATA['prefixes'] JSON-serializable
                entry = (net, p, classification)

                if net.version == 4:
                    key = net.network_address.packed[0]
                    if key not in index['v4']:
                        index['v4'][key] = []
                    index['v4'][key].append(entry)
                else:
                    # First 16 bits for v6
                    key = int.from_bytes(net.network_address.packed[:2], 'big')
                    if key not in index['v6']:
                        index['v6'][key] = []
                    index['v6'][key].append(entry)
            except ValueError:
                continue

    DATA['lookup_index'] = index
    v4_count = sum(len(v) for v in index['v4'].values())
    v6_count = sum(len(v) for v in index['v6'].values())
    print(f"Index built: {v4_count:,} v4 prefixes, {v6_count:,} v6 prefixes")
    return index


def lookup_ip(ip_str: str) -> List[dict]:
    """
    Find all prefixes that contain this IP, sorted by prefix length (longest first).
    """
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return []

    index = build_lookup_index()
    matches = []

    if addr.version == 4:
        key = addr.packed[0]
        candidates = index['v4'].get(key, [])
    else:
        key = int.from_bytes(addr.packed[:2], 'big')
        candidates = index['v6'].get(key, [])

    # Unpack tuples: (network, prefix_dict, classification)
    for net, p, classification in candidates:
        if addr in net:
            protocol = p.get('protocol', 'unknown')
            connected_devices = p.get('connected_devices', [])
            matches.append({
                'prefix': p['prefix'],
                'prefix_len': net.prefixlen,
                'address_family': 'ipv4' if net.version == 4 else 'ipv6',
                'classification': classification,
                'protocol': protocol,
                'is_connected': len(connected_devices) > 0,
                'connected_devices': connected_devices,
                'vrf': p.get('vrf', 'default'),
                'devices': p.get('devices', []),
                'next_hops': p.get('next_hops', []),
            })

    # Sort by prefix length descending (longest/most specific first)
    return sorted(matches, key=lambda x: -x['prefix_len'])


# ============================================================================
# API Routes
# ============================================================================

@app.route('/api/summary')
def api_summary():
    """Get global route summary statistics with IPv4/IPv6 breakdowns."""
    return jsonify(DATA['summary'])


@app.route('/api/summary/ipv4')
def api_summary_v4():
    """Get IPv4-specific summary statistics."""
    summary = DATA['summary']
    return jsonify({
        'total_prefixes': summary.get('ipv4_prefixes', 0),
        'unique_prefixes': summary.get('unique_ipv4_prefixes', 0),
        'by_classification': summary.get('by_classification_v4', {}),
        'unique_by_classification': summary.get('unique_by_classification_v4', {}),
        'by_protocol': summary.get('by_protocol_v4', {}),
        'prefix_lengths': summary.get('prefix_lengths_v4', {}),
    })


@app.route('/api/summary/ipv6')
def api_summary_v6():
    """Get IPv6-specific summary statistics."""
    summary = DATA['summary']
    return jsonify({
        'total_prefixes': summary.get('ipv6_prefixes', 0),
        'unique_prefixes': summary.get('unique_ipv6_prefixes', 0),
        'by_classification': summary.get('by_classification_v6', {}),
        'unique_by_classification': summary.get('unique_by_classification_v6', {}),
        'by_protocol': summary.get('by_protocol_v6', {}),
        'prefix_lengths': summary.get('prefix_lengths_v6', {}),
    })


@app.route('/api/sites')
def api_sites():
    """Get all sites with route counts including IPv4/IPv6 breakdown."""
    return jsonify(DATA['sites'])


@app.route('/api/sites/<site_name>')
def api_site_detail(site_name: str):
    """Get details for a specific site with v4/v6 breakdown."""
    site = DATA['sites'].get(site_name)
    if not site:
        abort(404, description=f"Site '{site_name}' not found")
    return jsonify(site)


@app.route('/api/sites/compare')
def api_sites_compare():
    """
    Compare IPv6 coverage across sites - useful for migration planning.

    Returns sites sorted by IPv6 prefix count.
    """
    sites_data = []
    for name, site in DATA['sites'].items():
        v4 = site.get('ipv4_prefixes', 0)
        v6 = site.get('ipv6_prefixes', 0)
        total = site.get('total_prefixes', 0)
        sites_data.append({
            'site': name,
            'devices': len(site.get('devices', [])),
            'total_prefixes': total,
            'ipv4_prefixes': v4,
            'ipv6_prefixes': v6,
            'ipv6_pct': round(v6 / total * 100, 1) if total else 0,
            'by_classification_v6': site.get('by_classification_v6', {}),
            'by_protocol_v6': site.get('by_protocol_v6', {}),
        })

    # Sort by IPv6 count descending
    sites_data.sort(key=lambda x: -x['ipv6_prefixes'])

    return jsonify({
        'sites': sites_data,
        'total_sites': len(sites_data),
    })


@app.route('/api/classifications')
def api_classifications():
    """Get list of prefix classifications with counts and v4/v6 breakdown."""
    summary = DATA['summary']
    result = {}

    for cls, count in DATA['prefixes'].items():
        v4_unique = summary.get('unique_by_classification_v4', {}).get(cls, 0)
        v6_unique = summary.get('unique_by_classification_v6', {}).get(cls, 0)
        result[cls] = {
            'total': len(count),
            'ipv4': v4_unique,
            'ipv6': v6_unique,
        }

    return jsonify(result)


@app.route('/api/prefixes')
def api_prefixes():
    """
    Get prefixes with optional filtering including address family.

    Query params:
        classification: Filter by type (private, public, cgn, ula, etc.)
        protocol: Filter by protocol (BGP, OSPF, Static, etc.)
        device: Filter by device name (partial match)
        family: Filter by address family (ipv4, ipv6, v4, v6)
        limit: Max results (default 100)
        offset: Pagination offset
    """
    classification = request.args.get('classification')
    protocol = request.args.get('protocol')
    device = request.args.get('device')
    family = request.args.get('family')
    limit = request.args.get('limit', 100, type=int)
    offset = request.args.get('offset', 0, type=int)

    # Start with all or filtered by classification
    if classification:
        if classification not in DATA['prefixes']:
            return jsonify({'error': f'Unknown classification: {classification}',
                            'available': list(DATA['prefixes'].keys())}), 400
        results = list(DATA['prefixes'][classification])
    else:
        # Flatten all classifications
        results = []
        for prefixes in DATA['prefixes'].values():
            results.extend(prefixes)

    # Apply address family filter
    if family:
        family_lower = family.lower()
        if family_lower in ('ipv4', 'v4', '4'):
            results = [p for p in results if p.get('address_family') == 'ipv4']
        elif family_lower in ('ipv6', 'v6', '6'):
            results = [p for p in results if p.get('address_family') == 'ipv6']

    # Apply protocol filter
    if protocol:
        protocol_lower = protocol.lower()
        results = [p for p in results if p.get('protocol', '').lower() == protocol_lower]

    # Apply device filter
    if device:
        device_lower = device.lower()
        results = [p for p in results if any(device_lower in d.lower() for d in p.get('devices', []))]

    total = len(results)

    # Count v4/v6 in filtered results
    v4_count = sum(1 for p in results if p.get('address_family') == 'ipv4')
    v6_count = sum(1 for p in results if p.get('address_family') == 'ipv6')

    results = results[offset:offset + limit]

    return jsonify({
        'total': total,
        'ipv4_count': v4_count,
        'ipv6_count': v6_count,
        'limit': limit,
        'offset': offset,
        'prefixes': results,
    })


@app.route('/api/prefixes/<path:prefix>')
def api_prefix_detail(prefix: str):
    """Get details for a specific prefix."""
    # URL decode handles the /
    for classification, prefixes in DATA['prefixes'].items():
        for p in prefixes:
            if p['prefix'] == prefix:
                result = dict(p)
                result['classification'] = classification
                return jsonify(result)

    abort(404, description=f"Prefix '{prefix}' not found")


@app.route('/api/lookup/<ip>')
def api_lookup(ip: str):
    """
    Lookup which prefixes contain this IP address.
    Returns matches sorted by prefix length (most specific first).
    """
    matches = lookup_ip(ip)

    return jsonify({
        'query': ip,
        'address_family': 'ipv6' if ':' in ip else 'ipv4',
        'match_count': len(matches),
        'best_match': matches[0] if matches else None,
        'all_matches': matches,
    })


@app.route('/api/lookup', methods=['POST'])
def api_lookup_bulk():
    """
    Bulk IP lookup.

    POST body: {"ips": ["10.1.1.1", "2001:db8::1", ...]}
    """
    data = request.get_json()
    if not data or 'ips' not in data:
        return jsonify({'error': 'Missing "ips" array in request body'}), 400

    results = {}
    v4_queries = 0
    v6_queries = 0

    for ip in data['ips'][:100]:  # Limit to 100 per request
        matches = lookup_ip(ip)
        af = 'ipv6' if ':' in ip else 'ipv4'
        if af == 'ipv4':
            v4_queries += 1
        else:
            v6_queries += 1
        results[ip] = {
            'address_family': af,
            'match_count': len(matches),
            'best_match': matches[0] if matches else None,
        }

    return jsonify({
        'query_count': len(results),
        'ipv4_queries': v4_queries,
        'ipv6_queries': v6_queries,
        'results': results,
    })


@app.route('/api/protocols')
def api_protocols():
    """Get protocol distribution with v4/v6 breakdown."""
    summary = DATA['summary']
    protocols = {}

    all_protocols = set(summary.get('by_protocol', {}).keys())

    for proto in all_protocols:
        total = summary.get('by_protocol', {}).get(proto, 0)
        v4 = summary.get('by_protocol_v4', {}).get(proto, 0)
        v6 = summary.get('by_protocol_v6', {}).get(proto, 0)
        protocols[proto] = {
            'total': total,
            'ipv4': v4,
            'ipv6': v6,
        }

    return jsonify(protocols)


@app.route('/api/prefix-lengths')
def api_prefix_lengths():
    """Get prefix length distribution for v4 and v6."""
    summary = DATA['summary']
    return jsonify({
        'ipv4': summary.get('prefix_lengths_v4', {}),
        'ipv6': summary.get('prefix_lengths_v6', {}),
    })


@app.route('/api/trace')
def api_trace():
    """
    Trace path between source and destination IPs.

    Query params:
        source: Source IP address (must be connected on a device)
        dest: Destination IP address
        vrf: VRF name (default: default)
    """
    source = request.args.get('source')
    dest = request.args.get('dest')
    vrf = request.args.get('vrf', 'default')

    if not source or not dest:
        return jsonify({'error': 'Missing source or dest parameter'}), 400

    tracer = DATA.get('tracer')
    if not tracer:
        return jsonify({'error': 'Path tracer not initialized'}), 500

    result = tracer.trace(source, dest, vrf)
    return jsonify(result.to_dict())


@app.route('/api/trace/device/<ip>')
def api_trace_find_device(ip: str):
    """
    Find which device owns an IP (has it as connected).
    Useful for validating source IPs before tracing.
    """
    tracer = DATA.get('tracer')
    if not tracer:
        return jsonify({'error': 'Path tracer not initialized'}), 500

    result = tracer.find_connected_device(ip)
    if result:
        return jsonify({
            'ip': ip,
            'device': result[0],
            'prefix': result[1].get('prefix'),
            'protocol': result[1].get('protocol'),
        })
    else:
        return jsonify({
            'ip': ip,
            'device': None,
            'error': 'IP not found as connected on any device',
        }), 404


# ============================================================================
# Web UI Routes
# ============================================================================

@app.route('/')
def index():
    """Main dashboard."""
    return render_template('index.html', summary=DATA['summary'])


@app.route('/browse')
def browse():
    """Prefix browser."""
    return render_template('browse.html', classifications=list(DATA['prefixes'].keys()))


@app.route('/lookup')
def lookup_page():
    """IP lookup page."""
    return render_template('lookup.html')


@app.route('/sites')
def sites_page():
    """Sites overview."""
    return render_template('sites.html', sites=DATA['sites'])


@app.route('/trace')
def trace_page():
    """Virtual path tracer."""
    return render_template('trace.html')


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description='VCollector Route Browser')
    parser.add_argument('--data', '-d', type=Path, required=True,
                        help='Path to analysis.json from route_analyzer.py')
    parser.add_argument('--host', default='127.0.0.1', help='Host to bind (default: 127.0.0.1)')
    parser.add_argument('--port', '-p', type=int, default=5000, help='Port (default: 5000)')
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    parser.add_argument('--preindex', action='store_true', help='Build lookup index on startup')

    args = parser.parse_args()

    if not args.data.exists():
        print(f"Error: {args.data} not found")
        print(f"\nGenerate it with:")
        print(f"  python route_analyzer.py ./extracted/routes.json --json {args.data}")
        return 1

    load_analysis(args.data)

    if args.preindex:
        build_lookup_index()

    print(f"\nStarting server at http://{args.host}:{args.port}")
    print(f"\nAPI endpoints:")
    print(f"  /api/summary/ipv6     - IPv6-only summary stats")
    print(f"  /api/sites/compare    - Site-by-site IPv6 comparison")
    print(f"  /api/prefixes?family=ipv6 - Filter prefixes by address family")
    print(f"  /api/trace?source=X&dest=Y - Virtual path trace with ECMP")
    print(f"  /api/trace/device/IP  - Find device owning an IP")

    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == '__main__':
    main()