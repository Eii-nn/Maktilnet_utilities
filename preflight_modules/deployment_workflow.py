import time

from netmiko import ConnectHandler

from firmwareChecknUpdate import FirmwareUpgrade
from ovpn import prepare_rsc

from .config import (HOTSPOT_DIR, NPKDIR, PREFLIGHT_RSC,
                     RouterOSDesiredVersion, SCAN_TIMEOUT, STATIC_IP_ADD,
                     USERNAME, get_mikrotik_device)
from .ssh_utils import (connect_mikrotik, list_files, run_cmd, simpleSSHCommand,
                        smart_scp_upload)
from .wireless_manager import WirelessManager


class PreflightWorkflow:
    def __init__(self, password: str, on_progress, on_success, on_error):
        self.password = password
        self.on_progress = on_progress
        self.on_success = on_success
        self.on_error = on_error
        self.mikrotik_device = get_mikrotik_device(password)
        self._current_percent = 0
        self._current_step_id = "find_router"
        self._current_sub_id = "scan"

    def _progress(self, percent, step_id, sub_id=None, hint=None):
        self._current_percent = percent
        self._current_step_id = step_id
        self._current_sub_id = sub_id
        self.on_progress(percent, step_id, sub_id, hint)

    def run(self):
        try:
            self._progress(5, "find_router", "scan")

            preConfig, current_password = connect_mikrotik(USERNAME, self.password)
            self.mikrotik_device["password"] = current_password
            self.password = current_password

            self._progress(18, "find_router", "connect")
            self._progress(22, "prepare", "network")

            run_cmd(
                preConfig,
                f"/ip address add address={STATIC_IP_ADD}/24 interface=ether3 disabled=no",
            )
            run_cmd(preConfig, "/ip service enable api-ssl")
            run_cmd(preConfig, "/ip service enable ssh")
            preConfig.sendline("/quit")
            preConfig.close()
            time.sleep(5)

            self._progress(28, "prepare", "firmware")
            print(f"[*] MikroTik device password: {self.mikrotik_device['password']}")
            upgrader = FirmwareUpgrade(
                self.mikrotik_device,
                NPKDIR,
                RouterOSDesiredVersion,
                SCAN_TIMEOUT,
            )
            upgrader.upgrade_mikrotik_os_and_fw()

            try:
                wm = WirelessManager(self.mikrotik_device)
                wm.ensure_wireless_package(self.on_progress)
            except Exception as e:
                print(f"[-] Failed to install wireless package: {e}")

            self._progress(46, "clean", "remove_files")
            try:
                with ConnectHandler(**self.mikrotik_device) as removalNet:
                    simpleSSHCommand(
                        removalNet, '/file remove [/file find where name~"flash/hotspot/"]'
                    )
                    try:
                        simpleSSHCommand(removalNet, "/certificate remove [find]")
                    except Exception as cert_error:
                        print(f"[i] Certificate cleanup skipped: {cert_error}")
                    print("[+] Files cleared via SSH.")
            except Exception as e:
                print(f"[-] Netmiko cleanup failed: {e}")

            self._progress(66, "install_packages", "hotspot")
            print(f"[*] Uploading hotspot: {HOTSPOT_DIR}")
            if smart_scp_upload(
                HOTSPOT_DIR, USERNAME, STATIC_IP_ADD, "flash/", self.mikrotik_device["password"]
            ):
                print("[+] Uploaded Hotspot to MikroTik.")

            self._progress(76, "install_packages", "config")
            print("[*] Preparing RSC file")
            prepare_rsc(self.mikrotik_device)
            print("[+] RSC prepared with serial number as user")

            self._progress(86, "install_packages", "script")
            print(f"[*] Uploading preflight script: {PREFLIGHT_RSC}")
            if smart_scp_upload(
                PREFLIGHT_RSC, USERNAME, STATIC_IP_ADD, "/", self.mikrotik_device["password"]
            ):
                print("[+] Uploaded preflight.rsc to MikroTik.")
            else:
                raise RuntimeError("Failed to upload preflight.rsc")

            self._progress(92, "apply", "reset")
            try:
                with ConnectHandler(**self.mikrotik_device) as ssh_conn:
                    print("[*] Sending reset command...")
                    output = ssh_conn.send_command(
                        "/system reset-configuration "
                        "no-default=yes "
                        "skip-backup=yes "
                        "run-after-reset=preflight_config.rsc",
                        expect_string=r"(Dangerous!|\[.*@.*\])",
                        strip_prompt=False,
                        strip_command=False,
                        read_timeout=30,
                    )
                    print(output)
                    if "Dangerous!" in output:
                        print("[*] Confirming reset...")
                        try:
                            ssh_conn.send_command(
                                "y",
                                expect_string=r"(\[.*@.*\]|$)",
                                strip_prompt=False,
                                strip_command=False,
                                read_timeout=10,
                            )
                        except Exception:
                            pass
                    print("[+] Reset triggered — router is rebooting.")
            except Exception as e:
                print(f"[*] Connection closed by router (expected after reset): {e}")

            self._progress(
                100,
                "finish",
                hint="Your router is ready. You can close this window.",
            )
            self.on_success()

        except Exception as e:
            print(f"\n[X] CRITICAL ERROR: {str(e)}")
            self.on_error(e)
