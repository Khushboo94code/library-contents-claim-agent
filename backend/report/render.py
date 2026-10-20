"""Renders a human-readable HTML report from a claim packet.

The report is built FROM claim_packet.json — totals and lines come straight from the
structured data (which were themselves computed in code), so the report can never
disagree with the packet. Stages not yet produced simply show as empty/pending, which
is the correct behaviour per the brief ("an empty field is correct when unknown").
"""

from __future__ import annotations

from jinja2 import Template

from backend.schema import ClaimPacket

_TEMPLATE = Template(
    """<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
{% if processing %}<meta http-equiv="refresh" content="4"/>{% endif %}
<title>Claim Packet — {{ sweep_id }}</title>
<style>
  :root { color-scheme: light; --ink:#1C1C18; --sage:#A6C089; --sage-deep:#8FB06E;
          --lime:#DCEE4A; --lav:#C7BCDF; --cream:#F5F2EA; --taupe:#DED8C8; --line: rgba(28,28,24,.12); }
  body { font: 15px -apple-system, system-ui, sans-serif; margin: 0; padding: 0 20px 28px;
         background: #E8E3D6; color: var(--ink); }
  .hero { margin: 0 -20px 20px; padding: 28px 20px 22px;
          background: linear-gradient(135deg, #B2C994, #8FB06E); color: var(--ink); }
  h1 { font-size: 30px; font-weight: 800; margin: 0 0 4px; letter-spacing: -.6px; text-transform: uppercase; }
  .sub { opacity: .7; font-size: 13px; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px,1fr)); gap: 10px; margin-bottom: 22px; }
  .card { background: var(--cream); border: none; border-radius: 22px; padding: 16px;
          box-shadow: 0 8px 16px rgba(28,28,24,.06); }
  .card .n { font-size: 26px; font-weight: 800; color: var(--ink); letter-spacing: -.5px; }
  .card .l { font-size: 11px; opacity: .5; text-transform: uppercase; letter-spacing: .4px; margin-top: 2px; }
  h2 { font-size: 16px; margin: 26px 0 10px; display: flex; align-items: center; gap: 8px;
       text-transform: uppercase; letter-spacing: -.2px; font-weight: 800; }
  h2::before { content: ""; width: 5px; height: 16px; background: var(--sage-deep); border-radius: 3px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
  th { opacity: .5; font-weight: 700; font-size: 11px; text-transform: uppercase; }
  .pill { display: inline-block; padding: 3px 9px; border-radius: 10px; font-size: 11px; font-weight: 600; }
  .identified { background: #E2EFD0; color: #3E6B22; }
  .unidentified, .range { background: #F0EEBE; color: #6f6210; }
  .needs_appraisal, .priced { background: #E8E1F2; color: #5b4a86; }
  .empty { opacity: .5; font-style: italic; padding: 10px 0; }
  .money { font-variant-numeric: tabular-nums; }
  .muted { opacity: .5; }
  {% if pdf %}
  /* Print-friendly: pure-white page behind the warm cards. */
  body { background: #fff; }
  {% endif %}
</style></head>
<body>
  <div class="hero">
    <h1>Contents Claim Packet</h1>
    <div class="sub">Sweep {{ sweep_id }}
      {% if p.sweep.captured_at %}· {{ p.sweep.captured_at }}{% endif %}
      {% if p.sweep.country %}· {{ p.sweep.country }} / {{ p.sweep.currency }}{% endif %}</div>
  </div>
  {% if processing %}<div class="card" style="background:#F0EEBE;margin-bottom:16px">
    ⏳ <b>Compiling your packet…</b> reading spines and assembling the inventory. This page refreshes automatically.
  </div>{% endif %}

  <div class="cards">
    <div class="card"><div class="n">{{ p.totals.book_count }}</div><div class="l">Books</div></div>
    <div class="card"><div class="n">{{ p.totals.books_identified }}</div><div class="l">Identified</div></div>
    <div class="card"><div class="n">{{ p.items|length }}</div><div class="l">Non-book items</div></div>
    <div class="card"><div class="n">{{ "%.1f"|format(p.totals.shelf_run_m) }} m</div><div class="l">Shelf run</div></div>
    <div class="card"><div class="n money">{{ cur }}{{ "%.0f"|format(p.totals.books_replacement_cost) }}</div><div class="l">Books replacement</div></div>
    <div class="card"><div class="n">{{ p.review_queue|length }}</div><div class="l">Review queue</div></div>
  </div>

  <h2>Room</h2>
  {% if p.room.floor_area_m2 %}
  <table><tr><th>Floor area</th><th>Wall area</th><th>Dimensions (L×W×H)</th><th>Scale method</th></tr>
  <tr>
    <td>{{ "%.1f"|format(p.room.floor_area_m2) }} m²</td>
    <td>{{ "%.1f"|format(p.room.wall_area_m2 or 0) }} m²</td>
    <td>{{ "%.2f"|format(p.room.length_m or 0) }} × {{ "%.2f"|format(p.room.width_m or 0) }} × {{ "%.2f"|format(p.room.height_m or 0) }} m</td>
    <td class="muted">{{ p.room.scale_method or "—" }}</td>
  </tr></table>
  {% else %}<div class="empty">Room not measured yet (needs plane anchors or a fiducial-scaled sweep).</div>{% endif %}

  <h2>Books</h2>
  {% if p.books %}
  <table><tr><th>Title</th><th>Author</th><th>Status</th><th>Spine (h×t)</th><th>Replacement</th><th>Used</th></tr>
  {% for b in p.books %}<tr>
    <td>{{ b.title or "—" }}</td><td>{{ b.author or "—" }}</td>
    <td><span class="pill {{ b.status }}">{{ b.status }}</span></td>
    <td>{% if b.spine_height_cm %}{{ "%.1f"|format(b.spine_height_cm) }}×{{ "%.1f"|format(b.spine_thickness_cm or 0) }} cm{% else %}—{% endif %}</td>
    <td class="money">{% if b.replacement_cost.amount %}{{ cur }}{{ "%.0f"|format(b.replacement_cost.amount) }}{% else %}—{% endif %}</td>
    <td class="money">{% if b.used_value.amount %}{{ cur }}{{ "%.0f"|format(b.used_value.amount) }}{% else %}—{% endif %}</td>
  </tr>{% endfor %}</table>
  {% else %}<div class="empty">No books identified yet (Stage A identification pending).</div>{% endif %}

  <h2>Non-book items</h2>
  {% if p.items %}
  <table><tr><th>Category</th><th>Description</th><th>Status</th><th>Price range</th></tr>
  {% for it in p.items %}<tr>
    <td>{{ it.category or "—" }}</td><td>{{ it.description or "—" }}</td>
    <td><span class="pill {{ it.status }}">{{ it.status }}</span></td>
    <td class="money">{% if it.replacement_cost.low %}{{ cur }}{{ "%.0f"|format(it.replacement_cost.low) }}–{{ "%.0f"|format(it.replacement_cost.high or 0) }}{% else %}—{% endif %}</td>
  </tr>{% endfor %}</table>
  {% else %}<div class="empty">No non-book items yet (Stage C detection pending).</div>{% endif %}

  {% if floorplan_svg %}
  <h2>Floor plan</h2>
  <div style="background:#fff;border-radius:12px;padding:8px;max-width:420px">{{ floorplan_svg|safe }}</div>
  {% endif %}

  {% if p.agent_observations %}
  <h2>Agent observations <span class="muted">— logged live during the sweep</span></h2>
  <table><tr><th>Kind</th><th>Observation</th><th>Frame</th></tr>
  {% for o in p.agent_observations %}<tr>
    <td>{{ o.kind }}</td><td>{{ o.label }}{% if o.note %} <span class="muted">— {{ o.note }}</span>{% endif %}</td>
    <td class="muted">{{ o.frame_ref }}</td></tr>{% endfor %}</table>
  {% endif %}

  {% if p.evidence %}
  <h2>Evidence manifest <span class="muted">— every line traces to a frame</span></h2>
  <table><tr><th>Ref</th><th>Kind</th><th>Label</th><th>Frame</th></tr>
  {% for e in p.evidence %}<tr>
    <td class="muted">{{ e.ref_id }}</td><td>{{ e.kind }}</td><td>{{ e.label }}</td>
    <td class="muted">{{ e.frame_ref }}</td></tr>{% endfor %}</table>
  {% endif %}

  <h2>Review queue <span class="muted">— lines needing a human</span></h2>
  {% if p.review_queue %}
  <table><tr><th>Ref</th><th>Reason</th></tr>
  {% for r in p.review_queue %}<tr><td class="muted">{{ r.ref_id }}</td><td>{{ r.reason }}</td></tr>{% endfor %}</table>
  {% else %}<div class="empty">Nothing flagged.</div>{% endif %}
</body></html>"""
)


def render_report_html(packet: ClaimPacket, sweep_id: str, processing: bool = False,
                       floorplan_svg: str = "", pdf: bool = False) -> str:
    cur_symbols = {"INR": "₹", "USD": "$", "GBP": "£", "EUR": "€", "AED": "AED "}
    cur = cur_symbols.get(packet.sweep.currency, (packet.sweep.currency + " ") if packet.sweep.currency else "")
    # The PDF variant never auto-refreshes.
    return _TEMPLATE.render(p=packet, sweep_id=sweep_id, cur=cur,
                            processing=(processing and not pdf), floorplan_svg=floorplan_svg, pdf=pdf)
