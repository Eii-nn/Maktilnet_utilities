import os
import re
import subprocess
import sys
import time
from pathlib import Path

from netmiko import (ConnectHandler, NetmikoAuthenticationException,
                     NetmikoTimeoutException)

from .config import (FORCE_WIRELESS_UPLOAD, RouterOSDesiredVersion, STATIC_IP_ADD,
                     USERNAME, WIRELESS_PACKAGE, WIRELESS_PACKAGE_NAME,
                     WIRELESS_REBOOT_TIMEOUT)

class WirelessManager:
    def __init__(self, mikrotik_device: dict):
        self.mikrotik_device = mikrotik_device

    def resolve_wireless_package(self):
        """
        Resolve the configured package path on native Windows or WSL.

        A project-relative fallback is also checked so the script continues to
        work when the whole autopreflight folder is moved.
        """
        raw_path = WIRELESS_PACKAGE
        candidates = []

        if re.match(r"^[A-Za-z]:[\\/]", raw_path):
            if os.name == "nt":
                candidates.append(Path(raw_path))
            else:
                drive = raw_path[0].lower()
                relative_part = raw_path[3:].replace("\\", "/")
                candidates.append(Path(f"/mnt/{drive}") / relative_part)
        else:
            configured_path = Path(raw_path)
            if configured_path.is_absolute():
                candidates.append(configured_path)
            else:
                candidates.append(Path(__file__).resolve().parent.parent / configured_path)

        candidates.append(
            Path(__file__).resolve().parent.parent / "Download" / WIRELESS_PACKAGE_NAME
        )

        package_path = next(
            (candidate.resolve() for candidate in candidates if candidate.is_file()),
            None,
        )

        if package_path is None:
            checked = "\n  - ".join(str(candidate) for candidate in candidates)
            raise FileNotFoundError(
                "Wireless package is missing. Checked:\n" f"  - {checked}"
            )
        if package_path.stat().st_size <= 0:
            raise RuntimeError(f"Wireless package is empty: {package_path}")
        if not os.access(package_path, os.R_OK):
            raise PermissionError(f"Wireless package is not readable: {package_path}")

        print(f"[+] Wireless package found locally: {package_path}")
        return package_path

    def get_router_wireless_compatibility(self, ssh_conn):
        architecture = (
            ssh_conn.send_command(":put [/system resource get architecture-name]")
            .strip()
            .strip('"')
        )
        version_output = ssh_conn.send_command(
            ":put [/system resource get version]"
        ).strip()

        # Some RouterOS/Netmiko combinations suppress scalar command output.
        # Fall back to parsing the complete resource listing in that case.
        if not architecture or not version_output:
            resource_output = ssh_conn.send_command("/system resource print without-paging")
            if not architecture:
                architecture_match = re.search(
                    r"architecture-name:\s*(\S+)",
                    resource_output,
                    re.IGNORECASE,
                )
                if architecture_match:
                    architecture = architecture_match.group(1).strip().strip('"')
            if not version_output:
                version_match = re.search(
                    r"^\s*version:\s*(\S+)",
                    resource_output,
                    re.IGNORECASE | re.MULTILINE,
                )
                if version_match:
                    version_output = version_match.group(1).strip().strip('"')

        version_match = re.search(r"\d+\.\d+(?:\.\d+)?", version_output)
        version = version_match.group(0) if version_match else version_output.strip('"')

        if not architecture:
            raise RuntimeError("Unable to determine router architecture from RouterOS")
        if not version:
            raise RuntimeError("Unable to determine the installed RouterOS version")
        if architecture.lower() != "mipsbe":
            raise RuntimeError(
                "Wireless package compatibility mismatch: "
                f"router architecture is {architecture!r}, expected 'mmips'"
            )
        if version != RouterOSDesiredVersion:
            raise RuntimeError(
                "Wireless package compatibility mismatch: "
                f"router is running RouterOS {version!r}, "
                f"expected {RouterOSDesiredVersion!r}"
            )

    def get_wireless_package_status(self, ssh_conn):
        count_output = ssh_conn.send_command(
            '/system package print count-only where name="wireless"'
        ).strip()
        count_match = re.search(r"\d+", count_output)
        if not count_match:
            raise RuntimeError(
                f"Unable to determine wireless package status: {count_output}"
            )
        if int(count_match.group(0)) == 0:
            return None

        version_output = (
            ssh_conn.send_command(
                ':put [/system package get [find where name="wireless"] version]'
            )
            .strip()
            .strip('"')
        )
        version_match = re.search(r"\d+\.\d+(?:\.\d+)?", version_output)
        version = version_match.group(0) if version_match else version_output

        disabled_output = (
            ssh_conn.send_command(
                ':put [/system package get [find where name="wireless"] disabled]'
            )
            .strip()
            .strip('"')
            .lower()
        )
        disabled = disabled_output in {"true", "yes"}

        return {
            "version": version,
            "disabled": disabled,
        }

    def get_remote_wireless_file(self, ssh_conn):
        count_output = ssh_conn.send_command(
            f'/file print count-only where name="{WIRELESS_PACKAGE_NAME}"'
        ).strip()
        count_match = re.search(r"\d+", count_output)
        if not count_match:
            raise RuntimeError(f"Unable to check remote wireless package: {count_output}")
        if int(count_match.group(0)) == 0:
            return None

        size_output = (
            ssh_conn.send_command(
                f':put [/file get [find where name="{WIRELESS_PACKAGE_NAME}"] size]'
            )
            .strip()
            .strip('"')
        )
        size_match = re.search(r"\d+(?:\.\d+)?", size_output)
        size = float(size_match.group(0)) if size_match else 0

        return {
            "size": size,
            "raw_size": size_output,
        }

    def remove_remote_wireless_file(self, ssh_conn):
        ssh_conn.send_command(f'/file remove [find where name="{WIRELESS_PACKAGE_NAME}"]')

    def upload_wireless_package(self, package_path):
        print("[*] Uploading wireless package...")
        cmd = [
            "sshpass",
            "-p",
            self.mikrotik_device["password"],
            "scp",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            str(package_path),
            f"{USERNAME}@{STATIC_IP_ADD}:./",
        ]

        try:
            result = subprocess.run(
                cmd,
                timeout=1800,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as e:
            raise RuntimeError(
                "Wireless package upload failed: sshpass or scp was not found"
            ) from e
        except subprocess.TimeoutExpired as e:
            raise RuntimeError("Wireless package upload failed: SCP timed out") from e

        if result.returncode != 0:
            error = result.stderr.strip() or result.stdout.strip() or "unknown SCP error"
            raise RuntimeError(f"Wireless package upload failed: {error}")

    def verify_remote_wireless_upload(self, ssh_conn):
        remote_file = self.get_remote_wireless_file(ssh_conn)
        if remote_file is None:
            raise RuntimeError(
                "Wireless package upload failed: file was not found on the router"
            )
        if remote_file["size"] <= 0:
            raise RuntimeError("Wireless package upload failed: remote file size is zero")
        print(f"[+] Upload successful ({remote_file['raw_size']})")

    def reboot_router_for_wireless(self, ssh_conn):
        print("[*] Rebooting router to install package...")
        output = ssh_conn.send_command_timing(
            "/system reboot",
            strip_prompt=False,
            strip_command=False,
        )
        if re.search(r"(y/n|yes/no|Dangerous!)", output, re.IGNORECASE):
            ssh_conn.send_command_timing(
                "y",
                strip_prompt=False,
                strip_command=False,
            )

    def wait_for_router_after_wireless_reboot(self, timeout=WIRELESS_REBOOT_TIMEOUT):
        print("[*] Waiting for router to come back online...")
        start = time.monotonic()
        last_error = None
        time.sleep(5)

        while time.monotonic() - start < timeout:
            try:
                return ConnectHandler(
                    **self.mikrotik_device,
                    conn_timeout=20,
                    banner_timeout=20,
                )
            except Exception as e:
                last_error = e
                time.sleep(5)

        detail = f": {last_error}" if last_error else ""
        raise TimeoutError(f"Router reboot timeout after {timeout} seconds{detail}")

    def verify_wireless_package_installation(self, ssh_conn):
        self.get_router_wireless_compatibility(ssh_conn)
        package_status = self.get_wireless_package_status(ssh_conn)

        if package_status is None:
            raise RuntimeError(
                "Wireless package installation verification failed: "
                "package is not installed"
            )
        if package_status["disabled"]:
            raise RuntimeError(
                "Wireless package installation verification failed: " "package is disabled"
            )
        if package_status["version"] != RouterOSDesiredVersion:
            raise RuntimeError(
                "Wireless package installation verification failed: "
                f"installed version is {package_status['version']!r}, "
                f"expected {RouterOSDesiredVersion!r}"
            )

        print("[+] Wireless package installation verified")

    def ensure_wireless_package(self, on_progress):
        package_path = self.resolve_wireless_package()
        on_progress(32, "prepare", "wireless_check")

        try:
            ssh_conn = ConnectHandler(
                **self.mikrotik_device,
                conn_timeout=30,
                banner_timeout=20,
            )
        except NetmikoAuthenticationException as e:
            raise ConnectionError(
                f"Wireless package router authentication failed: {e}"
            ) from e
        except NetmikoTimeoutException as e:
            raise ConnectionError(f"Wireless package router connection failed: {e}") from e
        except Exception as e:
            raise ConnectionError(f"Wireless package router connection failed: {e}") from e

        reboot_required = False
        try:
            self.get_router_wireless_compatibility(ssh_conn)
            package_status = self.get_wireless_package_status(ssh_conn)

            if (
                package_status
                and not package_status["disabled"]
                and package_status["version"] == RouterOSDesiredVersion
                and not FORCE_WIRELESS_UPLOAD
            ):
                print("[+] Wireless package is already installed; skipping upload")
                print("[+] Wireless package installation verified")
                on_progress(44, "prepare", "wireless_check")
                return

            if package_status and package_status["disabled"] and not FORCE_WIRELESS_UPLOAD:
                print("[*] Wireless package is installed but disabled; enabling it")
                ssh_conn.send_command('/system package enable [find where name="wireless"]')
                reboot_required = True
            else:
                remote_file = self.get_remote_wireless_file(ssh_conn)
                if remote_file and FORCE_WIRELESS_UPLOAD:
                    print("[*] Force upload enabled; removing existing remote package")
                    self.remove_remote_wireless_file(ssh_conn)
                    remote_file = None
                elif remote_file and remote_file["size"] <= 0:
                    print("[*] Removing invalid zero-size remote wireless package")
                    self.remove_remote_wireless_file(ssh_conn)
                    remote_file = None

                if remote_file:
                    print("[+] Wireless package already exists on router; reusing it")
                else:
                    on_progress(36, "prepare", "wireless_upload")
                    self.upload_wireless_package(package_path)
                    self.verify_remote_wireless_upload(ssh_conn)

                reboot_required = True

            if reboot_required:
                on_progress(38, "prepare", "wireless_install")
                self.reboot_router_for_wireless(ssh_conn)
        finally:
            try:
                ssh_conn.disconnect()
            except Exception:
                pass

        on_progress(40, "prepare", "wireless_reboot")
        reconnected = self.wait_for_router_after_wireless_reboot()
        try:
            self.verify_wireless_package_installation(reconnected)
        finally:
            reconnected.disconnect()
        on_progress(44, "prepare", "wireless_reboot")
