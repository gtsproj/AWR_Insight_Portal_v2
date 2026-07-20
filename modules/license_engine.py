# modules/license_engine.py
# ============================================================
# AWR Insight Portal v2 — License Engine
#
# Key design:
#   - MAC-address bound license keys
#   - AES-256 encryption (no internet required)
#   - Supports: Trial (T15/T30), Standard, Professional, Enterprise
#   - Grace period: 7 days after expiry
#   - PDB handling: CDB=1 unit, first 3 PDBs free, extra=1 unit each
#   - Instance-wise DB counting (RAC = 1 unit per node)
#
# Key format: AVK-{TIER}-{BASE64_ENCRYPTED_PAYLOAD}
# ============================================================

import os
import sys
import json
import base64
import hashlib
import logging
import uuid
import struct
from datetime import date, datetime, timedelta
from typing import Optional

logger = logging.getLogger("license_engine")

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "common"))

# ── Encryption key (embedded — change for each customer build) ────
# In production: replace with a unique secret per portal build
# This is XOR-based obfuscation suitable for offline license validation
_LICENSE_SECRET = b"AWRInsightPortalV2_Avekshaa2026_SecretKey_DoNotShare"

# ── License tiers ─────────────────────────────────────────────────
# All tiers include all three recommendation modes (Rules / Local AI / Cloud AI)
# Differentiation is purely by DB instance count and SAR server count
TIERS = {
    "T15": {"name": "Trial 15 Days",   "db_limit": 2,  "sar_limit": 2,  "trial": True,  "days": 15},
    "T30": {"name": "Trial 30 Days",   "db_limit": 2,  "sar_limit": 2,  "trial": True,  "days": 30},
    "STD": {"name": "Standard",        "db_limit": 5,  "sar_limit": 5,  "trial": False, "days": 365},
    "PRO": {"name": "Professional",    "db_limit": 15, "sar_limit": 15, "trial": False, "days": 365},
    "ENT": {"name": "Enterprise",      "db_limit": -1, "sar_limit": -1, "trial": False, "days": 365},
}

# All tiers include all modes — customer owns Ollama and Cloud AI API keys
# Mode selection is entirely customer-controlled in Settings
ALLOWED_MODES = ["rules", "local_ai", "cloud_ai"]

GRACE_DAYS = 7          # days after expiry before hard block
PDB_FREE_COUNT = 3      # PDBs included free per CDB
MONTHLY_AI_LIMIT = 200  # default monthly cloud AI recommendation cap


# ══════════════════════════════════════════════════════════════════
# MAC ADDRESS
# ══════════════════════════════════════════════════════════════════

def get_mac_address() -> str:
    """
    Get MAC address for license binding.

    Checks portal_config.license_mac_override first —
    if set, uses that MAC directly (most reliable for production).

    Otherwise auto-detects physical LAN adapter MAC with priority:
      1. Physical LAN (Ethernet) adapter that is UP
      2. Physical LAN adapter (any state)
      3. Wi-Fi adapter
      4. Any non-virtual adapter
    """
    import sys as _sys

    # ── Check for pinned MAC in config ───────────────────────────
    try:
        from db import get_db_connection as _gdb
        _conn = _gdb()
        with _conn.cursor() as _cur:
            _cur.execute(
                "SELECT value FROM portal_config WHERE key='license_mac_override'"
            )
            _row = _cur.fetchone()
        _conn.close()
        if _row and _row[0] and _row[0].strip():
            pinned = _row[0].strip().replace("-", ":").upper()
            logger.debug(f"Using pinned MAC from config: {pinned}")
            return pinned
    except Exception:
        pass

    # ── Auto-detect ───────────────────────────────────────────────
    SKIP_NAMES = [
        "vmware", "virtualbox", "vbox", "virtual", "hyper-v", "hyperv",
        "wsl", "docker", "loopback", "lo", "bluetooth", "teredo",
        "isatap", "6to4", "tap", "tun", "vpn", "pptp", "l2tp",
        "miniport", "pseudo", "tunnel", "wan miniport",
        "microsoft wi-fi direct", "hosted network",
        "local area connection*",  # Windows virtual/hotspot adapters
    ]

    # True physical LAN adapter names
    LAN_NAMES = [
        "ethernet", "gigabit", "realtek", "intel", "broadcom",
        "marvell", "atheros", "eth0", "eth1", "enp", "ens",
        "em0", "em1", "bge", "lan",
    ]
    # Exclude "local area connection*" pattern (virtual)
    LAN_EXCLUDE = ["local area connection*"]

    WIFI_NAMES = [
        "wi-fi", "wifi", "wireless", "802.11", "wlan",
        "wlan0", "wlan1",
    ]

    def _is_valid_mac(mac: str) -> bool:
        if not mac or mac in ("00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"):
            return False
        try:
            first_byte = int(mac.replace(":", "").replace("-", "")[:2], 16)
            if first_byte & 0x02:  # locally administered
                return False
        except Exception:
            pass
        return True

    def _normalise_mac(mac: str) -> str:
        clean = mac.replace("-", ":").upper()
        parts = clean.split(":")
        if len(parts) == 6:
            return ":".join(p.zfill(2) for p in parts)
        return clean

    try:
        import psutil
        iface_addrs = psutil.net_if_addrs()
        iface_stats = psutil.net_if_stats()

        lan_candidates  = []
        wifi_candidates = []
        other_candidates = []

        for iface_name, addrs in iface_addrs.items():
            name_lower = iface_name.lower()

            # Skip virtual/software adapters
            if any(skip in name_lower for skip in SKIP_NAMES):
                continue

            # Get MAC
            mac = None
            for addr in addrs:
                addr_str = addr.address or ""
                if (":" in addr_str or "-" in addr_str) and len(addr_str) in (17, 14):
                    mac = _normalise_mac(addr_str)
                    break

            if not mac or not _is_valid_mac(mac):
                continue

            stats = iface_stats.get(iface_name)
            is_up = stats.isup if stats else True
            entry = (iface_name, mac, is_up)

            # Classify — exclude "Local Area Connection*" pattern
            is_lan_exclude = any(x in name_lower for x in LAN_EXCLUDE)
            is_lan  = not is_lan_exclude and any(x in name_lower for x in LAN_NAMES)
            is_wifi = any(x in name_lower for x in WIFI_NAMES)

            if is_lan:
                lan_candidates.append(entry)
            elif is_wifi:
                wifi_candidates.append(entry)
            elif not is_lan_exclude:
                other_candidates.append(entry)

        def _best(candidates):
            up = [c for c in candidates if c[2]]
            return (up or candidates)[0][1] if (up or candidates) else None

        mac = (_best(lan_candidates) or
               _best(wifi_candidates) or
               _best(other_candidates))
        if mac:
            return mac

    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"psutil MAC detection failed: {e}")

    # ── Windows ipconfig fallback ─────────────────────────────────
    if _sys.platform == "win32":
        try:
            import subprocess, re
            result = subprocess.run(["ipconfig", "/all"],
                                    capture_output=True, text=True, timeout=10)
            lines = result.stdout.splitlines()
            in_ethernet = False
            in_wifi     = False
            lan_mac  = None
            wifi_mac = None

            for line in lines:
                ll = line.lower()
                if "ethernet adapter" in ll:
                    # Skip virtual
                    in_ethernet = not any(s in ll for s in SKIP_NAMES)
                    in_wifi = False
                elif "wireless lan adapter" in ll or "wi-fi" in ll:
                    in_wifi = True
                    in_ethernet = False
                elif "adapter" in ll:
                    in_ethernet = False
                    in_wifi = False

                if "physical address" in ll:
                    match = re.search(
                        r'([0-9A-Fa-f]{2}[-:][0-9A-Fa-f]{2}[-:][0-9A-Fa-f]{2}'
                        r'[-:][0-9A-Fa-f]{2}[-:][0-9A-Fa-f]{2}[-:][0-9A-Fa-f]{2})',
                        line)
                    if match:
                        m = _normalise_mac(match.group(1))
                        if _is_valid_mac(m):
                            if in_ethernet and not lan_mac:
                                lan_mac = m
                            elif in_wifi and not wifi_mac:
                                wifi_mac = m

            if lan_mac:
                return lan_mac
            if wifi_mac:
                return wifi_mac
        except Exception as e:
            logger.debug(f"ipconfig fallback failed: {e}")

    # Final fallback
    try:
        import uuid
        mac = uuid.UUID(int=uuid.getnode()).hex[-12:]
        return ":".join(mac[i:i+2] for i in range(0, 12, 2)).upper()
    except Exception:
        return "00:00:00:00:00:00"

    # ── Windows fallback via ipconfig ────────────────────────────
    if _sys.platform == "win32":
        try:
            import subprocess
            result = subprocess.run(
                ["ipconfig", "/all"],
                capture_output=True, text=True, timeout=10
            )
            lines = result.stdout.splitlines()
            in_ethernet = False
            in_wifi     = False
            lan_mac  = None
            wifi_mac = None

            for line in lines:
                ll = line.lower()
                # Detect adapter section headers
                if "ethernet adapter" in ll:
                    in_ethernet = True
                    in_wifi     = False
                    # Skip virtual
                    if any(s in ll for s in SKIP_NAMES):
                        in_ethernet = False
                elif "wireless lan adapter" in ll or "wi-fi" in ll:
                    in_wifi     = True
                    in_ethernet = False
                    if any(s in ll for s in SKIP_NAMES):
                        in_wifi = False
                elif "adapter" in ll:
                    in_ethernet = False
                    in_wifi     = False

                # Extract MAC
                if "physical address" in ll:
                    match = re.search(
                        r'([0-9A-Fa-f]{2}[-:][0-9A-Fa-f]{2}[-:][0-9A-Fa-f]{2}'
                        r'[-:][0-9A-Fa-f]{2}[-:][0-9A-Fa-f]{2}[-:][0-9A-Fa-f]{2})',
                        line
                    )
                    if match:
                        mac_found = _normalise_mac(match.group(1))
                        if _is_valid_mac(mac_found):
                            if in_ethernet and not lan_mac:
                                lan_mac = mac_found
                            elif in_wifi and not wifi_mac:
                                wifi_mac = mac_found

            if lan_mac:
                return lan_mac
            if wifi_mac:
                return wifi_mac

        except Exception as e:
            logger.debug(f"ipconfig MAC fallback failed: {e}")

    # ── Final fallback — uuid.getnode ────────────────────────────
    try:
        mac = uuid.UUID(int=uuid.getnode()).hex[-12:]
        mac_str = ":".join(mac[i:i+2] for i in range(0, 12, 2)).upper()
        if _is_valid_mac(mac_str):
            return mac_str
    except Exception:
        pass

    return "00:00:00:00:00:00"


def get_machine_fingerprint() -> str:
    """
    Stable machine fingerprint from physical MAC + hostname.
    More robust than MAC alone — hostname change + MAC change = new fingerprint.
    Used for displaying to customer during license request.
    """
    import socket
    mac      = get_mac_address()
    hostname = socket.gethostname().upper()
    raw      = f"{mac}|{hostname}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16].upper()


def get_all_physical_macs() -> list:
    """
    Return all physical adapter MACs for display in Settings.
    Useful when customer has multiple NICs — Avekshaa can choose
    which one to bind the license to.
    Returns list of dicts: [{name, mac, type, is_up}]
    """
    SKIP_NAMES = [
        "vmware", "virtualbox", "vbox", "virtual", "hyper-v",
        "wsl", "docker", "loopback", "bluetooth", "teredo",
        "isatap", "tap", "tun", "vpn", "miniport", "pseudo",
        "tunnel", "wan miniport", "microsoft wi-fi direct",
    ]

    result = []
    try:
        import psutil
        iface_addrs = psutil.net_if_addrs()
        iface_stats = psutil.net_if_stats()

        for iface_name, addrs in iface_addrs.items():
            if any(s in iface_name.lower() for s in SKIP_NAMES):
                continue
            for addr in addrs:
                addr_str = addr.address or ""
                if (":" in addr_str or "-" in addr_str) and len(addr_str) in (17, 14):
                    mac = addr_str.replace("-", ":").upper()
                    nl  = iface_name.lower()
                    t   = ("LAN" if any(x in nl for x in ["ethernet","lan","eth","enp","ens"])
                           else "Wi-Fi" if any(x in nl for x in ["wi-fi","wifi","wireless","wlan"])
                           else "Other")
                    st  = iface_stats.get(iface_name)
                    result.append({
                        "name": iface_name, "mac": mac,
                        "type": t, "is_up": st.isup if st else False
                    })
                    break
    except Exception:
        pass

    return result


# ══════════════════════════════════════════════════════════════════
# KEY GENERATION (Avekshaa internal tool)
# ══════════════════════════════════════════════════════════════════

# ── Epoch for days-since calculation ─────────────────────────────
_KEY_EPOCH = date(2024, 1, 1)

# ── Tier codes for binary packing ────────────────────────────────
_TIER_CODE = {"T15": 1, "T30": 2, "STD": 3, "PRO": 4, "ENT": 5}
_CODE_TIER = {v: k for k, v in _TIER_CODE.items()}


def _xor_crypt(data: bytes, key: bytes) -> bytes:
    """XOR cipher with repeating key."""
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def generate_license_key(
    tier: str,
    mac_address: str,
    db_count: int,
    sar_count: int,
    expiry_date: date,
    customer_id: str,
    customer_name: str,
) -> str:
    """
    Generate a compact license key.

    Binary payload (16 bytes):
      1 byte  — tier code
      6 bytes — MAC address octets
      1 byte  — db_count  (max 255; 0 = unlimited for ENT)
      1 byte  — sar_count (max 255; 0 = unlimited for ENT)
      2 bytes — days since 2024-01-01 (expiry)
      3 bytes — customer_id hash (sha256 first 3 bytes)
      2 bytes — checksum (sum of above % 65536)

    Output format: AVK-{TIER}-{22 base64url chars}
    Total length: ~30 characters
    """
    if tier not in _TIER_CODE:
        raise ValueError(f"Unknown tier: {tier}. Valid: {list(_TIER_CODE.keys())}")

    # Normalise MAC → 6 bytes
    mac_clean = mac_address.replace("-", ":").upper()
    mac_bytes = bytes(int(x, 16) for x in mac_clean.split(":"))
    if len(mac_bytes) != 6:
        raise ValueError(f"Invalid MAC address: {mac_address}")

    days = (expiry_date - _KEY_EPOCH).days
    if days < 0 or days > 65535:
        raise ValueError(f"Expiry date out of range: {expiry_date}")

    db_val  = 0 if db_count == -1 else min(db_count, 255)
    sar_val = 0 if sar_count == -1 else min(sar_count, 255)

    cust_hash = hashlib.sha256(customer_id.encode()).digest()[:3]

    # Pack 14-byte payload
    payload = struct.pack(
        ">B6sBBH3s",
        _TIER_CODE[tier],
        mac_bytes,
        db_val,
        sar_val,
        days,
        cust_hash,
    )

    # 2-byte checksum
    chk = sum(payload) & 0xFFFF
    payload += struct.pack(">H", chk)  # 16 bytes total

    # XOR encrypt → base64url (no padding — 16 bytes = exactly 24 b64 chars, strip trailing ==)
    encrypted = _xor_crypt(payload, _LICENSE_SECRET)
    b64 = base64.urlsafe_b64encode(encrypted).decode().rstrip("=")

    return f"AVK-{tier}-{b64}"


def validate_license_key(key: str) -> dict:
    """
    Validate a compact license key.
    Returns same dict structure as before for full compatibility.
    """
    empty = {
        "valid": False, "in_grace": False, "hard_expired": False,
        "tier": "", "tier_name": "", "is_trial": False,
        "db_limit": 0, "sar_limit": 0, "expiry": None,
        "days_left": -9999, "customer_id": "", "customer_name": "",
        "issued": None, "mac_match": False, "error": "",
    }

    if not key or not key.strip():
        return {**empty, "error": "No license key provided"}

    key   = key.strip()
    parts = key.split("-", 2)
    if len(parts) != 3 or parts[0] != "AVK":
        return {**empty, "error": "Invalid key format (expected AVK-TIER-PAYLOAD)"}

    tier = parts[1]
    if tier not in _TIER_CODE:
        return {**empty, "error": f"Unknown license tier: {tier}"}

    # Decode and decrypt
    try:
        pad       = "=" * (4 - len(parts[2]) % 4)
        encrypted = base64.urlsafe_b64decode(parts[2] + pad)
        payload   = _xor_crypt(encrypted, _LICENSE_SECRET)
    except Exception as e:
        return {**empty, "error": f"Key decryption failed: {e}"}

    # Verify checksum
    try:
        chk_stored = struct.unpack(">H", payload[-2:])[0]
        chk_calc   = sum(payload[:-2]) & 0xFFFF
        if chk_stored != chk_calc:
            return {**empty, "error": "Integrity check failed — key may be tampered or corrupted"}
    except Exception:
        return {**empty, "error": "Key structure invalid"}

    # Unpack
    try:
        tier_code, mac_bytes, db_val, sar_val, days, cust_hash = \
            struct.unpack(">B6sBBH3s", payload[:-2])
    except Exception as e:
        return {**empty, "error": f"Key unpack failed: {e}"}

    # Resolve values
    key_mac    = ":".join(f"{b:02X}" for b in mac_bytes)
    db_limit   = -1 if db_val == 0 else int(db_val)
    sar_limit  = -1 if sar_val == 0 else int(sar_val)
    expiry_dt  = _KEY_EPOCH + timedelta(days=int(days))
    today      = date.today()
    days_left  = (expiry_dt - today).days
    in_grace   = (-GRACE_DAYS <= days_left < 0)
    hard_exp   = (days_left < -GRACE_DAYS)

    # MAC match
    current_mac = get_mac_address().replace(":", "").upper()
    key_mac_raw = key_mac.replace(":", "").upper()
    mac_match   = (current_mac == key_mac_raw)
    if not mac_match:
        logger.warning(f"License MAC mismatch: key={key_mac}, server={get_mac_address()}")

    valid     = mac_match and not hard_exp
    tier_info = TIERS.get(tier, {})

    return {
        "valid":         valid,
        "in_grace":      in_grace,
        "hard_expired":  hard_exp,
        "mac_match":     mac_match,
        "tier":          tier,
        "tier_name":     tier_info.get("name", tier),
        "is_trial":      tier_info.get("trial", False),
        "db_limit":      db_limit,
        "sar_limit":     sar_limit,
        "expiry":        expiry_dt,
        "days_left":     days_left,
        "customer_id":   "",   # not stored in compact format
        "customer_name": "",
        "issued":        "",
        "error": "" if valid else (
            "License expired — within grace period, please renew" if in_grace else
            f"License expired {abs(days_left)} days ago" if hard_exp else
            "MAC address mismatch — license not valid for this server"
        ),
    }


# ══════════════════════════════════════════════════════════════════
# KEY VALIDATION
# ══════════════════════════════════════════════════════════════════

def count_licensed_units(conn) -> dict:
    """
    Count license units using awr_db_info as master table.

    awr_db_info has one row per DB instance with:
      db_name, instance, rac, rac_nodes, cdb, pdb_name, host_name

    Counting rules:
      Standalone DB  — 1 unit per instance
      RAC node       — 1 unit per node (each node is a separate instance row)
      CDB instance   — 1 unit per CDB instance
      PDB            — first 3 per CDB free, each additional = 1 unit
      SAR server     — 1 unit per unique hostname
    """
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT ON (db_name, instance)
                    db_name,
                    instance,
                    COALESCE(rac, 'NO')       AS is_rac,
                    COALESCE(rac_nodes, 1)    AS rac_nodes,
                    COALESCE(cdb, '')          AS cdb,
                    COALESCE(pdb_name, '')     AS pdb_name,
                    COALESCE(host_name, '')    AS host_name
                FROM awr_db_info
                ORDER BY db_name, instance, created_at DESC
            """)
            db_rows = cur.fetchall()
            cur.execute("SELECT COUNT(DISTINCT hostname) FROM sar_cpu_stats")
            sar_count = cur.fetchone()[0] or 0
    except Exception as e:
        logger.warning(f"count_licensed_units (awr_db_info): {e}")
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(DISTINCT dbname) FROM awr_load_profile")
                db_simple = cur.fetchone()[0] or 0
                cur.execute("SELECT COUNT(DISTINCT hostname) FROM sar_cpu_stats")
                sar_count = cur.fetchone()[0] or 0
            return {"db_units": db_simple, "sar_units": sar_count,
                    "db_breakdown": [], "sar_breakdown": [],
                    "pdb_free": 0, "pdb_charged": 0}
        except Exception:
            return {"db_units": 0, "sar_units": 0, "db_breakdown": [],
                    "sar_breakdown": [], "pdb_free": 0, "pdb_charged": 0}

    db_units    = 0
    pdb_free    = 0
    pdb_charged = 0
    breakdown   = []
    cdb_pdbs    = {}   # cdb_name -> [pdb_names]
    cdb_host    = {}   # cdb_name -> instance
    counted     = set()

    for (db_name, instance, is_rac, rac_nodes,
         cdb, pdb_name, host_name) in db_rows:

        key = f"{db_name}|{instance}"
        if key in counted:
            continue
        counted.add(key)

        # PDB — defer to CDB group
        if pdb_name and cdb:
            cdb_pdbs.setdefault(cdb, []).append(pdb_name)
            cdb_host.setdefault(cdb, instance)
            continue

        # CDB without PDB name = the CDB root itself
        if cdb and not pdb_name:
            db_units += 1
            breakdown.append({"dbname": db_name, "instance": instance,
                               "type": "CDB", "host": host_name, "units": 1})
            continue

        # RAC node — 1 unit per instance
        if is_rac and is_rac.upper() == "YES":
            db_units += 1
            breakdown.append({"dbname": db_name, "instance": instance,
                               "type": "RAC Node", "host": host_name,
                               "rac_nodes": rac_nodes, "units": 1})
            continue

        # Standalone
        db_units += 1
        breakdown.append({"dbname": db_name, "instance": instance,
                           "type": "STANDALONE", "host": host_name, "units": 1})

    # PDB free allowance per CDB
    for cdb_name, pdbs in cdb_pdbs.items():
        free    = min(len(pdbs), PDB_FREE_COUNT)
        charged = max(0, len(pdbs) - PDB_FREE_COUNT)
        pdb_free    += free
        pdb_charged += charged
        db_units    += charged
        breakdown.append({"dbname": cdb_name, "instance": cdb_host.get(cdb_name, ""),
                           "type": "CDB+PDB", "pdbs": pdbs,
                           "pdb_count": len(pdbs), "pdb_free": free,
                           "pdb_charged": charged, "units": charged})

    return {"db_units": db_units, "sar_units": sar_count,
            "db_breakdown": breakdown, "sar_breakdown": [],
            "pdb_free": pdb_free, "pdb_charged": pdb_charged}



# ══════════════════════════════════════════════════════════════════
# FULL LICENSE STATUS
# ══════════════════════════════════════════════════════════════════

def get_license_status(conn=None, config: dict = None) -> dict:
    """
    Master license status check.
    Returns comprehensive status used by portal and enforcement.
    """
    from datetime import date as _date

    # Load config
    if config is None:
        try:
            from db import get_db_connection as _gdb
            _conn = _gdb()
            with _conn.cursor() as cur:
                cur.execute("SELECT key, value FROM portal_config")
                config = {r[0]: r[1] for r in cur.fetchall()}
            _conn.close()
        except Exception:
            config = {}

    license_key = config.get("license_key", "").strip()

    # Validate key
    key_info = validate_license_key(license_key)

    # Get usage
    usage = {"db_units": 0, "sar_units": 0, "db_breakdown": [],
             "pdb_free": 0, "pdb_charged": 0}
    if conn:
        try:
            usage = count_licensed_units(conn)
        except Exception as e:
            logger.debug(f"Usage count failed: {e}")

    # Determine enforcement flags
    hard_expired  = key_info.get("hard_expired", True)
    in_grace      = key_info.get("in_grace", False)
    mac_mismatch  = not key_info.get("mac_match", False) and bool(license_key)
    db_limit      = key_info.get("db_limit", 0)
    sar_limit     = key_info.get("sar_limit", 0)
    db_used       = usage["db_units"]
    sar_used      = usage["sar_units"]
    db_exceeded   = (db_limit != -1 and db_used > db_limit)
    sar_exceeded  = (sar_limit != -1 and sar_used > sar_limit)
    days_left     = key_info.get("days_left", -9999)
    expiry_warn   = (0 <= days_left <= 30)

    # Determine overall status
    # Resource-specific flags default to the overall flag; only db_exceeded /
    # sar_exceeded (below) narrow them to a single resource. Kept alongside
    # the original allow_parse/allow_ai_new (unchanged) for callers that
    # care which resource — AWR vs SAR — actually triggered the block.
    if not license_key:
        status = "no_key"
        status_msg = "No license key. The portal requires a license key to operate. Contact Avekshaa Technologies."
        allow_parse      = False
        allow_grafana    = False
        allow_ai_new     = False
        allow_ai_past    = False
        allow_parse_awr  = False
        allow_parse_sar  = False
        allow_ai_new_awr = False
        allow_ai_new_sar = False
    elif mac_mismatch:
        status = "mac_mismatch"
        status_msg = f"License not valid for this server (MAC mismatch). Contact Avekshaa to re-key for server MAC: {get_mac_address()}"
        allow_parse   = False
        allow_grafana = False
        allow_ai_new  = False
        allow_ai_past = False
        allow_parse_awr  = False
        allow_parse_sar  = False
        allow_ai_new_awr = False
        allow_ai_new_sar = False
    elif hard_expired:
        status = "expired"
        status_msg = f"License expired {abs(days_left)} days ago (grace period ended). Portal is in read-only mode. Contact Avekshaa to renew."
        allow_parse   = False
        allow_grafana = False
        allow_ai_new  = False
        allow_ai_past = True   # show past recommendations only
        allow_parse_awr  = False
        allow_parse_sar  = False
        allow_ai_new_awr = False
        allow_ai_new_sar = False
    elif in_grace:
        status = "grace"
        status_msg = f"License expired — {GRACE_DAYS + days_left} days of grace period remaining. Please renew urgently."
        allow_parse   = True
        allow_grafana = True
        allow_ai_new  = True
        allow_ai_past = True
        allow_parse_awr  = True
        allow_parse_sar  = True
        allow_ai_new_awr = True
        allow_ai_new_sar = True
    elif db_exceeded:
        status = "db_exceeded"
        status_msg = f"DB limit exceeded: {db_used} instances in use, {db_limit} licensed. New AWR parsing blocked for unlicensed DBs. Upgrade your license."
        allow_parse   = False   # enforced per-DB in queue processor
        allow_grafana = True
        allow_ai_new  = False
        allow_ai_past = True
        # Only the AWR (DB) resource is over limit — SAR is unaffected.
        allow_parse_awr  = False
        allow_parse_sar  = not sar_exceeded
        allow_ai_new_awr = False
        allow_ai_new_sar = not sar_exceeded
    elif sar_exceeded:
        status = "sar_exceeded"
        status_msg = f"SAR server limit exceeded: {sar_used} servers, {sar_limit} licensed. New SAR parsing blocked. Upgrade your license."
        allow_parse   = False
        allow_grafana = True
        allow_ai_new  = False
        allow_ai_past = True
        # Only the SAR resource is over limit — AWR is unaffected.
        allow_parse_awr  = True
        allow_parse_sar  = False
        allow_ai_new_awr = True
        allow_ai_new_sar = False
    else:
        status = "ok" if not expiry_warn else "expiry_warning"
        if key_info.get("is_trial"):
            status_msg = (f"Trial license — {days_left} days remaining "
                         f"({key_info.get('tier_name')}). "
                         f"Contact Avekshaa for a production license.")
        elif expiry_warn:
            status_msg = f"License expires in {days_left} days. Please renew with Avekshaa."
        else:
            status_msg = (f"Licensed — {key_info.get('tier_name')} | "
                         f"{db_used}/{db_limit if db_limit != -1 else '∞'} DBs | "
                         f"{sar_used}/{sar_limit if sar_limit != -1 else '∞'} SAR servers")
        allow_parse   = True
        allow_grafana = True
        allow_ai_new  = True
        allow_ai_past = True
        allow_parse_awr  = True
        allow_parse_sar  = True
        allow_ai_new_awr = True
        allow_ai_new_sar = True

    # Monthly AI usage
    monthly_ai = _get_monthly_ai_usage(conn)
    ai_monthly_limit = int(config.get("ai_monthly_limit", MONTHLY_AI_LIMIT))
    ai_cap_reached = (monthly_ai >= ai_monthly_limit)

    result = {
        # Overall
        "status":           status,
        "status_msg":       status_msg,
        "valid":            key_info.get("valid", False),
        # Key info
        "tier":             key_info.get("tier", ""),
        "tier_name":        key_info.get("tier_name", ""),
        "is_trial":         key_info.get("is_trial", False),
        "customer_name":    key_info.get("customer_name", ""),
        "expiry":           key_info.get("expiry").isoformat() if key_info.get("expiry") else "",
        "days_left":        days_left,
        "in_grace":         in_grace,
        "hard_expired":     hard_expired,
        "expiry_warning":   expiry_warn,
        # Limits
        "db_limit":         db_limit,
        "sar_limit":        sar_limit,
        "db_used":          db_used,
        "sar_used":         sar_used,
        "db_exceeded":      db_exceeded,
        "sar_exceeded":     sar_exceeded,
        "db_breakdown":     usage.get("db_breakdown", []),
        "pdb_free":         usage.get("pdb_free", 0),
        "pdb_charged":      usage.get("pdb_charged", 0),
        # Enforcement flags
        "allow_parse":      allow_parse,
        "allow_grafana":    allow_grafana,
        "allow_ai_new":     allow_ai_new and not ai_cap_reached,
        "allow_ai_past":    allow_ai_past,
        # Resource-specific enforcement flags (AWR vs SAR) — use these to
        # avoid a SAR-only overage blocking AWR parsing/AI-recs, or vice versa.
        "allow_parse_awr":  allow_parse_awr,
        "allow_parse_sar":  allow_parse_sar,
        "allow_ai_new_awr": allow_ai_new_awr and not ai_cap_reached,
        "allow_ai_new_sar": allow_ai_new_sar and not ai_cap_reached,
        # AI usage
        "ai_monthly_used":  monthly_ai,
        "ai_monthly_limit": ai_monthly_limit,
        "ai_cap_reached":   ai_cap_reached,
        # Server info
        "mac_address":      get_mac_address(),
        "machine_fp":       get_machine_fingerprint(),
    }

    # Log significant events
    _log_license_event(status, status_msg, db_used, sar_used)

    # Write tier back to portal_config so settings page can display it
    # Only update if key is valid and tier is known
    if key_info.get("valid") and key_info.get("tier") and conn:
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO portal_config (key, value, section)
                    VALUES ('license_tier', %s, 'license')
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """, (key_info["tier"],))
            conn.commit()
        except Exception:
            pass

    return result


def _get_monthly_ai_usage(conn) -> int:
    """Count AI recommendations generated this calendar month."""
    if not conn:
        return 0
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM awr_ai_recommendations
                WHERE created_at >= date_trunc('month', NOW())
                  AND ai_provider IN ('ollama','claude','openai','gemini')
            """)
            return cur.fetchone()[0] or 0
    except Exception:
        return 0


def _log_license_event(status: str, message: str, db_used: int, sar_used: int):
    """Log license events to audit table (non-fatal)."""
    # Only log warning/error states to avoid flooding
    if status in ("ok", "expiry_warning"):
        return
    try:
        from db import get_db_connection as _gdb
        conn = _gdb()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO awr_license_audit
                  (event_type, message, db_count, sar_count, event_time)
                VALUES (%s, %s, %s, %s, NOW())
            """, (status, message[:500], db_used, sar_used))
        conn.commit()
        conn.close()
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════
# DB-LEVEL ENFORCEMENT (per-DB parsing control)
# ══════════════════════════════════════════════════════════════════

def is_db_licensed(dbname: str, conn, queued_dbs: set = None) -> bool:
    """
    Check if a specific database is within the licensed count.

    Rules:
    - Enterprise (-1) → always True
    - DB already in awr_db_info → it IS licensed (it got there by passing a prior check)
    - DB not in awr_db_info but in queued_dbs (DONE/PROCESSING) → also licensed
    - New DB never seen before → licensed only if (existing_count < db_limit)
    """
    try:
        with conn.cursor() as cur:
            # Explicit whitelist
            cur.execute("""
                SELECT 1 FROM awr_licensed_dbs
                WHERE dbname = %s AND active = TRUE
            """, (dbname,))
            if cur.fetchone():
                return True

            # Get license limit
            cur.execute("""
                SELECT key, value FROM portal_config
                WHERE key IN ('license_key', 'license_db_count')
            """)
            cfg      = {r[0]: r[1] for r in cur.fetchall()}
            key_info = validate_license_key(cfg.get("license_key", ""))
            db_limit = key_info.get("db_limit", 5)

            if db_limit == -1:
                return True  # Enterprise unlimited

            # Get DBs already in awr_db_info (already parsed at least once)
            cur.execute("SELECT DISTINCT db_name FROM awr_db_info")
            known_dbs = {r[0].upper() for r in cur.fetchall()}

            dbname_up = dbname.upper()

            # If DB is already in awr_db_info → always allow
            # It was registered when it first parsed successfully
            if dbname_up in known_dbs:
                return True

            # If DB is in queued_dbs (currently processing/done in this session)
            # → it already passed the license check earlier, allow continuation
            if queued_dbs and dbname_up in {d.upper() for d in queued_dbs}:
                return True

            # New DB never seen before
            # Count all licensed slots already consumed:
            # = DBs in awr_db_info + DBs in queued_dbs that aren't in awr_db_info yet
            queued_upper = {d.upper() for d in queued_dbs} if queued_dbs else set()
            new_queued   = queued_upper - known_dbs  # queued but not yet in db_info
            total_used   = len(known_dbs) + len(new_queued)

            if total_used < db_limit:
                logger.info(
                    f"is_db_licensed: '{dbname_up}' is new DB #{total_used + 1} "
                    f"of {db_limit} licensed — allowed."
                )
                return True

            logger.warning(
                f"is_db_licensed: '{dbname_up}' would be DB #{total_used + 1} "
                f"but license allows only {db_limit}. "
                f"Known DBs: {known_dbs}, Queued: {new_queued}. Blocking."
            )
            return False

    except Exception as e:
        logger.debug(f"is_db_licensed check failed: {e}")
        return True  # fail open


# ══════════════════════════════════════════════════════════════════
# KEY GENERATION CLI (Avekshaa internal use)
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="AWR Insight Portal v2 — License Key Generator (Avekshaa Internal)")
    sub = parser.add_subparsers(dest="cmd")

    # Generate key
    gen = sub.add_parser("generate", help="Generate a license key")
    gen.add_argument("--tier",     required=True, choices=list(TIERS.keys()))
    gen.add_argument("--mac",      required=True, help="Server MAC address")
    gen.add_argument("--db",       type=int, default=5)
    gen.add_argument("--sar",      type=int, default=5)
    gen.add_argument("--expiry",   required=True, help="YYYY-MM-DD")
    gen.add_argument("--customer", required=True, help="Customer ID (e.g. CUST001)")
    gen.add_argument("--name",     default="", help="Customer name")

    # Validate key
    val = sub.add_parser("validate", help="Validate a license key")
    val.add_argument("--key", required=True)

    # Show MAC
    sub.add_parser("mac", help="Show this server's MAC address")

    args = parser.parse_args()

    if args.cmd == "generate":
        key = generate_license_key(
            tier          = args.tier,
            mac_address   = args.mac,
            db_count      = args.db,
            sar_count     = args.sar,
            expiry_date   = date.fromisoformat(args.expiry),
            customer_id   = args.customer,
            customer_name = args.name,
        )
        print(f"\n{'='*60}")
        print(f"LICENSE KEY GENERATED")
        print(f"{'='*60}")
        print(f"Key:      {key}")
        print(f"Tier:     {TIERS[args.tier]['name']}")
        print(f"MAC:      {args.mac}")
        print(f"DB Limit: {args.db}")
        print(f"SAR Limit:{args.sar}")
        print(f"Expiry:   {args.expiry}")
        print(f"Customer: {args.customer} ({args.name})")
        print(f"{'='*60}\n")

    elif args.cmd == "validate":
        info = validate_license_key(args.key)
        print(json.dumps(info, indent=2, default=str))

    elif args.cmd == "mac":
        print(f"MAC Address:        {get_mac_address()}")
        print(f"Machine Fingerprint:{get_machine_fingerprint()}")
