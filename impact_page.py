"""
impact_page.py — Page 5 : Analyse d'impact d'un changement.

« Si on remplace ce composant, qu'est-ce qui est affecté ? »
  1. l'utilisateur choisit une pièce et décrit le changement (nouvelle version, nouveau fournisseur, nouveau coût) ;
  2. un graphe interactif place la pièce au centre et « allume » ce qui est impacté en remontant la structure
     (assemblage parent → sous-ensemble → produit final) ainsi que les fournisseurs concernés ;
  3. un résumé chiffré : assemblages touchés, fournisseurs, écart de coût, conflits de maturité ;
  4. un bouton exporte le rapport d'impact (HTML imprimable en PDF).

Utilisation dans une app Streamlit :
    import impact_page
    impact_page.render(m, comp, MAT)          # m = pc.load_model(), comp = pc.components(m)
Ou en autonome :  streamlit run impact_page.py
"""
from __future__ import annotations

import datetime as dt
import html

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import plm_core as pc
import leo
import shared as sh

NAVY = sh.INK          # couleur institutionnelle
C_PART = sh.AMBER       # pièce modifiée
C_HIT = sh.PRIMARY      # impacté
C_SUP = sh.TEAL         # fournisseur concerné
C_CTX = "#CBD5E1"       # contexte non impacté
C_EDGE = "#94A3B8"
RELEASED_STATES = {"RELEASED", "FROZEN", "SHARED"}
MATURITIES = ["IN_WORK", "FROZEN", "RELEASED"]


def _short(name: str) -> str:
    return name.replace("ECAR-EI_", "").replace("ECAR-3D_", "").strip()


def fr(v: float, dec: int = 2, unit: str = "", sign: bool = False) -> str:
    """Nombre au format français : 1 234,56 ; signe moins typographique."""
    txt = f"{abs(v):,.{dec}f}".replace(",", "\u202f").replace(".", ",")
    pre = "−" if v < 0 else ("+" if sign and v > 0 else "")
    return f"{pre}{txt}{(' ' + unit) if unit else ''}"


def fr_money(v: float, sign: bool = True) -> str:
    """Montant lisible : € sous 10 k€, puis k€, puis M€."""
    a = abs(v)
    if a >= 1e6:
        return fr(v / 1e6, 2, "M€", sign)
    if a >= 1e4:
        return fr(v / 1e3, 1, "k€", sign)
    return fr(v, 2, "€", sign)


def _next_rev(rev: str) -> str:
    return chr(ord(rev[-1]) + 1) if rev and rev[-1].isalpha() and rev[-1] < "Z" else rev + "+"


# --------------------------------------------------------------------------- #
# Calcul de l'impact
# --------------------------------------------------------------------------- #
def compute(m: pc.PLMModel, comp: pd.DataFrame, pid: str, change: dict, mat: pd.DataFrame) -> dict:
    """change = {version: bool, new_rev, new_mass, new_material, new_maturity,
                 supplier: bool, new_supplier, cost: bool, new_unit_cost}"""
    P = m.products
    ci = comp.set_index("xml_id")
    cur = ci.loc[pid]
    base = pc.change_impact(
        m, comp, pid,
        new_material=change["new_material"] if change["version"] else None,
        new_mass=change["new_mass"] if change["version"] else None,
        new_supplier=change["new_supplier"] if change["supplier"] else None, mat=mat)

    # coût : devis saisi > recalcul par la masse / matière (référence recalculée de la même façon,
    # pour qu'un changement nul donne un écart nul)
    old_unit = pc.change_impact(m, comp, pid, mat=mat)["new_unit_cost"]
    if change["cost"]:
        new_unit = change["new_unit_cost"]
    elif change["version"]:
        new_unit = base["new_unit_cost"]
    else:
        new_unit = old_unit
    delta_unit = new_unit - old_unit if not change["cost"] else new_unit - cur.unit_cost_eur
    delta_total = delta_unit * cur.qty

    # assemblages impactés, du plus proche au plus lointain
    levels = {}
    for path in base["paths"]:
        for d, node in enumerate(reversed(path)):
            if d and (node not in levels or d < levels[node]):
                levels[node] = d
    assemblies = sorted(levels, key=lambda x: levels[x])

    # fournisseurs concernés
    sups = [dict(role="Actuel" if not change["supplier"] or change["new_supplier"] == cur.supplier else "Sortant",
                 supplier=cur.supplier, country=cur.country)]
    if change["supplier"] and change["new_supplier"] != cur.supplier:
        ns = pc.SUPPLIERS.set_index("supplier").loc[change["new_supplier"]]
        sups.append(dict(role="Entrant", supplier=change["new_supplier"], country=ns.country,
                         lead_time_wk=ns.lead_time_wk, qualified_sources=ns.qualified_sources))

    # conflits de maturité
    new_mat_state = change["new_maturity"] if change["version"] else cur.maturity
    conflicts = []
    for a in assemblies:
        am = P.loc[a, "maturity"]
        if am in RELEASED_STATES and new_mat_state not in RELEASED_STATES:
            conflicts.append(dict(level="Bloquant", object=a, name=P.loc[a, "name"],
                                  text=f"Une pièce {new_mat_state} entrerait dans {_short(P.loc[a, 'name'])}, "
                                       f"déjà {am} : l'assemblage doit repasser en révision "
                                       f"({P.loc[a, 'revision']} → {_next_rev(P.loc[a, 'revision'])})."))
    if cur.maturity in RELEASED_STATES and (change["version"] or change["supplier"]):
        conflicts.append(dict(level="Attention", object=pid, name=cur["name"],
                              text=f"La pièce est {cur.maturity} : la modifier impose une nouvelle révision "
                                   f"et une validation formelle (ECR/ECO)."))
    if change["supplier"] and change["new_supplier"] != cur.supplier:
        conflicts.append(dict(level="Attention", object=pid, name=cur["name"],
                              text=f"Changement de fournisseur : homologation (PPAP) à prévoir, "
                                   f"délai {sups[-1]['lead_time_wk']} semaines."))
    if not conflicts:
        txt = ("Aucun conflit : la pièce et ses assemblages parents sont tous IN_WORK, "
               "le changement peut entrer dans la révision en cours.")
        conflicts.append(dict(level="OK", object="", name="", text=txt))

    return dict(base=base, cur=cur, new_unit=new_unit, delta_unit=delta_unit, delta_total=delta_total,
                levels=levels, assemblies=assemblies, suppliers=sups, conflicts=conflicts,
                new_maturity=new_mat_state,
                delta_mass=(change["new_mass"] - cur.mass_kg) * cur.qty if change["version"] else 0.0,
                delta_co2=base["delta_co2"] if change["version"] else 0.0)


# --------------------------------------------------------------------------- #
# Graphe : pièce au centre, la structure s'allume en remontant
# --------------------------------------------------------------------------- #
def _context_children(m, parent, keep, limit=12):
    """Enfants (références EI / assemblages) d'un parent, sans les pièces 3D portées."""
    P = m.products
    seen = []
    for c, _ in m.children.get(parent, []):
        if c in seen:
            continue
        if P.loc[c, "node_type"] != "Pièce 3D" or P.loc[parent, "node_type"] == "Assemblage":
            seen.append(c)
    others = [c for c in seen if c != keep]
    return others[:limit], max(0, len(others) - limit)


def _slot(k: int) -> float:
    """Position verticale du k-ième voisin : alterné haut/bas, avec une marge autour de la ligne d'impact."""
    return (1 if k % 2 == 0 else -1) * (1.3 + 0.5 * (k // 2))


def build_graph(m: pc.PLMModel, pid: str, imp: dict, animate: bool = True) -> go.Figure:
    P = m.products
    path = max(imp["base"]["paths"], key=len)          # racine → ... → pièce
    chain = list(reversed(path))                        # pièce, parent, ..., racine
    nodes = {}                                          # id -> dict(x, y, kind, level, label, hover)
    edges = []                                          # (a, b, kind, level)

    def hov(i):
        r = P.loc[i]
        return (f"<b>{r['name']}</b><br>{i} · {r.plm_id}<br>Rév. {r.revision} · {r.maturity}"
                f"<br>{r.mass_kg} kg{(' · ' + r.material) if r.material else ''}")

    # colonnes : x = niveau ; la pièce au centre (x=0), la structure monte vers la droite
    for lvl, node in enumerate(chain):
        nodes[node] = dict(x=lvl * 1.6, y=0, kind="part" if lvl == 0 else "hit", level=lvl,
                           label=_short(P.loc[node, "name"]) + f" ({P.loc[node, 'revision']})", hover=hov(node))
        if lvl:
            edges.append((chain[lvl - 1], node, "hit", lvl))
        # contexte : frères non impactés dans la même colonne
        if lvl + 1 < len(chain):
            sibs, more = _context_children(m, chain[lvl + 1], node)
            for k, s in enumerate(sibs):
                if s in nodes:
                    continue
                nodes[s] = dict(x=lvl * 1.6, y=_slot(k), kind="ctx", level=99,
                                label=_short(P.loc[s, "name"]), hover=hov(s) + "<br><i>non impacté</i>")
                edges.append((s, chain[lvl + 1], "ctx", 99))
            if more:
                key = f"more_{lvl}"
                nodes[key] = dict(x=lvl * 1.6, y=_slot(len(sibs)), kind="ctx", level=99,
                                  label=f"+{more} autres", hover=f"{more} autres composants non impactés")

    # fournisseurs à gauche de la pièce
    sups = imp["suppliers"]
    for k, s in enumerate(sups):
        key = f"sup_{k}"
        y = 0 if len(sups) == 1 else (0.9 if k == 0 else -0.9)
        role = s["role"]
        nodes[key] = dict(x=-1.6, y=y, kind="sup", level=1,
                          label=f"{s['supplier']} ({role.lower()})",
                          hover=f"<b>{s['supplier']}</b> · {s['country']}<br>Fournisseur {role.lower()}*")
        edges.append((key, chain[0], "sup_out" if role == "Sortant" else "sup", 1))

    n_lvl = len(chain)
    frames_lvls = list(range(n_lvl)) if animate else [n_lvl - 1]

    def traces(upto):
        def seg(kinds, cond):
            xs, ys = [], []
            for a, b, kind, lv in edges:
                if kind in kinds and cond(lv):
                    xs += [nodes[a]["x"], nodes[b]["x"], None]
                    ys += [nodes[a]["y"], nodes[b]["y"], None]
            return xs, ys

        out = []
        x, y = seg({"ctx"}, lambda lv: True)
        out.append(go.Scatter(x=x, y=y, mode="lines", line=dict(color="#E2E8F0", width=1),
                              hoverinfo="skip", showlegend=False))
        x, y = seg({"hit"}, lambda lv: True)  # chemin éteint
        out.append(go.Scatter(x=x, y=y, mode="lines", line=dict(color="#E2E8F0", width=3),
                              hoverinfo="skip", showlegend=False))
        x, y = seg({"hit"}, lambda lv: lv <= upto)
        out.append(go.Scatter(x=x, y=y, mode="lines", line=dict(color=C_HIT, width=4),
                              hoverinfo="skip", showlegend=False))
        x, y = seg({"sup"}, lambda lv: lv <= upto + 1)
        out.append(go.Scatter(x=x, y=y, mode="lines", line=dict(color=C_SUP, width=3),
                              hoverinfo="skip", showlegend=False))
        x, y = seg({"sup_out"}, lambda lv: lv <= upto + 1)
        out.append(go.Scatter(x=x, y=y, mode="lines", line=dict(color=C_SUP, width=2, dash="dot"),
                              hoverinfo="skip", showlegend=False))

        def node_trace(sel, name, color, size, symbol, textpos="bottom center", bold=False):
            ids = [k for k, v in nodes.items() if sel(v)]
            pos = [("top center" if nodes[i]["kind"] == "hit" and nodes[i]["level"] % 2 == 0 else textpos)
                   for i in ids]
            return go.Scatter(
                x=[nodes[i]["x"] for i in ids], y=[nodes[i]["y"] for i in ids], mode="markers+text",
                name=name, text=[nodes[i]["label"] for i in ids], textposition=pos,
                textfont=dict(size=12 if bold else 10, color="#0F172A" if bold else "#64748B"),
                hovertext=[nodes[i]["hover"] for i in ids], hoverinfo="text",
                marker=dict(size=size, color=color, symbol=symbol, line=dict(color="white", width=2)))

        out.append(node_trace(lambda v: v["kind"] == "ctx", "Non impacté", C_CTX, 12, "circle", "middle right"))
        out.append(node_trace(lambda v: v["kind"] == "hit" and v["level"] > upto, "Impacté (en attente)",
                              "#E2E8F0", 22, "circle", bold=True))  # libellés sous les nœuds du chemin
        out.append(node_trace(lambda v: v["kind"] == "hit" and v["level"] <= upto, "Assemblage impacté",
                              C_HIT, 26, "circle", bold=True))
        out.append(node_trace(lambda v: v["kind"] == "sup" and upto >= 0, "Fournisseur concerné*",
                              C_SUP, 24, "square", bold=True))
        out.append(node_trace(lambda v: v["kind"] == "part", "Pièce modifiée", C_PART, 34, "star", bold=True))
        return out

    fig = go.Figure(data=traces(frames_lvls[-1]))
    if animate:
        fig.frames = [go.Frame(data=traces(k), name=str(k)) for k in frames_lvls]
        fig.update_layout(updatemenus=[dict(
            type="buttons", showactive=False, x=0, y=1.13, xanchor="left",
            bgcolor=NAVY, bordercolor=NAVY, font=dict(color="#FFFFFF", size=12),
            buttons=[dict(label="Propager l'impact  ▸", method="animate",
                          args=[None, dict(frame=dict(duration=900, redraw=True), fromcurrent=False,
                                           transition=dict(duration=300), mode="immediate")])])])
    ys = [v["y"] for v in nodes.values()]
    fig.update_layout(
        height=max(460, 140 + 70 * (max(ys) - min(ys))),
        plot_bgcolor="white", paper_bgcolor="white", margin=dict(t=70, l=10, r=10, b=10),
        font=dict(color="#0F172A"),
        xaxis=dict(visible=False, range=[-2.6, (n_lvl - 1) * 1.6 + 1.4]),
        yaxis=dict(visible=False, range=[min(ys) - 0.6, max(ys) + 0.6]),
        legend=dict(orientation="h", y=1.08, x=1, xanchor="right", font=dict(size=11, color="#0F172A")),
        hoverlabel=dict(bgcolor="white"))
    for lvl, lab in enumerate(["Pièce", "Assemblage parent", "Sous-ensemble", "Produit final", "Niveau 4"][:n_lvl]):
        fig.add_annotation(x=lvl * 1.6, y=max(ys) + 0.5, text=f"<b>{lab}</b>", showarrow=False,
                           font=dict(size=11, color="#475569"))
    fig.add_annotation(x=-1.6, y=max(ys) + 0.5, text="<b>Fournisseurs</b>", showarrow=False,
                       font=dict(size=11, color="#475569"))
    return fig


# --------------------------------------------------------------------------- #
# Rapport d'impact (HTML autonome, imprimable en PDF)
# --------------------------------------------------------------------------- #
def report_html(m, pid, change, imp, fig, volume) -> str:
    P = m.products
    cur = imp["cur"]
    e = html.escape
    now = dt.datetime.now().strftime("%d/%m/%Y %H:%M")
    rows_change = []
    if change["version"]:
        rows_change += [("Révision", cur.revision, change["new_rev"]),
                        ("Masse unitaire", fr(cur.mass_kg, 3, "kg"), fr(change["new_mass"], 3, "kg")),
                        ("Matériau", cur.material, change["new_material"]),
                        ("Maturité", cur.maturity, change["new_maturity"])]
    if change["supplier"]:
        rows_change.append(("Fournisseur", cur.supplier, change["new_supplier"]))
    rows_change.append(("Coût unitaire*", fr(cur.unit_cost_eur, 2, "€"), fr(imp["new_unit"], 2, "€")
                        + (" (devis)" if change["cost"] else " (recalculé)")))
    tr = "".join(f"<tr><td>{e(a)}</td><td>{e(str(b))}</td><td><b>{e(str(c))}</b></td></tr>" for a, b, c in rows_change)
    asm = "".join(
        f"<tr><td>{imp['levels'][a]}</td><td>{e(P.loc[a, 'name'])}</td><td>{a}</td><td>{P.loc[a, 'plm_id']}</td>"
        f"<td>{P.loc[a, 'revision']}</td><td>{P.loc[a, 'maturity']}</td></tr>" for a in imp["assemblies"])
    sup = "".join(f"<tr><td>{e(s['role'])}</td><td>{e(s['supplier'])}</td><td>{e(s['country'])}</td></tr>"
                  for s in imp["suppliers"])
    conf = "".join(f"<li><b>{e(c['level'])}</b> — {e(c['text'])}</li>" for c in imp["conflicts"])
    fig_html = fig.to_html(full_html=False, include_plotlyjs="cdn", config={"displayModeBar": False})
    return f"""<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8">
<title>Rapport d'impact — {e(cur['name'])}</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600&display=swap">
<style>
body{{font-family:'IBM Plex Sans',Segoe UI,Arial,sans-serif;color:#0F172A;max-width:1000px;margin:32px auto;padding:0 24px}}
.banner{{background:#0B2545;color:#fff;padding:20px 26px;border-radius:6px;margin-bottom:8px}}
.banner .eyebrow{{font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:#9DB4D6}}
.banner h1{{color:#fff;margin:4px 0 6px 0}} .banner .meta{{color:#C9D6EA}}
h1{{font-size:24px;font-weight:600;margin-bottom:4px}}
h2{{font-size:15px;font-weight:600;color:#0B2545;text-transform:uppercase;letter-spacing:.06em;margin-top:28px;border-bottom:1px solid #D5DBE5;padding-bottom:6px}}
.k{{border-left:4px solid #0B2545 !important}}
.meta{{color:#64748B;font-size:13px}} table{{border-collapse:collapse;width:100%;font-size:14px}}
td,th{{border-bottom:1px solid #E2E8F0;padding:6px 8px;text-align:left}} th{{background:#F8FAFC}}
.kpis{{display:flex;gap:12px;margin:16px 0}} .k{{flex:1;border:1px solid #E2E8F0;border-radius:10px;padding:12px}}
.k b{{display:block;font-size:22px}} .k span{{color:#64748B;font-size:12px}} .note{{color:#64748B;font-size:12px}}
@media print{{.noprint{{display:none}}}}
</style></head><body>
<p class="noprint note">Pour obtenir un PDF : Fichier › Imprimer › Enregistrer en PDF.</p>
<div class="banner"><div class="eyebrow">PLM Intelligence · Rapport d'impact</div>
<h1>{e(cur['name'])} ({pid})</h1>
<div class="meta">Généré le {now} · Pièce : ID PLM {cur.plm_id}, rév. {cur.revision}, quantité {int(cur.qty)} par produit
· Produit final : {e(P.loc[m.root, 'name'])} rév. {P.loc[m.root, 'revision']}</div></div>
<h2>Changement envisagé</h2>
<table><tr><th>Attribut</th><th>Avant</th><th>Après</th></tr>{tr}</table>
<p><b>Motif :</b> {e(change.get('reason') or 'non renseigné')}</p>
<h2>Résumé</h2>
<div class="kpis">
<div class="k"><b>{len(imp['assemblies'])}</b><span>assemblages touchés</span></div>
<div class="k"><b>{len(imp['suppliers'])}</b><span>fournisseur(s) concerné(s)</span></div>
<div class="k"><b>{fr_money(imp['delta_total'])}</b><span>écart de coût par produit*</span></div>
<div class="k"><b>{fr_money(imp['delta_total'] * volume)}</b><span>par an à {fr(volume, 0)} unités*</span></div>
<div class="k"><b>{fr(imp['delta_mass'], 2, 'kg', True)}</b><span>écart de masse par produit</span></div>
</div>
<h2>Propagation dans la structure</h2>{fig_html}
<table><tr><th>Niveau</th><th>Assemblage</th><th>ID XML</th><th>ID PLM</th><th>Rév.</th><th>Maturité</th></tr>{asm}</table>
<h2>Fournisseurs concernés</h2><table><tr><th>Rôle</th><th>Fournisseur*</th><th>Pays</th></tr>{sup}</table>
<h2>Conflits de maturité et points de vigilance</h2><ul>{conf}</ul>
<h2>Sources</h2><p class="note">Données de structure, révisions, maturités et masses : export XPDMXML 3DEXPERIENCE
(objets {pid}, {', '.join(imp['assemblies'])}). * Coûts et fournisseurs : hypothèses simulées
(référentiel matières et fournisseurs de l'outil), à remplacer par les données achats.</p>
</body></html>"""


# --------------------------------------------------------------------------- #
# Liens avec les autres pages
# --------------------------------------------------------------------------- #
SESSION_KEY = "piece_a_simuler"


def goto_impact(pid: str):
    """Ouvre la page Impact sur cette pièce (pièce active partagée)."""
    sh.goto("impact", pid)


def preselected_part(ids: list[str]) -> str:
    """Pièce présélectionnée : ?piece=ID_xx dans l'URL, sinon session (page 3), sinon le noyau stator."""
    m = sh.ctx()["m"]
    cur = st.session_state.get(sh.SEL)
    for cand in (st.query_params.get("piece"), sh.ei_of(m, cur) if cur else None, "ID_13"):
        if cand in ids:
            return cand
    return ids[0]


# --------------------------------------------------------------------------- #
# Interface
# --------------------------------------------------------------------------- #
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&display=swap');
.ia-banner {background: #0B2545; color: #FFFFFF; border-radius: 6px; padding: 22px 28px; margin-bottom: 22px;}
.ia-banner .eyebrow {font-size: 0.72rem; letter-spacing: 0.14em; text-transform: uppercase; color: #9DB4D6;}
.ia-banner h1 {font-size: 1.65rem; font-weight: 600; margin: 4px 0 6px 0; color: #FFFFFF; padding: 0;}
.ia-banner .meta {font-size: 0.85rem; color: #C9D6EA;}
.ia-section {display: flex; align-items: baseline; gap: 12px; border-bottom: 1px solid #D5DBE5;
  margin: 26px 0 14px 0; padding-bottom: 6px;}
.ia-section .num {font-size: 0.8rem; font-weight: 600; color: #FFFFFF; background: #2453D6;
  border-radius: 999px; padding: 2px 9px;}
.ia-section .title {font-size: 1.12rem; font-weight: 600; color: #0B2545;}
.ia-section .sub {font-size: 0.82rem; color: #64748B; margin-left: auto;}
.ia-current {background: #F4F6FA; border: 1px solid #E1E6EF; border-radius: 6px; padding: 12px 16px;
  font-size: 0.86rem; color: #334155; line-height: 1.6;}
.ia-current b {color: #0B2545;}
.ia-kpis {display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; margin: 4px 0 8px 0;}
.ia-kpi {background: #FFFFFF; border: 1px solid #E1E6EF; border-left: 4px solid #2453D6; border-radius: 12px;
  padding: 12px 14px;}
.ia-kpi .lab {font-size: 0.74rem; text-transform: uppercase; letter-spacing: 0.06em; color: #64748B;}
.ia-kpi .val {font-size: 1.5rem; font-weight: 600; color: #0B2545; margin-top: 2px;}
.ia-kpi .hint {font-size: 0.75rem; color: #64748B;}
.ia-kpi.good {border-left-color: #15803D;} .ia-kpi.good .val {color: #15803D;}
.ia-kpi.bad {border-left-color: #B91C1C;} .ia-kpi.bad .val {color: #B91C1C;}
.ia-status {border: 1px solid #E1E6EF; border-left: 4px solid; border-radius: 6px; padding: 10px 14px;
  margin-bottom: 8px; font-size: 0.88rem; color: #1E293B; background: #FFFFFF;}
.ia-status .tag {font-size: 0.7rem; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase;
  display: block; margin-bottom: 2px;}
.ia-status.Bloquant {border-left-color: #B91C1C;} .ia-status.Bloquant .tag {color: #B91C1C;}
.ia-status.Attention {border-left-color: #B45309;} .ia-status.Attention .tag {color: #B45309;}
.ia-status.OK {border-left-color: #15803D;} .ia-status.OK .tag {color: #15803D;}
.ia-src {display: inline-block; font-size: 0.72rem; color: #475569; border: 1px solid #D5DBE5;
  border-radius: 3px; padding: 1px 8px; margin: 2px 0 6px 0; background: #F8FAFC;}
.ia-label {font-size: 0.78rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em;
  color: #0B2545; margin: 6px 0 6px 0;}
.ia-note {font-size: 0.78rem; color: #64748B;}
div[data-testid="stCheckbox"] label p {font-weight: 600; color: #0B2545;}
.stDownloadButton button {background: #2453D6 !important; color: #FFFFFF !important; border: 0 !important;
  padding: 0.55rem 1.4rem !important; font-weight: 500 !important;}
</style>
"""


def _css():
    st.markdown(CSS, unsafe_allow_html=True)


def _section(num: int, title: str, sub: str = ""):
    st.markdown(f"<div class='ia-section'><span class='num'>{num:02d}</span><span class='title'>{title}</span>"
                f"<span class='sub'>{html.escape(sub)}</span></div>", unsafe_allow_html=True)


def _label(text: str):
    st.markdown(f"<div class='ia-label'>{html.escape(text)}</div>", unsafe_allow_html=True)


def source_tag(text: str):
    st.markdown(f"<span class='ia-src'>Source : {html.escape(text)}</span>", unsafe_allow_html=True)


def _kpi(label, value, hint="", tone=""):
    return (f"<div class='ia-kpi {tone}'><div class='lab'>{label}</div><div class='val'>{value}</div>"
            f"<div class='hint'>{hint}</div></div>")


def render(m: pc.PLMModel, comp: pd.DataFrame, mat: pd.DataFrame | None = None, show_header: bool = True):
    mat = pc.MATERIALS if mat is None else mat
    P = m.products
    ci = comp.set_index("xml_id")
    root = P.loc[m.root]
    _css()

    if show_header:
        sh.hero("Évaluer · Analyse d'impact", "Que change le remplacement d'une pièce ?",
                f"{sh.short(root['name'])} · rév. {root.revision} · {root.maturity} · export 3DEXPERIENCE du "
                f"{root.created[:10]} · {len(P)} objets PLM")

    # 01 — définition du changement ---------------------------------------
    _section(1, "Définir le changement", "Pièce concernée et nature de la modification")
    ids = list(comp.sort_values("display_name").xml_id)
    pre = preselected_part(ids)
    c0, c1 = st.columns([1.3, 1])
    # pièce active partagée -> sélecteur ; seul un choix de l'utilisateur modifie la pièce active
    cur_sel = st.session_state.get(sh.SEL)
    if st.session_state.get("_seen_imp") != cur_sel or st.session_state.get("imp_pid") not in ids:
        st.session_state["imp_pid"] = pre
        st.session_state["_imp_pushed"] = pre
    with c0:
        pid = st.selectbox("Pièce à modifier", ids,
                           format_func=lambda i: f"{ci.loc[i, 'display_name']}  ·  {i}  ·  {ci.loc[i, 'supplier']}",
                           key="imp_pid")
    if pid != st.session_state.get("_imp_pushed"):
        sh.select(pid)
        st.session_state["_imp_pushed"] = pid
    st.session_state["_seen_imp"] = st.session_state.get(sh.SEL)
    cur = ci.loc[pid]
    with c1:
        st.markdown(
            f"<div class='ia-current'><b>Configuration actuelle</b><br>"
            f"Révision {cur.revision} · {cur.maturity} · {fr(cur.mass_kg, 3, 'kg')} · quantité {int(cur.qty)}<br>"
            f"{html.escape(cur.material)}<br>"
            f"Fournisseur : {html.escape(cur.supplier)} · coût unitaire {fr(cur.unit_cost_eur, 2, '€')}*</div>",
            unsafe_allow_html=True)
        source_tag(f"XML Product {pid} ({cur.plm_id}) · fournisseur et coût : hypothèses")

    t1, t2, t3 = st.columns(3, gap="large")
    with t1:
        v_on = st.checkbox("Nouvelle version", value=True, key=f"imp_v_{pid}")
        mats = list(mat.index)
        new_rev = st.text_input("Révision", _next_rev(cur.revision), disabled=not v_on, key=f"imp_rev_{pid}")
        new_mass = st.number_input("Masse unitaire (kg)", 0.0, value=float(cur.mass_kg), step=0.05,
                                   format="%.3f", disabled=not v_on, key=f"imp_mass_{pid}")
        new_material = st.selectbox("Matériau", mats, disabled=not v_on, key=f"imp_mat_{pid}",
                                    index=mats.index(cur.material) if cur.material in mats else 0)
        new_maturity = st.selectbox("Maturité de la nouvelle version", MATURITIES, disabled=not v_on,
                                    key=f"imp_matu_{pid}")
    with t2:
        s_on = st.checkbox("Nouveau fournisseur", key=f"imp_s_{pid}")
        sups = list(pc.SUPPLIERS.supplier)
        new_sup = st.selectbox("Fournisseur", sups, disabled=not s_on, key=f"imp_sup_{pid}",
                               index=sups.index(cur.supplier) if cur.supplier in sups else 0)
        reason = st.text_area("Motif du changement", placeholder="Ex. : réduction de coût et de masse, "
                              "seconde source, exposition au cours du cuivre", height=120, key=f"imp_reason_{pid}")
    with t3:
        k_on = st.checkbox("Nouveau coût (devis)", key=f"imp_k_{pid}")
        new_cost = st.number_input("Coût unitaire (€)", 0.0, value=float(cur.unit_cost_eur), step=1.0,
                                   format="%.2f", disabled=not k_on, key=f"imp_cost_{pid}")
        volume = st.number_input("Volume annuel (produits)", 1000, 1_000_000, 50_000, 5000, key="imp_vol")
        st.markdown("<div class='ia-note'>Sans devis, le coût est recalculé à partir de la masse et du "
                    "matériau (hypothèses de prix).</div>", unsafe_allow_html=True)

    change = dict(version=v_on, new_rev=new_rev, new_mass=new_mass, new_material=new_material,
                  new_maturity=new_maturity, supplier=s_on, new_supplier=new_sup,
                  cost=k_on, new_unit_cost=new_cost, reason=reason)
    imp = compute(m, comp, pid, change, mat)

    # 02 — propagation ------------------------------------------------------
    _section(2, "Propagation dans la structure", "Lancer l'animation pour remonter de la pièce au produit final")
    fig = build_graph(m, pid, imp, animate=True)
    st.plotly_chart(fig, width="stretch", key=f"imp_graph_{pid}",
                    config={"displaylogo": False, "modeBarButtonsToRemove": ["select2d", "lasso2d"]})
    source_tag("XML ProductInst (Owned → Instancing), remontée jusqu'à la racine " + m.root
               + " · fournisseurs : hypothèses")

    leo.set_page_context("impact", (
        f"Pièce modifiée : {cur.display_name} ; changements : "
        + ", ".join(k for k, on in (("nouvelle version " + str(new_rev), v_on), ("fournisseur " + new_sup, s_on),
                                    ("coût devis", k_on)) if on)
        + f"\nAssemblages touchés : {', '.join(_short(P.loc[x, 'name']) for x in imp['assemblies'])}"
        + f"\nÉcart de coût par produit* : {imp['delta_total']:.2f} € ; écart de masse : {imp['delta_mass']:+.3f} kg"
        + "\nConflits : " + "; ".join(f"[{c['level']}] {c['text']}" for c in imp["conflicts"])))

    # 03 — synthèse ---------------------------------------------------------
    _section(3, "Synthèse de l'impact", f"Volume de référence : {fr(volume, 0)} produits par an")
    tone = lambda v: "good" if v < -1e-9 else ("bad" if v > 1e-9 else "")
    n_block = sum(c["level"] == "Bloquant" for c in imp["conflicts"])
    st.markdown("<div class='ia-kpis'>"
                + _kpi("Assemblages touchés", len(imp["assemblies"]), "à réviser jusqu'au produit final")
                + _kpi("Fournisseurs concernés", len(imp["suppliers"]), "actuel et entrant")
                + _kpi("Écart de coût / produit", fr_money(imp["delta_total"]), "estimation*", tone(imp["delta_total"]))
                + _kpi("Écart de coût / an", fr_money(imp["delta_total"] * volume), "estimation*",
                       tone(imp["delta_total"]))
                + _kpi("Conflits bloquants", n_block, f"écart de masse {fr(imp['delta_mass'], 2, 'kg', True)}",
                       "bad" if n_block else "good")
                + "</div>", unsafe_allow_html=True)
    source_tag("Assemblages, maturités et masses : XML (" + ", ".join(imp["assemblies"]) + ") · coût : "
               + ("devis saisi" if k_on else "masse × prix matière × facteur de transformation, hypothèses"))

    a, b = st.columns([1.25, 1], gap="large")
    with a:
        _label("Assemblages à réviser")
        st.dataframe(pd.DataFrame([dict(Niveau=imp["levels"][x], Assemblage=P.loc[x, "name"], **{"ID XML": x},
                                        **{"ID PLM": P.loc[x, "plm_id"]}, Rév=P.loc[x, "revision"],
                                        Maturité=P.loc[x, "maturity"]) for x in imp["assemblies"]]),
                     hide_index=True, width="stretch")
        _label("Fournisseurs concernés*")
        st.dataframe(pd.DataFrame(imp["suppliers"]).rename(columns={
            "role": "Rôle", "supplier": "Fournisseur", "country": "Pays", "lead_time_wk": "Délai (sem.)",
            "qualified_sources": "Sources qualifiées"}), hide_index=True, width="stretch")
    with b:
        _label("Maturité et points de vigilance")
        tags = {"Bloquant": "Conflit bloquant", "Attention": "Point de vigilance", "OK": "Conforme"}
        for c in imp["conflicts"]:
            st.markdown(f"<div class='ia-status {c['level']}'><span class='tag'>{tags[c['level']]}</span>"
                        f"{html.escape(c['text'])}</div>", unsafe_allow_html=True)
        same = imp["base"]["same_supplier"]
        if len(same):
            _label(f"Autres pièces chez {cur.supplier}")
            st.markdown("<div class='ia-note'>" + html.escape(", ".join(
                f"{r.display_name} ({r.xml_id})" for _, r in same.iterrows())) + "</div>", unsafe_allow_html=True)

    # 04 — export -----------------------------------------------------------
    _section(4, "Rapport d'impact", "Document de décision à transmettre")
    rep_fig = build_graph(m, pid, imp, animate=False)
    rep_fig.update_layout(template="plotly_white")
    rep = report_html(m, pid, change, imp, rep_fig, volume)
    e1, e2 = st.columns([1, 3])
    e1.download_button("Exporter le rapport", rep.encode("utf-8"),
                       file_name=f"rapport_impact_{pid}_{dt.date.today():%Y%m%d}.html", mime="text/html")
    e2.markdown("<div class='ia-note' style='padding-top:10px'>Rapport HTML autonome, graphe inclus. "
                "Ouvrir dans un navigateur puis Imprimer › Enregistrer en PDF. "
                "* Coûts et fournisseurs : hypothèses simulées, à remplacer par les données achats.</div>",
                unsafe_allow_html=True)
    return imp


if __name__ == "__main__":
    st.set_page_config(page_title="Analyse d'impact · PLM Intelligence", layout="wide")
    _m = pc.load_model()
    render(_m, pc.components(_m))
