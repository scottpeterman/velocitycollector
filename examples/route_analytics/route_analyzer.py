#!/usr/bin/env python3
"""
Routing Table Analyzer (IPv6 Enhanced)

Analyzes extracted route data to produce hierarchical reports on:
- Prefix counts by type (public/private/link-local/etc)
- Address space usage with full IPv4/IPv6 breakdowns
- Protocol distribution per address family
- Per-device and per-site breakdowns with v4/v6 visibility

Usage:
    python route_analyzer.py ./extracted/routes.json
    python route_analyzer.py ./extracted/routes.json --by-site
    python route_analyzer.py ./extracted/routes.json --show-prefixes private
    python route_analyzer.py ./extracted/routes.json --show-prefixes ula --limit 50
    python route_analyzer.py ./extracted/routes.json --json report.json
    python route_analyzer.py ./extracted/routes.json --family ipv6
"""

import argparse
import ipaddress
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


# ============================================================================
# Prefix Classification
# ============================================================================

class PrefixClassifier:
    """Classify IPv4/IPv6 prefixes by type."""

    # IPv4 special ranges
    IPV4_PRIVATE = [
        ipaddress.ip_network('10.0.0.0/8'),
        ipaddress.ip_network('172.16.0.0/12'),
        ipaddress.ip_network('192.168.0.0/16'),
    ]
    IPV4_CGN = ipaddress.ip_network('100.64.0.0/10')  # Carrier-grade NAT
    IPV4_LINK_LOCAL = ipaddress.ip_network('169.254.0.0/16')
    IPV4_LOOPBACK = ipaddress.ip_network('127.0.0.0/8')
    IPV4_MULTICAST = ipaddress.ip_network('224.0.0.0/4')
    IPV4_BROADCAST = ipaddress.ip_network('255.255.255.255/32')
    IPV4_THIS_NET = ipaddress.ip_network('0.0.0.0/8')
    IPV4_DOCUMENTATION = [
        ipaddress.ip_network('192.0.2.0/24'),  # TEST-NET-1
        ipaddress.ip_network('198.51.100.0/24'),  # TEST-NET-2
        ipaddress.ip_network('203.0.113.0/24'),  # TEST-NET-3
    ]

    # IPv6 special ranges
    IPV6_LINK_LOCAL = ipaddress.ip_network('fe80::/10')
    IPV6_ULA = ipaddress.ip_network('fc00::/7')  # Unique Local Address
    IPV6_MULTICAST = ipaddress.ip_network('ff00::/8')
    IPV6_LOOPBACK = ipaddress.ip_network('::1/128')
    IPV6_DOCUMENTATION = ipaddress.ip_network('2001:db8::/32')
    IPV6_6TO4 = ipaddress.ip_network('2002::/16')
    IPV6_TEREDO = ipaddress.ip_network('2001::/32')
    IPV6_MAPPED_V4 = ipaddress.ip_network('::ffff:0:0/96')  # IPv4-mapped
    IPV6_NAT64 = ipaddress.ip_network('64:ff9b::/96')  # NAT64 well-known prefix
    IPV6_GUA = ipaddress.ip_network('2000::/3')  # Global Unicast Address space

    @classmethod
    def classify(cls, prefix_str: str) -> Tuple[str, Optional[ipaddress.ip_network]]:
        """
        Classify a prefix string.

        Returns: (classification, network_object)
        Classifications:
            IPv4: public, private, cgn, link-local, loopback, multicast, documentation, invalid
            IPv6: public (GUA), ula, link-local, loopback, multicast, documentation,
                  6to4, teredo, nat64, v4-mapped, invalid
        """
        try:
            # Handle prefix formats
            if '/' not in prefix_str:
                # Bare IP - assume host route
                prefix_str = f"{prefix_str}/32" if ':' not in prefix_str else f"{prefix_str}/128"

            network = ipaddress.ip_network(prefix_str, strict=False)
        except ValueError:
            return ('invalid', None)

        if network.version == 4:
            return cls._classify_v4(network)
        else:
            return cls._classify_v6(network)

    @classmethod
    def _classify_v4(cls, net: ipaddress.IPv4Network) -> Tuple[str, ipaddress.ip_network]:
        # Check special ranges in order of specificity
        if net.subnet_of(cls.IPV4_LOOPBACK):
            return ('loopback', net)
        if net.subnet_of(cls.IPV4_LINK_LOCAL):
            return ('link-local', net)
        if net.subnet_of(cls.IPV4_MULTICAST):
            return ('multicast', net)
        if net.subnet_of(cls.IPV4_CGN):
            return ('cgn', net)
        if net.subnet_of(cls.IPV4_THIS_NET):
            return ('default/this-net', net)
        for doc in cls.IPV4_DOCUMENTATION:
            if net.subnet_of(doc):
                return ('documentation', net)
        for priv in cls.IPV4_PRIVATE:
            if net.subnet_of(priv):
                return ('private', net)

        return ('public', net)

    @classmethod
    def _classify_v6(cls, net: ipaddress.IPv6Network) -> Tuple[str, ipaddress.ip_network]:
        if net.subnet_of(cls.IPV6_LOOPBACK):
            return ('loopback', net)
        if net.subnet_of(cls.IPV6_LINK_LOCAL):
            return ('link-local', net)
        if net.subnet_of(cls.IPV6_MULTICAST):
            return ('multicast', net)
        if net.subnet_of(cls.IPV6_ULA):
            return ('ula', net)  # Similar to private
        if net.subnet_of(cls.IPV6_DOCUMENTATION):
            return ('documentation', net)
        if net.subnet_of(cls.IPV6_6TO4):
            return ('6to4', net)
        if net.subnet_of(cls.IPV6_NAT64):
            return ('nat64', net)
        if net.subnet_of(cls.IPV6_MAPPED_V4):
            return ('v4-mapped', net)
        # Teredo is subset of 2001::/16, check before generic GUA
        if net.subnet_of(cls.IPV6_TEREDO):
            return ('teredo', net)

        # Global unicast (2000::/3) is public
        if net.subnet_of(cls.IPV6_GUA):
            return ('public', net)

        return ('other', net)


# ============================================================================
# Data Structures
# ============================================================================

@dataclass
class PrefixDetail:
    """Details about a specific prefix."""
    prefix: str
    classification: str
    protocol: str
    vrf: str
    address_family: str  # 'ipv4' or 'ipv6'
    prefix_length: int = 0
    devices: Set[str] = field(default_factory=set)
    next_hops: Set[str] = field(default_factory=set)
    connected_devices: Set[str] = field(default_factory=set)

    def __hash__(self):
        return hash(self.prefix)


@dataclass
class PrefixStats:
    """Statistics for a group of prefixes."""
    count: int = 0
    ipv4_count: int = 0
    ipv6_count: int = 0
    total_addresses_v4: int = 0
    total_addresses_v6: int = 0
    by_classification: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    by_protocol: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    by_prefix_length: Dict[str, Dict[int, int]] = field(
        default_factory=lambda: {'v4': defaultdict(int), 'v6': defaultdict(int)}
    )
    # New: per-family breakdowns
    by_classification_v4: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    by_classification_v6: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    by_protocol_v4: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    by_protocol_v6: Dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def add_prefix(self, prefix_str: str, classification: str, network, protocol: str = None):
        self.count += 1
        self.by_classification[classification] += 1

        if protocol:
            self.by_protocol[protocol] += 1

        if network:
            if network.version == 4:
                self.ipv4_count += 1
                self.total_addresses_v4 += network.num_addresses
                self.by_prefix_length['v4'][network.prefixlen] += 1
                self.by_classification_v4[classification] += 1
                if protocol:
                    self.by_protocol_v4[protocol] += 1
            else:
                self.ipv6_count += 1
                self.total_addresses_v6 += network.num_addresses
                self.by_prefix_length['v6'][network.prefixlen] += 1
                self.by_classification_v6[classification] += 1
                if protocol:
                    self.by_protocol_v6[protocol] += 1


@dataclass
class DeviceRouteStats:
    """Route statistics for a single device."""
    device_name: str
    vendor: str
    template: str
    stats: PrefixStats = field(default_factory=PrefixStats)
    by_vrf: Dict[str, PrefixStats] = field(default_factory=lambda: defaultdict(PrefixStats))


@dataclass
class SiteRouteStats:
    """Route statistics for a site (extracted from device name)."""
    site_name: str
    devices: Dict[str, DeviceRouteStats] = field(default_factory=dict)
    stats: PrefixStats = field(default_factory=PrefixStats)


# ============================================================================
# Analysis Engine
# ============================================================================

class RouteAnalyzer:
    """Analyze extracted route data."""

    def __init__(self, store_prefixes: bool = True):
        self.store_prefixes = store_prefixes
        self.devices: Dict[str, DeviceRouteStats] = {}
        self.sites: Dict[str, SiteRouteStats] = {}
        self.global_stats = PrefixStats()
        # Store prefix details for drill-down
        self.prefixes_by_class: Dict[str, Dict[str, PrefixDetail]] = defaultdict(dict)
        self.all_prefixes: Dict[str, PrefixDetail] = {}

    def analyze_file(self, filepath: Path):
        """Analyze an extracted routes JSON file."""
        with open(filepath) as f:
            data = json.load(f)

        if not isinstance(data, dict) or 'devices' not in data:
            print(f"Warning: {filepath} not in expected format")
            return

        for device_name, device_data in data['devices'].items():
            vendor = device_data.get('vendor', 'unknown')
            template = device_data.get('template', 'unknown')
            records = device_data.get('records', [])

            device_stats = DeviceRouteStats(
                device_name=device_name,
                vendor=vendor,
                template=template
            )

            site_name = self.extract_site(device_name)
            if site_name not in self.sites:
                self.sites[site_name] = SiteRouteStats(site_name=site_name)
            site = self.sites[site_name]

            for record in records:
                prefix_str = self.get_prefix_field(record)
                if not prefix_str:
                    continue

                classification, network = PrefixClassifier.classify(prefix_str)
                protocol = self.get_protocol_field(record)
                vrf = self.get_vrf_field(record)
                next_hops = self.get_next_hops(record)

                # Update stats
                device_stats.stats.add_prefix(prefix_str, classification, network, protocol)
                device_stats.by_vrf[vrf].add_prefix(prefix_str, classification, network, protocol)
                site.stats.add_prefix(prefix_str, classification, network, protocol)
                self.global_stats.add_prefix(prefix_str, classification, network, protocol)

                # Store prefix details for drill-down
                if self.store_prefixes:
                    if prefix_str not in self.all_prefixes:
                        af = 'ipv4' if network and network.version == 4 else 'ipv6'
                        prefix_len = network.prefixlen if network else 0
                        detail = PrefixDetail(
                            prefix=prefix_str,
                            classification=classification,
                            protocol=protocol,
                            vrf=vrf,
                            address_family=af,
                            prefix_length=prefix_len,
                        )
                        self.all_prefixes[prefix_str] = detail
                        self.prefixes_by_class[classification][prefix_str] = detail

                    # Update which devices have this prefix
                    self.all_prefixes[prefix_str].devices.add(device_name)
                    for nh in next_hops:
                        self.all_prefixes[prefix_str].next_hops.add(nh)

                    # Track connected devices
                    if protocol in ('Connected', 'Direct', 'Local', 'C', 'L'):
                        self.all_prefixes[prefix_str].connected_devices.add(device_name)

            self.devices[device_name] = device_stats
            site.devices[device_name] = device_stats

    def extract_site(self, device_name: str) -> str:
        """Extract site code from device name."""
        patterns = [
            r'\.([a-z]{2,4}\d+)$',  # .fra1, .iad1
            r'\.([a-z]{2,4}-\d+)$',  # .dc-1
            r'-([a-z]{2,4}\d+)$',  # -fra1
            r'\.(\w+-\w+)$',  # .site-name
        ]

        for pattern in patterns:
            match = re.search(pattern, device_name.lower())
            if match:
                return match.group(1)

        return 'unknown'

    def get_prefix_field(self, record: dict) -> Optional[str]:
        """Extract prefix from record - handles different schemas."""
        if 'PREFIX' in record:
            return record['PREFIX']

        if 'NETWORK' in record:
            network = record['NETWORK']
            prefix_len = record.get('PREFIX_LENGTH', '')
            if prefix_len:
                return f"{network}/{prefix_len}"
            return network

        return None

    def get_vrf_field(self, record: dict) -> str:
        """Extract VRF/table from record."""
        return record.get('TABLE') or record.get('VRF') or 'default'

    def get_protocol_field(self, record: dict) -> str:
        """Extract protocol from record."""
        proto = record.get('PROTOCOL', 'unknown')
        proto_map = {
            'B I': 'iBGP', 'B E': 'eBGP', 'BGP': 'BGP',
            'O': 'OSPF', 'O E2': 'OSPF-E2', 'O IA': 'OSPF-IA', 'OSPF': 'OSPF',
            'O3': 'OSPFv3', 'OE2': 'OSPFv3-E2',  # OSPFv3 for IPv6
            'C': 'Connected', 'Direct': 'Connected', 'L': 'Local', 'Local': 'Local',
            'S': 'Static', 'Static': 'Static',
            'IS-IS': 'ISIS', 'i': 'ISIS',
            'R': 'RIP', 'RIP': 'RIP',
            'RA': 'RA',  # Router Advertisement (IPv6)
        }
        return proto_map.get(proto, proto)

    def get_next_hops(self, record: dict) -> List[str]:
        """Extract next hops from record."""
        nh = record.get('NEXT_HOP', [])
        if isinstance(nh, list):
            return [h for h in nh if h]
        return [nh] if nh else []

    def get_prefixes_by_classification(self, classification: str,
                                       family: str = None) -> List[PrefixDetail]:
        """Get all prefixes with given classification, optionally filtered by family."""
        prefixes = list(self.prefixes_by_class.get(classification, {}).values())

        # Filter by address family if specified
        if family:
            family_lower = family.lower()
            if family_lower in ('ipv4', 'v4', '4'):
                prefixes = [p for p in prefixes if p.address_family == 'ipv4']
            elif family_lower in ('ipv6', 'v6', '6'):
                prefixes = [p for p in prefixes if p.address_family == 'ipv6']

        # Sort by network address
        def sort_key(p):
            try:
                net = ipaddress.ip_network(p.prefix, strict=False)
                # Sort IPv4 before IPv6, then by address
                return (net.version, net.network_address)
            except:
                return (99, p.prefix)

        return sorted(prefixes, key=sort_key)

    def get_unique_prefix_count(self) -> Dict[str, int]:
        """Get count of unique prefixes per classification."""
        return {cls: len(prefixes) for cls, prefixes in self.prefixes_by_class.items()}

    def get_unique_prefix_count_by_family(self) -> Dict[str, Dict[str, int]]:
        """Get count of unique prefixes per classification, split by address family."""
        result = {'ipv4': defaultdict(int), 'ipv6': defaultdict(int)}
        for cls, prefixes in self.prefixes_by_class.items():
            for p in prefixes.values():
                result[p.address_family][cls] += 1
        return {
            'ipv4': dict(result['ipv4']),
            'ipv6': dict(result['ipv6']),
        }

    def print_prefixes(self, classification: str, limit: int = None,
                       show_devices: bool = False, family: str = None):
        """Print prefixes for a specific classification."""
        prefixes = self.get_prefixes_by_classification(classification, family=family)

        if not prefixes:
            print(f"\nNo prefixes found for classification: {classification}")
            if family:
                print(f"(filtered to {family})")
            print(f"Available classifications: {', '.join(sorted(self.prefixes_by_class.keys()))}")
            return

        total = len(prefixes)
        if limit:
            prefixes = prefixes[:limit]

        family_str = f" ({family})" if family else ""
        print(f"\n{'=' * 80}")
        print(f"PREFIXES: {classification.upper()}{family_str} ({total} unique)")
        print(f"{'=' * 80}")

        if show_devices:
            print(f"\n{'Prefix':<40} {'AF':<5} {'Protocol':<10} {'VRF':<15} {'Devices':<20}")
            print("-" * 90)
            for p in prefixes:
                devices = ', '.join(sorted(p.devices)[:3])
                if len(p.devices) > 3:
                    devices += f"... (+{len(p.devices) - 3})"
                af = 'v4' if p.address_family == 'ipv4' else 'v6'
                print(f"{p.prefix:<40} {af:<5} {p.protocol:<10} {p.vrf:<15} {devices:<20}")
        else:
            # Compact view
            print(f"\n{'Prefix':<45} {'AF':<5} {'Protocol':<12} {'#Dev':>6} {'Next Hops'}")
            print("-" * 95)
            for p in prefixes:
                nh_str = ', '.join(sorted(p.next_hops)[:2])
                if len(p.next_hops) > 2:
                    nh_str += f"... (+{len(p.next_hops) - 2})"
                af = 'v4' if p.address_family == 'ipv4' else 'v6'
                print(f"{p.prefix:<45} {af:<5} {p.protocol:<12} {len(p.devices):>6} {nh_str}")

        if limit and total > limit:
            print(f"\n... showing {limit} of {total} prefixes (use --limit to see more)")

    def print_prefix_tree(self, classification: str, limit: int = None, family: str = None):
        """Print prefixes in a hierarchical tree format grouped by major network."""
        prefixes = self.get_prefixes_by_classification(classification, family=family)

        if not prefixes:
            print(f"\nNo prefixes found for classification: {classification}")
            return

        # Group by major network (first octet for v4, first 16 bits for v6)
        groups: Dict[str, List[PrefixDetail]] = defaultdict(list)

        for p in prefixes:
            try:
                net = ipaddress.ip_network(p.prefix, strict=False)
                if net.version == 4:
                    # Group by /8
                    key = f"{net.network_address.packed[0]}.0.0.0/8"
                else:
                    # Group by /32 for better v6 organization
                    packed = net.network_address.packed
                    key = f"{packed[0]:02x}{packed[1]:02x}:{packed[2]:02x}{packed[3]:02x}::/32"
            except:
                key = "invalid"
            groups[key].append(p)

        total = len(prefixes)
        shown = 0

        family_str = f" ({family})" if family else ""
        print(f"\n{'=' * 80}")
        print(f"PREFIX TREE: {classification.upper()}{family_str} ({total} unique prefixes)")
        print(f"{'=' * 80}")

        for group_key in sorted(groups.keys()):
            group_prefixes = groups[group_key]
            print(f"\n{group_key} ({len(group_prefixes)} prefixes)")

            for p in sorted(group_prefixes, key=lambda x: x.prefix)[:20]:
                if limit and shown >= limit:
                    break
                devices_str = f"[{len(p.devices)} devices]"
                print(f"  ├─ {p.prefix:<40} {p.protocol:<10} {devices_str}")
                shown += 1

            if len(group_prefixes) > 20:
                print(f"  └─ ... and {len(group_prefixes) - 20} more")

            if limit and shown >= limit:
                remaining_groups = len(groups) - list(groups.keys()).index(group_key) - 1
                print(f"\n... {remaining_groups} more groups, {total - shown} more prefixes")
                break

    def print_report(self, by_site: bool = False, verbose: bool = False):
        """Print hierarchical report with IPv4/IPv6 breakdowns."""

        def format_addresses(v4: int, v6: int) -> str:
            def fmt(n):
                if n >= 1_000_000_000_000_000:
                    return f"{n / 1_000_000_000_000_000:.1f}P"
                elif n >= 1_000_000_000_000:
                    return f"{n / 1_000_000_000_000:.1f}T"
                elif n >= 1_000_000_000:
                    return f"{n / 1_000_000_000:.1f}B"
                elif n >= 1_000_000:
                    return f"{n / 1_000_000:.1f}M"
                elif n >= 1_000:
                    return f"{n / 1_000:.1f}K"
                return str(n)

            return f"v4:{fmt(v4)} v6:{fmt(v6)}"

        def print_stats(stats: PrefixStats, indent: int = 0, show_family_breakdown: bool = True):
            pad = "  " * indent

            print(f"{pad}Total Prefixes: {stats.count:,}")
            print(f"{pad}  IPv4: {stats.ipv4_count:,}  IPv6: {stats.ipv6_count:,}")
            print(f"{pad}Address Space: {format_addresses(stats.total_addresses_v4, stats.total_addresses_v6)}")

            print(f"{pad}By Type:")
            for cls in sorted(stats.by_classification.keys()):
                count = stats.by_classification[cls]
                pct = count / stats.count * 100 if stats.count else 0
                v4_count = stats.by_classification_v4.get(cls, 0)
                v6_count = stats.by_classification_v6.get(cls, 0)
                if show_family_breakdown and (v4_count > 0 or v6_count > 0):
                    print(f"{pad}  {cls:<15} {count:>8,} ({pct:>5.1f}%)  [v4:{v4_count:,} v6:{v6_count:,}]")
                else:
                    print(f"{pad}  {cls:<15} {count:>8,} ({pct:>5.1f}%)")

            if stats.by_protocol:
                print(f"{pad}By Protocol:")
                for proto in sorted(stats.by_protocol.keys(), key=lambda x: -stats.by_protocol[x]):
                    count = stats.by_protocol[proto]
                    pct = count / stats.count * 100 if stats.count else 0
                    v4_count = stats.by_protocol_v4.get(proto, 0)
                    v6_count = stats.by_protocol_v6.get(proto, 0)
                    if show_family_breakdown and (v4_count > 0 or v6_count > 0):
                        print(f"{pad}  {proto:<15} {count:>8,} ({pct:>5.1f}%)  [v4:{v4_count:,} v6:{v6_count:,}]")
                    else:
                        print(f"{pad}  {proto:<15} {count:>8,} ({pct:>5.1f}%)")

        # Unique prefix summary
        unique_counts = self.get_unique_prefix_count()
        unique_by_family = self.get_unique_prefix_count_by_family()
        total_unique = len(self.all_prefixes)
        unique_v4 = sum(unique_by_family['ipv4'].values())
        unique_v6 = sum(unique_by_family['ipv6'].values())

        print("\n" + "=" * 70)
        print("ROUTING TABLE ANALYSIS")
        print("=" * 70)
        print(f"\nDevices: {len(self.devices)}")
        print(f"Sites: {len(self.sites)}")
        print(f"\nUnique Prefixes: {total_unique:,}")
        print(f"  IPv4: {unique_v4:,}  IPv6: {unique_v6:,}")
        print()
        print_stats(self.global_stats)

        # Unique counts per classification with v4/v6 breakdown
        print(f"\nUnique Prefixes by Type:")
        all_classes = set(unique_by_family['ipv4'].keys()) | set(unique_by_family['ipv6'].keys())
        for cls in sorted(all_classes):
            count = unique_counts.get(cls, 0)
            v4 = unique_by_family['ipv4'].get(cls, 0)
            v6 = unique_by_family['ipv6'].get(cls, 0)
            pct = count / total_unique * 100 if total_unique else 0
            print(f"  {cls:<15} {count:>8,} ({pct:>5.1f}%)  [v4:{v4:,} v6:{v6:,}]")

        if by_site:
            print("\n" + "-" * 70)
            print("BY SITE")
            print("-" * 70)

            for site_name in sorted(self.sites.keys()):
                site = self.sites[site_name]
                v4 = site.stats.ipv4_count
                v6 = site.stats.ipv6_count
                print(f"\n{'=' * 50}")
                print(f"Site: {site_name} ({len(site.devices)} devices)")
                print(f"  IPv4 Routes: {v4:,}  IPv6 Routes: {v6:,}")
                print(f"{'=' * 50}")
                print_stats(site.stats, indent=1)

                if verbose:
                    for dev_name in sorted(site.devices.keys()):
                        dev = site.devices[dev_name]
                        print(f"\n  Device: {dev_name} ({dev.vendor})")
                        print(f"    IPv4: {dev.stats.ipv4_count:,}  IPv6: {dev.stats.ipv6_count:,}")
                        print_stats(dev.stats, indent=2, show_family_breakdown=False)
        else:
            print("\n" + "-" * 70)
            print("TOP 10 DEVICES BY PREFIX COUNT")
            print("-" * 70)

            sorted_devices = sorted(
                self.devices.values(),
                key=lambda d: d.stats.count,
                reverse=True
            )[:10]

            print(f"\n{'Device':<30} {'Vendor':<10} {'Total':>10} {'IPv4':>10} {'IPv6':>10}")
            print("-" * 72)
            for dev in sorted_devices:
                print(
                    f"{dev.device_name:<30} {dev.vendor:<10} {dev.stats.count:>10,} {dev.stats.ipv4_count:>10,} {dev.stats.ipv6_count:>10,}")

        # Prefix length distribution
        print("\n" + "-" * 70)
        print("PREFIX LENGTH DISTRIBUTION")
        print("-" * 70)

        print("\nIPv4:")
        v4_lengths = self.global_stats.by_prefix_length['v4']
        if v4_lengths:
            max_v4 = max(v4_lengths.values())
            for length in sorted(v4_lengths.keys()):
                count = v4_lengths[length]
                bar = "█" * min(50, int(count / max_v4 * 50)) if max_v4 else ""
                print(f"  /{length:<3} {count:>8,} {bar}")
        else:
            print("  (no IPv4 prefixes)")

        print("\nIPv6:")
        v6_lengths = self.global_stats.by_prefix_length['v6']
        if v6_lengths:
            max_v6 = max(v6_lengths.values())
            for length in sorted(v6_lengths.keys()):
                count = v6_lengths[length]
                bar = "█" * min(50, int(count / max_v6 * 50)) if max_v6 else ""
                print(f"  /{length:<3} {count:>8,} {bar}")
        else:
            print("  (no IPv6 prefixes)")

        print("\n" + "=" * 70)

    def to_dict(self) -> dict:
        """Export analysis to dictionary for JSON with full IPv4/IPv6 breakdowns."""
        unique_by_family = self.get_unique_prefix_count_by_family()

        return {
            'summary': {
                'total_devices': len(self.devices),
                'total_sites': len(self.sites),
                'total_prefixes': self.global_stats.count,
                'unique_prefixes': len(self.all_prefixes),
                # Address family totals
                'ipv4_prefixes': self.global_stats.ipv4_count,
                'ipv6_prefixes': self.global_stats.ipv6_count,
                'unique_ipv4_prefixes': sum(unique_by_family['ipv4'].values()),
                'unique_ipv6_prefixes': sum(unique_by_family['ipv6'].values()),
                # Overall breakdowns
                'by_classification': dict(self.global_stats.by_classification),
                'unique_by_classification': self.get_unique_prefix_count(),
                'by_protocol': dict(self.global_stats.by_protocol),
                # Per-family breakdowns
                'by_classification_v4': dict(self.global_stats.by_classification_v4),
                'by_classification_v6': dict(self.global_stats.by_classification_v6),
                'unique_by_classification_v4': unique_by_family['ipv4'],
                'unique_by_classification_v6': unique_by_family['ipv6'],
                'by_protocol_v4': dict(self.global_stats.by_protocol_v4),
                'by_protocol_v6': dict(self.global_stats.by_protocol_v6),
                # Prefix length distributions
                'prefix_lengths_v4': dict(self.global_stats.by_prefix_length['v4']),
                'prefix_lengths_v6': dict(self.global_stats.by_prefix_length['v6']),
            },
            'sites': {
                name: {
                    'devices': list(site.devices.keys()),
                    'total_prefixes': site.stats.count,
                    'ipv4_prefixes': site.stats.ipv4_count,
                    'ipv6_prefixes': site.stats.ipv6_count,
                    'by_classification': dict(site.stats.by_classification),
                    'by_classification_v4': dict(site.stats.by_classification_v4),
                    'by_classification_v6': dict(site.stats.by_classification_v6),
                    'by_protocol': dict(site.stats.by_protocol),
                    'by_protocol_v4': dict(site.stats.by_protocol_v4),
                    'by_protocol_v6': dict(site.stats.by_protocol_v6),
                }
                for name, site in self.sites.items()
            },
            'prefixes': {
                cls: [
                    {
                        'prefix': p.prefix,
                        'address_family': p.address_family,
                        'prefix_length': p.prefix_length,
                        'protocol': p.protocol,
                        'vrf': p.vrf,
                        'devices': list(p.devices),
                        'next_hops': list(p.next_hops),
                        'connected_devices': list(p.connected_devices),
                    }
                    for p in sorted(prefixes.values(), key=lambda x: x.prefix)
                ]
                for cls, prefixes in self.prefixes_by_class.items()
            }
        }


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Analyze extracted routing table data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python route_analyzer.py ./extracted/routes.json
    python route_analyzer.py ./extracted/routes.json --by-site
    python route_analyzer.py ./extracted/routes.json --show-prefixes private
    python route_analyzer.py ./extracted/routes.json --show-prefixes ula --family ipv6
    python route_analyzer.py ./extracted/routes.json --show-prefixes public --family ipv6 --limit 100
    python route_analyzer.py ./extracted/routes.json --show-prefixes private --tree
    python route_analyzer.py ./extracted/routes.json --json analysis.json

Classifications:
    IPv4: public, private, cgn, link-local, loopback, multicast, documentation
    IPv6: public (GUA), ula, link-local, loopback, multicast, documentation, 6to4, teredo, nat64
        """
    )

    parser.add_argument('files', nargs='+', type=Path, help="Extracted routes JSON file(s)")
    parser.add_argument('--by-site', '-s', action='store_true', help="Group by site")
    parser.add_argument('--verbose', '-v', action='store_true', help="Show per-device details")
    parser.add_argument('--json', '-j', type=Path, help="Export to JSON")
    parser.add_argument('--show-prefixes', '-p', metavar='CLASS',
                        help="Show prefixes for classification (private, cgn, public, ula, etc.)")
    parser.add_argument('--family', '-f', choices=['ipv4', 'ipv6', 'v4', 'v6'],
                        help="Filter by address family")
    parser.add_argument('--tree', '-t', action='store_true',
                        help="Show prefixes in tree format grouped by major network")
    parser.add_argument('--limit', '-l', type=int, default=100,
                        help="Limit prefix output (default: 100, 0 for all)")
    parser.add_argument('--show-devices', '-d', action='store_true',
                        help="Show which devices have each prefix")

    args = parser.parse_args()

    analyzer = RouteAnalyzer(store_prefixes=True)

    for filepath in args.files:
        if not filepath.exists():
            print(f"Warning: {filepath} not found, skipping")
            continue
        analyzer.analyze_file(filepath)

    if not analyzer.devices:
        print("No route data found")
        sys.exit(1)

    # Always show summary
    analyzer.print_report(by_site=args.by_site, verbose=args.verbose)

    # Show prefix drill-down if requested
    if args.show_prefixes:
        limit = args.limit if args.limit > 0 else None
        if args.tree:
            analyzer.print_prefix_tree(args.show_prefixes, limit=limit, family=args.family)
        else:
            analyzer.print_prefixes(args.show_prefixes, limit=limit,
                                    show_devices=args.show_devices, family=args.family)

    if args.json:
        with open(args.json, 'w') as f:
            json.dump(analyzer.to_dict(), f, indent=2)
        print(f"\nJSON exported to: {args.json}")


if __name__ == '__main__':
    main()