"""
Credential Discovery - Test and map SSH credentials to devices.

Path: vcollector/core/cred_discovery.py

Reuses existing SSHClient and SSHExecutorPool infrastructure.
Tests connectivity only (connect, detect prompt, disconnect) without
executing commands to minimize impact on devices.

Usage:
    from vcollector.core.cred_discovery import CredentialDiscovery

    discovery = CredentialDiscovery(resolver, dcim_repo)
    result = discovery.discover(
        devices=devices,
        credential_names=['lab', 'legacy'],
        max_workers=8,
        progress_callback=lambda completed, total, r: print(f"{completed}/{total}")
    )

    print(f"Matched: {result.matched_count}, No match: {result.no_match_count}")
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Callable, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from vcollector.ssh.client import SSHClient, SSHClientOptions
from vcollector.ssh.executor import SSHErrorCategory, categorize_ssh_error
from vcollector.vault.resolver import CredentialResolver
from vcollector.vault.models import SSHCredentials
from vcollector.dcim.dcim_repo import DCIMRepository, Device


logger = logging.getLogger(__name__)


@dataclass
class CredentialTestResult:
    """Result of testing a single credential against a device."""
    device_id: int
    device_name: str
    host: str
    credential_id: Optional[int] = None
    credential_name: Optional[str] = None
    success: bool = False
    error: Optional[str] = None
    error_category: SSHErrorCategory = SSHErrorCategory.UNKNOWN
    duration_ms: float = 0
    prompt_detected: Optional[str] = None


@dataclass
class DeviceDiscoveryResult:
    """Result of discovering working credential for a device."""
    device_id: int
    device_name: str
    host: str
    matched_credential_id: Optional[int] = None
    matched_credential_name: Optional[str] = None
    success: bool = False
    attempts: int = 0
    test_results: List[CredentialTestResult] = field(default_factory=list)
    duration_ms: float = 0

    @property
    def first_working(self) -> Optional[CredentialTestResult]:
        """Get the first successful test result."""
        return next((r for r in self.test_results if r.success), None)


@dataclass
class DiscoveryResult:
    """Overall discovery operation result."""
    total_devices: int = 0
    matched_count: int = 0
    no_match_count: int = 0
    already_configured: int = 0
    skipped_count: int = 0
    duration_seconds: float = 0
    device_results: List[DeviceDiscoveryResult] = field(default_factory=list)
    credentials_tested: List[str] = field(default_factory=list)

    # Breakdown by credential
    matches_by_credential: Dict[str, int] = field(default_factory=dict)

    def add_device_result(self, result: DeviceDiscoveryResult):
        """Add a device result and update counters."""
        self.device_results.append(result)
        self.total_devices += 1

        if result.success:
            self.matched_count += 1
            cred_name = result.matched_credential_name or 'unknown'
            self.matches_by_credential[cred_name] = \
                self.matches_by_credential.get(cred_name, 0) + 1
        else:
            self.no_match_count += 1


class CredentialDiscovery:
    """
    Discover working SSH credentials for devices.

    Tests each device against available credentials until one works,
    then optionally updates the device's preferred credential in DCIM.
    """

    def __init__(
        self,
        resolver: CredentialResolver,
        dcim_repo: Optional[DCIMRepository] = None,
        timeout: int = 15,
        max_workers: int = 8,
    ):
        """
        Initialize credential discovery.

        Args:
            resolver: Unlocked CredentialResolver instance.
            dcim_repo: Optional DCIMRepository for updating devices.
            timeout: SSH connection timeout in seconds.
            max_workers: Maximum concurrent connection tests.
        """
        self.resolver = resolver
        self.dcim_repo = dcim_repo
        self.timeout = timeout
        self.max_workers = max_workers

    def discover(
        self,
        devices: List[Device],
        credential_names: Optional[List[str]] = None,
        skip_configured: bool = False,
        skip_recently_tested: bool = True,
        recent_hours: int = 24,
        update_devices: bool = True,
        progress_callback: Optional[Callable[[int, int, DeviceDiscoveryResult], None]] = None,
    ) -> DiscoveryResult:
        """
        Discover working credentials for a list of devices.

        Args:
            devices: List of Device objects to test.
            credential_names: Specific credentials to test (None = all).
            skip_configured: Skip devices that already have credential_id set.
            skip_recently_tested: Skip devices tested within recent_hours.
            recent_hours: Hours threshold for "recently tested".
            update_devices: Update device.credential_id on success.
            progress_callback: Optional callback(completed, total, result).

        Returns:
            DiscoveryResult with per-device outcomes.
        """
        start_time = time.time()
        result = DiscoveryResult()

        # Get credentials to test
        all_creds = self.resolver.list_credentials()
        if credential_names:
            creds_to_test = [c for c in all_creds if c.name in credential_names]
        else:
            creds_to_test = all_creds

        if not creds_to_test:
            logger.warning("No credentials available to test")
            return result

        result.credentials_tested = [c.name for c in creds_to_test]
        logger.info(f"Testing {len(devices)} devices with {len(creds_to_test)} credentials")

        # Load actual SSH credentials (decrypted)
        ssh_creds_map: Dict[int, SSHCredentials] = {}
        for cred in creds_to_test:
            try:
                ssh_creds = self.resolver.get_ssh_credentials(credential_name=cred.name)
                if ssh_creds:
                    ssh_creds_map[cred.id] = ssh_creds
            except Exception as e:
                logger.warning(f"Failed to load credential '{cred.name}': {e}")

        # Filter devices
        devices_to_test = []
        for device in devices:
            # Skip if no IP
            if not device.primary_ip4:
                logger.debug(f"Skipping {device.name}: no primary_ip4")
                result.skipped_count += 1
                continue

            # Skip if already configured (optional)
            if skip_configured and device.credential_id:
                logger.debug(f"Skipping {device.name}: already has credential_id")
                result.already_configured += 1
                continue

            # Skip if recently tested (check credential_tested_at)
            if skip_recently_tested and hasattr(device, 'credential_tested_at'):
                if device.credential_tested_at:
                    try:
                        tested_at = datetime.fromisoformat(device.credential_tested_at)
                        hours_ago = (datetime.now() - tested_at).total_seconds() / 3600
                        if hours_ago < recent_hours:
                            logger.debug(f"Skipping {device.name}: tested {hours_ago:.1f}h ago")
                            result.skipped_count += 1
                            continue
                    except (ValueError, TypeError):
                        pass  # Invalid timestamp, proceed with test

            devices_to_test.append(device)

        logger.info(f"Testing {len(devices_to_test)} devices "
                   f"(skipped: {result.skipped_count}, already configured: {result.already_configured})")

        if not devices_to_test:
            result.duration_seconds = time.time() - start_time
            return result

        # Test devices in parallel
        completed = 0
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(
                    self._test_device,
                    device,
                    creds_to_test,
                    ssh_creds_map,
                ): device
                for device in devices_to_test
            }

            for future in as_completed(futures):
                device = futures[future]
                completed += 1

                try:
                    device_result = future.result()
                except Exception as e:
                    logger.error(f"Discovery failed for {device.name}: {e}")
                    device_result = DeviceDiscoveryResult(
                        device_id=device.id,
                        device_name=device.name,
                        host=device.primary_ip4,
                        success=False,
                    )

                result.add_device_result(device_result)

                # Update device in database
                if update_devices and self.dcim_repo and device_result.success:
                    try:
                        self.dcim_repo.update_device(
                            device.id,
                            credential_id=device_result.matched_credential_id,
                            credential_tested_at=datetime.now().isoformat(sep=' ', timespec='seconds'),
                            credential_test_result='success',
                        )
                    except Exception as e:
                        logger.warning(f"Failed to update device {device.name}: {e}")
                elif update_devices and self.dcim_repo:
                    # Update test result even if no match
                    try:
                        self.dcim_repo.update_device(
                            device.id,
                            credential_tested_at=datetime.now().isoformat(sep=' ', timespec='seconds'),
                            credential_test_result='failed',
                        )
                    except Exception as e:
                        logger.warning(f"Failed to update device {device.name}: {e}")

                if progress_callback:
                    try:
                        progress_callback(completed, len(devices_to_test), device_result)
                    except Exception as cb_err:
                        logger.warning(f"Progress callback error: {cb_err}")

        result.duration_seconds = time.time() - start_time
        return result

    def _test_device(
        self,
        device: Device,
        credentials: List[Any],  # CredentialInfo from resolver
        ssh_creds_map: Dict[int, SSHCredentials],
    ) -> DeviceDiscoveryResult:
        """
        Test a single device against all credentials.

        Tries device's existing credential_id first (if set),
        then tries each credential in order until one works.
        """
        start_time = time.time()
        host = device.primary_ip4

        result = DeviceDiscoveryResult(
            device_id=device.id,
            device_name=device.name,
            host=host,
        )

        # Build ordered list of credentials to try
        # If device has a preferred credential, try that first
        ordered_creds = []
        if device.credential_id and device.credential_id in ssh_creds_map:
            # Find the credential info for the preferred one
            preferred = next((c for c in credentials if c.id == device.credential_id), None)
            if preferred:
                ordered_creds.append(preferred)

        # Add remaining credentials
        for cred in credentials:
            if cred.id not in [c.id for c in ordered_creds]:
                ordered_creds.append(cred)

        # Test each credential
        for cred in ordered_creds:
            ssh_creds = ssh_creds_map.get(cred.id)
            if not ssh_creds:
                continue

            result.attempts += 1
            test_result = self._test_credential(device, cred, ssh_creds)
            result.test_results.append(test_result)

            if test_result.success:
                result.success = True
                result.matched_credential_id = cred.id
                result.matched_credential_name = cred.name
                break

            # Don't continue testing if it's a non-auth failure
            # (device unreachable, timeout, etc.)
            if test_result.error_category not in (
                SSHErrorCategory.AUTH_FAILURE,
                SSHErrorCategory.KEY_EXCHANGE_FAILURE,
            ):
                logger.debug(f"{device.name}: Stopping tests due to {test_result.error_category.value}")
                break

        result.duration_ms = (time.time() - start_time) * 1000
        return result

    def _test_credential(
        self,
        device: Device,
        cred_info: Any,
        ssh_creds: SSHCredentials,
    ) -> CredentialTestResult:
        """
        Test a single credential against a device.

        Performs connect + prompt detection only (no command execution).
        """
        start_time = time.time()
        host = device.primary_ip4
        port = device.ssh_port or 22

        result = CredentialTestResult(
            device_id=device.id,
            device_name=device.name,
            host=host,
            credential_id=cred_info.id,
            credential_name=cred_info.name,
        )

        client = None
        try:
            # Build SSH options - minimal for connectivity test
            ssh_options = SSHClientOptions(
                host=host,
                port=port,
                username=ssh_creds.username,
                password=ssh_creds.password,
                key_content=ssh_creds.key_content,
                key_password=ssh_creds.key_passphrase,
                timeout=self.timeout,
                shell_timeout=5,  # Short timeout for prompt detection
                debug=False,
            )

            client = SSHClient(ssh_options)
            client.connect()

            # Try to detect prompt (validates shell is working)
            detected_prompt = client.find_prompt()

            result.success = True
            result.prompt_detected = detected_prompt
            result.error_category = SSHErrorCategory.SUCCESS

            logger.debug(f"{device.name}: Auth success with '{cred_info.name}' "
                        f"(prompt: {detected_prompt!r})")

        except Exception as e:
            result.success = False
            result.error = str(e)
            result.error_category = categorize_ssh_error(e)

            logger.debug(f"{device.name}: Auth failed with '{cred_info.name}' - "
                        f"{result.error_category.value}: {e}")

        finally:
            if client:
                try:
                    client.disconnect()
                except Exception:
                    pass  # Ignore disconnect errors

            result.duration_ms = (time.time() - start_time) * 1000

        return result

    def test_single(
        self,
        device: Device,
        credential_name: Optional[str] = None,
    ) -> DeviceDiscoveryResult:
        """
        Test a single device (convenience method).

        Args:
            device: Device to test.
            credential_name: Specific credential to test (None = all).

        Returns:
            DeviceDiscoveryResult for the device.
        """
        cred_names = [credential_name] if credential_name else None
        result = self.discover(
            devices=[device],
            credential_names=cred_names,
            update_devices=False,
        )

        if result.device_results:
            return result.device_results[0]

        return DeviceDiscoveryResult(
            device_id=device.id,
            device_name=device.name,
            host=device.primary_ip4 or '',
            success=False,
        )