#!/usr/bin/env python3

import glob
import os
import re
import subprocess
import time

from netmiko import (
    ConnectHandler,
    NetmikoAuthenticationException,
    NetmikoTimeoutException,
)

from ip_handler import IPAdder

ARCHITECTURES = ["arm64", "arm", "mipsbe", "mmips", "ppc", "smips", "tile", "x86"]


class FirmwareUpgrade:
    def __init__(
        self,
        device,  # 👈 full netmiko device dict
        npk_dir,
        desired_version,
        scan_timeout=15,
    ):

        self.device = device
        self.npk_dir = npk_dir
        self.desired_version = desired_version
        self.scan_timeout = scan_timeout

        self.ip = device["host"]
        self.username = device["username"]

        # Will fail if self.device ke naa sa ubos
        arch = self._get_architecture()
        if arch is None:
            raise RuntimeError("Failed to retrieve architecture")

        self.npk_filename = f"routeros-{desired_version}-{arch}.npk"

    def _get_architecture(self):
        retries = 10
        for _ in range(retries):
            try:
                print("[*] Checking Device Architecture...")

                with ConnectHandler(
                    **self.device, conn_timeout=30, banner_timeout=20
                ) as conn:
                    output = conn.send_command(
                        ":put [/system resource get architecture-name]"
                    )

                arch = output.strip()
                if arch in ARCHITECTURES:
                    return arch
                else:
                    raise RuntimeError("Architecture not on the supported list")

            except (NetmikoTimeoutException, NetmikoAuthenticationException) as e:
                print(f"[!] Connection failed: {e}")

            except Exception as e:
                print(f"[!] Unexpected error: {e}")

    # -------------------------------
    # FIRMWARE CHECK
    # -------------------------------
    def is_firmware_version(self):
        """
        Checks if the MikroTik RouterOS version matches self.desired_version
        Returns True if it matches, False otherwise.
        """
        try:
            print("[*] Checking RouterOS Version...")

            with ConnectHandler(
                **self.device, conn_timeout=20, banner_timeout=20
            ) as conn:

                output = conn.send_command("/system resource get version")

            current_version = output.strip()

            print(f"🔍 Detected RouterOS Version: {current_version}")

            return current_version == self.desired_version

        except (NetmikoTimeoutException, NetmikoAuthenticationException) as e:
            print(f"[!] Connection failed: {e}")
            return False

        except Exception as e:
            print(f"[!] Version check failed: {e}")
            return False

    # -------------------------------
    # SCP UPLOAD
    # -------------------------------
    def smart_scp_upload(self):
        local_glob = os.path.join(self.npk_dir, self.npk_filename)
        files = glob.glob(local_glob)

        if not os.path.exists(self.npk_dir):
            print(f"NPK directory does not exist: {self.npk_dir}")
            return False

        if not files:
            print(f"No files found matching: {local_glob}")
            return False

        npk_files = [f for f in files if f.endswith(".npk")]
        if not npk_files:
            print("No .npk files found — aborting upload")
            return False

        print("📦 Files to be uploaded:")
        for f in npk_files:
            print(f"   - {f}")

        cmd = [
            "sshpass",
            "-p",
            self.device["password"],
            "scp",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
        ]

        cmd.extend(npk_files)
        cmd.append(f"{self.username}@{self.ip}:./")

        result = subprocess.run(
            cmd,
            timeout=1800,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            print("SCP failed")
            print(result.stderr.strip())
            return False

        print("✅ SCP upload successful")
        return True

    # -------------------------------
    # WAIT FOR ROUTER
    # -------------------------------
    def wait_for_online(self, timeout=300):
        print("⏳ Waiting for router to come back online...")
        time.sleep(30)

        start = time.time()
        while time.time() - start < timeout:
            try:
                with ConnectHandler(
                    **self.device, conn_timeout=20, banner_timeout=20
                ) as conn:
                    version = conn.send_command("/system resource get version")
                    print(f"✅ Router is ONLINE — Version: {version}")
                    return True
            except Exception:
                time.sleep(5)

        print("Router did not come back online in time")
        return False

    # -------------------------------
    # OS + FIRMWARE UPGRADE
    # -------------------------------
    def upgrade_mikrotik_os_and_fw(self):

        try:
            print(f"🔗 Connecting to {self.ip}...")
            if self.is_firmware_version():
                print("✅ Firmware already matches target version")
                return

            with ConnectHandler(
                **self.device, conn_timeout=20, banner_timeout=20
            ) as conn:

                print("📤 Uploading RouterOS packages...")
                if not self.smart_scp_upload():
                    return False

                print("🔁 Rebooting to install RouterOS...")
                conn.send_command("/system reboot", expect_string=r"y/")
                try:
                    conn.send_command("y", expect_string=r"")
                except Exception:
                    pass

            if not self.wait_for_online():
                return False

            print("⚙️ Upgrading RouterBOARD firmware...")
            with ConnectHandler(**self.device) as conn:
                conn.send_command("/system routerboard upgrade", expect_string=r"y/")
                try:
                    conn.send_command("y", expect_string=r"")
                except Exception:
                    pass

                print("🔁 Final reboot to flash firmware...")
                conn.send_command("/system reboot", expect_string=r"y/")
                try:
                    conn.send_command("y", expect_string=r"")
                except Exception:
                    pass
            # Ensure static ip address
            # ip_adder = IPAdder()
            # if not ip_adder.add_ip_alias("192.168.88.101"):
            #     # If this fails, we can't do SSH/Netmiko later
            #     print("🛑 Critical: Local IP setup failed.")
            #     return
            return self.wait_for_online()

        except Exception as e:
            print(f"Upgrade failed: {e}")
            return False
