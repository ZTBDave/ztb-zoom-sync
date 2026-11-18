#!/usr/bin/env python3
"""
ZTB Zoom Endpoint Group & Network Object Automation
Compatible with Mike Dechow's ztb-objects-bulk repository pattern.

Fetches Zoom IPs, FQDNs, and Ports from Zoom Support KB0060548,
generates pipe-delimited CSVs for bulk ingestion, and uses ZTB REST APIs to
provision and update Network and L4 Port Objects.
"""

import os
import re
import csv
import json
import uuid
import ipaddress
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

# ==============================================================================
# CONFIGURATION
# ==============================================================================
ZTB_BASE_URL = os.getenv("ZTB_BASE_URL")
if not ZTB_BASE_URL:
    raise ValueError(
        "Missing required environment variable: ZTB_BASE_URL is not set.\n"
        "Export it in your shell or .env: export ZTB_BASE_URL='https://<tenant>-api.goairgap.com'"
    )
ZTB_BASE_URL = ZTB_BASE_URL.rstrip("/")
ZTB_UI_ORIGIN = ZTB_BASE_URL.replace("-api.", ".")

ZTB_API_KEY = os.getenv("ZTB_API_KEY")
if not ZTB_API_KEY:
    raise ValueError(
        "Missing required environment variable: ZTB_API_KEY is not set.\n"
        "Export it in your shell or .env: export ZTB_API_KEY='<your_api_key>'"
    )

ZOOM_KB_URL = "https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0060548"

# Authoritative baseline fallback endpoints per Zoom Network Architecture
DEFAULT_ZOOM_FQDNS = [
    "*.zoom.us",
    "*.zoom.com",
    "*.zoomstatus.com",
    "*.zoomapp.cloud",
    "*.zoomonprem.com",
    "zdmapi.zoom.us",
]

DEFAULT_ZOOM_PORTS = {
    "tcp": ["80", "443", "5091", "8801", "8802"],
    "udp": ["3478", "3479", "8801-8810", "20000-64000"],
}

DEFAULT_ZOOM_IPV4_SUBNETS = [
    "3.7.35.0/25",
    "3.235.82.0/23",
    "3.235.96.0/23",
    "4.34.125.128/25",
    "4.35.64.128/25",
    "8.5.128.0/23",
    "15.220.80.0/24",
    "18.254.23.128/25",
    "18.254.61.0/25",
    "50.239.202.0/23",
    "50.239.204.0/24",
    "52.61.100.128/25",
    "64.125.62.0/24",
    "64.211.144.0/24",
    "64.224.32.0/19",
    "65.39.152.0/24",
    "69.174.57.0/24",
    "69.174.108.0/22",
    "101.36.167.0/24",
    "101.36.170.0/23",
    "103.122.166.0/23",
    "111.33.115.0/25",
    "115.110.154.192/26",
    "120.29.148.0/24",
    "134.224.0.0/16",
    "137.66.128.0/17",
    "144.195.0.0/16",
    "147.124.96.0/19",
    "149.137.0.0/17",
    "156.45.0.0/17",
    "159.124.0.0/16",
    "162.12.232.0/22",
    "162.255.36.0/22",
    "165.254.88.0/23",
    "166.108.64.0/18",
    "168.140.0.0/17",
    "170.114.0.0/16",
    "173.231.80.0/20",
    "192.204.12.0/22",
    "198.251.128.0/17",
    "204.80.104.0/21",
    "204.141.28.0/22",
    "206.247.0.0/16",
    "207.226.132.0/24",
    "209.9.211.0/24",
    "209.9.215.0/24",
    "213.19.144.0/24",
    "213.244.140.0/24",
    "221.122.63.0/24",
    "221.122.64.0/24",
]


# ==============================================================================
# 1. PARSING & EXTRACTION HELPERS
# ==============================================================================
def normalize_ipv4_cidr(ip_str: str) -> str | None:
    """
    Validates IPv4 network string and guarantees CIDR notation (/32 for single hosts).
    Crucial for ZTB: API stores raw strings; missing mask causes silent match failure.
    """
    ip_str = ip_str.strip()
    try:
        net = ipaddress.ip_network(ip_str, strict=False)
        if isinstance(net, ipaddress.IPv4Network):
            return f"{net.network_address}/{net.prefixlen}"
    except ValueError:
        pass
    return None


def normalize_fqdn(raw: str) -> str | None:
    """
    Normalizes URLs/FQDNs to comply with ZTB FQDN validation rules:
    - Replaces patterns like '*xxx.yyy.zzz' with '*.yyy.zzz' (wildcards the entire first word).
    - Strips http://, https://, URL paths, and port suffixes.
    - Discards invalid characters or internal wildcards (e.g. foo.*.com).
    """
    cleaned = raw.strip().lower()
    if cleaned.startswith("http://"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("https://"):
        cleaned = cleaned[8:]
    cleaned = cleaned.split("/")[0].split(":")[0].strip()

    if not cleaned or "." not in cleaned:
        return None

    # Replace *xxx.yyy.zzz with *.yyy.zzz (wildcard the entire first word)
    if cleaned.startswith("*") and not cleaned.startswith("*."):
        cleaned = re.sub(r"^\*[^.]+\.", "*.", cleaned)

    # Discard if internal wildcard exists or multiple wildcards
    if cleaned.count("*") > 1 or ("*" in cleaned and not cleaned.startswith("*.")):
        return None

    return cleaned


def fetch_zoom_endpoints() -> dict:
    """
    Scrapes Zoom KB0060548 for published CIDRs, FQDNs, and port configurations.
    Falls back gracefully to official architecture defaults if scraping is blocked.
    """
    print(f"[*] Fetching Zoom Network Endpoints from: {ZOOM_KB_URL}")
    ipv4_set = set()
    fqdn_set = set()
    tcp_ports = set(DEFAULT_ZOOM_PORTS["tcp"])
    udp_ports = set(DEFAULT_ZOOM_PORTS["udp"])

    for f in DEFAULT_ZOOM_FQDNS:
        norm = normalize_fqdn(f)
        if norm:
            fqdn_set.add(norm)

    for subnet in DEFAULT_ZOOM_IPV4_SUBNETS:
        cidr = normalize_ipv4_cidr(subnet)
        if cidr:
            ipv4_set.add(cidr)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    try:
        resp = requests.get(ZOOM_KB_URL, headers=headers, timeout=20)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            text = soup.get_text()

            # Extract any IPv4 CIDRs found on page
            ip_pattern = r"\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?\b"
            for match in re.findall(ip_pattern, text):
                cidr = normalize_ipv4_cidr(match)
                if cidr and not cidr.startswith("127.") and not cidr.startswith("0."):
                    ipv4_set.add(cidr)

            # Extract Zoom domain patterns
            domain_pattern = r"(?:\*\.)?[a-zA-Z0-9_-]+\.zoom(?:\.us|\.com|\.cloud)"
            for match in re.findall(domain_pattern, text):
                norm = normalize_fqdn(match)
                if norm:
                    fqdn_set.add(norm)

            print("[+] Successfully scraped Zoom Support Knowledge Base.")
        else:
            print(f"[!] Support page returned status {resp.status_code}; using authoritative baseline.")
    except Exception as exc:
        print(f"[!] Scraping failed ({exc}); utilizing authoritative baseline defaults.")

    return {
        "ips": sorted(ipv4_set),
        "fqdns": sorted(fqdn_set),
        "tcp_ports": sorted(tcp_ports),
        "udp_ports": sorted(udp_ports),
    }


# ==============================================================================
# 2. EXPORT COMPATIBILITY FOR ztb-objects-bulk (CSV EXPORT)
# ==============================================================================
def export_pipe_delimited_csv(filename: str, objects: list[dict]):
    """
    Exports objects using the pipe (|) delimiter required by ZTB CSV ingestion
    and Mike Dechow's ztb-objects-bulk ingestion format.
    Format: name|type|values
    """
    print(f"[*] Exporting bulk object file to {filename} (pipe-delimited)...")
    with open(filename, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="|")
        writer.writerow(["name", "type", "values"])
        for obj in objects:
            writer.writerow([obj["name"], obj["type"], ",".join(obj["values"])])
    print(f"[+] Successfully wrote {len(objects)} object rows to {filename}.")


# ==============================================================================
# 3. ZTB REST API CLIENT
# ==============================================================================
class ZTBClient:
    def __init__(self, api_base: str, api_key: str, ui_origin: str):
        self.api_base = api_base
        self.api_key = api_key
        self.ui_origin = ui_origin
        self.session = requests.Session()
        self.token = None

    def authenticate(self):
        """Exchange API Key for Bearer delegate token and set headers."""
        auth_url = f"{self.api_base}/api/v3/api-key-auth/login"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Origin": self.ui_origin,
            "Referer": f"{self.ui_origin}/",
            "X-Requested-With": "XMLHttpRequest",
        }
        try:
            resp = self.session.post(auth_url, json={"api_key": self.api_key}, headers=headers, timeout=15)
            if resp.status_code >= 400:
                print(f"[!] Auth Failed ({resp.status_code}): {resp.text}")
            resp.raise_for_status()
        except requests.exceptions.ConnectionError:
            print(f"\n[!] Connection Error: Unable to resolve or connect to '{self.api_base}'.")
            print("    Please check that ZTB_BASE_URL points to your live tenant API endpoint")
            print("    (e.g., https://<tenant>-api.goairgap.com) and that DNS resolution works.\n")
            raise SystemExit(1) from None
        except requests.exceptions.HTTPError as exc:
            print(f"\n[!] Authentication HTTP Error ({exc.response.status_code}): {exc.response.text}\n")
            raise SystemExit(1) from None

        data = resp.json()
        self.token = data.get("result", {}).get("delegate_token") or data.get("delegate_token")
        if not self.token:
            raise RuntimeError(f"Missing delegate_token in response: {data}")

        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Origin": self.ui_origin,
            "Referer": f"{self.ui_origin}/",
            "X-Requested-With": "XMLHttpRequest",
        })
        print("[+] Authenticated to ZTB Management API.")

    def upsert_group(self, name: str, group_type: str, member_attributes: dict) -> int:
        """Create or update group object via /api/v2/groups."""
        get_url = f"{self.api_base}/api/v2/groups"
        params = {"group_type": group_type, "group_owner": "user", "size": 500, "page": 0}
        resp = self.session.get(get_url, params=params, timeout=20)
        resp.raise_for_status()

        groups_list = resp.json().get("result", resp.json() if isinstance(resp.json(), list) else [])
        existing_id = None
        for g in groups_list:
            if g.get("name") == name or g.get("display_name") == name:
                existing_id = g.get("group_id")
                break

        payload = {
            "name": name,
            "display_name": name,
            "type": group_type,
            "autonomous": False,
            "hidden": False,
            "owner": "user",
            "member_attributes": member_attributes,
        }

        if existing_id:
            print(f"[*] Updating existing {group_type} '{name}' (ID: {existing_id})...")
            url = f"{self.api_base}/api/v2/groups/{existing_id}?refresh_token=enabled"
            r = self.session.put(url, json=payload, timeout=30)
            if r.status_code >= 400:
                print(f"[!] Group update error ({r.status_code}): {r.text}")
            r.raise_for_status()
            return existing_id
        else:
            print(f"[*] Creating new {group_type} '{name}'...")
            url = f"{self.api_base}/api/v2/groups?refresh_token=enabled"
            r = self.session.post(url, json=payload, timeout=30)
            if r.status_code >= 400:
                print(f"[!] Group creation error ({r.status_code}): {r.text}")
            r.raise_for_status()
            res_data = r.json().get("result", r.json())
            group_id = res_data.get("group_id")
            print(f"[+] Created object '{name}' (ID: {group_id}).")
            return group_id


# ==============================================================================
# MAIN EXECUTION ROUTINE
# ==============================================================================
def main():
    # 1. Fetch Zoom Endpoints
    zoom = fetch_zoom_endpoints()
    print(f"[+] Extracted: {len(zoom['ips'])} CIDRs, {len(zoom['fqdns'])} FQDNs, "
          f"{len(zoom['tcp_ports'])} TCP port groups, {len(zoom['udp_ports'])} UDP port groups.")

    # 2. Export pipe-delimited CSV for Mike Dechow's ztb-objects-bulk pattern
    bulk_objects = [
        {"name": "Zoom-Required-IPs", "type": "network", "values": zoom["ips"]},
        {"name": "Zoom-Required-FQDNs", "type": "network", "values": zoom["fqdns"]},
    ]
    export_pipe_delimited_csv("ztb_zoom_network_objects.csv", bulk_objects)

    # 3. Direct ZTB REST API Execution (Upsert Network Objects)
    ztb = ZTBClient(ZTB_BASE_URL, ZTB_API_KEY, ZTB_UI_ORIGIN)
    ztb.authenticate()

    # Create/Update IP Network Object
    ip_group_id = ztb.upsert_group(
        name="Zoom-Required-IPs",
        group_type="network",
        member_attributes={
            "ip_prefix": zoom["ips"],
            "ip_prefix_local": zoom["ips"],
            "fqdn": [],
            "fqdn_local": [],
        },
    )

    # Create/Update FQDN Network Object
    fqdn_group_id = ztb.upsert_group(
        name="Zoom-Required-FQDNs",
        group_type="network",
        member_attributes={
            "ip_prefix": [],
            "ip_prefix_local": [],
            "fqdn": zoom["fqdns"],
            "fqdn_local": zoom["fqdns"],
        },
    )

    # Create/Update L4 Port Object
    port_entries = []
    if zoom["tcp_ports"]:
        port_entries.append(f"tcp:{','.join(zoom['tcp_ports'])}")
    if zoom["udp_ports"]:
        port_entries.append(f"udp:{','.join(zoom['udp_ports'])}")

    port_group_id = ztb.upsert_group(
        name="Zoom-Required-Ports",
        group_type="l4port",
        member_attributes={"port_group": port_entries},
    )

    print("\n[+] Object synchronization complete:")
    print(f"    - IP Object:   Zoom-Required-IPs   (ID: {ip_group_id})")
    print(f"    - FQDN Object: Zoom-Required-FQDNs (ID: {fqdn_group_id})")
    print(f"    - Port Object: Zoom-Required-Ports (ID: {port_group_id})\n")


if __name__ == "__main__":
    main()
