import sys

from .scanner import scan


def main():
    music_dir = sys.argv[1] if len(sys.argv) > 1 else "/music"
    groups, stats = scan(music_dir)
    print(f"{stats['total_tracks']} faixas, {stats['total_groups']} álbuns "
          f"({stats['clean_groups']} ok, {len(groups)} com problema, "
          f"{stats['single_track_groups']} faixas avulsas ignoradas)")
    for g in groups:
        flags = []
        if g.missing_albumartist:
            flags.append("ALBUMARTIST ausente")
        if g.inconsistent_albumartist:
            flags.append("ALBUMARTIST inconsistente")
        if g.mode == "per_track":
            flags.append("colisão de artistas")
        if g.mojibake:
            flags.append("encoding suspeito")
        print(f"[{len(g.tracks)} faixas] {g.dir_path}/{g.album!r} -- {', '.join(flags)}")
        if g.mode == "uniform":
            print(f"  sugestão: ALBUMARTIST={g.suggested_albumartist!r}")
        else:
            for t in g.tracks:
                print(f"  {t.path}: ARTIST={t.artist!r} -> ALBUMARTIST={t.suggested_albumartist!r}")


if __name__ == "__main__":
    main()
