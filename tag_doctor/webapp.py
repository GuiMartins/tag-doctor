import json
import os
import threading
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from . import fixer, lookup as lookup_module, navidrome_client, report
from .scanner import Group, compute_has_issue, scan

DEFAULT_MUSIC_DIR = os.environ.get("MUSIC_DIR", "/music")
DATA_DIR = os.environ.get("DATA_DIR", "/data")
STATE_FILE = os.path.join(DATA_DIR, "state.json")

app = FastAPI()

_lock = threading.Lock()
_state = {
    "music_dir": DEFAULT_MUSIC_DIR,
    "groups": [],  # list[Group]
    "stats": {},
    "last_scan": None,
    "scanning": False,
    "fixed_count": 0,
    "flash": None,
}

# In-memory only (not persisted) - fetched metadata candidates awaiting user confirmation,
# keyed by group gid. Holds raw image bytes, so keeping it out of state.json is deliberate.
_lookup_cache = {}


def _load_state():
    if not os.path.isfile(STATE_FILE):
        return
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        with _lock:
            _state["music_dir"] = data.get("music_dir", DEFAULT_MUSIC_DIR)
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
            "music_dir": _state["music_dir"],
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
        music_dir = _state["music_dir"]
    try:
        groups, stats = scan(music_dir)
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


@app.get("/", response_class=HTMLResponse)
def dashboard():
    with _lock:
        music_dir = _state["music_dir"]
        groups = list(_state["groups"])
        stats = dict(_state["stats"])
        last_scan = _state["last_scan"]
        scanning = _state["scanning"]
        fixed_count = _state["fixed_count"]
        flash = _state["flash"]
        _state["flash"] = None
    lookup_results = {
        gid: {
            "source": data["source"], "title": data["title"], "artist": data["artist"],
            "genre": data["genre"], "has_image": bool(data.get("image_bytes")),
        }
        for gid, data in _lookup_cache.items()
    }
    return report.render_html(
        music_dir=music_dir,
        groups=groups,
        stats=stats,
        last_scan=last_scan,
        scanning=scanning,
        fixed_count=fixed_count,
        flash=flash,
        navidrome_configured=navidrome_client.configured(),
        lookup_results=lookup_results,
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


@app.post("/settings/music-dir")
async def set_music_dir(request: Request):
    form = await request.form()
    new_path = form.get("music_dir", "").strip()

    if not new_path:
        with _lock:
            _state["flash"] = ("bad", "o caminho não pode ficar vazio.")
        return RedirectResponse(url="/", status_code=303)
    if not os.path.isdir(new_path):
        with _lock:
            _state["flash"] = (
                "bad",
                f"\"{new_path}\" não existe (ou não é uma pasta) dentro do container. "
                "Só dá pra apontar pra dentro do que foi montado no volume (ex: subpastas de "
                f"{DEFAULT_MUSIC_DIR}).",
            )
        return RedirectResponse(url="/", status_code=303)

    with _lock:
        _state["music_dir"] = new_path
        _state["flash"] = ("ok", f"caminho atualizado pra \"{new_path}\". Rode um novo scan pra aplicar.")
    _save_state()
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
    new_genre = form.get("genre", "").strip()
    extra_fields = {}
    if group.mojibake and new_album and new_album != group.album:
        extra_fields["album"] = new_album
    if group.missing_genre and new_genre:
        extra_fields["genre"] = new_genre

    if group.mode == "uniform":
        value = form.get("albumartist", "").strip()
        if not value:
            with _lock:
                _state["flash"] = ("bad", "ALBUMARTIST não pode ficar vazio.")
            return RedirectResponse(url="/", status_code=303)
        for t in group.tracks:
            fields = {"albumartist": value}
            fields.update(extra_fields)
            updates.append((t.path, fields))
    else:
        for t in group.tracks:
            value = form.get(f"albumartist__{t.path}", "").strip()
            fields = dict(extra_fields)
            if value:
                fields["albumartist"] = value
            if fields:
                updates.append((t.path, fields))

    with _lock:
        music_dir = _state["music_dir"]
    ok, errors = fixer.set_tags(music_dir, updates)
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
        music_dir = _state["music_dir"]

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
        ok, errors = fixer.set_tags(music_dir, updates)
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


@app.post("/lookup/{gid}")
def do_lookup(gid: str):
    group = _find_group(gid)
    if group is None:
        with _lock:
            _state["flash"] = ("bad", "esse álbum já não está mais pendente (rode um novo scan).")
        return RedirectResponse(url="/", status_code=303)

    artist = group.suggested_albumartist or (group.tracks[0].artist if group.tracks else "")
    result = lookup_module.lookup(artist, group.album)

    if result is None:
        _lookup_cache.pop(gid, None)
        with _lock:
            _state["flash"] = ("bad", f'nenhum resultado encontrado pra "{group.album}".')
    else:
        _lookup_cache[gid] = result
        found = []
        if result["genre"]:
            found.append(f'gênero "{result["genre"]}"')
        if result["image_bytes"]:
            found.append("capa")
        with _lock:
            _state["flash"] = (
                "ok",
                f'achei em {result["source"]} ({result["artist"]} - {result["title"]}): '
                + (", ".join(found) if found else "nada de útil, só o registro"),
            )
    return RedirectResponse(url="/", status_code=303)


@app.get("/lookup-image/{gid}")
def lookup_image(gid: str):
    data = _lookup_cache.get(gid)
    if not data or not data.get("image_bytes"):
        return Response(status_code=404)
    return Response(content=data["image_bytes"], media_type=data.get("image_mime") or "image/jpeg")


@app.post("/apply-lookup/{gid}")
async def apply_lookup(gid: str, request: Request):
    group = _find_group(gid)
    data = _lookup_cache.get(gid)
    if group is None or data is None:
        with _lock:
            _state["flash"] = ("bad", "essa busca expirou - clique em Buscar online de novo.")
        return RedirectResponse(url="/", status_code=303)

    form = await request.form()
    apply_genre = form.get("apply_genre") == "on" and bool(data.get("genre"))
    apply_cover = form.get("apply_cover") == "on" and bool(data.get("image_bytes"))

    with _lock:
        music_dir = _state["music_dir"]

    ok_total = 0
    errors = []
    if apply_genre:
        updates = [(t.path, {"genre": data["genre"]}) for t in group.tracks]
        ok, errs = fixer.set_tags(music_dir, updates)
        ok_total += ok
        errors += errs
    if apply_cover:
        ok, errs = fixer.embed_cover(music_dir, [t.path for t in group.tracks], data["image_bytes"], data["image_mime"])
        ok_total += ok
        errors += errs

    with _lock:
        for g in _state["groups"]:
            if g.gid == gid:
                if apply_genre:
                    g.missing_genre = False
                if apply_cover:
                    g.missing_cover = False
                g.has_issue = compute_has_issue(g)
                if not g.has_issue:
                    _state["groups"] = [x for x in _state["groups"] if x.gid != gid]
                    _state["fixed_count"] += 1
                break
        _state["flash"] = (
            "ok" if not errors else "bad",
            f"{ok_total} arquivo(s) atualizados"
            + (f", {len(errors)} erro(s): {errors[0][1]}" if errors else "."),
        )
    _lookup_cache.pop(gid, None)
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


@app.get("/api/browse")
def browse(path: str = ""):
    target = path.strip() or DEFAULT_MUSIC_DIR
    target = os.path.normpath(target)

    if not os.path.isdir(target):
        return {"error": f'"{target}" não existe ou não é uma pasta.'}

    try:
        entries = os.scandir(target)
        dirs = sorted(
            (e.name for e in entries if e.is_dir(follow_symlinks=True) and not e.name.startswith(".")),
            key=str.lower,
        )
    except PermissionError:
        return {"error": f'sem permissão pra ler "{target}".'}

    parent = os.path.dirname(target) if target != "/" else None
    return {"path": target, "parent": parent, "dirs": dirs}
