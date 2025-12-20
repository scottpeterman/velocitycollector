"""
VelocityCollector CLI - Main entry point.

Refactored for database-first architecture with legacy JSON support.

Usage:
    vcollector                         # Launch GUI
    vcollector gui                     # Launch GUI (explicit)
    vcollector init                    # Initialize environment
    vcollector vault <command> [options]
    vcollector run [options]
    vcollector jobs <command> [options]
"""

import argparse
import sys


def launch_gui():
    """Launch the PyQt6 GUI application."""
    try:
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtGui import QFont
        from vcollector.ui.gui import VelocityCollectorGUI

        app = QApplication(sys.argv)
        app.setStyle('Fusion')
        font = QFont("Segoe UI", 10)
        app.setFont(font)

        window = VelocityCollectorGUI()
        window.show()
        return app.exec()
    except ImportError as e:
        print(f"Error: GUI dependencies not available: {e}")
        print("Install with: pip install PyQt6")
        return 1


def main():
    """Main CLI entry point."""

    # If no arguments, launch GUI
    if len(sys.argv) == 1:
        return launch_gui()

    # Check for explicit gui command (before argparse to avoid issues)
    if len(sys.argv) >= 2 and sys.argv[1] == 'gui':
        return launch_gui()

    parser = argparse.ArgumentParser(
        prog="vcollector",
        description="Network data collection engine with encrypted credential vault",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  (no args)   Launch GUI application
  gui         Launch GUI application (explicit)
  init        Initialize VelocityCollector (first-time setup)
  vault       Manage encrypted credential vault
  run         Execute collection jobs
  jobs        Manage job definitions
  creds       Credential discovery and testing

Examples:
  # Launch GUI
  vcollector
  
  # First-time setup
  vcollector init
  vcollector vault init
  vcollector vault add lab --username admin
  
  # Run jobs (database-first)
  vcollector run --job arista-arp              # By slug
  vcollector run --job 42                      # By ID
  vcollector run --jobs "cisco-*"              # Pattern match
  
  # Run legacy JSON jobs
  vcollector run --job jobs/cisco_configs.json
  
  # Job management
  vcollector jobs list                         # List all jobs
  vcollector jobs list --vendor arista         # Filter by vendor
  vcollector jobs show arista-arp              # Show job details
  vcollector jobs history --limit 10           # Recent runs

  # Credential discovery
  vcollector creds discover                    # Test all devices
  vcollector creds discover --site dc1         # Filter by site
  vcollector creds test spine-1                # Test single device
  vcollector creds status                      # Coverage report

Use 'vcollector <command> --help' for more information on a command.
""",
    )

    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 0.3.0",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    # GUI subcommand (explicit)
    subparsers.add_parser(
        "gui",
        help="Launch the GUI application",
    )

    # Init subcommand
    init_parser = subparsers.add_parser(
        "init",
        help="Initialize VelocityCollector environment",
        description="Create directory structure and databases for fresh installation",
    )
    _setup_init_parser(init_parser)

    # Vault subcommand
    vault_parser = subparsers.add_parser(
        "vault",
        help="Manage encrypted credential vault",
        description="Manage encrypted credential vault",
    )
    _setup_vault_parser(vault_parser)

    # Run subcommand
    run_parser = subparsers.add_parser(
        "run",
        help="Execute collection jobs",
        description="Execute collection jobs against network devices",
    )
    _setup_run_parser(run_parser)

    # Jobs subcommand
    jobs_parser = subparsers.add_parser(
        "jobs",
        help="Manage job definitions",
        description="Manage and inspect job definitions",
    )
    _setup_jobs_parser(jobs_parser)

    # Creds subcommand
    creds_parser = subparsers.add_parser(
        "creds",
        help="Credential discovery and testing",
        description="Discover and test SSH credentials for devices",
    )
    _setup_creds_parser(creds_parser)

    # Parse args
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    # Dispatch to subcommand handler
    if args.command == "gui":
        return launch_gui()
    elif args.command == "init":
        from vcollector.cli.init import handle_init

        return handle_init(args)
    elif args.command == "vault":
        from vcollector.cli.vault import handle_vault

        return handle_vault(args)
    elif args.command == "run":
        from vcollector.cli.run import handle_run

        return handle_run(args)
    elif args.command == "jobs":
        from vcollector.cli.jobs import handle_jobs

        return handle_jobs(args)
    elif args.command == "creds":
        from vcollector.cli.creds import handle_creds

        return handle_creds(args)
    else:
        parser.print_help()
        return 1


def _setup_init_parser(parser: argparse.ArgumentParser):
    """Set up init subcommand parser."""
    parser.add_argument(
        "--dir", "-d",
        help="Base directory (default: ~/.vcollector)"
    )

    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="Reinitialize even if exists (WARNING: resets databases)"
    )

    parser.add_argument(
        "--no-defaults",
        action="store_true",
        help="Skip creating default platforms and roles"
    )

    parser.add_argument(
        "--skip-tfsm",
        action="store_true",
        help="Skip TextFSM templates database"
    )


def _setup_vault_parser(parser: argparse.ArgumentParser):
    """Set up vault subcommand parser."""
    subparsers = parser.add_subparsers(dest="vault_command", metavar="<action>")

    # vault init
    subparsers.add_parser("init", help="Initialize vault with master password")

    # vault add
    add_parser = subparsers.add_parser("add", help="Add credential set")
    add_parser.add_argument("name", help="Credential set name")
    add_parser.add_argument("--username", "-u", required=True, help="SSH username")
    add_parser.add_argument("--key-file", "-k", help="Path to SSH private key")
    add_parser.add_argument("--default", "-d", action="store_true", help="Set as default")

    # vault list
    subparsers.add_parser("list", help="List credential sets")

    # vault remove
    remove_parser = subparsers.add_parser("remove", help="Remove credential set")
    remove_parser.add_argument("name", help="Credential set name")

    # vault set-default
    default_parser = subparsers.add_parser("set-default", help="Set default credential")
    default_parser.add_argument("name", help="Credential set name")

    # vault change-password
    subparsers.add_parser("change-password", help="Change master password")

    # vault export
    export_parser = subparsers.add_parser("export", help="Export credentials (encrypted)")
    export_parser.add_argument("--output", "-o", required=True, help="Output file")

    # vault import
    import_parser = subparsers.add_parser("import", help="Import credentials")
    import_parser.add_argument("file", help="Import file")


def _setup_run_parser(parser: argparse.ArgumentParser):
    """Set up run subcommand parser."""
    # Job selection (mutually exclusive)
    job_group = parser.add_mutually_exclusive_group()
    job_group.add_argument(
        "--job",
        help="Job to run (slug, ID, or path to JSON file)"
    )
    job_group.add_argument(
        "--jobs",
        nargs="+",
        help="Multiple jobs (slugs, patterns, or file globs)"
    )
    job_group.add_argument(
        "--jobs-dir",
        help="Directory containing legacy JSON job files"
    )

    # Credentials
    parser.add_argument(
        "--credential", "-c",
        help="Credential set name (default: use default credential)"
    )
    parser.add_argument(
        "--vault-pass",
        help="Vault password (or set VCOLLECTOR_VAULT_PASS)"
    )

    # Execution control
    parser.add_argument(
        "--max-concurrent-jobs",
        type=int,
        default=4,
        help="Max jobs to run in parallel (default: 4)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit devices per job"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        help="Override SSH timeout (seconds)"
    )

    # Output control
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show plan without executing"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable SSH debug output"
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Don't save captures to files"
    )
    parser.add_argument(
        "--force-save", "-f",
        action="store_true",
        help="Save output even if validation fails"
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip confirmation prompt"
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Minimal output"
    )


def _setup_jobs_parser(parser: argparse.ArgumentParser):
    """Set up jobs subcommand parser."""
    subparsers = parser.add_subparsers(dest="jobs_command", metavar="<action>")

    # jobs list
    list_parser = subparsers.add_parser(
        "list",
        help="List available jobs"
    )
    list_parser.add_argument(
        "--vendor", "-v",
        help="Filter by vendor"
    )
    list_parser.add_argument(
        "--type", "-t",
        help="Filter by capture type"
    )
    list_parser.add_argument(
        "--enabled",
        action="store_true",
        default=None,
        help="Show only enabled jobs"
    )
    list_parser.add_argument(
        "--search", "-s",
        help="Search in name, slug, description"
    )
    list_parser.add_argument(
        "--legacy",
        action="store_true",
        help="List legacy JSON files instead of database"
    )
    list_parser.add_argument(
        "--dir",
        help="Jobs directory (for --legacy mode)"
    )

    # jobs show
    show_parser = subparsers.add_parser(
        "show",
        help="Show job details"
    )
    show_parser.add_argument(
        "job_file",
        help="Job slug, ID, or file path"
    )

    # jobs validate
    validate_parser = subparsers.add_parser(
        "validate",
        help="Validate job configuration"
    )
    validate_parser.add_argument(
        "job_file",
        help="Job slug, ID, or file path"
    )

    # jobs create
    create_parser = subparsers.add_parser(
        "create",
        help="Create new job in database"
    )
    create_parser.add_argument(
        "--vendor", "-v",
        required=True,
        help="Vendor name (cisco, arista, etc.)"
    )
    create_parser.add_argument(
        "--type", "-t",
        required=True,
        help="Capture type (config, arp, mac, etc.)"
    )
    create_parser.add_argument(
        "--output", "-o",
        help="Export to JSON file (optional)"
    )

    # jobs history
    history_parser = subparsers.add_parser(
        "history",
        help="Show job execution history"
    )
    history_parser.add_argument(
        "--job", "-j",
        help="Filter by job slug"
    )
    history_parser.add_argument(
        "--limit", "-n",
        type=int,
        default=20,
        help="Number of entries (default: 20)"
    )

    # jobs migrate (for importing legacy JSON to database)
    migrate_parser = subparsers.add_parser(
        "migrate",
        help="Migrate legacy JSON jobs to database"
    )
    migrate_parser.add_argument(
        "--dir",
        required=True,
        help="Directory containing JSON job files"
    )
    migrate_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be migrated"
    )


def _setup_creds_parser(parser: argparse.ArgumentParser):
    """Set up creds subcommand parser."""
    subparsers = parser.add_subparsers(dest="creds_command", metavar="<action>")

    # creds discover
    discover_parser = subparsers.add_parser(
        "discover",
        help="Bulk credential discovery for devices",
    )
    discover_parser.add_argument(
        "--site", "-s",
        help="Filter by site name/slug",
    )
    discover_parser.add_argument(
        "--platform", "-p",
        help="Filter by platform slug",
    )
    discover_parser.add_argument(
        "--role", "-r",
        help="Filter by role slug",
    )
    discover_parser.add_argument(
        "--status",
        default="active",
        help="Device status filter (default: active)",
    )
    discover_parser.add_argument(
        "--search",
        help="Search in device name/IP",
    )
    discover_parser.add_argument(
        "--credentials", "-c",
        help="Comma-separated credential names to test (default: all)",
    )
    discover_parser.add_argument(
        "--workers", "-w",
        type=int,
        default=8,
        help="Max concurrent connections (default: 8)",
    )
    discover_parser.add_argument(
        "--timeout", "-t",
        type=int,
        default=15,
        help="SSH connection timeout in seconds (default: 15)",
    )
    discover_parser.add_argument(
        "--limit", "-n",
        type=int,
        help="Limit number of devices to test",
    )
    discover_parser.add_argument(
        "--skip-configured",
        action="store_true",
        help="Skip devices that already have credential_id set",
    )
    discover_parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="Test even recently-tested devices",
    )
    discover_parser.add_argument(
        "--no-update",
        action="store_true",
        help="Don't update device credential_id on success",
    )
    discover_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be tested without testing",
    )
    discover_parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip confirmation prompt",
    )
    discover_parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Minimal output",
    )
    discover_parser.add_argument(
        "--vault-pass",
        help="Vault password (or set VCOLLECTOR_VAULT_PASS)",
    )

    # creds test
    test_parser = subparsers.add_parser(
        "test",
        help="Test credentials for a single device",
    )
    test_parser.add_argument(
        "device",
        help="Device name or IP address",
    )
    test_parser.add_argument(
        "--credential", "-c",
        help="Specific credential to test (default: try all)",
    )
    test_parser.add_argument(
        "--timeout", "-t",
        type=int,
        default=15,
        help="SSH timeout in seconds (default: 15)",
    )
    test_parser.add_argument(
        "--update", "-u",
        action="store_true",
        help="Update device credential_id on success",
    )
    test_parser.add_argument(
        "--vault-pass",
        help="Vault password (or set VCOLLECTOR_VAULT_PASS)",
    )

    # creds status
    subparsers.add_parser(
        "status",
        help="Show credential coverage report",
    )


if __name__ == "__main__":
    sys.exit(main())