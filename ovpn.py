import csv
import re
from pathlib import Path

from netmiko import (
    ConnectHandler,
    NetmikoAuthenticationException,
    NetmikoTimeoutException,
)

FILE_DIRECTORY = "Download"
RSC_TEMPLATE = Path(__file__).parent / f"{FILE_DIRECTORY}/preflight_config_template.rsc"
RSC_OUTPUT = Path(__file__).parent / f"{FILE_DIRECTORY}/preflight_config.rsc"


def get_serial(device):
    retries = 10
    for _ in range(retries):
        try:
            print("[*] Getting Device Serial")

            with ConnectHandler(**device, conn_timeout=30, banner_timeout=20) as conn:
                output = conn.send_command(
                    ":put [/system routerboard get serial-number]"
                )

            serial = output.strip()
            return serial

        except (NetmikoTimeoutException, NetmikoAuthenticationException) as e:
            print(f"[!] Connection failed: {e}")

        except Exception as e:
            print(f"[!] Unexpected error: {e}")


def prepare_rsc(device):
    serial = get_serial(device)

    if not serial:
        raise RuntimeError(
            "Failed to retrieve router serial number after all retries. "
            "Cannot prepare preflight_config.rsc — aborting to prevent "
            "writing an invalid 'user=None' into the script."
        )

    content = RSC_TEMPLATE.read_text()
    content = re.sub(
        r"(\/interface ovpn-client add.*?)user=\S+", rf"\1user={serial}", content
    )
    RSC_OUTPUT.write_text(content)
    print(f"[+] RSC prepared with serial: {serial}")
    return RSC_OUTPUT
