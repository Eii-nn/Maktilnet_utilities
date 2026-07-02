import time
import pexpect
import sys
import os
import subprocess
from netmiko import ConnectHandler

USERNAME = "admin"
STATIC_IP_ADD = "192.168.88.100"


password = input("Enter Mikrotik Password: ").strip() or ""

mikrotik_device = {
    'device_type': 'mikrotik_routeros',
    'host': STATIC_IP_ADD,
    'username': USERNAME,
    'password': password,
}

def simpleSSHCommand(ssh_conn, cmd):
    print(f"[*] Running Command SSH: {cmd}...")
    output = ssh_conn.send_command(cmd)
    print(output)
    return output

try:
    with ConnectHandler(**mikrotik_device) as checkingNM:
        simpleSSHCommand(checkingNM,'/system routerboard print')
        time.sleep(1)
        simpleSSHCommand(checkingNM,'/certificate print')
        time.sleep(1)
        simpleSSHCommand(checkingNM,'/interface ovpn-client print')
        time.sleep(1)
        
except Exception as e:
    print(f"[-] Netmiko cleanup failed: {e}")
