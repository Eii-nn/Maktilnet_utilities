import glob
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pexpect

from .config import PROMPT_REGEX


class AnsiFilteredLog:
    """
    pexpect logfile that strips VT100/ANSI escape sequences before printing.

    MikroTik's terminal is a full PTY — every keystroke causes the router to
    echo back cursor-movement and line-erase codes (ESC[K, ESC[A, etc.).
    Dumping those raw bytes to the GUI log produces the garbage '[K' noise
    and character-by-character password echoes seen in the output.
    """

    # Matches ESC + any CSI sequence, or lone ESC + single char
    _ANSI_RE = re.compile(r"\x1b(?:\[[0-9;]*[A-Za-z]|.)", re.DOTALL)

    def write(self, data):
        cleaned = self._ANSI_RE.sub("", data)
        if cleaned:
            sys.stdout.write(cleaned)

    def flush(self):
        sys.stdout.flush()

def list_files(folder):
    return [p.name for p in Path(folder).iterdir() if p.is_file()]

def check_all_exists(path: Path, files: list) -> bool:
    return path.is_dir() and all((path / f).is_file() for f in files)

def run_cmd(child: pexpect.spawn, command, interactions=None, timeout=30):
    child.setecho(False)
    interactions = interactions or []
    patterns = [p for p, _ in interactions]
    responses = [r for _, r in interactions]
    patterns.append(PROMPT_REGEX)

    if command:
        child.send(f"{command}\r\n")

    try:
        while True:
            idx = child.expect(patterns, timeout=timeout)
            if idx == len(patterns) - 1:
                break
            response = responses[idx]
            child.send(f"{response}\r\n" if response else "\r\n")
    except pexpect.TIMEOUT:
        print(f"[!] Timeout while executing: {command}")
    except pexpect.EOF:
        raise ConnectionError("Connection lost while executing command")

    return child.before.strip()

def simpleSSHCommand(ssh_conn, cmd):
    print(f"[*] Running SSH command: {cmd}")
    return ssh_conn.send_command(cmd)

def get_default_mikrotik_mac(timeout=90):
    """
    Parses the mactelnet address that has the default MikroTik router configuration.
    Looks for the default identity name "MikroTik" and default IP "192.168.88.1" or "0.0.0.0".
    Returns a tuple of (mac, ip), or (None, None) if not found.
    """
    print("[*] Scanning for default MikroTik router MAC address...")
    try:
        process = subprocess.Popen(
            ["mactelnet", "-l", "-B", "-t", str(timeout)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        start_time = time.time()
        for line in iter(process.stdout.readline, ""):
            if time.time() - start_time > timeout + 2:
                break
                
            line = line.strip()
            if not line or line.startswith("MAC-Address") or "Searching for MikroTik" in line:
                continue

            parts = line.split(",")
            print(parts)
            if len(parts) >= 9:
                # Strip single quotes in case mactelnet batch mode wraps values in them
                mac_raw = parts[0].strip().strip("'")
                
                # Ensure MAC is correctly 0-padded (e.g. 4c:5e:c:c3... -> 4c:5e:0c:c3...)
                mac = ":".join(f"{int(x, 16):02x}" for x in mac_raw.split(":"))
                
                identity = parts[1].strip().strip("'")
                ip = parts[8].strip().strip("'")
                
                if identity == "MikroTik" and ip in ("0.0.0.0", "192.168.88.1"):
                    print(f"[+] Found default MikroTik at {mac} (IP: {ip})")
                    process.terminate()
                    return mac, ip
                    
        process.terminate()
    except Exception as e:
        print(f"[!] Error scanning for MikroTik MAC: {e}")
    return None, None

def reset_default_config_via_ssh(ip, username="admin", password=""):
    """
    Wipes the default configuration on a router that is already reachable via SSH.
    This is used when mactelnet fails because the router has the default config loaded
    (IP 192.168.88.1 is reachable), causing it to reject new MAC-Telnet connections.
    After this reset, the router will reboot into a clean, no-defaults state.
    """
    print(f"[*] Router has default config (IP: {ip}) — wiping via SSH before proceeding...")
    try:
        result = subprocess.run(
            [
                "sshpass", "-p", password,
                "ssh",
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                "-o", "WarnWeakCrypto=no",
                f"{username}@{ip}",
                "/system reset-configuration no-defaults=yes skip-backup=yes",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            print("[+] Default config reset via SSH. Router is rebooting into clean state...")
        else:
            # Router may drop the session before returning an exit code — that's expected
            print(f"[*] SSH reset command sent (router likely rebooting): {result.stderr.strip() or 'no error output'}")
    except subprocess.TimeoutExpired:
        # Session drop after reset is expected
        print("[*] SSH session timed out after reset command — this is expected.")
    except Exception as e:
        print(f"[!] Could not reset via SSH: {e}")

def connect_mikrotik(username, password, max_retries=60):
    login_patterns = [
        "Login:",
        "Password:",
        r"Do you want to see the software license\? \[Y/n\]:",
        "new password>",
        "repeat new password>",
        r"Connecting to [0-9A-Fa-f:]+\.\.\.done",
        PROMPT_REGEX,
        "Login failed",
        r"(?i)default configuration.*\[[yY]/[nN]\]:",
        "Connection failed.",
    ]

    temporaryPass = "_temp_"
    use_v7 = True
    current_password = password

    mac_address, detected_ip = get_default_mikrotik_mac()
    if not mac_address:
        raise ConnectionError("Could not find a default MikroTik router via mactelnet scan.")

    # If the router already has its default configuration loaded and is reachable via IP,
    # mactelnet connections will fail. Wipe the config via SSH first, then wait for the
    # router to reboot into a clean state before starting the mactelnet retry loop.
    if detected_ip == "192.168.88.1":
        reset_default_config_via_ssh(detected_ip)
        print("[*] Waiting for router to reboot after SSH reset...")
        mac_address, detected_ip = get_default_mikrotik_mac()
        if not mac_address:
            raise ConnectionError("Router did not reappear after SSH reset.")
        print(f"[+] Router reappeared at {mac_address} (IP: {detected_ip})")

    for attempt in range(1, max_retries + 1):
        print(f"[*] Attempt {attempt}/{max_retries} ({'v7' if use_v7 else 'v6'})...")
        child = None
        try:
            child = pexpect.spawn(
                f"mactelnet {mac_address}",
                timeout=60,
                encoding="utf-8",
                echo=False,
            )
            child.logfile = AnsiFilteredLog()
            child.delaybeforesend = 0.3

            connected = False
            while True:
                idx = child.expect(login_patterns, timeout=20)

                match idx:
                    case 0:
                        child.send(f"{username}\r")
                    case 1:
                        child.send(f"{current_password}\r")
                    case 2:
                        child.send("n\r")
                    case 3:  # new password prompt
                        current_password = temporaryPass
                        child.send(f"{temporaryPass}\r")
                    case 4:  # repeat new password prompt
                        child.send(f"{temporaryPass}\r")
                    case 5:  # "Connecting to <MAC>...done" banner — ignore and wait
                        continue
                    case 6:  # prompt matched → return immediately
                        print("\n[+] Login successful.")
                        return child, current_password  # caller is responsible for closing
                    case 7:  # auth failure → hard stop, no more retries
                        raise ConnectionError(
                            f"Authentication failed (attempt {attempt}). "
                            "Check the password."
                        )
                    case 8:  # default configuration prompt
                        print("\n[*] Default configuration prompt detected. Keeping defaults for now to maintain connection...")
                        child.send("y\r")
                    case 9:  # Connection failed
                        print("\n[-] Connection failed string detected from mactelnet. Retrying...")
                        break
                    case _:
                        print(f"[!] Unexpected pattern on attempt {attempt}.")
                        break  # retry outer loop

        except ConnectionError:
            raise  # auth errors propagate immediately, no silent swallow
        except pexpect.TIMEOUT:
            print(f"[!] Attempt {attempt} timed out.")
            use_v7 = not use_v7
        except pexpect.EOF:
            print(f"[!] Attempt {attempt} got unexpected EOF.")
            use_v7 = not use_v7
        finally:
            # guarantee child is closed on every non-return path
            if child is not None and not child.closed and \
               not (child.isalive()):
                try:
                    child.close()
                except Exception:
                    pass

        time.sleep(2)  # brief pause before next mactelnet attempt

    raise ConnectionError("Failed to connect after all retries.")

def smart_scp_download(remote_path, remote_user, remote_ip, local_dest, password):
    cmd = [
        "sshpass",
        "-p",
        password,
        "scp",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-r",
        f"{remote_user}@{remote_ip}:{remote_path}",
        local_dest,
    ]
    return subprocess.run(cmd, capture_output=True).returncode == 0

def smart_scp_upload(local_path, remote_user, remote_ip, remote_dest, password):
    files = glob.glob(local_path)
    if not files:
        return False
    cmd = [
        "sshpass",
        "-p",
        password,
        "scp",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
    ]
    if any(os.path.isdir(f) for f in files):
        cmd.append("-r")
    cmd.extend(files)
    cmd.append(f"{remote_user}@{remote_ip}:{remote_dest}")
    success = subprocess.run(cmd, capture_output=True).returncode == 0
    if success:
        print("[*] SCP complete, waiting for router to flush to storage...")
        time.sleep(3)  # tagaan ang MikroTik time to write to flash
    return success


