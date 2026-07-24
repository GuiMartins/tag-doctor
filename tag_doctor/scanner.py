import hashlib
import os
import re
from collections import Counter
from dataclasses import dataclass, field, asdict

import mutagen

AUDIO_EXTS = {".flac", ".mp3", ".m4a", ".mp4", ".ogg", ".oga", ".opus", ".wma"}

# a "?" with a letter on both sides is almost always a lost accented character
# (e.g. "Ac?stico"); "Crisis? What Crisis?" (letter, ?, space) is left alone.
MOJIBAKE_RE = re.compile(r"[^\W\d_]\?[^\W\d_]", re.UNICODE)


def canonical_artist(artist):
    """First name before a '/'-joined collab list: 'Daft Punk/ Panda Bear' -> 'Daft Punk'."""
    return artist.split("/")[0].strip()


def has_mojibake(*values):
    return any(v and MOJIBAKE_RE.search(v) for v in values)


@dataclass
class TrackTag:
    path: str  # relative to MUSIC_DIR
    album: str
    albumartist: str
    artist: str
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
    mode: str = "uniform"  # "uniform" (one ALBUMARTIST for the group) or "per_track" (collision)
    suggested_albumartist: str = ""  # only meaningful when mode == "uniform"
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

    return get1("album"), get1("albumartist"), get1("artist")


def _analyze(dir_path, album, raw_tracks):
    canonical_counts = Counter(canonical_artist(t[2]) for t in raw_tracks if t[2])
    aa_values = [t[1] for t in raw_tracks]
    non_empty_aa = [a for a in aa_values if a]

    missing = any(a == "" for a in aa_values)
    inconsistent = len(set(non_empty_aa)) > 1
    artist_varies = len(set(t[2] for t in raw_tracks)) > 1
    moji = has_mojibake(album, *[t[1] for t in raw_tracks], *[t[2] for t in raw_tracks])

    collision = len(canonical_counts) > 1
    mode = "per_track" if collision else "uniform"

    if mode == "uniform":
        if non_empty_aa:
            group_suggestion = Counter(non_empty_aa).most_common(1)[0][0]
        elif canonical_counts:
            group_suggestion = canonical_counts.most_common(1)[0][0]
        else:
            group_suggestion = ""
    else:
        group_suggestion = ""

    tracks = []
    for path, albumartist, artist in raw_tracks:
        if mode == "per_track":
            suggestion = albumartist or canonical_artist(artist)
        else:
            suggestion = group_suggestion
        tracks.append(TrackTag(path, album, albumartist, artist, suggestion))

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
        mode=mode,
        suggested_albumartist=group_suggestion,
        has_issue=missing or inconsistent or moji,
    )


def scan(music_dir):
    """Walk music_dir recursively, group tracks by (parent dir, ALBUM tag), and
    return (groups_with_issues, stats). Groups with a single track are skipped -
    there's nothing to fragment."""
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
            album, albumartist, artist = tags
            total_tracks += 1
            rel = os.path.relpath(full, music_dir)
            reldir = os.path.relpath(root, music_dir)
            key = (reldir, album)
            raw_groups.setdefault(key, []).append((rel, albumartist, artist))

    groups = []
    clean = 0
    singles = 0
    for (reldir, album), raw_tracks in raw_groups.items():
        if len(raw_tracks) < 2:
            singles += 1
            continue
        g = _analyze(reldir, album, raw_tracks)
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
