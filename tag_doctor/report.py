import html
from datetime import datetime

FAVICON = (
    "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAx"
    "MjggMTI4Ij4KICA8cmVjdCB3aWR0aD0iMTI4IiBoZWlnaHQ9IjEyOCIgcng9IjI0IiBmaWxsPSIjN2MzYWVkIi8+CiAgPHBhdGgg"
    "ZD0iTTI0IDI2IGgzMiBsMzAgMjYgLTMwIDI2IGgtMzIgeiIgZmlsbD0ibm9uZSIgc3Ryb2tlPSIjZmZmZmZmIiBzdHJva2Utd2lk"
    "dGg9IjgiCiAgICAgICAgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIi8+CiAgPGNpcmNsZSBj"
    "eD0iNzAiIGN5PSI1MiIgcj0iNSIgZmlsbD0iI2ZmZmZmZiIvPgogIDxjaXJjbGUgY3g9Ijk0IiBjeT0iOTQiIHI9IjIyIiBmaWxs"
    "PSIjZmZmZmZmIi8+CiAgPHBhdGggZD0iTTg0IDk1IGw3IDcgMTQgLTE1IiBmaWxsPSJub25lIiBzdHJva2U9IiM3YzNhZWQiIHN0"
    "cm9rZS13aWR0aD0iNyIKICAgICAgICBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiLz4KPC9z"
    "dmc+Cg=="
)


def _esc(value):
    return html.escape(str(value if value is not None else ""))


def format_ts(iso_str):
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
    except ValueError:
        return iso_str
    return dt.strftime("%d/%m/%Y %H:%M UTC")


def _badges(group):
    badges = []
    if group.missing_albumartist:
        badges.append(("warn", "ALBUMARTIST ausente"))
    if group.inconsistent_albumartist:
        badges.append(("warn", "ALBUMARTIST inconsistente"))
    if group.mode == "per_track":
        badges.append(("bad", "múltiplos artistas no mesmo título"))
    if group.mojibake:
        badges.append(("bad", "encoding suspeito"))
    return "".join(f"<span class='badge {cls}'>{_esc(label)}</span>" for cls, label in badges)


def _track_rows(group):
    rows = []
    for t in group.tracks:
        rows.append(
            f"<tr><td class='mono'>{_esc(t.path)}</td>"
            f"<td>{_esc(t.artist) or '<i>—</i>'}</td>"
            f"<td>{_esc(t.albumartist) or '<i>(vazio)</i>'}</td></tr>"
        )
    return "".join(rows)


def _group_card(group):
    track_count = len(group.tracks)
    album_fix_html = ""
    if group.mojibake:
        album_fix_html = f"""
        <label class="field-label">Nome do álbum (parece ter acentuação corrompida - corrija se precisar)</label>
        <input type="text" name="album" value="{_esc(group.album)}" class="text-input">
        """

    if group.mode == "uniform":
        body = f"""
        <label class="field-label">ALBUMARTIST a aplicar em todas as {track_count} faixas</label>
        <input type="text" name="albumartist" value="{_esc(group.suggested_albumartist)}"
               class="text-input" required>
        """
    else:
        rows = "".join(
            f"""<div class="pt-row">
                  <span class="mono pt-path">{_esc(t.path)}</span>
                  <span class="pt-artist">{_esc(t.artist)}</span>
                  <input type="text" name="albumartist__{_esc(t.path)}"
                         value="{_esc(t.suggested_albumartist)}" class="text-input pt-input">
                </div>"""
            for t in group.tracks
        )
        body = f"""
        <p class="muted small">Faixas de artistas diferentes com o mesmo título de álbum - revise o
        ALBUMARTIST de cada uma (não precisa ser igual pra todas).</p>
        {rows}
        """

    return f"""
    <div class="card group-card">
      <div class="group-head">
        <b>{_esc(group.album) or '<i>(sem tag ALBUM)</i>'}</b>
        <span class="muted small">{_esc(group.dir_path)} · {track_count} faixas</span>
        {_badges(group)}
      </div>
      <form method="post" action="/apply/{_esc(group.gid)}" class="apply-form">
        {album_fix_html}
        {body}
        <button type="submit">Aplicar</button>
      </form>
      <details>
        <summary>ver tags atuais das faixas</summary>
        <table><thead><tr><th>Arquivo</th><th>ARTIST</th><th>ALBUMARTIST</th></tr></thead>
        <tbody>{_track_rows(group)}</tbody></table>
      </details>
    </div>
    """


def render_html(music_dir, groups, stats, last_scan, scanning, fixed_count, flash, navidrome_configured):
    total_groups = stats.get("total_groups", 0)
    total_tracks = stats.get("total_tracks", 0)
    problem_groups = len(groups)
    unreadable = stats.get("unreadable", 0)

    if scanning:
        banner = "<div class='banner running'><span class='spinner'></span> Escaneando...</div>"
    elif not stats:
        banner = "<div class='banner'>Nenhum scan ainda. Clique em \"Escanear agora\".</div>"
    elif problem_groups == 0:
        banner = "<div class='banner ok'>Nenhum álbum com problema de tag encontrado.</div>"
    else:
        tracks_affected = sum(len(g.tracks) for g in groups)
        banner = (
            f"<div class='banner bad'>{problem_groups} álbum(ns) com problema de tag "
            f"({tracks_affected} faixas afetadas).</div>"
        )

    flash_html = ""
    if flash:
        kind, message = flash
        flash_html = f"<div class='banner {kind}'>{_esc(message)}</div>"

    cards = ""
    if stats:
        cards = f"""
        <div class="cards">
          <div class="card"><div class="n">{total_groups}</div><div class="l">álbuns encontrados</div></div>
          <div class="card"><div class="n">{total_tracks}</div><div class="l">faixas lidas</div></div>
          <div class="card"><div class="n">{problem_groups}</div><div class="l">com problema</div></div>
          <div class="card"><div class="n">{fixed_count}</div><div class="l">corrigidos nesta sessão</div></div>
        </div>
        """
        if unreadable:
            cards += f"<p class='muted small'>{unreadable} arquivo(s) sem tags legíveis, ignorado(s).</p>"

    groups_html = "".join(_group_card(g) for g in groups)

    apply_all_html = ""
    if groups:
        apply_all_html = """
        <form method="post" action="/apply-all" id="apply-all-form"
              onsubmit="return confirm('Aplicar as sugestões automáticas em todos os álbuns pendentes?');">
          <button type="submit" class="secondary-btn">Aplicar todas as sugestões</button>
        </form>
        """

    nd_html = ""
    if navidrome_configured:
        nd_html = """
        <form method="post" action="/rescan-navidrome">
          <button type="submit" class="secondary-btn">Forçar rescan no Navidrome</button>
        </form>
        """

    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>tag-doctor</title>
<link rel="icon" type="image/svg+xml" href="{FAVICON}">
<script>
(function() {{
  var saved = localStorage.getItem('theme');
  if (saved) document.documentElement.setAttribute('data-theme', saved);
}})();
</script>
<style>
:root {{ color-scheme: light dark; }}
* {{ box-sizing: border-box; }}
body {{ font-family: -apple-system, Segoe UI, sans-serif; margin: 0; padding: 2rem;
        background: #0f1115; color: #e6e6e6; max-width: 1100px; }}
@media (prefers-color-scheme: light) {{
  :root:not([data-theme]) body {{ background: #f7f7f8; color: #1a1a1a; }}
}}
:root[data-theme="light"] body {{ background: #f7f7f8; color: #1a1a1a; }}
:root[data-theme="dark"] body {{ background: #0f1115; color: #e6e6e6; }}
h1 {{ font-size: 1.4rem; margin: 0 0 .2rem; }}
.meta {{ color: #888; font-size: .85rem; margin-bottom: 1.5rem; display: flex; align-items: center;
         gap: .8rem; flex-wrap: wrap; }}
.banner {{ padding: .8rem 1rem; border-radius: 8px; font-weight: 600; margin-bottom: 1rem;
           display: flex; align-items: center; gap: .6rem; background: rgba(127,127,127,.12); }}
.banner.ok {{ background: #1e3a2a; color: #7ee2a8; }}
.banner.bad {{ background: #3a1e1e; color: #ff8a8a; }}
.banner.running {{ background: #1e2a3a; color: #7eb8f0; }}
.spinner {{ width: 14px; height: 14px; border-radius: 50%; border: 2px solid rgba(126,184,240,.3);
            border-top-color: #7eb8f0; animation: spin .7s linear infinite; flex: none; }}
@keyframes spin {{ to {{ transform: rotate(360deg); }} }}
.cards {{ display: flex; flex-wrap: wrap; gap: .75rem; margin-bottom: .5rem; }}
.card {{ background: rgba(127,127,127,.1); border: 1px solid rgba(127,127,127,.18);
         border-radius: 10px; padding: .7rem 1.1rem; min-width: 100px; }}
.card .n {{ font-size: 1.4rem; font-weight: 700; line-height: 1.1; }}
.card .l {{ font-size: .72rem; color: #999; margin-top: .15rem; }}
.toolbar {{ display: flex; gap: .6rem; margin: 1rem 0 1.5rem; flex-wrap: wrap; }}
table {{ width: 100%; border-collapse: collapse; font-size: .82rem; margin-top: .5rem; }}
th {{ text-align: left; padding: .5rem .6rem; font-size: .7rem; text-transform: uppercase;
      letter-spacing: .03em; color: #888; border-bottom: 1px solid rgba(127,127,127,.25); }}
td {{ text-align: left; padding: .45rem .6rem; border-bottom: 1px solid rgba(127,127,127,.12); }}
td.mono, .mono {{ font-family: ui-monospace, Consolas, monospace; font-size: .78rem; }}
tr:last-child td {{ border-bottom: none; }}
.badge {{ display: inline-block; white-space: nowrap; padding: .2rem .6rem; border-radius: 999px;
          font-size: .68rem; font-weight: 600; margin-left: .3rem; }}
.badge.warn {{ background: #3a331e; color: #f0c674; }}
.badge.bad {{ background: #3a1e1e; color: #ff8a8a; }}
.muted {{ color: #888; }}
.small {{ font-size: .8rem; }}
button, .secondary-btn {{ background: #3d6bde; color: white; border: none; padding: .5rem 1.1rem;
          border-radius: 6px; font-size: .85rem; cursor: pointer; }}
button:hover {{ background: #4d7bee; }}
.secondary-btn {{ background: rgba(127,127,127,.18); color: inherit; }}
.secondary-btn:hover {{ background: rgba(127,127,127,.28); }}
form {{ display: inline; }}
.group-card {{ display: block; margin-bottom: 1rem; padding: 1.1rem 1.3rem; }}
.group-head {{ display: flex; align-items: center; gap: .5rem; flex-wrap: wrap; margin-bottom: .8rem; }}
.apply-form {{ display: block; }}
.field-label {{ display: block; font-size: .78rem; color: #999; margin: .5rem 0 .3rem; }}
.text-input {{ width: 100%; max-width: 480px; padding: .45rem .6rem; border-radius: 6px;
               border: 1px solid rgba(127,127,127,.35); background: rgba(127,127,127,.08);
               color: inherit; font-size: .85rem; margin-bottom: .6rem; }}
.pt-row {{ display: flex; align-items: center; gap: .6rem; margin-bottom: .4rem; flex-wrap: wrap; }}
.pt-path {{ flex: 1 1 260px; color: #999; }}
.pt-artist {{ flex: 0 0 auto; color: #999; font-size: .8rem; }}
.pt-input {{ flex: 1 1 200px; margin-bottom: 0; }}
details {{ margin-top: .8rem; }}
details > summary {{ cursor: pointer; color: #999; font-size: .8rem; padding: .3rem 0; user-select: none; }}
details > summary:hover {{ color: #ccc; }}
.settings-panel {{ margin-bottom: 1.2rem; }}
.settings-panel > summary {{ font-size: .85rem; }}
.settings-body {{ display: flex; gap: 1.5rem; flex-wrap: wrap; padding: 1rem 0 .2rem; }}
.settings-field {{ flex: 1 1 280px; }}
.select-input {{ padding: .45rem .6rem; border-radius: 6px; border: 1px solid rgba(127,127,127,.35);
                 background: rgba(127,127,127,.08); color: inherit; font-size: .85rem; }}
</style></head>
<body>
<h1>tag-doctor</h1>
<div class="meta">
  <span>pasta: <span class="mono">{_esc(music_dir)}</span></span>
  <span>· último scan: {_esc(format_ts(last_scan)) or 'nunca'}</span>
</div>
<details class="card settings-panel">
  <summary>Configurações</summary>
  <div class="settings-body">
    <form method="post" action="/settings/music-dir" class="settings-field">
      <label class="field-label">Pasta a escanear (caminho dentro do container)</label>
      <input type="text" name="music_dir" value="{_esc(music_dir)}" class="text-input" style="max-width:100%">
      <button type="submit" class="secondary-btn">Salvar caminho</button>
    </form>
    <div class="settings-field">
      <label class="field-label">Tema</label>
      <select id="theme-select" class="select-input">
        <option value="auto">Automático (segue o sistema)</option>
        <option value="light">Claro</option>
        <option value="dark">Escuro</option>
      </select>
    </div>
  </div>
</details>
<div class="toolbar">
  <form method="post" action="/scan" id="scan-form">
    <button type="submit" id="scan-btn" {"disabled" if scanning else ""}>
      {"Escaneando..." if scanning else "Escanear agora"}
    </button>
  </form>
  {apply_all_html}
  {nd_html}
</div>
{flash_html}
{banner}
{cards}
{'<h2 style="font-size:1rem;color:#aaa;margin:1.8rem 0 .8rem;">Álbuns pendentes de revisão</h2>' if groups else ''}
{groups_html}
<script>
document.getElementById('scan-form').addEventListener('submit', function() {{
  var btn = document.getElementById('scan-btn');
  btn.disabled = true;
  btn.textContent = 'Escaneando...';
}});

(function() {{
  var select = document.getElementById('theme-select');
  var saved = localStorage.getItem('theme');
  select.value = saved || 'auto';
  select.addEventListener('change', function() {{
    if (select.value === 'auto') {{
      document.documentElement.removeAttribute('data-theme');
      localStorage.removeItem('theme');
    }} else {{
      document.documentElement.setAttribute('data-theme', select.value);
      localStorage.setItem('theme', select.value);
    }}
  }});
}})();
{"""
(function poll() {
  fetch('/api/state').then(r => r.json()).then(d => {
    if (d.scanning) { setTimeout(poll, 2000); } else { location.reload(); }
  }).catch(() => setTimeout(poll, 3000));
})();
""" if scanning else ""}
</script>
</body></html>"""
