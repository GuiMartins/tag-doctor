"""Read/purge the "missing" rows in Navidrome's database.

Navidrome's Scanner.PurgeMissing=never (the default, and the only safe setting when
the library lives on an external disk) keeps deleted files in the database flagged
`missing=1` instead of dropping them. That's deliberate: a disk that fails to mount
would otherwise wipe the whole index, taking playlists, play counts and favourites
with it. The trade-off is that files you delete on purpose linger forever, and
Navidrome ships no command or UI to clear them.

This module does that cleanup, with a guard Navidrome itself can't offer: tag-doctor
already has the library mounted, so every row is cross-checked against the real
filesystem and only rows whose file is genuinely gone are deleted.
"""

import os
import sqlite3

from .scanner import AUDIO_EXTS

ND_DB = os.environ.get("ND_DB", "")
# Navidrome stores media_file.path relative to its own library root, which is very
# likely a different mount point inside this container (it may not even be the same
# disk). Defaults to MUSIC_DIR, which is right when both containers mount the same
# library at the same place.
ND_LIBRARY_DIR = os.environ.get("ND_LIBRARY_DIR") or os.environ.get("MUSIC_DIR", "/music")

# Only media_file_artists, scrobbles and scrobble_buffer declare a real foreign key to
# media_file, so those cascade on their own (and the FTS index follows via triggers).
# Everything below has to be cleaned by hand: annotation and bookmark are polymorphic
# (item_id + item_type, no constraint possible), and playlist_tracks.media_file_id has no
# foreign key at all - only its playlist_id does. Deleting a media_file without these
# leaves rows pointing at nothing, which is what makes a playlist show a wrong song count.
_ORPHAN_DELETES = (
    "DELETE FROM annotation WHERE item_type = 'media_file' AND item_id = ?",
    "DELETE FROM bookmark WHERE item_type = 'media_file' AND item_id = ?",
    "DELETE FROM playlist_tracks WHERE media_file_id = ?",
)
# playqueue.items is a serialized id list rather than rows; Navidrome tolerates dead ids
# there and rebuilds the queue on the next save, so it is deliberately left alone.


def configured():
    return bool(ND_DB) and os.path.isfile(ND_DB)


def _connect(readonly):
    mode = "ro" if readonly else "rw"
    conn = sqlite3.connect(f"file:{ND_DB}?mode={mode}", uri=True, timeout=15)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def library_mounted():
    """True if ND_LIBRARY_DIR holds actual audio, rather than being an empty mount point.

    This is the check that makes the purge safe. If the library disk is unmounted, every
    single row looks gone from disk and a naive purge would delete the entire database -
    exactly the failure this whole feature exists to clean up after.
    """
    if not os.path.isdir(ND_LIBRARY_DIR):
        return False
    dirs_seen = 0
    for _root, _dirs, files in os.walk(ND_LIBRARY_DIR):
        for name in files:
            if os.path.splitext(name)[1].lower() in AUDIO_EXTS:
                return True
        dirs_seen += 1
        if dirs_seen > 500:
            break
    return False


def list_missing():
    """Rows Navidrome flagged as missing. Returns (rows, error_message).

    Each row is a dict with path/title/artist/on_disk. `on_disk` True means the file is
    back (the flag is stale and a rescan would clear it), so it must not be purged.
    """
    if not configured():
        return [], "Banco do Navidrome não configurado (defina ND_DB)."
    try:
        conn = _connect(readonly=True)
        try:
            cur = conn.execute(
                "SELECT id, path, title, artist FROM media_file WHERE missing = 1 ORDER BY path"
            )
            rows = cur.fetchall()
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        return [], f"Não consegui ler o banco do Navidrome: {exc}"

    return [
        {
            "id": r[0],
            "path": r[1],
            "title": r[2] or "",
            "artist": r[3] or "",
            "on_disk": os.path.isfile(os.path.join(ND_LIBRARY_DIR, r[1])),
        }
        for r in rows
    ], None


def missing_count():
    """Cheap indexed count, safe to run on every page render (no filesystem checks)."""
    if not configured():
        return None
    try:
        conn = _connect(readonly=True)
        try:
            return conn.execute("SELECT COUNT(*) FROM media_file WHERE missing = 1").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.OperationalError:
        return None


def purge_missing():
    """Delete the rows whose file really is gone. Returns (ok, message)."""
    if not configured():
        return False, "Banco do Navidrome não configurado (defina ND_DB)."

    if not library_mounted():
        return False, (
            f'Não achei nenhum arquivo de áudio em "{ND_LIBRARY_DIR}" - a biblioteca do '
            "Navidrome parece estar desmontada. Limpeza cancelada: rodar assim apagaria "
            "a biblioteca inteira do banco."
        )

    rows, error = list_missing()
    if error:
        return False, error

    gone = [r for r in rows if not r["on_disk"]]
    back = len(rows) - len(gone)

    if not gone:
        if back:
            return False, (
                f"Nenhum arquivo pra limpar: os {back} marcados como ausentes voltaram a "
                "existir no disco. Rode um rescan no Navidrome pra ele desmarcar."
            )
        return False, "Nenhum arquivo marcado como ausente no Navidrome."

    ids = [(r["id"],) for r in gone]
    try:
        conn = _connect(readonly=False)
        try:
            with conn:
                playlists = _playlists_holding(conn, ids)
                for statement in _ORPHAN_DELETES:
                    conn.executemany(statement, ids)
                conn.executemany("DELETE FROM media_file WHERE id = ?", ids)
                for playlist_id in playlists:
                    _renumber_playlist(conn, playlist_id)
                    _refresh_playlist_totals(conn, playlist_id)
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        return False, f"Erro ao escrever no banco do Navidrome: {exc}"

    message = f"{len(gone)} arquivo(s) removidos do banco do Navidrome."
    if playlists:
        message += f" {len(playlists)} playlist(s) tiveram as faixas renumeradas."
    if back:
        message += (
            f" {back} continuam marcados como ausentes porque o arquivo existe no disco - "
            "esses só precisam de rescan."
        )
    return True, message


def _playlists_holding(conn, ids):
    """Playlists that reference any of these files, collected before the delete."""
    found = set()
    for (media_file_id,) in ids:
        rows = conn.execute(
            "SELECT DISTINCT playlist_id FROM playlist_tracks WHERE media_file_id = ?",
            (media_file_id,),
        ).fetchall()
        found.update(r[0] for r in rows)
    return sorted(found)


def _renumber_playlist(conn, playlist_id):
    """Close the gaps left in the track positions.

    playlist_tracks.id IS the 1-based position, under a unique index on
    (playlist_id, id). Updating positions in place would collide with that index
    mid-statement, so the rows are rewritten in order instead.
    """
    tracks = conn.execute(
        "SELECT media_file_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY id",
        (playlist_id,),
    ).fetchall()
    conn.execute("DELETE FROM playlist_tracks WHERE playlist_id = ?", (playlist_id,))
    conn.executemany(
        "INSERT INTO playlist_tracks (id, playlist_id, media_file_id) VALUES (?, ?, ?)",
        [(pos, playlist_id, row[0]) for pos, row in enumerate(tracks, start=1)],
    )


def _refresh_playlist_totals(conn, playlist_id):
    """Recompute the cached counters, or the UI keeps showing the old song count."""
    conn.execute(
        """
        UPDATE playlist SET
          song_count = (SELECT COUNT(*) FROM playlist_tracks WHERE playlist_id = :pid),
          duration = (SELECT COALESCE(SUM(mf.duration), 0) FROM playlist_tracks pt
                      JOIN media_file mf ON mf.id = pt.media_file_id WHERE pt.playlist_id = :pid),
          size = (SELECT COALESCE(SUM(mf.size), 0) FROM playlist_tracks pt
                  JOIN media_file mf ON mf.id = pt.media_file_id WHERE pt.playlist_id = :pid),
          updated_at = datetime('now')
        WHERE id = :pid
        """,
        {"pid": playlist_id},
    )
