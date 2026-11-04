"""Graphes 3D communs : structure produit en orbite, nuage de risques, surface de sensibilité."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import plotly.graph_objects as go

import plm_core as pc
import shared as sh


def _orbit(fig: go.Figure, n: int = 60, radius: float = 1.9, z: float = 0.85, duration: int = 90) -> go.Figure:
    """Ajoute une rotation automatique de la caméra (boutons Tourner / Pause)."""
    eyes = [dict(x=radius * math.cos(2 * math.pi * k / n), y=radius * math.sin(2 * math.pi * k / n), z=z)
            for k in range(n)]
    fig.frames = [go.Frame(layout=dict(scene=dict(camera=dict(eye=e))), name=str(k)) for k, e in enumerate(eyes)]
    btn = dict(type="buttons", direction="left", x=0.01, y=0.99, xanchor="left", yanchor="top", pad=dict(r=6, t=4),
               bgcolor="#FFFFFF", bordercolor="#DFE5EF", font=dict(size=12, color=sh.INK), showactive=False,
               buttons=[dict(label="Tourner", method="animate",
                             args=[None, dict(frame=dict(duration=duration, redraw=True), transition=dict(duration=0),
                                              fromcurrent=True, mode="immediate", loop=True)]),
                        dict(label="Pause", method="animate",
                             args=[[None], dict(frame=dict(duration=0, redraw=False), mode="immediate")])])
    fig.update_layout(updatemenus=[btn], scene_camera=dict(eye=eyes[0]))
    return fig


# --------------------------------------------------------------------------- #
# Structure produit en 3D
# --------------------------------------------------------------------------- #
def bom_3d(m: pc.PLMModel, include_3d: bool = False, highlight: str | None = None, height: int = 560) -> go.Figure:
    P = m.products
    nodes: list[dict] = []

    def keep(x):
        return include_3d or P.loc[x, "node_type"] != "Pièce 3D"

    def walk(x, level, parent, top):
        uid = len(nodes)
        nodes.append(dict(uid=uid, xid=x, level=level, parent=parent, top=top, kids=[]))
        if parent is not None:
            nodes[parent]["kids"].append(uid)
        for c, _ in m.children.get(x, []):
            if keep(c):
                walk(c, level + 1, uid, top if top else c)

    walk(m.root, 0, None, None)

    def leaves(n):
        n["w"] = 1 if not n["kids"] else sum(leaves(nodes[k]) for k in n["kids"])
        return n["w"]

    leaves(nodes[0])

    def place(n, a0, a1):
        mid = (a0 + a1) / 2
        r = n["level"] * 1.0
        n.update(x=r * math.cos(mid), y=r * math.sin(mid), z=-0.75 * n["level"])
        a = a0
        for k in n["kids"]:
            span = (a1 - a0) * nodes[k]["w"] / n["w"]
            place(nodes[k], a, a + span)
            a += span

    place(nodes[0], 0, 2 * math.pi)

    tops = list(dict.fromkeys(n["top"] for n in nodes if n["top"]))
    color = {t: sh.COLORWAY[i % len(sh.COLORWAY)] for i, t in enumerate(tops)}
    mmax = max(float(P.mass_kg.max() or 1), 1e-6)

    def size(x):
        v = P.loc[x, "mass_kg"]
        return 5 + 30 * math.sqrt(max(float(v or 0), 0) / mmax)

    def hover(x):
        r = P.loc[x]
        mass = f"{r.mass_kg:.3f} kg" if pd.notna(r.mass_kg) else "masse non fournie"
        return (f"<b>{sh.short(r['name'])}</b><br>{r.node_type} · rév. {r.revision} · {r.maturity}<br>{mass}"
                + (f"<br>{r.material}" if r.material else ""))

    fig = go.Figure()
    for t in [None] + tops:
        grp = [n for n in nodes if n["top"] == t]
        if not grp:
            continue
        xs, ys, zs = [], [], []
        for n in grp:
            if n["parent"] is not None:
                p = nodes[n["parent"]]
                xs += [p["x"], n["x"], None]
                ys += [p["y"], n["y"], None]
                zs += [p["z"], n["z"], None]
        col = color.get(t, sh.INK)
        if xs:
            fig.add_trace(go.Scatter3d(x=xs, y=ys, z=zs, mode="lines", line=dict(color=col, width=3),
                                       opacity=0.45, hoverinfo="skip", showlegend=False, legendgroup=str(t)))
        fig.add_trace(go.Scatter3d(
            x=[n["x"] for n in grp], y=[n["y"] for n in grp], z=[n["z"] for n in grp],
            mode="markers+text",
            text=[sh.short(P.loc[n["xid"], "name"]) if n["level"] <= 1 else "" for n in grp],
            textposition="top center", textfont=dict(size=11, color=sh.INK),
            hovertext=[hover(n["xid"]) for n in grp], hoverinfo="text",
            marker=dict(size=[size(n["xid"]) for n in grp], color=col, opacity=0.92,
                        line=dict(color="#FFFFFF", width=1.5)),
            name="Produit" if t is None else sh.short(P.loc[t, "name"]), legendgroup=str(t)))

    if highlight is not None:
        hl = [n for n in nodes if n["xid"] == highlight]
        if hl:
            fig.add_trace(go.Scatter3d(
                x=[n["x"] for n in hl], y=[n["y"] for n in hl], z=[n["z"] for n in hl], mode="markers",
                marker=dict(size=[size(n["xid"]) + 14 for n in hl], color="rgba(0,0,0,0)",
                            line=dict(color=sh.AMBER, width=6)),
                name="Pièce active", hoverinfo="skip"))

    hidden = dict(visible=False, showbackground=False)
    fig.update_layout(height=height, margin=dict(l=0, r=0, t=10, b=0),
                      scene=dict(xaxis=hidden, yaxis=hidden, zaxis=hidden, aspectmode="manual",
                                 aspectratio=dict(x=1, y=1, z=0.75), dragmode="orbit"),
                      legend=dict(orientation="h", y=0.02, x=0.5, xanchor="center"))
    return _orbit(fig)


# --------------------------------------------------------------------------- #
# Nuage de risques : masse × coût × score
# --------------------------------------------------------------------------- #
def risk_3d(comp: pd.DataFrame, height: int = 560) -> go.Figure:
    df = comp.copy()
    df["mass_plot"] = df.mass_total_kg.clip(lower=0.001)
    df["cost_plot"] = df.total_cost_eur.clip(lower=0.01)
    fig = go.Figure()
    # tiges verticales : relient chaque pièce au plancher, pour lire la hauteur (score)
    xs, ys, zs = [], [], []
    for _, r in df.iterrows():
        xs += [r.mass_plot, r.mass_plot, None]
        ys += [r.cost_plot, r.cost_plot, None]
        zs += [0, r.risk_score, None]
    fig.add_trace(go.Scatter3d(x=xs, y=ys, z=zs, mode="lines", line=dict(color="#CBD5E1", width=2),
                               hoverinfo="skip", showlegend=False))
    qmax = max(float(df.qty.max()), 1)
    for lvl in ["Faible", "Modéré", "Élevé"]:
        d = df[df.risk_level == lvl]
        if d.empty:
            continue
        fig.add_trace(go.Scatter3d(
            x=d.mass_plot, y=d.cost_plot, z=d.risk_score, mode="markers", name=f"Risque {lvl.lower()}",
            marker=dict(size=7 + 12 * np.sqrt(d.qty / qmax), color=sh.RISK_COLORS[lvl], opacity=0.92,
                        line=dict(color="#FFFFFF", width=1.5)),
            customdata=np.stack([d.display_name, d.family, d.supplier, d.qty], axis=-1),
            hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]} · fournisseur* %{customdata[2]}<br>"
                          "masse totale %{x:.2f} kg · coût* %{y:,.0f} €<br>score %{z:.0f}/100 · "
                          "qté %{customdata[3]}<extra></extra>"))
    fig.update_layout(height=height, margin=dict(l=0, r=0, t=10, b=0),
                      scene=dict(xaxis=dict(title="Masse totale (kg)", type="log"),
                                 yaxis=dict(title="Coût estimé* (€)", type="log"),
                                 zaxis=dict(title="Score de risque", range=[0, 100]),
                                 aspectmode="manual", aspectratio=dict(x=1.1, y=1.1, z=0.8)),
                      legend=dict(orientation="h", y=0.02, x=0.5, xanchor="center"))
    return _orbit(fig, radius=2.1, z=0.9)


# --------------------------------------------------------------------------- #
# Surface : surcoût annuel selon la hausse d'une matière et le volume
# --------------------------------------------------------------------------- #
def shock_surface(comp: pd.DataFrame, mat: pd.DataFrame, shocks: dict[str, float], material: str,
                  volume: int, height: int = 520) -> go.Figure:
    # coût matière par produit et par matériau (masse totale × €/kg), comme pc.price_shock
    mcost = (comp[comp.material.isin(mat.index)]
             .assign(c=lambda d: d.mass_total_kg * d.material.map(mat.eur_per_kg))
             .groupby("material").c.sum())
    base = sum(mcost.get(k, 0) * v / 100 for k, v in shocks.items() if k != material)
    unit = mcost.get(material, 0) / 100                                              # € par point de hausse
    xs = np.arange(-30, 101, 5)
    vols = np.linspace(max(volume * 0.2, 1000), volume * 2, 25)
    X, Y = np.meshgrid(xs, vols)
    Z = (base + unit * X) * Y / 1000                                                 # k€ par an
    fig = go.Figure(go.Surface(
        x=X, y=Y, z=Z, opacity=0.95, showscale=False,
        colorscale=[[0, sh.TEAL], [0.35, "#E0E9FF"], [0.7, sh.PRIMARY], [1, sh.INK]],
        contours=dict(z=dict(show=True, usecolormap=True, project_z=True, width=2)),
        hovertemplate="hausse %{x:.0f} %<br>volume %{y:,.0f}<br>surcoût %{z:,.0f} k€/an<extra></extra>"))
    cur = shocks.get(material, 0)
    fig.add_trace(go.Scatter3d(x=[cur], y=[volume], z=[(base + unit * cur) * volume / 1000], mode="markers+text",
                               marker=dict(size=9, color=sh.AMBER, line=dict(color="#FFFFFF", width=2)),
                               text=["scénario actuel"], textposition="top center",
                               textfont=dict(color=sh.INK, size=12), name="Scénario", hoverinfo="skip"))
    fig.update_layout(height=height, margin=dict(l=0, r=0, t=10, b=0), showlegend=False,
                      scene=dict(xaxis=dict(title=f"Hausse {material.split(' ')[0].lower()} (%)"),
                                 yaxis=dict(title="Volume annuel"), zaxis=dict(title="Surcoût (k€/an)"),
                                 aspectmode="manual", aspectratio=dict(x=1.1, y=1.1, z=0.75)))
    return _orbit(fig, radius=2.0, z=0.9, duration=110)
