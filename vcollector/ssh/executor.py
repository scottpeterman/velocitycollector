"""
SSH Executor Pool - Concurrent device connections.

Path: vcollector/ssh/executor.py

Provides concurrent SSH command execution using ThreadPoolExecutor.
Integrates with the credential vault and validation engine.

Enhanced with comprehensive error trapping and logging.
Supports per-device credentials via extra_data['credentials'].
"""

import logging
import socket
import time
import traceback
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Callable, Tuple, Any, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed

from vcollector.vault.models import SSHCredentials
from vcollector.ssh.client import SSHClient, SSHClientOptions


# Module logger - configure at application level
logger = logging.getLogger(__name__)


class SSHErrorCategory(Enum):
    """Categorized SSH error types for better diagnostics."""
    SUCCESS = "success"
    CONNECTION_REFUSED = "connection_refused"
    CONNECTION_TIMEOUT = "connection_timeout"
    DNS_FAILURE = "dns_failure"
    AUTH_FAILURE = "auth_failure"
    KEY_EXCHANGE_FAILURE = "key_exchange"
    COMMAND_TIMEOUT = "command_timeout"
    PROMPT_DETECTION = "prompt_detection"
    CHANNEL_ERROR = "channel_error"
    PROTOCOL_ERROR = "protocol_error"
    SOCKET_ERROR = "socket_error"
    DISCONNECT_ERROR = "disconnect_error"
    UNKNOWN = "unknown"


def categorize_ssh_error(exception: Exception) -> SSHErrorCategory:
    """
    Categorize an SSH exception for better error reporting.

    Args:
        exception: The caught exception.

    Returns:
        SSHErrorCategory indicating the type of failure.
    """
    error_msg = str(exception).lower()
    error_type = type(exception).__name__

    # Connection refused
    if "connection refused" in error_msg or "errno 111" in error_msg:
        return SSHErrorCategory.CONNECTION_REFUSED

    # Connection timeout
    if "timed out" in error_msg or "timeout" in error_type.lower():
        if "command" in error_msg or "execute" in error_msg:
            return SSHErrorCategory.COMMAND_TIMEOUT
        return SSHErrorCategory.CONNECTION_TIMEOUT

    # DNS failure
    if "name or service not known" in error_msg or "getaddrinfo" in error_msg:
        return SSHErrorCategory.DNS_FAILURE

    # Authentication failure
    if any(x in error_msg for x in ["auth", "permission denied", "no supported authentication"]):
        return SSHErrorCategory.AUTH_FAILURE

    # Key exchange
    if any(x in error_msg for x in ["key exchange", "kex", "incompatible", "no matching"]):
        return SSHErrorCategory.KEY_EXCHANGE_FAILURE

    # Prompt detection
    if "prompt" in error_msg:
        return SSHErrorCategory.PROMPT_DETECTION

    # Channel errors
    if "channel" in error_msg or "eof" in error_msg:
        return SSHErrorCategory.CHANNEL_ERROR

    # Socket errors
    if isinstance(exception, (socket.error, OSError)) or "socket" in error_msg:
        return SSHErrorCategory.SOCKET_ERROR

    # Protocol errors (SSH-specific)
    if "ssh" in error_type.lower() or "paramiko" in error_type.lower():
        return SSHErrorCategory.PROTOCOL_ERROR

    return SSHErrorCategory.UNKNOWN


@dataclass
class ExecutorOptions:
    """Options for SSH execution."""
    timeout: int = 60
    shell_timeout: int = 10
    inter_command_time: float = 1.0
    expect_prompt_timeout: int = 30000
    prompt_count: int = 3
    debug: bool = False
    legacy_mode: bool = False
    # New options for enhanced error handling
    log_level: int = logging.INFO
    capture_traceback: bool = True
    retry_count: int = 0  # 0 = no retries
    retry_delay: float = 2.0


@dataclass
class ExecutionResult:
    """Result of a single device execution."""
    host: str
    success: bool
    output: str = ""
    error: Optional[str] = None
    duration_ms: float = 0
    prompt_detected: Optional[str] = None
    # Enhanced error information
    error_category: SSHErrorCategory = SSHErrorCategory.SUCCESS
    error_traceback: Optional[str] = None
    retry_count: int = 0
    disconnect_error: Optional[str] = None  # Capture disconnect errors separately
    credential_name: Optional[str] = None  # Which credential was used (for per-device creds)

    def __repr__(self) -> str:
        if self.success:
            return f"ExecutionResult(host={self.host}, success=True, duration={self.duration_ms:.0f}ms)"
        return f"ExecutionResult(host={self.host}, success=False, category={self.error_category.value}, error={self.error!r})"


@dataclass
class BatchExecutionSummary:
    """Summary statistics for a batch execution."""
    total: int = 0
    success: int = 0
    failed: int = 0
    duration_ms: float = 0
    errors_by_category: Dict[SSHErrorCategory, int] = field(default_factory=dict)

    def add_result(self, result: ExecutionResult):
        """Add a result to the summary."""
        self.total += 1
        if result.success:
            self.success += 1
        else:
            self.failed += 1
            cat = result.error_category
            self.errors_by_category[cat] = self.errors_by_category.get(cat, 0) + 1

    def __repr__(self) -> str:
        parts = [f"BatchSummary: {self.success}/{self.total} success"]
        if self.errors_by_category:
            error_parts = [f"{cat.value}={count}" for cat, count in self.errors_by_category.items()]
            parts.append(f"errors=[{', '.join(error_parts)}]")
        parts.append(f"duration={self.duration_ms:.0f}ms")
        return " | ".join(parts)


class SSHExecutorPool:
    """
    Concurrent SSH command executor.

    Manages a pool of SSH connections for parallel device access.

    Usage:
        pool = SSHExecutorPool(
            credentials=creds,
            options=ExecutorOptions(timeout=60, debug=True),
            max_workers=12
        )

        targets = [
            ("192.168.1.1", "show version", {"device_name": "router1"}),
            ("192.168.1.2", "show version", {"device_name": "router2"}),
        ]

        results, summary = pool.execute_batch(targets)
        print(summary)
        for r in results:
            if not r.success:
                print(f"{r.host}: {r.error_category.value} - {r.error}")
    """

    def __init__(
        self,
        credentials: SSHCredentials,
        options: Optional[ExecutorOptions] = None,
        max_workers: int = 12,
    ):
        """
        Initialize executor pool.

        Args:
            credentials: SSH credentials from vault.
            options: Execution options (timeouts, etc.).
            max_workers: Maximum concurrent connections.
        """
        self.credentials = credentials
        self.options = options or ExecutorOptions()
        self.max_workers = max_workers

        # Configure module logger based on options
        if self.options.debug:
            logger.setLevel(logging.DEBUG)
        else:
            logger.setLevel(self.options.log_level)

    def execute_batch(
        self,
        targets: List[Tuple[str, str, Any]],
        progress_callback: Optional[Callable[[int, int, ExecutionResult], None]] = None,
    ) -> Tuple[List[ExecutionResult], BatchExecutionSummary]:
        """
        Execute commands against multiple devices concurrently.

        Args:
            targets: List of (host, command, extra_data) tuples.
                - host: IP address or hostname
                - command: Comma-separated commands to execute
                - extra_data: Optional dict with device metadata
            progress_callback: Optional callback(completed, total, result).

        Returns:
            Tuple of (List of ExecutionResult in same order as targets, BatchExecutionSummary).
        """
        batch_start = time.time()
        total = len(targets)
        result_map = {}
        summary = BatchExecutionSummary()

        logger.info(f"Starting batch execution: {total} targets, {self.max_workers} workers")

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all tasks
            futures = {
                executor.submit(
                    self._execute_with_retry,
                    host,
                    command,
                    extra_data
                ): (i, host, command, extra_data)
                for i, (host, command, extra_data) in enumerate(targets)
            }

            completed = 0

            for future in as_completed(futures):
                idx, host, command, extra_data = futures[future]
                completed += 1

                try:
                    result = future.result()
                except Exception as e:
                    # This should rarely happen since _execute_with_retry catches everything
                    # but we handle it just in case
                    error_cat = categorize_ssh_error(e)
                    tb = traceback.format_exc() if self.options.capture_traceback else None

                    logger.error(f"Unexpected executor error for {host}: {e}", exc_info=self.options.debug)

                    # Get credential_name from extra_data if available
                    cred_name = extra_data.get('credential_name') if isinstance(extra_data, dict) else None

                    result = ExecutionResult(
                        host=host,
                        success=False,
                        error=f"Executor error: {e}",
                        error_category=error_cat,
                        error_traceback=tb,
                        credential_name=cred_name,
                    )

                result_map[idx] = result
                summary.add_result(result)

                # Log individual results
                device_name = extra_data.get('device_name', host) if isinstance(extra_data, dict) else host
                if result.success:
                    logger.debug(f"[{completed}/{total}] {device_name}: OK ({result.duration_ms:.0f}ms)")
                else:
                    logger.warning(f"[{completed}/{total}] {device_name}: FAILED - {result.error_category.value}: {result.error}")
                    if result.error_traceback and self.options.debug:
                        logger.debug(f"Traceback for {device_name}:\n{result.error_traceback}")

                if progress_callback:
                    try:
                        progress_callback(completed, total, result)
                    except Exception as cb_error:
                        logger.warning(f"Progress callback error: {cb_error}")

        # Finalize summary
        summary.duration_ms = (time.time() - batch_start) * 1000

        logger.info(str(summary))

        # Log error breakdown if there were failures
        if summary.errors_by_category:
            logger.info("Error breakdown:")
            for category, count in sorted(summary.errors_by_category.items(), key=lambda x: -x[1]):
                logger.info(f"  {category.value}: {count}")

        # Return results in original order
        return [result_map[i] for i in range(len(targets))], summary

    def _execute_with_retry(
        self,
        host: str,
        command: str,
        extra_data: Optional[dict] = None,
    ) -> ExecutionResult:
        """
        Execute command with optional retry logic.

        Args:
            host: Device IP/hostname.
            command: Comma-separated commands.
            extra_data: Optional device metadata.

        Returns:
            ExecutionResult with output or error.
        """
        last_result = None
        retry_count = 0

        for attempt in range(self.options.retry_count + 1):
            if attempt > 0:
                logger.debug(f"{host}: Retry attempt {attempt}/{self.options.retry_count}")
                time.sleep(self.options.retry_delay)

            result = self._execute_single(host, command, extra_data)
            result.retry_count = attempt

            if result.success:
                return result

            last_result = result
            retry_count = attempt

            # Don't retry certain error types
            if result.error_category in (
                SSHErrorCategory.AUTH_FAILURE,
                SSHErrorCategory.DNS_FAILURE,
                SSHErrorCategory.KEY_EXCHANGE_FAILURE,
            ):
                logger.debug(f"{host}: Non-retryable error category: {result.error_category.value}")
                break

        last_result.retry_count = retry_count
        return last_result

    def _execute_single(
        self,
        host: str,
        command: str,
        extra_data: Optional[dict] = None,
    ) -> ExecutionResult:
        """
        Execute command on single device.

        Args:
            host: Device IP/hostname.
            command: Comma-separated commands.
            extra_data: Optional device metadata. May include 'credentials' key
                       for per-device credential override.

        Returns:
            ExecutionResult with output or error.
        """
        start_time = time.time()
        client = None
        disconnect_error = None
        credential_name = None  # Track which credential was used

        logger.debug(f"{host}: Starting SSH connection")

        # Use per-device credentials if provided, otherwise use pool default
        creds = self.credentials
        if extra_data and extra_data.get('credentials'):
            creds = extra_data['credentials']
            credential_name = extra_data.get('credential_name')  # May be set by runner
            logger.debug(f"{host}: Using per-device credential")

        try:
            # Build SSH client options
            ssh_options = SSHClientOptions(
                host=host,
                username=creds.username,
                password=creds.password,
                key_content=creds.key_content,
                key_password=creds.key_passphrase,
                timeout=self.options.timeout,
                shell_timeout=self.options.shell_timeout,
                inter_command_time=self.options.inter_command_time,
                expect_prompt_timeout=self.options.expect_prompt_timeout,
                prompt_count=self.options.prompt_count,
                debug=self.options.debug,
                legacy_mode=self.options.legacy_mode,
            )

            # Create client and connect
            logger.debug(f"{host}: Creating SSH client")
            client = SSHClient(ssh_options)

            logger.debug(f"{host}: Connecting...")
            client.connect()
            logger.debug(f"{host}: Connected successfully")

            # Auto-detect prompt
            logger.debug(f"{host}: Detecting prompt...")
            detected_prompt = client.find_prompt()
            client.set_expect_prompt(detected_prompt)
            logger.debug(f"{host}: Prompt detected: {detected_prompt!r}")

            # Calculate prompt count based on commands
            commands = command.split(',')
            num_commands = sum(1 for cmd in commands if cmd.strip() and cmd.strip() not in ('\\n', '\n'))
            client._options.prompt_count = num_commands

            # Execute commands
            logger.debug(f"{host}: Executing {num_commands} command(s)")
            output = client.execute_command(command)

            duration_ms = (time.time() - start_time) * 1000
            logger.debug(f"{host}: Execution complete ({duration_ms:.0f}ms, {len(output)} bytes)")

            return ExecutionResult(
                host=host,
                success=True,
                output=output,
                duration_ms=duration_ms,
                prompt_detected=detected_prompt,
                error_category=SSHErrorCategory.SUCCESS,
                credential_name=credential_name,
            )

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            error_category = categorize_ssh_error(e)
            error_traceback = traceback.format_exc() if self.options.capture_traceback else None

            logger.debug(f"{host}: Failed with {error_category.value}: {e}")

            return ExecutionResult(
                host=host,
                success=False,
                error=str(e),
                duration_ms=duration_ms,
                error_category=error_category,
                error_traceback=error_traceback,
                credential_name=credential_name,
            )

        finally:
            if client:
                try:
                    logger.debug(f"{host}: Disconnecting...")
                    client.disconnect()
                    logger.debug(f"{host}: Disconnected")
                except Exception as disc_err:
                    # Capture disconnect errors instead of silently ignoring them
                    disconnect_error = str(disc_err)
                    logger.warning(f"{host}: Disconnect error (non-fatal): {disc_err}")
                    # Note: We don't fail the result for disconnect errors since
                    # the command may have succeeded. The error is logged and
                    # can be tracked if needed.

    def execute_single(
        self,
        host: str,
        command: str,
        extra_data: Optional[dict] = None,
    ) -> ExecutionResult:
        """
        Execute command on a single device (synchronous).

        Convenience method for single-device execution.
        """
        return self._execute_with_retry(host, command, extra_data)


def configure_logging(
    level: int = logging.INFO,
    format_string: Optional[str] = None,
    handler: Optional[logging.Handler] = None,
):
    """
    Configure logging for the SSH executor module.

    Call this at application startup to enable logging.

    Args:
        level: Logging level (default: INFO).
        format_string: Optional custom format string.
        handler: Optional custom handler (default: StreamHandler).

    Example:
        from vcollector.ssh.executor import configure_logging
        configure_logging(level=logging.DEBUG)
    """
    if format_string is None:
        format_string = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    if handler is None:
        handler = logging.StreamHandler()

    handler.setFormatter(logging.Formatter(format_string))
    logger.addHandler(handler)
    logger.setLevel(level)