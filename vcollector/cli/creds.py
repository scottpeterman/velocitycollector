"""
Credentials CLI handler - Credential discovery and testing.

Path: vcollector/cli/creds.py

Handles: vcollector creds <command> [options]

Commands:
    discover    Bulk credential discovery for devices
    test        Test credentials for a single device
    status      Show credential coverage report
"""

import os
import sys
import getpass
from typing import Optional, List
from pathlib import Path

from vcollector.vault.resolver import CredentialResolver
from vcollector.dcim.dcim_repo import DCIMRepository, Device


def handle_creds(args) -> int:
    """Handle creds subcommand."""

    if not args.creds_command:
        print("Usage: vcollector creds <command>")
        print("Commands: discover, test, status")
        return 1

    if args.creds_command == 'discover':
        return _handle_discover(args)
    elif args.creds_command == 'test':
        return _handle_test(args)
    elif args.creds_command == 'status':
        return _handle_status(args)
    else:
        print(f"Unknown command: {args.creds_command}")
        return 1


def _handle_discover(args) -> int:
    """Handle credential discovery."""
    from vcollector.core.cred_discovery import CredentialDiscovery

    # Get vault password
    vault_pass = args.vault_pass or os.environ.get('VCOLLECTOR_VAULT_PASS')
    if not vault_pass:
        vault_pass = getpass.getpass("Vault password: ")

    # Unlock vault
    resolver = CredentialResolver()

    if not resolver.is_initialized():
        print("Error: Vault not initialized. Run 'vcollector vault init' first.")
        return 1

    if not resolver.unlock_vault(vault_pass):
        print("Error: Invalid vault password")
        return 1

    try:
        # Get credentials to test
        all_creds = resolver.list_credentials()
        if not all_creds:
            print("Error: No credentials in vault")
            return 1

        cred_names = None
        if args.credentials:
            cred_names = [c.strip() for c in args.credentials.split(',')]
            # Validate credential names
            valid_names = {c.name for c in all_creds}
            invalid = [n for n in cred_names if n not in valid_names]
            if invalid:
                print(f"Error: Unknown credentials: {', '.join(invalid)}")
                print(f"Available: {', '.join(sorted(valid_names))}")
                return 1

        # Get devices from DCIM
        with DCIMRepository() as dcim:
            # Build device filter
            devices = dcim.get_devices(
                site_id=_resolve_site_id(dcim, args.site) if args.site else None,
                platform_slug=args.platform,
                role_slug=args.role,
                status=args.status or 'active',
                search=args.search,
            )

            if args.limit:
                devices = devices[:args.limit]

            if not devices:
                print("No devices found matching filters")
                return 0

            # Show discovery plan
            creds_to_show = cred_names or [c.name for c in all_creds]
            print("Credential Discovery")
            print("=" * 60)
            print(f"Devices: {len(devices)}")
            if args.site:
                print(f"Site: {args.site}")
            if args.platform:
                print(f"Platform: {args.platform}")
            if args.role:
                print(f"Role: {args.role}")
            print(f"Credentials: {', '.join(creds_to_show)}")
            print(f"Workers: {args.workers}, Timeout: {args.timeout}s")
            print(f"Update devices: {not args.no_update}")
            print()

            if args.dry_run:
                print("Dry run - devices that would be tested:")
                for d in devices[:20]:
                    cred_status = f"[cred_id={d.credential_id}]" if d.credential_id else "[no cred]"
                    print(f"  {d.name} ({d.primary_ip4}) {cred_status}")
                if len(devices) > 20:
                    print(f"  ... and {len(devices) - 20} more")
                return 0

            # Confirm
            if not args.yes:
                confirm = input(f"Test {len(devices)} device(s)? [y/N]: ")
                if confirm.lower() != 'y':
                    print("Aborted")
                    return 0

            print("Testing...")
            print()

            # Run discovery
            discovery = CredentialDiscovery(
                resolver=resolver,
                dcim_repo=dcim if not args.no_update else None,
                timeout=args.timeout,
                max_workers=args.workers,
            )

            def progress(completed, total, result):
                if result.success:
                    print(f"  [{completed}/{total}] ✓ {result.device_name} → "
                          f"{result.matched_credential_name} ({result.duration_ms:.0f}ms)")
                else:
                    attempts = result.attempts
                    print(f"  [{completed}/{total}] ✗ {result.device_name} → "
                          f"NO MATCH (tried {attempts} credential(s))")

            result = discovery.discover(
                devices=devices,
                credential_names=cred_names,
                skip_configured=args.skip_configured,
                skip_recently_tested=not args.force,
                update_devices=not args.no_update,
                progress_callback=progress if not args.quiet else None,
            )

            # Print summary
            print()
            print("=" * 60)
            print("RESULTS")
            print("=" * 60)

            if result.matches_by_credential:
                for cred_name, count in sorted(result.matches_by_credential.items(),
                                               key=lambda x: -x[1]):
                    print(f"  {cred_name}: {count} devices")

            print(f"  NO MATCH: {result.no_match_count} devices")
            print()
            print(f"Matched: {result.matched_count}/{result.total_devices}")
            if result.skipped_count:
                print(f"Skipped: {result.skipped_count}")
            if result.already_configured:
                print(f"Already configured: {result.already_configured}")
            print(f"Duration: {result.duration_seconds:.1f}s")

            # List devices with no match
            no_match = [r for r in result.device_results if not r.success]
            if no_match and not args.quiet:
                print()
                print(f"Devices with no working credentials ({len(no_match)}):")
                for r in no_match[:10]:
                    # Show the error from the last attempt
                    if r.test_results:
                        last_error = r.test_results[-1]
                        print(f"  - {r.device_name} ({r.host}): {last_error.error_category.value}")
                    else:
                        print(f"  - {r.device_name} ({r.host})")
                if len(no_match) > 10:
                    print(f"  ... and {len(no_match) - 10} more")

            return 0 if result.no_match_count == 0 else 1

    finally:
        resolver.lock_vault()


def _handle_test(args) -> int:
    """Handle testing credentials for a single device."""
    from vcollector.core.cred_discovery import CredentialDiscovery

    device_filter = args.device
    if not device_filter:
        print("Error: Specify device name or IP")
        return 1

    # Get vault password
    vault_pass = args.vault_pass or os.environ.get('VCOLLECTOR_VAULT_PASS')
    if not vault_pass:
        vault_pass = getpass.getpass("Vault password: ")

    # Unlock vault
    resolver = CredentialResolver()

    if not resolver.is_initialized():
        print("Error: Vault not initialized")
        return 1

    if not resolver.unlock_vault(vault_pass):
        print("Error: Invalid vault password")
        return 1

    try:
        # Find device
        with DCIMRepository() as dcim:
            # Try by name first, then by IP
            devices = dcim.get_devices(search=device_filter, limit=10)

            if not devices:
                print(f"Error: No device found matching '{device_filter}'")
                return 1

            if len(devices) > 1:
                print(f"Multiple devices match '{device_filter}':")
                for d in devices:
                    print(f"  - {d.name} ({d.primary_ip4}) [{d.site_name}]")
                print("Specify a more specific filter")
                return 1

            device = devices[0]

            # Get credential to test
            cred_name = args.credential

            print(f"Testing: {device.name} ({device.primary_ip4})")
            if cred_name:
                print(f"Credential: {cred_name}")
            else:
                all_creds = resolver.list_credentials()
                print(f"Testing all {len(all_creds)} credentials...")
            print()

            discovery = CredentialDiscovery(
                resolver=resolver,
                dcim_repo=dcim if args.update else None,
                timeout=args.timeout,
                max_workers=1,
            )

            result = discovery.test_single(device, credential_name=cred_name)

            # Show results
            if result.success:
                print(f"✓ SUCCESS: {result.matched_credential_name}")
                if result.test_results:
                    working = result.test_results[-1]
                    if working.prompt_detected:
                        print(f"  Prompt: {working.prompt_detected!r}")
                    print(f"  Duration: {working.duration_ms:.0f}ms")
            else:
                print("✗ FAILED: No working credentials")
                print()
                print("Test results:")
                for tr in result.test_results:
                    status = "✓" if tr.success else "✗"
                    print(f"  {status} {tr.credential_name}: "
                          f"{tr.error_category.value} ({tr.duration_ms:.0f}ms)")
                    if tr.error and not tr.success:
                        # Truncate long errors
                        error = tr.error[:80] + '...' if len(tr.error) > 80 else tr.error
                        print(f"      {error}")

            return 0 if result.success else 1

    finally:
        resolver.lock_vault()


def _handle_status(args) -> int:
    """Show credential coverage status."""

    with DCIMRepository() as dcim:
        devices = dcim.get_devices(status='active')

        if not devices:
            print("No active devices in DCIM")
            return 0

        # Count devices by credential status
        configured = 0
        unconfigured = 0
        by_test_result = {'success': 0, 'failed': 0, 'untested': 0}
        by_credential = {}

        for device in devices:
            if device.credential_id:
                configured += 1
                # Group by credential_id (we'd need to join to get names)
                cred_id = device.credential_id
                by_credential[cred_id] = by_credential.get(cred_id, 0) + 1
            else:
                unconfigured += 1

            # Check test result if available
            test_result = getattr(device, 'credential_test_result', None) or 'untested'
            by_test_result[test_result] = by_test_result.get(test_result, 0) + 1

        print("Credential Coverage Report")
        print("=" * 60)
        print(f"Total active devices: {len(devices)}")
        print()
        print("Configuration status:")
        print(f"  Configured (credential_id set): {configured}")
        print(f"  Unconfigured: {unconfigured}")
        print()
        print("Test results:")
        print(f"  Success: {by_test_result.get('success', 0)}")
        print(f"  Failed: {by_test_result.get('failed', 0)}")
        print(f"  Untested: {by_test_result.get('untested', 0)}")

        if by_credential:
            print()
            print("By credential ID:")
            for cred_id, count in sorted(by_credential.items(), key=lambda x: -x[1]):
                print(f"  ID {cred_id}: {count} devices")

        # Calculate coverage percentage
        coverage = (configured / len(devices) * 100) if devices else 0
        tested_pct = ((len(devices) - by_test_result.get('untested', 0))
                      / len(devices) * 100) if devices else 0

        print()
        print(f"Coverage: {coverage:.1f}%")
        print(f"Tested: {tested_pct:.1f}%")

        return 0


def _resolve_site_id(dcim: DCIMRepository, site_filter: str) -> Optional[int]:
    """Resolve site name/slug to ID."""
    # Try as slug first
    site = dcim.get_site(slug=site_filter)
    if site:
        return site.id

    # Try partial name match
    sites = dcim.get_sites()
    for s in sites:
        if site_filter.lower() in s.name.lower() or site_filter.lower() in s.slug.lower():
            return s.id

    return None


def setup_creds_parser(subparsers):
    """Set up creds subcommand parser."""
    creds_parser = subparsers.add_parser(
        'creds',
        help='Credential discovery and testing',
        description='Discover and test SSH credentials for devices',
    )

    creds_subparsers = creds_parser.add_subparsers(
        dest='creds_command',
        metavar='<action>',
    )

    # creds discover
    discover_parser = creds_subparsers.add_parser(
        'discover',
        help='Bulk credential discovery',
    )
    discover_parser.add_argument(
        '--site', '-s',
        help='Filter by site name/slug',
    )
    discover_parser.add_argument(
        '--platform', '-p',
        help='Filter by platform slug',
    )
    discover_parser.add_argument(
        '--role', '-r',
        help='Filter by role slug',
    )
    discover_parser.add_argument(
        '--status',
        default='active',
        help='Device status filter (default: active)',
    )
    discover_parser.add_argument(
        '--search',
        help='Search in device name/IP',
    )
    discover_parser.add_argument(
        '--credentials', '-c',
        help='Comma-separated credential names to test (default: all)',
    )
    discover_parser.add_argument(
        '--workers', '-w',
        type=int,
        default=8,
        help='Max concurrent connections (default: 8)',
    )
    discover_parser.add_argument(
        '--timeout', '-t',
        type=int,
        default=15,
        help='SSH connection timeout in seconds (default: 15)',
    )
    discover_parser.add_argument(
        '--limit', '-n',
        type=int,
        help='Limit number of devices to test',
    )
    discover_parser.add_argument(
        '--skip-configured',
        action='store_true',
        help='Skip devices that already have credential_id set',
    )
    discover_parser.add_argument(
        '--force', '-f',
        action='store_true',
        help='Test even recently-tested devices',
    )
    discover_parser.add_argument(
        '--no-update',
        action='store_true',
        help="Don't update device credential_id on success",
    )
    discover_parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be tested without testing',
    )
    discover_parser.add_argument(
        '--yes', '-y',
        action='store_true',
        help='Skip confirmation prompt',
    )
    discover_parser.add_argument(
        '--quiet', '-q',
        action='store_true',
        help='Minimal output',
    )
    discover_parser.add_argument(
        '--vault-pass',
        help='Vault password (or set VCOLLECTOR_VAULT_PASS)',
    )

    # creds test
    test_parser = creds_subparsers.add_parser(
        'test',
        help='Test credentials for a single device',
    )
    test_parser.add_argument(
        'device',
        help='Device name or IP address',
    )
    test_parser.add_argument(
        '--credential', '-c',
        help='Specific credential to test (default: try all)',
    )
    test_parser.add_argument(
        '--timeout', '-t',
        type=int,
        default=15,
        help='SSH timeout in seconds (default: 15)',
    )
    test_parser.add_argument(
        '--update', '-u',
        action='store_true',
        help='Update device credential_id on success',
    )
    test_parser.add_argument(
        '--vault-pass',
        help='Vault password (or set VCOLLECTOR_VAULT_PASS)',
    )

    # creds status
    status_parser = creds_subparsers.add_parser(
        'status',
        help='Show credential coverage report',
    )

    return creds_parser