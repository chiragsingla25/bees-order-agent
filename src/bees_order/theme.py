"""The look of the page: fonts, colours (as CSS variables, with a dark-mode set) and the
header bar. Palette rule (60-30-10): neutral surfaces, ink text, and BEES-style yellow only
for the one main action (Place order) and the "just changed" highlight. Orange = short stock,
red = can't be ordered, green = placed / in stock. ui.py holds the behaviour."""

from html import escape

HEAD = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700'
        '&display=swap" rel="stylesheet">'
        # Phone padding lives here, not in CSS: Gradio prefixes every rule inside an @media
        # block with ".gradio-container … .contain", so the outer container can't be reached.
        '<style>@media (max-width: 640px) { .gradio-container { padding: 0 12px !important; }'
        ' .gradio-container .main { padding: 0 !important; } }</style>')

CSS = """
:root, .dark {
  --page: #FAFAF9; --surface: #FFFFFF; --sunken: #F4F4F2; --line: #ECEBE7;
  --ink: #0F0F10; --muted: #6B6B70; --faint: #A1A1A6;
  --accent: #FFD60A; --accent-ink: #0F0F10; --accent-soft: #FFF7CC;
  --warn: #C2410C; --warn-bg: #FFF1E6; --err: #B91C1C; --err-bg: #FDECEC;
  --ok: #15803D; --ok-bg: #E9F8EE;
  --shadow: 0 1px 2px rgba(15,15,16,.04), 0 8px 28px rgba(15,15,16,.06);
  --sans: 'Geist', ui-sans-serif, system-ui, sans-serif;
}
.dark {
  --page: #0C0C0D; --surface: #161618; --sunken: #1E1E21; --line: #29292D;
  --ink: #F5F5F4; --muted: #A1A1A6; --faint: #6B6B70; --accent-soft: #332C08;
  --warn: #FB923C; --warn-bg: #2B1A0E; --err: #F87171; --err-bg: #2B1414;
  --ok: #4ADE80; --ok-bg: #0F2A18; --shadow: 0 1px 2px rgba(0,0,0,.4);
}
body, .gradio-container, .gradio-container * { font-family: var(--sans) !important; }
body, .gradio-container { background: var(--page) !important; color: var(--ink); }
.gradio-container { max-width: 1200px !important; margin: 0 auto !important;
  padding: 0 24px !important; }
.num { font-variant-numeric: tabular-nums; letter-spacing: -.01em; }

/* Top bar */
#topbar { position: sticky; top: 0; z-index: 10; display: flex; align-items: center;
  justify-content: space-between; gap: 16px; margin: 12px 0 8px; padding: 10px 12px 10px 10px;
  background: color-mix(in srgb, var(--surface) 82%, transparent);
  backdrop-filter: saturate(1.4) blur(14px); -webkit-backdrop-filter: saturate(1.4) blur(14px);
  border: 1px solid var(--line); border-radius: 18px; box-shadow: var(--shadow); }
#topbar .brand { display: flex; align-items: center; gap: 12px; }
#topbar .mark { width: 38px; height: 38px; border-radius: 12px; display: grid; place-items: center;
  background: linear-gradient(135deg, #FFE45C, var(--accent) 55%, #F5B800);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.6), 0 4px 12px rgba(245,184,0,.35); }
#topbar .mark svg { width: 20px; height: 20px; }
#topbar .title { font-weight: 600; font-size: 1.02rem; letter-spacing: -.02em; color: var(--ink);
  line-height: 1.2; }
#topbar .title .ai { font-size: .66rem; font-weight: 600; letter-spacing: .04em; margin-left: 6px;
  padding: 2px 6px; border-radius: 6px; vertical-align: 2px; color: var(--muted);
  background: var(--sunken); border: 1px solid var(--line); }
#topbar .sub { font-size: .8rem; color: var(--muted); line-height: 1.2; margin-top: 2px; }
#topbar .right { display: flex; align-items: center; gap: 8px; }
#topbar .chip { display: inline-flex; align-items: center; gap: 6px; font-size: .78rem;
  color: var(--muted); padding: 6px 10px; border-radius: 999px; background: var(--sunken);
  border: 1px solid var(--line); white-space: nowrap; }
#topbar .live::before { content: ""; width: 7px; height: 7px; border-radius: 50%;
  background: var(--ok); box-shadow: 0 0 0 3px var(--ok-bg); animation: pulse 2s infinite; }
@keyframes pulse { 50% { box-shadow: 0 0 0 6px transparent; } }
#topbar a.icon { width: 34px; height: 34px; display: grid; place-items: center; border-radius: 10px;
  color: var(--ink); border: 1px solid var(--line); background: var(--surface); }
#topbar a.icon:hover { background: var(--sunken); }
@media (max-width: 640px) { #topbar .chip:not(.live) { display: none; } }

/* Chat */
#chat-card { background: var(--surface); border: 1px solid var(--line) !important;
  border-radius: 24px !important; box-shadow: var(--shadow); padding: 8px !important;
  gap: 8px !important; }
#chat, #chat > div { background: transparent !important; border: none !important; }
#chat .message-row { padding: 2px 8px !important; }
#chat .message { font-size: .95rem !important; line-height: 1.55 !important;
  box-shadow: none !important; border: none !important; }
#chat .message.user { background: var(--ink) !important; color: var(--page) !important;
  border-radius: 18px 18px 4px 18px !important; padding: 10px 14px !important; }
#chat .message.bot { background: var(--sunken) !important; color: var(--ink) !important;
  border-radius: 18px 18px 18px 4px !important; padding: 10px 14px !important; }
#chat .message.user *, #chat .message.bot * { color: inherit !important; }
#chat .message p { margin: 0 0 6px !important; } #chat .message p:last-child { margin: 0 !important; }
#chat button[aria-label="Clear"] { display: none !important; }  /* the cart has its own */
#composer { background: var(--sunken) !important; border-radius: 16px !important;
  border: 1px solid var(--line) !important; padding: 4px 6px !important; }
#composer textarea { background: transparent !important; border: none !important;
  box-shadow: none !important; font-size: .95rem !important; }
#composer button { background: var(--ink) !important; color: var(--page) !important;
  border-radius: 12px !important; }

/* Side panel */
#side { position: sticky; top: 12px; align-self: flex-start; gap: 12px !important; }
#side-tabs { background: transparent !important; border: none !important; padding: 0 !important; }
#side-tabs .tab-wrapper { background: var(--sunken); border-radius: 12px; padding: 4px;
  border: none !important; height: auto !important; }
#side-tabs button[role="tab"] { flex: 1; border-radius: 9px !important; border: none !important;
  font-weight: 500; color: var(--muted) !important; padding: 8px 12px !important; }
#side-tabs button[role="tab"].selected { background: var(--surface) !important;
  color: var(--ink) !important; box-shadow: var(--shadow); }
#side-tabs button[role="tab"]::after { display: none !important; }
#side-tabs .tabitem { padding: 12px 0 0 !important; border: none !important;
  background: transparent !important; }
.card { background: var(--surface); border: 1px solid var(--line); border-radius: 20px;
  box-shadow: var(--shadow); padding: 18px 18px 12px; color: var(--ink); font-size: .92rem; }
.card-head { display: flex; justify-content: space-between; align-items: baseline;
  margin-bottom: 6px; }
.card-head .t { font-weight: 600; font-size: 1rem; letter-spacing: -.01em; }
.card-head .n { color: var(--muted); font-size: .8rem; }
.line { display: flex; justify-content: space-between; gap: 12px; padding: 12px 10px;
  margin: 0 -10px; border-radius: 12px; transition: background .4s; }
.line + .line { box-shadow: inset 0 1px 0 var(--line); }
.line .name { font-weight: 500; letter-spacing: -.01em; }
.line .meta { color: var(--muted); font-size: .8rem; margin-top: 2px; }
.line .amt { white-space: nowrap; font-weight: 600; }
.pill { display: inline-flex; align-items: center; gap: 5px; margin-top: 6px; font-size: .74rem;
  font-weight: 600; padding: 3px 9px; border-radius: 999px; }
.pill::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.warn .pill, .pill.warn { color: var(--warn); background: var(--warn-bg); }
.err .pill, .pill.err { color: var(--err); background: var(--err-bg); }
.pill.ok { color: var(--ok); background: var(--ok-bg); }
.line.err .name, .line.err .amt { color: var(--faint); text-decoration: line-through; }
.line.ask { box-shadow: none !important; outline: 1.5px dashed var(--ink); outline-offset: -1.5px;
  margin: 6px -10px; }
.line.ask .pill { color: var(--accent-ink); background: var(--accent-soft); }
.line.changed { background: var(--accent-soft); }
.line.changed + .line { box-shadow: none; }
.line .per { text-align: right; }
.total { display: flex; justify-content: space-between; align-items: baseline;
  padding: 14px 0 4px; margin-top: 4px; border-top: 1px solid var(--line); }
.total .k { color: var(--muted); font-size: .85rem; }
.total .v { font-weight: 600; font-size: 1.35rem; }
.hint { color: var(--muted); font-size: .8rem; padding: 4px 0 4px; }
.empty { text-align: center; padding: 40px 8px 32px; color: var(--muted); font-size: .88rem; }
.empty .ico { width: 44px; height: 44px; margin: 0 auto 12px; border-radius: 14px;
  background: var(--sunken); display: grid; place-items: center; font-size: 1.2rem; }
.empty .big { font-size: 1rem; color: var(--ink); font-weight: 600; margin-bottom: 4px; }
.receipt { text-align: center; padding: 24px 8px 16px; }
.receipt .no { font-size: 2rem; font-weight: 700; margin: 12px 0 2px; letter-spacing: -.03em; }
.receipt .sum { color: var(--muted); font-size: .88rem; }
.group { font-size: .72rem; font-weight: 600; text-transform: uppercase; letter-spacing: .06em;
  color: var(--faint); padding: 14px 0 2px; }
.group:first-of-type { padding-top: 4px; }
.prod { max-height: 431px; overflow-y: auto; margin: 0 -18px; padding: 0 18px; }
#search textarea, #search input { background: var(--sunken) !important; border-radius: 12px !important;
  border: 1px solid var(--line) !important; box-shadow: none !important; }

#place { background: var(--accent) !important; color: var(--accent-ink) !important;
  border: none !important; font-weight: 600 !important; font-size: 1rem !important;
  border-radius: 14px !important; min-height: 52px; box-shadow: 0 6px 20px rgba(255,214,10,.35); }
#place:hover { filter: brightness(.97); }
@media (max-width: 640px) {
  #topbar .sub { display: none; }
  #chat { height: 460px !important; }
}
#place:disabled { background: var(--sunken) !important; color: var(--faint) !important;
  box-shadow: none; }
#clear { background: transparent !important; border: none !important; box-shadow: none !important;
  color: var(--muted) !important; font-size: .82rem !important; }
#clear:hover { color: var(--err) !important; }
"""


def topbar(github_url: str) -> str:
    return (
        '<div id="topbar"><div class="brand"><span class="mark"><svg viewBox="0 0 24 24" '
        'fill="none" stroke="#0F0F10" stroke-width="2" stroke-linejoin="round"><path d="M12 2.5l8.2 '
        '4.75v9.5L12 21.5l-8.2-4.75v-9.5z"/><path d="M8.5 12.5l2.4 2.4 4.6-5" stroke-linecap="round"/>'
        '</svg></span><div><div class="title">BEES Order Agent<span class="ai">AI</span></div>'
        '<div class="sub">Order drinks by chat · priced and stock-checked</div></div></div>'
        '<div class="right"><span class="chip live">Live stock</span>'
        '<span class="chip">Unofficial demo · synthetic data</span>'
        f'<a class="icon" href="{escape(github_url)}" target="_blank" title="GitHub">'
        '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M12 .5a11.5 '
        '11.5 0 0 0-3.64 22.41c.58.1.79-.25.79-.56v-2c-3.2.7-3.88-1.37-3.88-1.37-.53-1.33-1.29-1.69'
        '-1.29-1.69-1.05-.72.08-.7.08-.7 1.16.08 1.77 1.19 1.77 1.19 1.03 1.77 2.71 1.26 3.37.96.1-.75'
        '.4-1.26.73-1.55-2.55-.29-5.24-1.28-5.24-5.68 0-1.25.45-2.28 1.19-3.08-.12-.29-.52-1.46.11-'
        '3.04 0 0 .97-.31 3.17 1.18a11 11 0 0 1 5.77 0c2.2-1.49 3.17-1.18 3.17-1.18.63 1.58.23 2.75'
        '.11 3.04.74.8 1.19 1.83 1.19 3.08 0 4.41-2.69 5.38-5.26 5.67.41.36.78 1.06.78 2.14v3.17c0 '
        '.31.21.67.8.56A11.5 11.5 0 0 0 12 .5z"/></svg></a></div></div>')
