import os

import mutagen


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
