import os

import mutagen
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover


def set_tags(music_dir, updates):
    """updates: list of (relative_path, {tag_name: value}). Only touches the
    given tags - never rewrites ARTIST, TRACKNUMBER, etc. Returns (ok, errors)
    where errors is a list of (path, message)."""
    ok = 0
    errors = []
    for rel_path, fields in updates:
        full = os.path.join(music_dir, rel_path)
        try:
            f = mutagen.File(full, easy=True)
            if f is None or f.tags is None:
                errors.append((rel_path, "não consegui abrir as tags"))
                continue
            for key, value in fields.items():
                f.tags[key] = [value]
            f.save()
            ok += 1
        except Exception as exc:  # noqa: BLE001 - keep going on other files
            errors.append((rel_path, str(exc)))
    return ok, errors


def embed_cover(music_dir, track_paths, image_bytes, mime):
    """Embed a front-cover image into each track. Supports FLAC, MP3 (ID3) and MP4/M4A -
    other formats are reported as unsupported rather than silently skipped."""
    ok = 0
    errors = []
    for rel_path in track_paths:
        full = os.path.join(music_dir, rel_path)
        try:
            f = mutagen.File(full)
            if isinstance(f, FLAC):
                f.clear_pictures()
                pic = Picture()
                pic.data = image_bytes
                pic.type = 3  # front cover
                pic.mime = mime
                pic.desc = "Cover"
                f.add_picture(pic)
                f.save()
            elif isinstance(f, MP3):
                if f.tags is None:
                    f.add_tags()
                f.tags.delall("APIC")
                f.tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=image_bytes))
                f.save()
            elif isinstance(f, MP4):
                fmt = MP4Cover.FORMAT_PNG if "png" in mime else MP4Cover.FORMAT_JPEG
                f["covr"] = [MP4Cover(image_bytes, imageformat=fmt)]
                f.save()
            else:
                errors.append((rel_path, "formato sem suporte a capa embutida por aqui ainda"))
                continue
            ok += 1
        except Exception as exc:  # noqa: BLE001
            errors.append((rel_path, str(exc)))
    return ok, errors
