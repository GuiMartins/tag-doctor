import hashlib
import os
import secrets

import requests

ND_URL = os.environ.get("ND_URL", "").rstrip("/")
ND_USER = os.environ.get("ND_USER", "")
ND_PASS = os.environ.get("ND_PASS", "")


def configured():
    return bool(ND_URL and ND_USER and ND_PASS)


def trigger_rescan():
    if not configured():
        return False, "Navidrome não configurado (defina ND_URL, ND_USER, ND_PASS)."

    salt = secrets.token_hex(6)
    token = hashlib.md5((ND_PASS + salt).encode("utf-8")).hexdigest()
    params = {
        "u": ND_USER,
        "t": token,
        "s": salt,
        "v": "1.16.1",
        "c": "tag-doctor",
        "f": "json",
    }
    try:
        r = requests.get(f"{ND_URL}/rest/startScan", params=params, timeout=10)
        r.raise_for_status()
        status = r.json().get("subsonic-response", {}).get("status")
        if status == "ok":
            return True, "Rescan disparado no Navidrome."
        return False, f"Navidrome recusou o pedido: {r.text[:300]}"
    except Exception as exc:  # noqa: BLE001
        return False, f"Erro ao chamar o Navidrome: {exc}"
