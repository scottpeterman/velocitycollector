#!/usr/bin/env python3
"""
Virtual Path Tracer

Traces forward and return paths through the network using RIB data.
Handles ECMP branching to show all possible paths.

Usage:
    from path_tracer import PathTracer

    tracer = PathTracer(analysis_data)
    result = tracer.trace("10.47.32.15", "2001:db8::1")
"""

import ipaddress
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple


@dataclass
class PathHop:
    """A single hop in a path."""
    device: str
    prefix_matched: str
    prefix_length: int
    protocol: str
    next_hops: List[str]
    vrf: str = "default"
    is_connected: bool = False
    is_destination: bool = False
    is_exit: bool = False  # Leaves our network
    is_unknown: bool = False  # Next-hop device not found
    children: List['PathHop'] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'device': self.device,
            'prefix_matched': self.prefix_matched,
            'prefix_length': self.prefix_length,
            'protocol': self.protocol,
            'next_hops': self.next_hops,
            'vrf': self.vrf,
            'is_connected': self.is_connected,
            'is_destination': self.is_destination,
            'is_exit': self.is_exit,
            'is_unknown': self.is_unknown,
            'children': [c.to_dict() for c in self.children],
        }


@dataclass
class TraceResult:
    """Complete trace result with forward and return paths."""
    source_ip: str
    destination_ip: str
    source_device: Optional[str]
    destination_device: Optional[str]
    forward_path: Optional[PathHop]
    return_path: Optional[PathHop]
    forward_hop_count: int = 0
    return_hop_count: int = 0
    forward_path_count: int = 0  # Total unique paths considering ECMP
    return_path_count: int = 0
    is_asymmetric: bool = False
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'source_ip': self.source_ip,
            'destination_ip': self.destination_ip,
            'source_device': self.source_device,
            'destination_device': self.destination_device,
            'forward_path': self.forward_path.to_dict() if self.forward_path else None,
            'return_path': self.return_path.to_dict() if self.return_path else None,
            'forward_hop_count': self.forward_hop_count,
            'return_hop_count': self.return_hop_count,
            'forward_path_count': self.forward_path_count,
            'return_path_count': self.return_path_count,
            'is_asymmetric': self.is_asymmetric,
            'errors': self.errors,
        }


class PathTracer:
    """
    Traces paths through the network using collected RIB data.
    """

    MAX_DEPTH = 15  # Prevent infinite loops

    def __init__(self, analysis_data: dict):
        """
        Initialize with analysis data from RouteAnalyzer.to_dict()

        Args:
            analysis_data: Dict with 'prefixes' key containing classified prefixes
        """
        self.prefixes = analysis_data.get('prefixes', {})
        self.sites = analysis_data.get('sites', {})

        # Build indexes
        self._connected_index: Dict[str, List[dict]] = {}  # IP -> prefix entries with connected devices
        self._prefix_index: Dict[str, Dict[int, List[dict]]] = {'v4': {}, 'v6': {}}  # For longest match
        self._device_prefixes: Dict[str, List[dict]] = {}  # device -> all its prefixes

        self._build_indexes()

    def _build_indexes(self):
        """Build lookup indexes for path tracing."""
        for classification, prefix_list in self.prefixes.items():
            for p in prefix_list:
                prefix_str = p['prefix']
                try:
                    net = ipaddress.ip_network(prefix_str, strict=False)
                except ValueError:
                    continue

                # Store parsed network
                p['_network'] = net
                p['_classification'] = classification

                # Index by prefix length for longest-match
                family = 'v4' if net.version == 4 else 'v6'
                plen = net.prefixlen
                if plen not in self._prefix_index[family]:
                    self._prefix_index[family][plen] = []
                self._prefix_index[family][plen].append(p)

                # Index connected prefixes by contained IPs
                connected_devices = p.get('connected_devices', [])
                if connected_devices:
                    # This prefix is directly connected somewhere
                    if prefix_str not in self._connected_index:
                        self._connected_index[prefix_str] = []
                    self._connected_index[prefix_str].append(p)

                # Index by device
                for device in p.get('devices', []):
                    if device not in self._device_prefixes:
                        self._device_prefixes[device] = []
                    self._device_prefixes[device].append(p)

    def find_connected_device(self, ip_str: str) -> Optional[Tuple[str, dict]]:
        """
        Find which device has this IP as connected/local.

        Returns: (device_name, prefix_entry) or None
        """
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            return None

        # Search connected prefixes for one containing this IP
        best_match = None
        best_len = -1

        for prefix_str, entries in self._connected_index.items():
            for entry in entries:
                net = entry.get('_network')
                if net and addr in net and net.prefixlen > best_len:
                    # Return the device that has this connected
                    for device in entry.get('connected_devices', []):
                        best_match = (device, entry)
                        best_len = net.prefixlen

        return best_match

    def longest_match_on_device(self, device: str, ip_str: str, vrf: str = "default") -> Optional[dict]:
        """
        Find the longest matching prefix for an IP on a specific device.

        This simulates what the device's RIB lookup would return.
        """
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            return None

        device_prefixes = self._device_prefixes.get(device, [])

        best_match = None
        best_len = -1

        for p in device_prefixes:
            net = p.get('_network')
            if not net:
                continue

            # Check VRF match
            if p.get('vrf', 'default') != vrf:
                continue

            # Check if IP is in this prefix and it's longer than current best
            if addr in net and net.prefixlen > best_len:
                best_match = p
                best_len = net.prefixlen

        return best_match

    def find_next_hop_device(self, next_hop_ip: str) -> Optional[str]:
        """
        Find which device owns a next-hop IP address.

        The next-hop IP should be connected on some device - find it.
        """
        result = self.find_connected_device(next_hop_ip)
        if result:
            return result[0]
        return None

    def _walk_path(self, start_device: str, target_ip: str, vrf: str = "default",
                   visited: Set[Tuple[str, str]] = None, depth: int = 0) -> Optional[PathHop]:
        """
        Recursively walk the path from start_device toward target_ip.

        Returns a PathHop tree with ECMP branches as children.
        """
        if visited is None:
            visited = set()

        # Loop/depth protection
        visit_key = (start_device, target_ip)
        if visit_key in visited or depth > self.MAX_DEPTH:
            return None
        visited = visited | {visit_key}  # Copy for branch isolation

        # Lookup the target on this device
        match = self.longest_match_on_device(start_device, target_ip, vrf)

        if not match:
            # No route - this shouldn't happen if device is in path
            return PathHop(
                device=start_device,
                prefix_matched="",
                prefix_length=0,
                protocol="",
                next_hops=[],
                vrf=vrf,
                is_unknown=True,
            )

        net = match.get('_network')
        next_hops = match.get('next_hops', [])
        connected_devices = match.get('connected_devices', [])
        protocol = match.get('protocol', 'unknown')

        hop = PathHop(
            device=start_device,
            prefix_matched=match['prefix'],
            prefix_length=net.prefixlen if net else 0,
            protocol=protocol,
            next_hops=next_hops,
            vrf=match.get('vrf', 'default'),
            is_connected=start_device in connected_devices,
        )

        # Check if target is connected on this device
        target_connected = self.find_connected_device(target_ip)
        if target_connected and target_connected[0] == start_device:
            hop.is_destination = True
            hop.is_connected = True
            return hop

        # If this device has it as connected but it's not the target owner,
        # we might be at an intermediate L3 interface
        if start_device in connected_devices:
            hop.is_connected = True
            # Still need to check if we're at destination

        # If no next hops, we're at the end (could be connected or blackhole)
        if not next_hops:
            if hop.is_connected:
                hop.is_destination = True
            return hop

        # Follow each next-hop (ECMP branching)
        for nh_ip in next_hops:
            if not nh_ip or nh_ip in ('0.0.0.0', '::', 'Null0', 'discard'):
                continue

            # Find device that owns this next-hop
            nh_device = self.find_next_hop_device(nh_ip)

            if not nh_device:
                # Next-hop exits our collected network
                child = PathHop(
                    device=f"[exit: {nh_ip}]",
                    prefix_matched="",
                    prefix_length=0,
                    protocol="",
                    next_hops=[],
                    is_exit=True,
                )
                hop.children.append(child)
                continue

            if nh_device == start_device:
                # Self-reference, skip
                continue

            # Recurse to next device
            child = self._walk_path(nh_device, target_ip, vrf, visited, depth + 1)
            if child:
                hop.children.append(child)

        return hop

    def _count_paths(self, hop: PathHop) -> int:
        """Count total unique paths in the tree."""
        if not hop:
            return 0
        if not hop.children:
            return 1
        return sum(self._count_paths(c) for c in hop.children)

    def _max_depth(self, hop: PathHop, current: int = 1) -> int:
        """Find the maximum depth of the path tree."""
        if not hop or not hop.children:
            return current
        return max(self._max_depth(c, current + 1) for c in hop.children)

    def _get_leaf_devices(self, hop: PathHop, devices: Set[str] = None) -> Set[str]:
        """Get all leaf devices (endpoints) in the path tree."""
        if devices is None:
            devices = set()

        if not hop:
            return devices

        if not hop.children:
            if not hop.is_exit and not hop.is_unknown:
                devices.add(hop.device)
        else:
            for child in hop.children:
                self._get_leaf_devices(child, devices)

        return devices

    def trace(self, source_ip: str, destination_ip: str, vrf: str = "default") -> TraceResult:
        """
        Trace the path between source and destination IPs.

        Args:
            source_ip: Source IP address (must be connected on some device)
            destination_ip: Destination IP address
            vrf: VRF/routing table context

        Returns:
            TraceResult with forward and return path trees
        """
        result = TraceResult(
            source_ip=source_ip,
            destination_ip=destination_ip,
            source_device=None,
            destination_device=None,
            forward_path=None,
            return_path=None,
        )

        # Validate IPs
        try:
            src_addr = ipaddress.ip_address(source_ip)
            dst_addr = ipaddress.ip_address(destination_ip)
        except ValueError as e:
            result.errors.append(f"Invalid IP address: {e}")
            return result

        # Find source device
        src_result = self.find_connected_device(source_ip)
        if not src_result:
            result.errors.append(f"Source IP {source_ip} not found as connected on any device")
            return result

        result.source_device = src_result[0]

        # Find destination device (might be external)
        dst_result = self.find_connected_device(destination_ip)
        if dst_result:
            result.destination_device = dst_result[0]

        # Trace forward path
        result.forward_path = self._walk_path(result.source_device, destination_ip, vrf)
        if result.forward_path:
            result.forward_hop_count = self._max_depth(result.forward_path)
            result.forward_path_count = self._count_paths(result.forward_path)

        # Trace return path
        if result.destination_device:
            result.return_path = self._walk_path(result.destination_device, source_ip, vrf)
            if result.return_path:
                result.return_hop_count = self._max_depth(result.return_path)
                result.return_path_count = self._count_paths(result.return_path)
        else:
            # Destination is external - find where forward path exits
            # and trace return from there
            forward_leaves = self._get_leaf_devices(result.forward_path)
            if forward_leaves:
                # Use the first leaf as return start point
                return_start = list(forward_leaves)[0]
                result.return_path = self._walk_path(return_start, source_ip, vrf)
                if result.return_path:
                    result.return_hop_count = self._max_depth(result.return_path)
                    result.return_path_count = self._count_paths(result.return_path)
                result.errors.append(f"Destination external - return path traced from {return_start}")

        # Check for asymmetry
        if result.forward_path and result.return_path:
            forward_devices = self._collect_devices(result.forward_path)
            return_devices = self._collect_devices(result.return_path)
            # Asymmetric if return path uses different devices (ignoring order)
            if forward_devices != return_devices:
                result.is_asymmetric = True

        return result

    def _collect_devices(self, hop: PathHop, devices: Set[str] = None) -> Set[str]:
        """Collect all devices in a path tree."""
        if devices is None:
            devices = set()

        if not hop:
            return devices

        if not hop.is_exit and not hop.is_unknown:
            devices.add(hop.device)

        for child in hop.children:
            self._collect_devices(child, devices)

        return devices


# CLI for testing
if __name__ == '__main__':
    import argparse
    import json

    parser = argparse.ArgumentParser(description='Virtual Path Tracer')
    parser.add_argument('--data', '-d', required=True, help='Path to analysis.json')
    parser.add_argument('--source', '-s', required=True, help='Source IP')
    parser.add_argument('--dest', '-t', required=True, help='Destination IP')
    parser.add_argument('--vrf', '-v', default='default', help='VRF (default: default)')
    parser.add_argument('--json', '-j', action='store_true', help='Output as JSON')

    args = parser.parse_args()

    with open(args.data) as f:
        data = json.load(f)

    tracer = PathTracer(data)
    result = tracer.trace(args.source, args.dest, args.vrf)

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(f"\n{'=' * 60}")
        print(f"PATH TRACE: {result.source_ip} → {result.destination_ip}")
        print(f"{'=' * 60}")
        print(f"Source Device: {result.source_device}")
        print(f"Destination Device: {result.destination_device or 'EXTERNAL'}")
        print(f"Forward: {result.forward_hop_count} hops, {result.forward_path_count} paths")
        print(f"Return: {result.return_hop_count} hops, {result.return_path_count} paths")
        print(f"Asymmetric: {'YES' if result.is_asymmetric else 'No'}")

        if result.errors:
            print(f"\nNotes: {', '.join(result.errors)}")


        def print_tree(hop, indent=0):
            if not hop:
                return
            prefix = "  " * indent
            marker = ""
            if hop.is_destination:
                marker = " [DESTINATION]"
            elif hop.is_exit:
                marker = " [EXIT]"
            elif hop.is_unknown:
                marker = " [NO ROUTE]"

            nh_str = ""
            if hop.next_hops and not hop.is_destination:
                nh_str = f" via {', '.join(hop.next_hops[:3])}"
                if len(hop.next_hops) > 3:
                    nh_str += f"... (+{len(hop.next_hops) - 3})"

            print(
                f"{prefix}{'└─' if indent > 0 else ''}{hop.device}: {hop.prefix_matched} ({hop.protocol}){nh_str}{marker}")

            for child in hop.children:
                print_tree(child, indent + 1)


        print(f"\n--- Forward Path ---")
        print_tree(result.forward_path)

        print(f"\n--- Return Path ---")
        print_tree(result.return_path)