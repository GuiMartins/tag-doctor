import json
import os
import threading
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

from . import fixer, navidrome_client, report
from .scanner import Group, scan

MUSIC_DIR = os.environ.get("MUSIC_DIR", "/music")
DATA_DIR = os.environ.get("DATA_DIR", "/data")
STATE_FILE = os.path.join(DATA_DIR, "state.json")

app = FastAPI()

_lock = threading.Lock()
_state = {
    "groups": [],  # list[Group]
    "stats": {},
    "last_scan": None,
    "scanning": False,
    "fixed_count": 0,
    "flash": None,
}


def _load_state():
    if not os.path.isfile(STATE_FILE):
        return
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        with _lock:
            _state["groups"] = [Group.from_dict(g) for g in data.get("groups", [])]
            _state["stats"] = data.get("stats", {})
            _state["last_scan"] = data.get("last_scan")
            _state["fixed_count"] = data.get("fixed_count", 0)
    except Exception:  # noqa: BLE001 - corrupt/old cache shouldn't crash startup
        pass


def _save_state():
    os.makedirs(DATA_DIR, exist_ok=True)
    with _lock:
        data = {
            "groups": [g.to_dict() for g in _state["groups"]],
            "stats": _state["stats"],
            "last_scan": _state["last_scan"],
            "fixed_count": _state["fixed_count"],
        }
    tmp_path = STATE_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp_path, STATE_FILE)


def _do_scan():
    with _lock:
        _state["scanning"] = True
    try:
        groups, stats = scan(MUSIC_DIR)
        with _lock:
            _state["groups"] = groups
            _state["stats"] = stats
            _state["last_scan"] = datetime.now(timezone.utc).isoformat()
            _state["fixed_count"] = 0
    finally:
        with _lock:
            _state["scanning"] = False
        _save_state()


@app.on_event("startup")
def on_startup():
    _load_state()


def _find_group(gid):
    with _lock:
        for g in _state["groups"]:
            if g.gid == gid:
                return g
    return None


def _remove_group(gid):
    with _lock:
        _state["groups"] = [g for g in _state["groups"] if g.gid != gid]
        _state["fixed_count"] += 1


@app.get("/")
def dashboard():
    with _lock:
        groups = list(_state["groups"])
        stats = dict(_state["stats"])
        last_scan = _state["last_scan"]
        scanning = _state["scanning"]
        fixed_count = _state["fixed_count"]
        flash = _state["flash"]
        _state["flash"] = None
    return report.render_html(
        music_dir=MUSIC_DIR,
        groups=groups,
        stats=stats,
        last_scan=last_scan,
        scanning=scanning,
        fixed_count=fixed_count,
        flash=flash,
        navidrome_configured=navidrome_client.configured(),
    )


@app.post("/scan")
def trigger_scan():
    with _lock:
        already = _state["scanning"]
        if not already:
            _state["scanning"] = True
    if not already:
        threading.Thread(target=_do_scan, daemon=True).start()
    return RedirectResponse(url="/", status_code=303)


@app.post("/apply/{gid}")
async def apply_group(gid: str, request: Request):
    group = _find_group(gid)
    if group is None:
        with _lock:
            _state["flash"] = ("bad", "esse álbum já não está mais pendente (rode um novo scan).")
        return RedirectResponse(url="/", status_code=303)

    form = await request.form()
    updates = []

    new_album = form.get("album", "").strip()
    album_fields = {}
    if group.mojibake and new_album and new_album != group.album:
        album_fields["album"] = new_album

    if group.mode == "uniform":
        value = form.get("albumartist", "").strip()
        if not value:
            with _lock:
                _state["flash"] = ("bad", "ALBUMARTIST não pode ficar vazio.")
            return RedirectResponse(url="/", status_code=303)
        for t in group.tracks:
            fields = {"albumartist": value}
            fields.update(album_fields)
            updates.append((t.path, fields))
    else:
        for t in group.tracks:
            value = form.get(f"albumartist__{t.path}", "").strip()
            if not value:
                continue
            fields = {"albumartist": value}
            fields.update(album_fields)
            updates.append((t.path, fields))

    ok, errors = fixer.set_tags(MUSIC_DIR, updates)
    _remove_group(gid)
    if errors:
        with _lock:
            _state["flash"] = ("bad", f"{ok} faixa(s) corrigida(s), {len(errors)} com erro: {errors[0][1]}")
    else:
        with _lock:
            _state["flash"] = ("ok", f"{ok} faixa(s) corrigida(s) em \"{group.album}\".")
    _save_state()
    return RedirectResponse(url="/", status_code=303)


@app.post("/apply-all")
def apply_all():
    with _lock:
        groups = list(_state["groups"])

    total_ok = 0
    total_errors = 0
    applied_gids = []
    for group in groups:
        updates = []
        if group.mode == "uniform":
            if not group.suggested_albumartist:
                continue
            for t in group.tracks:
                updates.append((t.path, {"albumartist": group.suggested_albumartist}))
        else:
            for t in group.tracks:
                if t.suggested_albumartist:
                    updates.append((t.path, {"albumartist": t.suggested_albumartist}))
        if not updates:
            continue
        ok, errors = fixer.set_tags(MUSIC_DIR, updates)
        total_ok += ok
        total_errors += len(errors)
        applied_gids.append(group.gid)

    with _lock:
        _state["groups"] = [g for g in _state["groups"] if g.gid not in applied_gids]
        _state["fixed_count"] += len(applied_gids)
        _state["flash"] = (
            "ok" if not total_errors else "bad",
            f"{total_ok} faixa(s) corrigida(s) em {len(applied_gids)} álbum(ns)"
            + (f", {total_errors} erro(s)." if total_errors else "."),
        )
    _save_state()
    return RedirectResponse(url="/", status_code=303)


@app.post("/rescan-navidrome")
def rescan_navidrome():
    success, message = navidrome_client.trigger_rescan()
    with _lock:
        _state["flash"] = ("ok" if success else "bad", message)
    return RedirectResponse(url="/", status_code=303)


@app.get("/api/state")
def api_state():
    with _lock:
        return {
            "scanning": _state["scanning"],
            "last_scan": _state["last_scan"],
            "pending": len(_state["groups"]),
        }
