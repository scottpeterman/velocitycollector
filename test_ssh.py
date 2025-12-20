#!/usr/bin/env python3
"""
Test script for SSH client
"""
import sys
import os

# Assuming the client is saved as ssh_client.py in same directory
from vcollector.ssh.client import SSHClient, SSHClientOptions


def main():
    # Key file - adjust path as needed, or set PYSSH_KEY env var
    key_file = os.path.expanduser("~/.ssh/id_rsa")  # or id_ed25519, etc.

    options = SSHClientOptions(
        host="host.come.com",
        port=22,
        username="speterman",
        key_file=key_file,
        # key_password="passphrase_if_needed",  # or set PYSSH_KEY_PASS
        debug=True,
        legacy_mode=True,  # Enable legacy algorithms for Juniper
        timeout=30,
        shell_timeout=5,
        inter_command_time=1,
        expect_prompt_timeout=10000,
    )

    client = SSHClient(options)

    try:
        print("Connecting...")
        client.connect()

        # Auto-detect prompt
        prompt = client.find_prompt()
        print(f"Detected prompt: {prompt}")
        client.set_expect_prompt(prompt)

        # Test commands - adjust prompt_count based on commands sent
        options.prompt_count = 2  # One for each command + initial

        result = client.execute_command("show version | no-more")
        print("\n--- Output ---")
        print(result)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    finally:
        client.disconnect()


if __name__ == "__main__":
    main()