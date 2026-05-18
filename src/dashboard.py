"""Dashboard Flask.

Routes :
    GET /                      → redirige vers /log
    GET /log                   → historique des décisions
    GET /inbox                 → emails score=4 en attente
    GET /promos                → promos en attente
    GET /social                → notifications sociales en attente
    GET /stats                 → statistiques

Usage :
    python src/dashboard.py [--port 5002] [--db path/to/postroom.db]
"""
import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, abort, redirect, render_template_string, request, url_for

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

import db as db_module

app = Flask(__name__)
DB_PATH = ROOT / "postroom.db"
CALIBRATE = False


@app.context_processor
def _inject_calibrate():
    return {"calibrate": CALIBRATE}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _conn() -> sqlite3.Connection:
    conn = db_module.get_conn(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(conn, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return conn.execute(sql, params).fetchall()


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

_BASE = """\
<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>postroom</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 0; background: #f5f5f5; color: #222; }
  header { background: #1a1a2e; color: #fff; padding: .75rem 1.5rem; display: flex; gap: 2rem; align-items: center; }
  header h1 { margin: 0; font-size: 1.1rem; letter-spacing: .05em; }
  nav a { color: #adf; text-decoration: none; font-size: .95rem; margin-right: .5rem; }
  nav a:hover { text-decoration: underline; }
  main { padding: 1.5rem; max-width: 1100px; margin: auto; }
  table { width: 100%; border-collapse: collapse; background: #fff; border-radius: 6px; overflow: hidden; box-shadow: 0 1px 3px #0001; }
  th { background: #e8e8e8; text-align: left; padding: .5rem .75rem; font-size: .8rem; text-transform: uppercase; letter-spacing: .04em; }
  td { padding: .45rem .75rem; font-size: .85rem; border-bottom: 1px solid #eee; vertical-align: top; }
  tr:last-child td { border-bottom: none; }
  .badge { display: inline-block; padding: .15em .5em; border-radius: 4px; font-size: .75rem; font-weight: 600; }
  .s1 { background: #fee; color: #c00; }
  .s2 { background: #fff3cd; color: #7a5800; }
  .s3 { background: #e6f4ea; color: #276221; }
  .s4 { background: #e3effe; color: #1045a0; }
  .keep   { background: #e6f4ea; color: #276221; }
  .delete { background: #fee; color: #c00; }
  .review { background: #fff3cd; color: #7a5800; }
  .llm    { background: #f0e6ff; color: #5a00a0; }
  .rule   { color: #666; font-size: .75rem; }
  .reason { color: #555; max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .empty  { padding: 2rem; text-align: center; color: #888; }
  form.inline { display: inline; }
  button { cursor: pointer; border: none; border-radius: 4px; padding: .25rem .6rem; font-size: .8rem; }
  .btn-snooze { background: #e0e0e0; color: #333; }
  .btn-done   { background: #276221; color: #fff; }
  .section-title { margin: 1.5rem 0 .5rem; font-size: 1rem; font-weight: 600; color: #444; }
  .stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px,1fr)); gap: 1rem; margin-bottom: 1.5rem; }
  .stat-card { background: #fff; border-radius: 6px; box-shadow: 0 1px 3px #0001; padding: 1rem; text-align: center; }
  .stat-card .num { font-size: 2rem; font-weight: 700; }
  .stat-card .lbl { font-size: .8rem; color: #666; margin-top: .25rem; }
  .calibrate-toggle { margin-left: auto; }
  .calibrate-toggle form { margin: 0; }
  .btn-toggle { font-size: .8rem; padding: .3rem .8rem; border-radius: 4px; border: 1px solid; cursor: pointer; font-weight: 600; }
  .btn-toggle.off { background: transparent; border-color: #555; color: #888; }
  .btn-toggle.on  { background: #ffd580; border-color: #ffd580; color: #1a1a2e; }
  .toast { position: fixed; bottom: 1.5rem; right: 1.5rem; padding: .6rem 1.2rem; border-radius: 6px; font-size: .85rem; font-weight: 600; opacity: 0; transition: opacity .3s; z-index: 999; pointer-events: none; }
  .toast.show { opacity: 1; }
  .toast-green  { background: #e6f4ea; color: #276221; border: 1px solid #276221; }
  .toast-red    { background: #fee;    color: #c00;    border: 1px solid #c00; }
  .toast-yellow { background: #fff3cd; color: #7a5800; border: 1px solid #e0a800; }
</style>
</head>
<body>
<header>
  <h1>📬 postroom</h1>
  <nav>
    <a href="{{ url_for('log') }}">Journal</a>
    <a href="{{ url_for('inbox') }}">Inbox (score 4)</a>
    <a href="{{ url_for('promos') }}">Promos</a>
    <a href="{{ url_for('social') }}">Réseau</a>
    <a href="{{ url_for('stats') }}">Stats</a>
    {% if calibrate %}<a href="{{ url_for('calibration') }}" style="color:#ffd580">⚙ Calibration</a>{% endif %}
  </nav>
  <div class="calibrate-toggle">
    <form method="post" action="{{ url_for('toggle_calibrate') }}">
      <button type="submit" class="btn-toggle {{ 'on' if calibrate else 'off' }}">
        Calibrate {{ 'ON' if calibrate else 'OFF' }}
      </button>
    </form>
  </div>
</header>
<main>
{% block content %}{% endblock %}
</main>
<div id="toast-stack" style="position:fixed;bottom:1.5rem;right:1.5rem;display:flex;flex-direction:column-reverse;gap:.5rem;z-index:999"></div>
<script>
(function(){
  function renderToasts() {
    var toasts = JSON.parse(sessionStorage.getItem('toasts') || '[]');
    var stack = document.getElementById('toast-stack');
    stack.innerHTML = '';
    toasts.forEach(function(t) {
      var div = document.createElement('div');
      div.className = 'toast toast-' + t.color + ' show';
      div.style.cssText = 'position:relative;max-width:280px;display:flex;justify-content:space-between;align-items:center;gap:.75rem';
      var subj = document.createElement('span');
      subj.style.cssText = 'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1';
      subj.textContent = t.subject || '(sans objet)';
      var undo = document.createElement('button');
      undo.textContent = 'Annuler';
      undo.style.cssText = 'font-size:.75rem;padding:.15rem .4rem;flex-shrink:0;background:#fff5;border:1px solid currentColor;border-radius:3px;cursor:pointer;color:inherit';
      undo.onclick = function() {
        fetch('/review/' + t.uid + '/undo', {method:'POST'})
          .then(function(){ removeToast(t.uid); });
      };
      div.appendChild(subj);
      div.appendChild(undo);
      stack.appendChild(div);
    });
  }

  window.doReview = function(uid, verdict, subject, spanId) {
    fetch('/review/' + uid + '/' + verdict, {method:'POST'})
      .then(function(r){ return r.json(); })
      .then(function(data) {
        var span = document.getElementById(spanId);
        if (span) {
          var badge = document.createElement('span');
          badge.className = 'badge ' + verdict;
          badge.textContent = verdict;
          span.replaceWith(badge);
        }
        var toasts = JSON.parse(sessionStorage.getItem('toasts') || '[]');
        toasts.push({color:data.toast, uid:uid, subject:subject||'', ts:Date.now()});
        sessionStorage.setItem('toasts', JSON.stringify(toasts));
        renderToasts();
      });
  };

  function removeToast(uid) {
    var toasts = JSON.parse(sessionStorage.getItem('toasts') || '[]');
    toasts = toasts.filter(function(t){ return t.uid !== uid; });
    sessionStorage.setItem('toasts', JSON.stringify(toasts));
    renderToasts();
  }

  renderToasts();

  setInterval(function(){
    var toasts = JSON.parse(sessionStorage.getItem('toasts') || '[]');
    var now = Date.now();
    var filtered = toasts.filter(function(t){ return now - t.ts < 8000; });
    if (filtered.length !== toasts.length) {
      sessionStorage.setItem('toasts', JSON.stringify(filtered));
      renderToasts();
    }
  }, 500);
})();
</script>
</body>
</html>
"""

_LOG_TMPL = (
    _BASE.replace("{% block content %}{% endblock %}", """\
<p class="section-title">Journal des décisions — {{ rows|length }} entrées</p>
{% if rows %}
<table>
  <thead><tr>
    <th>Date</th><th>Compte</th><th>Expéditeur</th><th>Objet</th>
    <th>Action</th><th>Score</th><th>LLM</th><th>Règle</th><th>Raison</th>
    {% if calibrate %}<th>Revue</th>{% endif %}
  </tr></thead>
  <tbody>
  {% for r in rows %}
  <tr{% if calibrate and r.reviewed %} style="opacity:.6"{% endif %}>
    <td style="white-space:nowrap">{{ r.decided_at[:16].replace("T"," ") }}</td>
    <td>{{ r.account }}</td>
    <td style="max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="{{ r.sender }}">{{ r.sender }}</td>
    <td style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="{{ r.subject }}">{{ r.subject }}</td>
    <td><span class="badge {{ r.action }}">{{ r.action }}</span></td>
    <td>{% if r.score %}<span class="badge s{{ r.score }}">{{ r.score }}</span>{% endif %}</td>
    <td style="text-align:center">{% if r.rule_id == 'llm' %}<span title="Classifié par LLM">🤖</span>{% endif %}</td>
    <td class="rule">{{ r.rule_id or "" }}</td>
    <td class="reason" title="{{ r.reason }}">{{ r.reason }}</td>
    {% if calibrate %}
    <td style="white-space:nowrap">
      {% if r.reviewed %}
        <span class="badge {{ r.reviewed }}" id="rev-{{ loop.index }}">{{ r.reviewed }}</span>
      {% else %}
        <span id="rev-{{ loop.index }}">
          <button class="btn-done" style="padding:.15rem .4rem;font-size:.75rem"
            onclick="doReview('{{ r.email_uid }}','keep','{{ r.subject|replace("'","\\'") }}','rev-{{ loop.index }}')">✓</button>
          <button style="background:#fee;color:#c00;border:none;border-radius:4px;padding:.15rem .4rem;font-size:.75rem;cursor:pointer"
            onclick="doReview('{{ r.email_uid }}','delete','{{ r.subject|replace("'","\\'") }}','rev-{{ loop.index }}')">🗑</button>
        </span>
      {% endif %}
    </td>
    {% endif %}
  </tr>
  {% endfor %}
  </tbody>
</table>
{% else %}
<div class="empty">Aucune décision enregistrée.</div>
{% endif %}
""")
)

_INBOX_TMPL = (
    _BASE.replace("{% block content %}{% endblock %}", """\
<p class="section-title">Inbox — actions requises ({{ rows|length }})</p>
{% if rows %}
<table>
  <thead><tr>
    <th>Ajouté le</th><th>Compte</th><th>Expéditeur</th><th>Objet</th>
    <th>Résumé</th><th>Snooze</th><th>Actions</th>
  </tr></thead>
  <tbody>
  {% for r in rows %}
  <tr>
    <td style="white-space:nowrap">{{ r.added_at[:16].replace("T"," ") }}</td>
    <td>{{ r.account }}</td>
    <td style="max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="{{ r.sender }}">{{ r.sender }}</td>
    <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="{{ r.subject }}">{{ r.subject }}</td>
    <td class="reason" title="{{ r.summary }}">{{ r.summary }}</td>
    <td>{{ r.snoozed_until[:10] if r.snoozed_until else "—" }}</td>
    <td>
      <form class="inline" method="post" action="{{ url_for('inbox_done', item_id=r.id) }}">
        <button class="btn-done" type="submit">✓ Traité</button>
      </form>
      &nbsp;
      <form class="inline" method="post" action="{{ url_for('inbox_snooze', item_id=r.id) }}">
        <input type="date" name="until" style="font-size:.8rem;padding:.1rem .3rem" required>
        <button class="btn-snooze" type="submit">Snooze</button>
      </form>
    </td>
  </tr>
  {% endfor %}
  </tbody>
</table>
{% else %}
<div class="empty">Aucune action en attente.</div>
{% endif %}
""")
)

_PROMOS_TMPL = (
    _BASE.replace("{% block content %}{% endblock %}", """\
<p class="section-title">Promos en attente ({{ rows|length }})</p>
<p style="font-size:.8rem;color:#888;margin-top:-.25rem">
  Conservées automatiquement 2 semaines · Suppression définitive après 1 semaine en corbeille
</p>
{% if rows %}
<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:1rem;margin-top:1rem">
{% for r in rows %}
<div style="background:#fff;border-radius:8px;box-shadow:0 1px 3px #0001;padding:1rem;display:flex;flex-direction:column;gap:.5rem">
  <div style="font-size:.75rem;color:#888">{{ r.added_at[:10] }} · expire {{ r.expires_at[:10] }} · {{ r.account }}</div>
  <div style="font-weight:600;font-size:.9rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="{{ r.subject }}">{{ r.subject }}</div>
  <div style="font-size:.8rem;color:#666;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="{{ r.sender }}">{{ r.sender }}</div>
  {% if r.summary %}
  <div style="font-size:.8rem;color:#555;font-style:italic">{{ r.summary }}</div>
  {% endif %}
  <div style="display:flex;gap:.5rem;margin-top:.25rem">
    <form class="inline" method="post" action="{{ url_for('promo_keep', item_id=r.id) }}" style="flex:1">
      <button class="btn-done" type="submit" style="width:100%">✓ Garder</button>
    </form>
    <form class="inline" method="post" action="{{ url_for('promo_delete', item_id=r.id) }}" style="flex:1">
      <button type="submit" style="width:100%;background:#fee;color:#c00;border:none;border-radius:4px;padding:.25rem .6rem;font-size:.8rem;cursor:pointer">🗑 Supprimer</button>
    </form>
  </div>
</div>
{% endfor %}
</div>
{% else %}
<div class="empty">Aucune promo en attente.</div>
{% endif %}
""")
)

_STATS_TMPL = (
    _BASE.replace("{% block content %}{% endblock %}", """\
<p class="section-title">Statistiques globales</p>
<div class="stat-grid">
  <div class="stat-card"><div class="num">{{ total_emails }}</div><div class="lbl">Emails traités</div></div>
  <div class="stat-card"><div class="num">{{ total_delete }}</div><div class="lbl">Supprimés</div></div>
  <div class="stat-card"><div class="num">{{ total_keep }}</div><div class="lbl">Conservés</div></div>
  <div class="stat-card"><div class="num">{{ total_review }}</div><div class="lbl">Revue (score 2)</div></div>
  <div class="stat-card"><div class="num">{{ pending_inbox }}</div><div class="lbl">En attente inbox</div></div>
</div>

<p class="section-title">Répartition des actions</p>
<table>
  <thead><tr><th>Action</th><th>Compte</th><th>Nombre</th></tr></thead>
  <tbody>
  {% for r in by_action %}
  <tr>
    <td><span class="badge {{ r.action }}">{{ r.action }}</span></td>
    <td>{{ r.account }}</td>
    <td>{{ r.cnt }}</td>
  </tr>
  {% endfor %}
  </tbody>
</table>

<p class="section-title">Règles les plus actives (top 10)</p>
<table>
  <thead><tr><th>Règle</th><th>Hits</th></tr></thead>
  <tbody>
  {% for r in top_rules %}
  <tr><td class="rule">{{ r.rule_id }}</td><td>{{ r.cnt }}</td></tr>
  {% endfor %}
  </tbody>
</table>
""")
)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return redirect(url_for("log"))


@app.route("/calibrate/toggle", methods=["POST"])
def toggle_calibrate():
    global CALIBRATE
    CALIBRATE = not CALIBRATE
    return redirect(request.referrer or url_for("log"))


@app.route("/log")
def log():
    limit = request.args.get("limit", 100, type=int)
    conn = _conn()
    try:
        if CALIBRATE:
            rows = _rows(conn, """
                SELECT d.decided_at, d.email_uid, d.reviewed,
                       e.account, e.sender, e.subject,
                       d.action, d.score, d.rule_id, d.reason
                FROM decisions d
                LEFT JOIN emails e ON e.uid = d.email_uid
                WHERE d.reviewed IS NULL
                ORDER BY d.decided_at DESC
                LIMIT ?
            """, (limit,))
        else:
            rows = _rows(conn, """
                SELECT d.decided_at, d.email_uid, d.reviewed,
                       e.account, e.sender, e.subject,
                       d.action, d.score, d.rule_id, d.reason
                FROM decisions d
                LEFT JOIN emails e ON e.uid = d.email_uid
                ORDER BY d.reviewed ASC, d.decided_at DESC
                LIMIT ?
            """, (limit,))
    finally:
        conn.close()
    return render_template_string(_LOG_TMPL, rows=rows)


@app.route("/inbox")
def inbox():
    conn = _conn()
    try:
        rows = _rows(conn, """
            SELECT rq.id, rq.added_at, rq.summary, rq.snoozed_until,
                   e.account, e.sender, e.subject
            FROM reply_queue rq
            LEFT JOIN emails e ON e.uid = rq.email_uid
            WHERE rq.status = 'pending'
              AND (rq.snoozed_until IS NULL OR rq.snoozed_until <= date('now'))
            ORDER BY rq.added_at DESC
        """)
    finally:
        conn.close()
    return render_template_string(_INBOX_TMPL, rows=rows)


@app.route("/inbox/<int:item_id>/done", methods=["POST"])
def inbox_done(item_id: int):
    now = datetime.now(timezone.utc).isoformat()
    conn = _conn()
    try:
        affected = conn.execute(
            "UPDATE reply_queue SET status='done', updated_at=? WHERE id=?",
            (now, item_id),
        ).rowcount
        conn.commit()
    finally:
        conn.close()
    if not affected:
        abort(404)
    return redirect(url_for("inbox"))


@app.route("/inbox/<int:item_id>/snooze", methods=["POST"])
def inbox_snooze(item_id: int):
    until = request.form.get("until", "")
    if not until:
        abort(400)
    now = datetime.now(timezone.utc).isoformat()
    conn = _conn()
    try:
        affected = conn.execute(
            "UPDATE reply_queue SET snoozed_until=?, updated_at=? WHERE id=?",
            (until, now, item_id),
        ).rowcount
        conn.commit()
    finally:
        conn.close()
    if not affected:
        abort(404)
    return redirect(url_for("inbox"))


@app.route("/promos")
def promos():
    conn = _conn()
    try:
        rows = _rows(conn, """
            SELECT pq.id, pq.added_at, pq.expires_at, pq.summary,
                   e.account, e.sender, e.subject
            FROM promo_queue pq
            LEFT JOIN emails e ON e.uid = pq.email_uid
            WHERE pq.status = 'pending'
            ORDER BY pq.expires_at ASC
        """)
    finally:
        conn.close()
    return render_template_string(_PROMOS_TMPL, rows=rows)


@app.route("/social")
def social():
    conn = _conn()
    try:
        rows = _rows(conn, """
            SELECT sq.id, sq.added_at, sq.expires_at, sq.summary,
                   e.account, e.sender, e.subject
            FROM social_queue sq
            LEFT JOIN emails e ON e.uid = sq.email_uid
            WHERE sq.status = 'pending'
            ORDER BY sq.added_at DESC
        """)
    finally:
        conn.close()
    return render_template_string(
        _PROMOS_TMPL.replace("Promos en attente", "Réseau social en attente")
                    .replace("url_for('promo_keep'", "url_for('social_keep'")
                    .replace("url_for('promo_delete'", "url_for('social_delete'")
                    .replace("2 semaines", "7 jours"),
        rows=rows,
    )


@app.route("/social/<int:item_id>/keep", methods=["POST"])
def social_keep(item_id: int):
    now = datetime.now(timezone.utc).isoformat()
    conn = _conn()
    try:
        affected = conn.execute(
            "UPDATE social_queue SET status='kept', updated_at=? WHERE id=?",
            (now, item_id),
        ).rowcount
        conn.commit()
    finally:
        conn.close()
    if not affected:
        abort(404)
    return redirect(url_for("social"))


@app.route("/social/<int:item_id>/delete", methods=["POST"])
def social_delete(item_id: int):
    now = datetime.now(timezone.utc).isoformat()
    conn = _conn()
    try:
        affected = conn.execute(
            "UPDATE social_queue SET status='deleted', updated_at=? WHERE id=?",
            (now, item_id),
        ).rowcount
        conn.commit()
    finally:
        conn.close()
    if not affected:
        abort(404)
    return redirect(url_for("social"))


@app.route("/promos/<int:item_id>/keep", methods=["POST"])
def promo_keep(item_id: int):
    now = datetime.now(timezone.utc).isoformat()
    conn = _conn()
    try:
        affected = conn.execute(
            "UPDATE promo_queue SET status='kept', updated_at=? WHERE id=?",
            (now, item_id),
        ).rowcount
        conn.commit()
    finally:
        conn.close()
    if not affected:
        abort(404)
    return redirect(url_for("promos"))


@app.route("/promos/<int:item_id>/delete", methods=["POST"])
def promo_delete(item_id: int):
    now = datetime.now(timezone.utc).isoformat()
    conn = _conn()
    try:
        affected = conn.execute(
            "UPDATE promo_queue SET status='deleted', updated_at=? WHERE id=?",
            (now, item_id),
        ).rowcount
        conn.commit()
    finally:
        conn.close()
    if not affected:
        abort(404)
    return redirect(url_for("promos"))


@app.route("/review/<path:uid>/<verdict>", methods=["POST"])
def review(uid: str, verdict: str):
    if not CALIBRATE:
        abort(403)
    if verdict not in ("keep", "delete"):
        abort(400)
    now = datetime.now(timezone.utc).isoformat()
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT d.action, e.subject FROM decisions d "
            "LEFT JOIN emails e ON e.uid = d.email_uid "
            "WHERE d.email_uid = ?", (uid,)
        ).fetchone()
        if not row:
            abort(404)
        conn.execute("UPDATE decisions SET reviewed = ? WHERE email_uid = ?", (verdict, uid))
        if verdict == "delete" and row["action"] in ("delete", "review"):
            conn.execute("UPDATE decisions SET delay_hours = 0 WHERE email_uid = ?", (uid,))
        conn.commit()
        if verdict == "keep" and row["action"] == "keep":
            toast = "green"
        elif verdict == "delete" and row["action"] in ("delete", "review"):
            toast = "red"
        else:
            toast = "yellow"
        subject = row["subject"] or ""
    finally:
        conn.close()
    from flask import jsonify
    return jsonify(ok=True, toast=toast, uid=uid, subject=subject)


@app.route("/review/<path:uid>/undo", methods=["POST"])
def review_undo(uid: str):
    if not CALIBRATE:
        abort(403)
    conn = _conn()
    try:
        conn.execute("UPDATE decisions SET reviewed = NULL WHERE email_uid = ?", (uid,))
        conn.commit()
    finally:
        conn.close()
    from flask import jsonify
    return jsonify(ok=True)


_CALIBRATION_TMPL = (
    _BASE.replace("{% block content %}{% endblock %}", """\
<p class="section-title">Désaccords pipeline / humain</p>
<p style="font-size:.8rem;color:#888;margin-top:-.25rem">
  Mails où ta décision diffère de l'action du pipeline — à utiliser pour affiner les règles.
</p>
{% if rows %}
<table>
  <thead><tr>
    <th>Date</th><th>Compte</th><th>Expéditeur</th><th>Objet</th>
    <th>Pipeline</th><th>Humain</th><th>LLM</th><th>Règle</th><th>Raison</th>
  </tr></thead>
  <tbody>
  {% for r in rows %}
  <tr>
    <td style="white-space:nowrap">{{ r.decided_at[:16].replace("T"," ") }}</td>
    <td>{{ r.account }}</td>
    <td style="max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="{{ r.sender }}">{{ r.sender }}</td>
    <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="{{ r.subject }}">{{ r.subject }}</td>
    <td><span class="badge {{ r.action }}">{{ r.action }}</span></td>
    <td><span class="badge {{ r.reviewed }}">{{ r.reviewed }}</span></td>
    <td style="text-align:center">{% if r.rule_id == 'llm' %}🤖{% endif %}</td>
    <td class="rule">{{ r.rule_id or "" }}</td>
    <td class="reason" title="{{ r.reason }}">{{ r.reason }}</td>
  </tr>
  {% endfor %}
  </tbody>
</table>
<p style="font-size:.8rem;color:#888;margin-top:.75rem">
  {{ rows|length }} désaccord(s) — {{ agree_count }} accord(s) enregistré(s)
</p>
{% else %}
<div class="empty">Aucun désaccord — les règles sont bien calibrées.</div>
{% endif %}
""")
)


@app.route("/calibration")
def calibration():
    if not CALIBRATE:
        abort(403)
    conn = _conn()
    try:
        rows = _rows(conn, """
            SELECT d.decided_at, d.email_uid, d.action, d.reviewed,
                   d.rule_id, d.reason, e.account, e.sender, e.subject
            FROM decisions d
            LEFT JOIN emails e ON e.uid = d.email_uid
            WHERE d.reviewed IS NOT NULL
              AND (
                (d.reviewed = 'keep'   AND d.action IN ('delete','review'))
                OR
                (d.reviewed = 'delete' AND d.action = 'keep')
              )
            ORDER BY d.decided_at DESC
        """)
        agree_count = conn.execute("""
            SELECT COUNT(*) FROM decisions
            WHERE reviewed IS NOT NULL
              AND (
                (reviewed = 'keep'   AND action = 'keep')
                OR
                (reviewed = 'delete' AND action IN ('delete','review'))
              )
        """).fetchone()[0]
    finally:
        conn.close()
    return render_template_string(_CALIBRATION_TMPL, rows=rows, agree_count=agree_count)


@app.route("/stats")
def stats():
    conn = _conn()
    try:
        total_emails = conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
        total_delete = conn.execute(
            "SELECT COUNT(*) FROM decisions WHERE action='delete'"
        ).fetchone()[0]
        total_keep = conn.execute(
            "SELECT COUNT(*) FROM decisions WHERE action='keep'"
        ).fetchone()[0]
        total_review = conn.execute(
            "SELECT COUNT(*) FROM decisions WHERE action='review'"
        ).fetchone()[0]
        pending_inbox = conn.execute(
            "SELECT COUNT(*) FROM reply_queue WHERE status='pending'"
        ).fetchone()[0]
        by_action = _rows(conn, """
            SELECT d.action, e.account, COUNT(*) AS cnt
            FROM decisions d
            LEFT JOIN emails e ON e.uid = d.email_uid
            GROUP BY d.action, e.account
            ORDER BY cnt DESC
        """)
        top_rules = _rows(conn, """
            SELECT rule_id, COUNT(*) AS cnt
            FROM rule_hits
            GROUP BY rule_id
            ORDER BY cnt DESC
            LIMIT 10
        """)
    finally:
        conn.close()
    return render_template_string(
        _STATS_TMPL,
        total_emails=total_emails,
        total_delete=total_delete,
        total_keep=total_keep,
        total_review=total_review,
        pending_inbox=pending_inbox,
        by_action=by_action,
        top_rules=top_rules,
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="postroom dashboard")
    parser.add_argument("--port", type=int, default=5002)
    parser.add_argument("--db", type=Path, default=ROOT / "postroom.db")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    DB_PATH = args.db
    app.run(host="0.0.0.0", port=args.port, debug=False)
