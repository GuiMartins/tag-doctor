import hashlib
import os
import re
from collections import Counter
from dataclasses import dataclass, field, asdict

import mutagen

AUDIO_EXTS = {".flac", ".mp3", ".m4a", ".mp4", ".ogg", ".oga", ".opus", ".wma"}

COVER_FILENAMES = {
    "cover.jpg", "cover.jpeg", "cover.png", "folder.jpg", "folder.jpeg", "folder.png",
    "front.jpg", "front.jpeg", "front.png", "album.jpg", "albumart.jpg", "albumart.png",
}

# a "?" with a letter on both sides is almost always a lost accented character
# (e.g. "Ac?stico"); "Crisis? What Crisis?" (letter, ?, space) is left alone.
MOJIBAKE_RE = re.compile(r"[^\W\d_]\?[^\W\d_]", re.UNICODE)


def canonical_artist(artist):
    """First name before a '/'-joined collab list: 'Daft Punk/ Panda Bear' -> 'Daft Punk'."""
    return artist.split("/")[0].strip()


def has_mojibake(*values):
    return any(v and MOJIBAKE_RE.search(v) for v in values)


def _leading_int(value):
    m = re.match(r"\d+", value or "")
    return int(m.group()) if m else None


@dataclass
class TrackTag:
    path: str  # relative to MUSIC_DIR
    album: str
    albumartist: str
    artist: str
    tracknumber: str = ""
    suggested_albumartist: str = ""


@dataclass
class Group:
    gid: str
    dir_path: str
    album: str
    tracks: list = field(default_factory=list)
    missing_albumartist: bool = False
    inconsistent_albumartist: bool = False
    artist_varies: bool = False
    mojibake: bool = False
    missing_genre: bool = False
    missing_cover: bool = False
    missing_album: bool = False
    missing_artist: bool = False
    missing_tracknumber: bool = False
    duplicate_tracknumber: bool = False
    mode: str = "uniform"  # "uniform" (one ALBUMARTIST for the group) or "per_track" (collision)
    suggested_albumartist: str = ""  # only meaningful when mode == "uniform"
    suggested_genre: str = ""
    has_issue: bool = False

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        d["tracks"] = [TrackTag(**t) for t in d.get("tracks", [])]
        return cls(**d)


def _read_tags(path):
    try:
        f = mutagen.File(path, easy=True)
    except Exception:  # noqa: BLE001 - unreadable/corrupt file, skip it
        return None
    if f is None or f.tags is None:
        return None

    def get1(key):
        v = f.tags.get(key)
        return v[0].strip() if v else ""

    return (
        get1("album"), get1("albumartist"), get1("artist"), get1("genre"),
        get1("tracknumber"), get1("discnumber"),
    )


def _has_embedded_art(path):
    try:
        f = mutagen.File(path)  # non-easy: need raw access to pictures/frames
    except Exception:  # noqa: BLE001
        return False
    if f is None:
        return False
    if getattr(f, "pictures", None):  # FLAC, OggVorbis/Opus
        return True
    tags = getattr(f, "tags", None)
    if tags is None:
        return False
    if any(str(k).startswith("APIC") for k in tags.keys()):  # ID3 (MP3)
        return True
    if "covr" in tags:  # MP4/M4A
        return True
    return False


def _dir_has_cover_file(dir_full_path):
    try:
        return any(e.is_file() and e.name.lower() in COVER_FILENAMES for e in os.scandir(dir_full_path))
    except OSError:
        return False


def _canonical_groups(raw_tracks):
    """Case-insensitive grouping of canonical artist names, so 'Gabriel O Pensador' and
    'Gabriel o Pensador' count as one artist, not a collision. Returns {casefold_key:
    Counter(original_casing -> count)}."""
    groups = {}
    for t in raw_tracks:
        if not t[2]:
            continue
        name = canonical_artist(t[2])
        groups.setdefault(name.casefold(), Counter())[name] += 1
    return groups


def _analyze(dir_path, album, raw_tracks):
    canonical_groups = _canonical_groups(raw_tracks)
    aa_values = [t[1] for t in raw_tracks]
    non_empty_aa = [a for a in aa_values if a]
    genre_values = [t[3] for t in raw_tracks]
    non_empty_genres = [g for g in genre_values if g]
    artist_values = [t[2] for t in raw_tracks]
    tracknumber_values = [t[4] for t in raw_tracks]
    discnumber_values = [t[5] for t in raw_tracks]

    collision = len(canonical_groups) > 1
    mode = "per_track" if collision else "uniform"

    missing = any(a == "" for a in aa_values)
    # In a per_track (collision) group, different ALBUMARTIST values across tracks are the
    # correct end state (each track keeps its own real artist) - only flag inconsistency for
    # uniform groups, where every track is supposed to share one ALBUMARTIST.
    inconsistent = mode == "uniform" and len(set(non_empty_aa)) > 1
    artist_varies = len(set(artist_values)) > 1
    moji = has_mojibake(album, *[t[1] for t in raw_tracks], *artist_values)
    missing_genre = any(g == "" for g in genre_values)
    genre_suggestion = Counter(non_empty_genres).most_common(1)[0][0] if non_empty_genres else ""

    missing_album = album == ""
    missing_artist = any(a == "" for a in artist_values)
    missing_tracknumber = any(t == "" for t in tracknumber_values)

    # Duplicate track numbers within the same disc (or with no disc tag at all) usually mean
    # a multi-disc release that's missing DISCNUMBER - Navidrome needs both to order it right.
    disc_buckets = {}
    for tn, dn in zip(tracknumber_values, discnumber_values):
        n = _leading_int(tn)
        if n is None:
            continue
        disc_buckets.setdefault(dn, []).append(n)
    duplicate_tracknumber = any(len(nums) != len(set(nums)) for nums in disc_buckets.values())

    if mode == "uniform":
        if non_empty_aa:
            group_suggestion = Counter(non_empty_aa).most_common(1)[0][0]
        elif canonical_groups:
            # single casefold-key group here (mode is "uniform") - pick its most common casing
            (variants,) = canonical_groups.values()
            group_suggestion = variants.most_common(1)[0][0]
        else:
            group_suggestion = ""
    else:
        group_suggestion = ""

    tracks = []
    for path, albumartist, artist, _genre, tracknumber, _discnumber in raw_tracks:
        if mode == "per_track":
            suggestion = albumartist or canonical_artist(artist)
        else:
            suggestion = group_suggestion
        tracks.append(TrackTag(path, album, albumartist, artist, tracknumber, suggestion))

    gid = hashlib.md5(f"{dir_path}|{album}".encode("utf-8")).hexdigest()[:12]
    return Group(
        gid=gid,
        dir_path=dir_path,
        album=album,
        tracks=tracks,
        missing_albumartist=missing,
        inconsistent_albumartist=inconsistent,
        artist_varies=artist_varies,
        mojibake=moji,
        missing_genre=missing_genre,
        missing_album=missing_album,
        missing_artist=missing_artist,
        missing_tracknumber=missing_tracknumber,
        duplicate_tracknumber=duplicate_tracknumber,
        mode=mode,
        suggested_albumartist=group_suggestion,
        suggested_genre=genre_suggestion,
        has_issue=(
            missing or inconsistent or moji or missing_genre or missing_album
            or missing_artist or missing_tracknumber or duplicate_tracknumber
        ),
    )


def scan(music_dir):
    """Walk music_dir recursively, group tracks by (parent dir, ALBUM tag), and
    return (groups_with_issues, stats). Single-track groups are still checked for
    missing tags (genre, cover, tracknumber, ...) - only ALBUMARTIST-consistency
    checks are inherently moot with just one track."""
    raw_groups = {}
    total_tracks = 0
    unreadable = 0

    for root, dirs, files in os.walk(music_dir):
        dirs.sort()
        for fname in sorted(files):
            ext = os.path.splitext(fname)[1].lower()
            if ext not in AUDIO_EXTS:
                continue
            full = os.path.join(root, fname)
            tags = _read_tags(full)
            if tags is None:
                unreadable += 1
                continue
            album, albumartist, artist, genre, tracknumber, discnumber = tags
            total_tracks += 1
            rel = os.path.relpath(full, music_dir)
            reldir = os.path.relpath(root, music_dir)
            key = (reldir, album)
            raw_groups.setdefault(key, []).append(
                (rel, albumartist, artist, genre, tracknumber, discnumber)
            )

    groups = []
    clean = 0
    singles = 0
    for (reldir, album), raw_tracks in raw_groups.items():
        if len(raw_tracks) < 2:
            singles += 1
        g = _analyze(reldir, album, raw_tracks)

        dir_full_path = os.path.join(music_dir, reldir)
        has_cover = _dir_has_cover_file(dir_full_path) or any(
            _has_embedded_art(os.path.join(music_dir, t.path)) for t in g.tracks
        )
        g.missing_cover = not has_cover
        g.has_issue = g.has_issue or g.missing_cover

        if g.has_issue:
            groups.append(g)
        else:
            clean += 1

    groups.sort(key=lambda g: -len(g.tracks))
    stats = {
        "total_tracks": total_tracks,
        "unreadable": unreadable,
        "total_groups": len(raw_groups),
        "clean_groups": clean,
        "single_track_groups": singles,
        "problem_groups": len(groups),
    }
    return groups, stats
