import subprocess
import os
import re

class IPAdder:
    def __init__(self, interface="enp3s0"):
        self.interface = interface

    def is_root(self):
        """Checks if the script has sudo/root privileges."""
        return os.geteuid() == 0

    def has_ip(self, target_ip):
        """Checks if a specific IP exists on the interface."""
        try:
            # Running 'ip addr show' to get interface details
            result = subprocess.run(
                ["ip", "addr", "show", self.interface],
                capture_output=True,
                text=True,
                check=True
            )
            # Use regex to find the exact IP (prevents partial matches like .10 matching .101)
            pattern = rf"inet {re.escape(target_ip)}/"
            return bool(re.search(pattern, result.stdout))
        except subprocess.CalledProcessError:
            print(f"❌ Error: Interface {self.interface} not found.")
            return False

    def add_ip_alias(self, target_ip, cidr="24"):
        """Adds the IP if it's missing. Bypasses if it exists."""
        if self.has_ip(target_ip):
            print(f"✅ [Bypass] {target_ip} is already configured on {self.interface}.")
            return True

        if not self.is_root():
            print(f"🚫 [Error] Must run as root/sudo to add IP {target_ip}.")
            return False

        try:
            print(f"🌐 [Config] Adding {target_ip}/{cidr} to {self.interface}...")
            subprocess.run(
                ["ip", "addr", "add", f"{target_ip}/{cidr}", "dev", self.interface],
                check=True
            )
            print(f"🚀 [Success] Interface {self.interface} updated.")
            return True
        except subprocess.CalledProcessError as e:
            print(f"❌ [Failed] Could not add IP: {e}")
            return False

    def remove_ip_alias(self, target_ip, cidr="24"):
        """Optional: Clean up the IP after the script finishes."""
        if not self.has_ip(target_ip):
            return True
            
        try:
            print(f"🧹 [Cleanup] Removing {target_ip} from {self.interface}...")
            subprocess.run(
                ["ip", "addr", "del", f"{target_ip}/{cidr}", "dev", self.interface],
                check=True
            )
            return True
        except Exception as e:
            print(f"⚠️ Cleanup failed: {e}")
            return False