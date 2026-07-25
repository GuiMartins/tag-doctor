import time

import requests

USER_AGENT = "tag-doctor/1.0 (https://github.com/GuiMartins/tag-doctor)"
_last_mb_request = 0.0


def _mb_throttle():
    # MusicBrainz's usage policy caps anonymous requests at ~1/sec.
    global _last_mb_request
    elapsed = time.monotonic() - _last_mb_request
    if elapsed < 1.1:
        time.sleep(1.1 - elapsed)
    _last_mb_request = time.monotonic()


def _mb_get(path, params):
    _mb_throttle()
    r = requests.get(
        f"https://musicbrainz.org{path}", params=params,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=10,
    )
    r.raise_for_status()
    return r.json()


def _search_release(artist, album):
    if not album:
        return None
    query = f'release:"{album}"'
    if artist:
        query += f' AND artist:"{artist}"'
    try:
        data = _mb_get("/ws/2/release/", {"query": query, "fmt": "json", "limit": 5})
    except Exception:  # noqa: BLE001
        return None
    releases = data.get("releases", [])
    if not releases:
        return None
    best = releases[0]
    credit = best.get("artist-credit") or [{}]
    return {
        "mbid": best["id"],
        "title": best.get("title", ""),
        "artist": credit[0].get("name", ""),
        "release_group_mbid": (best.get("release-group") or {}).get("id"),
    }


def _fetch_genre(release_group_mbid):
    if not release_group_mbid:
        return ""
    try:
        data = _mb_get(f"/ws/2/release-group/{release_group_mbid}", {"fmt": "json", "inc": "genres"})
    except Exception:  # noqa: BLE001
        return ""
    genres = data.get("genres", [])
    if not genres:
        return ""
    genres.sort(key=lambda g: -g.get("count", 0))
    return genres[0]["name"].title()


def _fetch_cover_art(mbid):
    for size in ("front-500", "front"):
        try:
            r = requests.get(
                f"https://coverartarchive.org/release/{mbid}/{size}",
                headers={"User-Agent": USER_AGENT}, timeout=10,
            )
            if r.status_code == 200 and r.content:
                return r.content, r.headers.get("Content-Type", "image/jpeg")
        except Exception:  # noqa: BLE001
            continue
    return None, None


def _itunes_fallback(artist, album):
    try:
        r = requests.get(
            "https://itunes.apple.com/search",
            params={"term": f"{artist} {album}".strip(), "entity": "album", "limit": 1},
            timeout=10,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
    except Exception:  # noqa: BLE001
        return None
    if not results:
        return None

    item = results[0]
    art_url = (item.get("artworkUrl100") or "").replace("100x100bb", "1200x1200bb")
    image_bytes, image_mime = None, None
    if art_url:
        try:
            img_r = requests.get(art_url, timeout=10)
            if img_r.status_code == 200:
                image_bytes = img_r.content
                image_mime = img_r.headers.get("Content-Type", "image/jpeg")
        except Exception:  # noqa: BLE001
            pass

    return {
        "source": "iTunes",
        "title": item.get("collectionName", ""),
        "artist": item.get("artistName", ""),
        "genre": item.get("primaryGenreName", ""),
        "image_bytes": image_bytes,
        "image_mime": image_mime,
    }


def lookup(artist, album):
    """Best-effort metadata lookup: MusicBrainz + Cover Art Archive first, iTunes Search
    fills in whatever's still missing (genre and/or cover). Returns None if nothing found."""
    result = {"source": None, "title": "", "artist": "", "genre": "", "image_bytes": None, "image_mime": None}

    release = _search_release(artist, album)
    if release:
        result["source"] = "MusicBrainz"
        result["title"] = release["title"]
        result["artist"] = release["artist"]
        result["genre"] = _fetch_genre(release["release_group_mbid"])
        image_bytes, image_mime = _fetch_cover_art(release["mbid"])
        result["image_bytes"] = image_bytes
        result["image_mime"] = image_mime

    if not result["image_bytes"] or not result["genre"]:
        fallback = _itunes_fallback(artist, album)
        if fallback:
            if not result["source"]:
                result["source"] = fallback["source"]
                result["title"] = fallback["title"]
                result["artist"] = fallback["artist"]
            if not result["genre"]:
                result["genre"] = fallback["genre"]
            if not result["image_bytes"]:
                result["image_bytes"] = fallback["image_bytes"]
                result["image_mime"] = fallback["image_mime"]

    if not result["source"]:
        return None
    return result
