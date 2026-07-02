#!/usr/bin/env python3
"""
mikrotik_downgrade.py
MikroTik RouterOS v7 → v6 Downgrade Tool
Standalone script — mirrors the look/flow of preflight_integrated2.py
"""

import glob
import json
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import pexpect
from netmiko import ConnectHandler

# ─────────────────────────────────────────────────────────────────────────────
#  CONFIGURATION & CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

CONFIG_FILE    = os.path.expanduser("~/.mikrotik_downgrade_config.json")
USERNAME       = "admin"
STATIC_IP_ADD  = "192.168.88.100"
SCAN_TIMEOUT   = 20
PROMPT_REGEX   = r"\[.*@.*\]\s+>\s*"

# ── Downgrade target ──────────────────────────────────────────────────────────
TARGET_VERSION = "6.49.19"                        # must match filename below
NPK_DIR        = "Download/"
NPK_FILENAME   = "routeros-mipsbe-6.49.19.npk"   # placed in NPK_DIR locally
REMOTE_NPK     = NPK_FILENAME                     # lands in router root /

REBOOT_WAIT    = 90    # seconds to sleep right after a reboot command
POLL_INTERVAL  = 15    # seconds between SSH reconnect attempts
POLL_ATTEMPTS  = 14    # max attempts (~3.5 min total after initial sleep)

mikrotik_device = {
    "device_type": "mikrotik_routeros",
    "host":        STATIC_IP_ADD,
    "username":    USERNAME,
    "password":    "",
}

# ─────────────────────────────────────────────────────────────────────────────
#  GUI OUTPUT REDIRECT
# ─────────────────────────────────────────────────────────────────────────────

class GUIOutput:
    """Redirects print() to the Tkinter Text widget, thread-safely."""
    def __init__(self, widget):
        self.widget = widget

    def write(self, string):
        root.after(0, self._safe_write, string)

    def _safe_write(self, string):
        self.widget.insert(tk.END, string)
        self.widget.see(tk.END)

    def flush(self):
        pass

# ─────────────────────────────────────────────────────────────────────────────
#  BACKEND HELPERS  (identical pattern to preflight_integrated2.py)
# ─────────────────────────────────────────────────────────────────────────────

def run_cmd(child: pexpect.spawn, command, interactions=None, timeout=30):
    child.setecho(False)
    interactions = interactions or []
    patterns  = [p for p, _ in interactions]
    responses = [r for _, r in interactions]
    patterns.append(PROMPT_REGEX)

    if command:
        child.send(f"{command}\r\n")

    try:
        while True:
            idx = child.expect(patterns, timeout=timeout)
            if idx == len(patterns) - 1:
                break
            child.send(f"{responses[idx]}\r\n" if responses[idx] else "\r\n")
    except pexpect.TIMEOUT:
        print(f"[!] Timeout executing: {command}")
    except pexpect.EOF:
        raise ConnectionError("Connection lost while executing command")
    return child.before.strip()


def get_mikrotik_mac():
    print("[*] Scanning for MikroTik...")
    proc = subprocess.Popen(
        ["mactelnet", "-l"],
        stdout=subprocess.PIPE, text=True
    )
    time.sleep(SCAN_TIMEOUT)
    proc.terminate()
    stdout, _ = proc.communicate()
    macs = re.findall(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", stdout)
    if not macs:
        raise RuntimeError("No MikroTik devices found during scan.")
    return sorted(set(macs))[0]


def connect_mikrotik(mac_address, username, password, max_retries=4):
    print(f"[*] Connecting to {mac_address} via MAC-Telnet ...")
    try_count  = 0
    versionTest = 7

    while try_count < max_retries:
        try:
            child = pexpect.spawn(
                f"mactelnet {mac_address}",
                timeout=60, encoding="utf-8", echo=False
            )
            child.logfile = sys.stdout

            login_patterns = [
                "Login:",
                "Password:",
                r"Do you want to see the software license\? \[Y/n\]:",
                "new password>",
                "repeat new password>",
                PROMPT_REGEX,
            ]

            while True:
                idx = child.expect(login_patterns, timeout=20)
                match idx:
                    case 0:
                        child.sendline(username)
                    case 1:
                        child.sendline(password)
                    case 2:
                        child.sendline("n")
                    case 3:
                        mikrotik_device["password"] = temporaryPass = "_temp_"
                        if versionTest == 7:
                            child.sendline(temporaryPass)
                        else:
                            child.send(f"{temporaryPass}\r\n")
                    case 4:
                        if versionTest == 7:
                            child.sendline(temporaryPass)
                        else:
                            child.send(f"{temporaryPass}\r\n")
                    case 5:
                        print("\n[+] Login Successful.")
                        return child
                    case _:
                        print(f"[!] Unexpected pattern index: {idx}")
                        break

        except (pexpect.TIMEOUT, pexpect.EOF) as e:
            versionTest = 7 if versionTest == 6 else 6
            try_count += 1
            print(f"[!] Attempt {try_count} failed: {e}")
            if try_count >= max_retries:
                raise


def smart_scp_upload(local_path, remote_user, remote_ip, remote_dest, password):
    files = glob.glob(local_path)
    if not files:
        return False
    cmd = [
        "sshpass", "-p", password,
        "scp",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
    ]
    if any(os.path.isdir(f) for f in files):
        cmd.append("-r")
    cmd.extend(files)
    cmd.append(f"{remote_user}@{remote_ip}:{remote_dest}")
    result = subprocess.run(cmd, capture_output=True)
    return result.returncode == 0


def simpleSSHCommand(ssh_conn, cmd):
    print(f"[*] SSH: {cmd}")
    return ssh_conn.send_command(cmd)


def wait_for_router(device: dict, label: str = "reboot"):
    print(f"\n[~] Waiting {REBOOT_WAIT}s for router ({label}) ...")
    time.sleep(REBOOT_WAIT)

    for attempt in range(1, POLL_ATTEMPTS + 1):
        try:
            with ConnectHandler(**device) as ssh:
                out = ssh.send_command("/system resource print")
                if out:
                    print(f"[+] Router back online (attempt {attempt}).")
                    return
        except Exception:
            print(f"[~] Not yet — retrying ({attempt}/{POLL_ATTEMPTS}) ...")
            time.sleep(POLL_INTERVAL)

    raise TimeoutError(
        "Router did not come back online in time. Check the device manually."
    )

# ─────────────────────────────────────────────────────────────────────────────
#  DOWNGRADE LOGIC  (runs in background thread)
# ─────────────────────────────────────────────────────────────────────────────

def _wireless_package_present(package_output: str) -> bool:
    """Return True only when the package query contains a wireless package row."""
    return bool(re.search(r"(?i)\bwireless\b", package_output or ""))


def remove_wireless_package_before_downgrade(ui_updater) -> bool:
    """
    Uninstall the RouterOS v7 ``wireless`` package before the v7 → v6
    downgrade. Returns True when apply-changes started a reboot, or False
    when the package was already absent.
    """
    ui_updater(35, "Checking wireless package ...")
    reboot_started = False

    try:
        with ConnectHandler(**mikrotik_device) as ssh:
            package_output = ssh.send_command(
                '/system package print without-paging where name="wireless"'
            )

            if not _wireless_package_present(package_output):
                print("[i] Wireless package is not installed — skipping removal.")
                return False

            print("[+] Wireless package detected.")
            print("[*] Scheduling wireless package for uninstall ...")
            uninstall_output = ssh.send_command_timing(
                "/system package uninstall wireless",
                strip_prompt=False,
                strip_command=False,
                delay_factor=2,
            )
            if uninstall_output.strip():
                print(f"[>] {uninstall_output.strip()}")

            # Confirm that RouterOS accepted the uninstall request before rebooting.
            scheduled_output = ssh.send_command(
                '/system package print detail without-paging where name="wireless"'
            )
            if "scheduled for uninstall" not in scheduled_output.lower():
                raise RuntimeError(
                    "RouterOS did not schedule the wireless package for uninstall. "
                    "Package changes were not applied."
                )

            print("[+] Wireless package scheduled for uninstall.")
            print("[*] Applying package changes — router will reboot ...")

            # apply-changes performs the reboot. Mark it first so a dropped SSH
            # session is treated as the expected reboot rather than a failure.
            reboot_started = True
            apply_output = ssh.send_command_timing(
                "/system package apply-changes",
                strip_prompt=False,
                strip_command=False,
                delay_factor=3,
            )
            if apply_output.strip():
                print(f"[>] {apply_output.strip()}")

            # Some RouterOS builds may still display a reboot confirmation.
            if re.search(r"(?i)(reboot.*yes|\[y/n\]|\[y/N\])", apply_output):
                ssh.send_command_timing(
                    "y",
                    strip_prompt=False,
                    strip_command=False,
                    delay_factor=2,
                )

            print("[*] Wireless removal applied — router rebooting ...")
            return True

    except Exception as e:
        low = str(e).lower()
        if reboot_started and any(
            key in low
            for key in (
                "closed", "eof", "timed", "connect", "reset by peer",
                "socket", "not open"
            )
        ):
            print("[i] SSH disconnected because the router is rebooting (expected).")
            return True
        raise


def verify_wireless_package_removed():
    """Raise an error if the wireless package still exists after the reboot."""
    with ConnectHandler(**mikrotik_device) as ssh:
        package_output = ssh.send_command(
            '/system package print without-paging where name="wireless"'
        )

    if _wireless_package_present(package_output):
        raise RuntimeError(
            "Wireless package is still installed after apply-changes. "
            "Downgrade was stopped to avoid a package compatibility failure."
        )

    print("[✓] Wireless package successfully removed.")

def downgrade_logic(ui_updater):
    sys.stdout = GUIOutput(log_area)

    mikrotikPassword = mikrotik_entry.get() or ""
    mikrotik_device["password"] = mikrotikPassword
    save_config()

    try:
        # ── Step 1 : Scan & set static IP via MAC-Telnet ───────────────────
        ui_updater(10, "Scanning for MikroTik ...")
        target_mac = get_mikrotik_mac()

        ui_updater(20, "Connecting via MAC-Telnet ...")
        preConfig = connect_mikrotik(target_mac, USERNAME, mikrotikPassword)

        ui_updater(30, "Setting static IP via MAC-Telnet ...")
        run_cmd(preConfig, f"/ip address add address={STATIC_IP_ADD}/24 interface=ether3 disabled=no")
        run_cmd(preConfig, "/ip service enable ssh")
        preConfig.sendline("/quit")
        preConfig.close()
        time.sleep(5)

        # ── Step 2 : Remove RouterOS v7 wireless package ───────────────────
        # RouterOS 6.49.19 uses the legacy bundled wireless drivers. Keeping
        # the separate RouterOS v7 wireless package can block the downgrade.
        wireless_rebooted = remove_wireless_package_before_downgrade(ui_updater)
        if wireless_rebooted:
            ui_updater(42, "Waiting after wireless package removal ...")
            wait_for_router(mikrotik_device, label="wireless package removal")
            verify_wireless_package_removed()

        # ── Step 3 : Upload .npk to router root ───────────────────────────
        ui_updater(50, f"Uploading {NPK_FILENAME} to router ...")
        local_glob = os.path.join(NPK_DIR, NPK_FILENAME)
        files = glob.glob(local_glob)
        if not files:
            raise FileNotFoundError(
                f"NPK not found at {local_glob}\n"
                f"Place '{NPK_FILENAME}' inside '{NPK_DIR}' and try again."
            )

        # Upload to router root  (remote_dest = "/")
        upload_ok = smart_scp_upload(
            local_glob, USERNAME, STATIC_IP_ADD, "/", mikrotik_device["password"]
        )

        if not upload_ok:
            raise RuntimeError("SCP upload of NPK failed.")
        print(f"[+] {NPK_FILENAME} uploaded to router root.")

        # ── Step 4 : /system package downgrade ────────────────────────────
        ui_updater(62, "Running package downgrade (router will reboot) ...")
        try:
            with ConnectHandler(**mikrotik_device) as ssh:
                out = ssh.send_command_timing(
                    "/system package downgrade",
                    strip_prompt=False, strip_command=False, delay_factor=3,
                )
                print(f"[>] {out.strip()}")
                # confirm y regardless — harmless if prompt already gone
                time.sleep(1)
                ssh.send_command_timing(
                    "y",
                    strip_prompt=False, strip_command=False, delay_factor=3,
                )
                print("[*] Downgrade confirmed — router rebooting ...")
        except Exception as e:
            low = str(e).lower()
            if any(k in low for k in ("closed", "eof", "timed", "connect")):
                print("[i] Connection dropped — router is rebooting (expected).")
            else:
                raise

        # ── Step 5 : Wait for router to come back ─────────────────────────
        ui_updater(72, "Waiting for router to reboot ...")
        wait_for_router(mikrotik_device, label="post-downgrade reboot")

        # ── Step 6 : /system routerboard upgrade ──────────────────────────
        ui_updater(82, "Running routerboard upgrade ...")
        try:
            with ConnectHandler(**mikrotik_device) as ssh:
                out = ssh.send_command_timing(
                    "/system routerboard upgrade",
                    strip_prompt=False, strip_command=False, delay_factor=3,
                )
                print(f"[>] {out.strip()}")
                time.sleep(1)
                ssh.send_command_timing(
                    "y",
                    strip_prompt=False, strip_command=False, delay_factor=3,
                )
                print("[*] Routerboard upgrade confirmed — rebooting ...")

                # reboot so new firmware takes effect
                time.sleep(2)
                try:
                    ssh.send_command_timing(
                        "/system reboot",
                        strip_prompt=False, strip_command=False, delay_factor=3,
                    )
                    time.sleep(1)
                    ssh.send_command_timing(
                        "y",
                        strip_prompt=False, strip_command=False, delay_factor=2,
                    )
                except Exception:
                    pass  # connection already dropping is fine
        except Exception as e:
            low = str(e).lower()
            if any(k in low for k in ("closed", "eof", "timed", "connect")):
                print("[i] Connection dropped — router is rebooting (expected).")
            else:
                raise

        # ── Step 7 : Wait for final reboot ────────────────────────────────
        ui_updater(90, "Waiting for final reboot ...")
        wait_for_router(mikrotik_device, label="post-routerboard reboot")

        # ── Step 8 : Verify version ────────────────────────────────────────
        ui_updater(95, "Verifying downgrade ...")
        try:
            with ConnectHandler(**mikrotik_device) as ssh:
                res = simpleSSHCommand(ssh, "/system resource print")
                print(res)
                if TARGET_VERSION in res:
                    print(f"[✓] Version confirmed: RouterOS {TARGET_VERSION}")
                else:
                    print(
                        f"[!] Warning: '{TARGET_VERSION}' not found in output. "
                        f"Verify manually."
                    )
        except Exception as e:
            print(f"[!] Version check failed: {e}")

        # ── Step 9 : Reset configuration ───────────────────────────────────
        ui_updater(98, "Resetting Router Configuration...")
        try:
            with ConnectHandler(**mikrotik_device) as ssh_conn:
                print("[*] Sending reset command...")
                output = ssh_conn.send_command_timing(
                    "/system reset-configuration "
                    "no-defaults=yes "
                    "skip-backup=yes",
                    strip_prompt=False,
                    strip_command=False,
                    delay_factor=2,
                )
                print(output)
                if "Dangerous" in output or "y/N" in output:
                    print("[*] Confirming reset...")
                    output += ssh_conn.send_command_timing(
                        "y",
                        strip_prompt=False,
                        strip_command=False,
                        delay_factor=2,
                    )
                print("[*] Router reset initiated. Device rebooting...")
        except Exception as e:
            low = str(e).lower()
            if any(k in low for k in ("closed", "eof", "timed", "reset by peer")):
                print("[i] Connection dropped — router reset/reboot expected.")
            else:
                print(f"[-] Error during reset: {e}")

        # ── Done ───────────────────────────────────────────────────────────
        ui_updater(100, "Downgrade Complete!", "#2ecc71")
        root.after(0, lambda: messagebox.showinfo(
            "Success",
            f"RouterOS successfully downgraded to v{TARGET_VERSION}!"
        ))

    except Exception as e:
        ui_updater(0, "FAILED", "#e74c3c")
        print(f"\n[✗] CRITICAL ERROR: {e}")
        root.after(0, lambda: messagebox.showerror("Error", str(e)))
    finally:
        root.after(0, lambda: deploy_btn.config(state="normal"))

# ─────────────────────────────────────────────────────────────────────────────
#  UI HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def update_ui(val, txt, color="#ffffff"):
    root.after(0, lambda: _apply_ui_updates(val, txt, color))

def _apply_ui_updates(val, txt, color):
    progress["value"] = val
    status_label.config(text=txt, fg=color)

def save_config():
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump({"m_pass": mikrotik_entry.get()}, f)
    except Exception:
        pass

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
                mikrotik_entry.insert(0, data.get("m_pass", ""))
        except Exception:
            pass

def on_deploy_click():
    deploy_btn.config(state="disabled")
    log_area.delete(1.0, tk.END)
    threading.Thread(
        target=downgrade_logic, args=(update_ui,), daemon=True
    ).start()

# ─────────────────────────────────────────────────────────────────────────────
#  UI SETUP  (same dark theme as preflight_integrated2.py)
# ─────────────────────────────────────────────────────────────────────────────

root = tk.Tk()
root.title("MikroTik Downgrade Tool  v7 → v6")
root.geometry("500x700")
root.configure(bg="#1a1a2e")

# ── Banner ────────────────────────────────────────────────────────────────────
banner_frame = tk.Frame(root, bg="#1a1a2e")
banner_frame.pack(pady=(18, 4))

tk.Label(
    banner_frame,
    text="⬇  ROUTEROS DOWNGRADE",
    fg="#ff6b35", bg="#1a1a2e",
    font=("Arial", 15, "bold"),
).pack()

tk.Label(
    banner_frame,
    text=f"v7  →  v{TARGET_VERSION}",
    fg="#a2a8d3", bg="#1a1a2e",
    font=("Arial", 10),
).pack()

# divider
tk.Frame(root, height=1, bg="#2a2a4a").pack(fill="x", padx=30, pady=(8, 0))

# ── Info strip ────────────────────────────────────────────────────────────────
info_frame = tk.Frame(root, bg="#12122a", pady=6)
info_frame.pack(fill="x", padx=30, pady=(0, 8))

def info_row(label, value, val_color="#00d2ff"):
    row = tk.Frame(info_frame, bg="#12122a")
    row.pack(fill="x", padx=12, pady=1)
    tk.Label(row, text=label, fg="#6c72a0", bg="#12122a",
             font=("Courier", 8), anchor="w", width=16).pack(side="left")
    tk.Label(row, text=value,  fg=val_color, bg="#12122a",
             font=("Courier", 8), anchor="w").pack(side="left")

info_row("NPK File",   NPK_FILENAME)
info_row("Target",     f"RouterOS {TARGET_VERSION}")
info_row("Router IP",  STATIC_IP_ADD)
info_row("Wireless",   "Auto-remove before downgrade", "#2ecc71")

# ── Password field ────────────────────────────────────────────────────────────
form = tk.Frame(root, bg="#1a1a2e")
form.pack(pady=6, padx=40, fill="x")

tk.Label(form, text="MikroTik Password",
         fg="#a2a8d3", bg="#1a1a2e",
         font=("Arial", 9)).pack(anchor="w")
mikrotik_entry = tk.Entry(form, show="*", bg="#16213e", fg="white",
                           insertbackground="white",
                           relief="flat", highlightthickness=1,
                           highlightbackground="#2a2a6e",
                           highlightcolor="#ff6b35")
mikrotik_entry.pack(fill="x", ipady=7, pady=(2, 8))

# ── Progress bar ──────────────────────────────────────────────────────────────
pb_style = ttk.Style()
pb_style.theme_use("clam")
pb_style.configure(
    "Down.Horizontal.TProgressbar",
    troughcolor="#16213e",
    background="#ff6b35",
    bordercolor="#1a1a2e",
    lightcolor="#ff6b35",
    darkcolor="#cc4a1a",
)

progress = ttk.Progressbar(root, length=420, style="Down.Horizontal.TProgressbar")
progress.pack(pady=(4, 2))

status_label = tk.Label(root, text="Enter MikroTik password to begin",
                         bg="#1a1a2e", fg="#a2a8d3",
                         font=("Arial", 9))
status_label.pack(pady=(0, 6))

# ── Log area ──────────────────────────────────────────────────────────────────
log_container = tk.Frame(root, bg="#0f0f1b",
                          highlightthickness=1, highlightbackground="#2a2a4a")
log_container.pack(pady=4, padx=20, fill="both", expand=True)

log_area = tk.Text(
    log_container,
    bg="#0a0a16", fg="#00ff9f",
    font=("Courier", 8),
    relief="flat",
    selectbackground="#2a2a6e",
    wrap="word",
)
log_scroll = tk.Scrollbar(log_container, command=log_area.yview,
                            bg="#1a1a2e", troughcolor="#0f0f1b")
log_area.configure(yscrollcommand=log_scroll.set)
log_scroll.pack(side="right", fill="y")
log_area.pack(side="left", fill="both", expand=True, padx=4, pady=4)

# ── Button row ────────────────────────────────────────────────────────────────
btn_row = tk.Frame(root, bg="#1a1a2e")
btn_row.pack(pady=14)

deploy_btn = tk.Button(
    btn_row,
    text="⬇  START DOWNGRADE",
    command=on_deploy_click,
    bg="#ff6b35", fg="white",
    activebackground="#cc4a1a",
    activeforeground="white",
    font=("Arial", 11, "bold"),
    relief="flat",
    padx=28, pady=12,
    cursor="hand2",
)
deploy_btn.pack()

# ── Footer ────────────────────────────────────────────────────────────────────
tk.Label(
    root,
    text=f"NPK must exist at  {NPK_DIR}{NPK_FILENAME}",
    fg="#3a3a5e", bg="#1a1a2e",
    font=("Arial", 7),
).pack(pady=(0, 6))

# ─────────────────────────────────────────────────────────────────────────────
load_config()
root.mainloop()
