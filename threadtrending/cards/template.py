"""The Threads-replica card, as HTML the renderer screenshots.

The page exposes ``window.__fit(html, fontPx)`` so the renderer can measure a
laid-out card without a round trip per attempt -- the binary search for the
largest font size that fits 384px runs entirely in-page.
"""
from __future__ import annotations

import html as html_lib
from typing import Any

from jinja2 import Environment

from . import theme as T

_env = Environment(autoescape=False)

VERIFIED_SVG = (
    '<svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true">'
    '<path fill="{color}" d="M12 1.5 14.6 4l3.5-.4 1 3.4 3 1.8-1.4 3.2 1.4 3.2-3 1.8'
    '-1 3.4-3.5-.4L12 22.5 9.4 20l-3.5.4-1-3.4-3-1.8L3.3 12 1.9 8.8l3-1.8 1-3.4'
    ' 3.5.4L12 1.5Z"/>'
    '<path fill="#fff" d="m10.8 15.3-3-3 1.3-1.3 1.7 1.7 4.1-4.1 1.3 1.3-5.4 5.4Z"/>'
    "</svg>"
)

HEART_SVG = (
    '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">'
    '<path fill="{color}" d="M12 21s-7.5-4.7-9.5-9A5.2 5.2 0 0 1 12 6.6 5.2 5.2 0 0 1'
    ' 21.5 12c-2 4.3-9.5 9-9.5 9Z"/></svg>'
)

REPLY_SVG = (
    '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">'
    '<path fill="none" stroke="{color}" stroke-width="1.8" stroke-linejoin="round"'
    ' d="M21 11.5a8 8 0 0 1-8 8H4l2.2-2.6A8 8 0 1 1 21 11.5Z"/></svg>'
)

CARD_TEMPLATE = """<!DOCTYPE html>
<html lang="vi"><head><meta charset="utf-8"><style>
{{ font_face }}
*{margin:0;padding:0;box-sizing:border-box;}
html,body{background:transparent;}
body{
  font-family:'TT','Segoe UI','Segoe UI Emoji','Segoe UI Symbol',
              'Noto Color Emoji',system-ui,sans-serif;
  -webkit-font-smoothing:antialiased;
  text-rendering:optimizeLegibility;
  font-variant-ligatures:none;
  font-kerning:normal;
}
#card{
  width:{{ card_w }}px;
  max-height:{{ card_max_h }}px;
  padding:{{ pad }}px;
  display:flex;
  gap:{{ gutter }}px;
  align-items:flex-start;
  background:{{ t.card_bg }};
  border:1px solid {{ t.card_border }};
  border-radius:24px;
  box-shadow:{{ t.shadow }};
  overflow:hidden;
}
#avatar{
  width:{{ avatar }}px;height:{{ avatar }}px;flex:0 0 {{ avatar }}px;
  border-radius:50%;object-fit:cover;
  box-shadow:inset 0 0 0 1px {{ t.avatar_ring }};
}
#col{flex:1 1 auto;min-width:0;}
#namerow{
  height:{{ name_row_h }}px;
  display:flex;align-items:center;gap:6px;
  margin-bottom:{{ name_gap }}px;
}
#name{
  font-size:26px;font-weight:600;color:{{ t.name }};
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:74%;
}
#verified{display:flex;align-items:center;flex:0 0 auto;}
#time{font-size:21px;color:{{ t.muted }};margin-left:auto;flex:0 0 auto;}
#replystrip{
  height:{{ reply_strip_h }}px;display:flex;align-items:center;
  font-size:20px;color:{{ t.reply_strip }};
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
#body{
  color:{{ t.body }};
  font-size:{{ font_px }}px;
  line-height:{{ line_px }}px;
  font-weight:400;
  overflow-wrap:break-word;
  word-break:normal;
  white-space:pre-wrap;
}
#actions{
  height:{{ action_row_h }}px;margin-top:{{ action_gap }}px;
  display:flex;align-items:center;gap:22px;
  font-size:20px;color:{{ t.meta }};
}
.act{display:flex;align-items:center;gap:6px;}
#pageind{margin-left:auto;font-size:19px;color:{{ t.muted }};letter-spacing:.06em;}
</style></head><body>
<div id="card">
  <img id="avatar" src="{{ avatar_src }}" alt="">
  <div id="col">
    <div id="namerow">
      <span id="name">{{ username }}</span>
      {% if is_verified %}<span id="verified">{{ verified_svg }}</span>{% endif %}
      <span id="time">{{ timestamp }}</span>
    </div>
    {% if reply_to %}<div id="replystrip">&#8627; trả lời @{{ reply_to }}</div>{% endif %}
    <div id="body">{{ body }}</div>
    <div id="actions">
      <span class="act">{{ heart_svg }}<span>{{ like_label }}</span></span>
      {% if reply_label %}<span class="act">{{ reply_svg }}<span>{{ reply_label }}</span></span>{% endif %}
      {% if page_label %}<span id="pageind">{{ page_label }}</span>{% endif %}
    </div>
  </div>
</div>
<script>
// Measure a laid-out card at a given font size. The binary search for the
// largest size that fits CARD_MAX_H runs in-page so Playwright is called once
// per candidate size rather than once per re-render.
window.__fit = function (bodyHtml, fontPx) {
  var body = document.getElementById('body');
  var card = document.getElementById('card');
  if (bodyHtml !== null) { body.innerHTML = bodyHtml; }
  body.style.fontSize = fontPx + 'px';
  body.style.lineHeight = Math.round(fontPx * {{ line_ratio }}) + 'px';
  // The card carries max-height + overflow:hidden so an overflowing render is
  // clipped rather than ragged. Measuring it in that state would report the
  // clamped 384px for ANY amount of text, the binary search would always
  // "succeed", and long comments would be silently truncated. Lift the clamp
  // for the measurement, then put it back.
  var clamp = card.style.maxHeight;
  card.style.maxHeight = 'none';
  void card.offsetHeight;                       // force layout before reading
  var natural = card.getBoundingClientRect().height;
  card.style.maxHeight = clamp || '{{ card_max_h }}px';
  return natural;
};
</script>
</body></html>"""

BANNER_TEMPLATE = """<!DOCTYPE html>
<html lang="vi"><head><meta charset="utf-8"><style>
{{ font_face }}
*{margin:0;padding:0;box-sizing:border-box;}
html,body{background:transparent;}
body{font-family:'TT','Segoe UI','Segoe UI Emoji',system-ui,sans-serif;
     -webkit-font-smoothing:antialiased;}
#banner{
  width:{{ banner_w }}px;height:{{ banner_h }}px;
  display:flex;align-items:center;justify-content:center;gap:10px;
  padding:0 26px;
  background:{{ t.banner_bg }};
  border:1px solid {{ t.banner_border }};
  border-radius:{{ radius }}px;
  color:{{ t.banner_text }};
  font-size:26px;font-weight:600;
  white-space:nowrap;overflow:hidden;
}
#sub{font-weight:400;opacity:.62;font-size:23px;}
</style></head><body>
<div id="banner"><span>{{ flame }} {{ title }}</span><span id="sub">· {{ subtitle }}</span></div>
</body></html>"""


def _compact_count(value: int) -> str:
    """Threads-style abbreviation: 1234 -> 1,2K."""
    if value < 1000:
        return str(value)
    if value < 1_000_000:
        return f"{value / 1000:.1f}".replace(".0", "").replace(".", ",") + "K"
    return f"{value / 1_000_000:.1f}".replace(".0", "").replace(".", ",") + "M"


def escape_body(text: str) -> str:
    """Escape for innerHTML while keeping author line breaks."""
    return html_lib.escape(text, quote=False).replace("\n", "<br>")


def render_card_html(
    *,
    username: str,
    body: str,
    avatar_src: str,
    is_verified: bool,
    timestamp: str,
    like_count: int,
    reply_count: int,
    reply_to: str | None,
    theme: T.Theme,
    font_px: int,
    page_label: str = "",
) -> str:
    tokens = T.tokens(theme)
    context: dict[str, Any] = {
        "font_face": T.font_face_css(),
        "t": tokens,
        "card_w": T.CARD_W,
        "card_max_h": T.CARD_MAX_H,
        "pad": T.PAD,
        "avatar": T.AVATAR,
        "gutter": T.GUTTER,
        "name_row_h": T.NAME_ROW_H,
        "name_gap": T.NAME_GAP,
        "reply_strip_h": T.REPLY_STRIP_H,
        "action_gap": T.ACTION_GAP,
        "action_row_h": T.ACTION_ROW_H,
        "line_ratio": T.LINE_RATIO,
        "font_px": font_px,
        "line_px": round(font_px * T.LINE_RATIO),
        "username": html_lib.escape(username, quote=False),
        "body": escape_body(body),
        "avatar_src": avatar_src,
        "is_verified": is_verified,
        "timestamp": html_lib.escape(timestamp, quote=False),
        "like_label": _compact_count(like_count),
        "reply_label": _compact_count(reply_count) if reply_count else "",
        "reply_to": html_lib.escape(reply_to, quote=False) if reply_to else "",
        "page_label": page_label,
        "verified_svg": VERIFIED_SVG.format(color=tokens["verified"]),
        "heart_svg": HEART_SVG.format(color=tokens["heart"]),
        "reply_svg": REPLY_SVG.format(color=tokens["meta"]),
    }
    return _env.from_string(CARD_TEMPLATE).render(**context)


def render_banner_html(
    *,
    title: str,
    subtitle: str = "đang trending trên Threads",
    theme: T.Theme = "dark",
) -> str:
    return _env.from_string(BANNER_TEMPLATE).render(
        font_face=T.font_face_css(),
        t=T.tokens(theme),
        banner_w=T.BANNER_W,
        banner_h=T.BANNER_H,
        radius=T.BANNER_H // 2,
        title=html_lib.escape(title, quote=False),
        subtitle=html_lib.escape(subtitle, quote=False),
        flame="\U0001F525",
    )
