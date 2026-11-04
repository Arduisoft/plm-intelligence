"""Socle commun de l'appli : données chargées, pièce active, navigation, thème, hypothèses.

Toutes les pages lisent les mêmes octets XML via `ctx()` et partagent une seule « pièce active »
(`st.session_state["sel_xml"]`, un identifiant XML du type ID_13).
"""
from __future__ import annotations

import hashlib
import html
from pathlib import Path

import pandas as pd
import streamlit as st

import plm_core as pc
from plm_data import analyse_xml

ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_XML = ROOT_DIR / "data" / "XPDMXML_ECAR_PLM.xml"
SEL = "sel_xml"
PAGES: dict = {}          # rempli par app.py : clé -> st.Page

NAVY = "#0B2545"


# --------------------------------------------------------------------------- #
# Données
# --------------------------------------------------------------------------- #
@st.cache_resource(show_spinner=False)
def _model(raw_hash: str, _raw: bytes) -> pc.PLMModel:
    return pc.load_model_bytes(_raw)


@st.cache_data(show_spinner=False)
def _components(raw_hash: str, mat_hash: str, weights: tuple, _raw: bytes, _mat: pd.DataFrame):
    m = _model(raw_hash, _raw)
    return pc.components(m, dict(weights), _mat)


def _hash_df(df: pd.DataFrame) -> str:
    return hashlib.sha256(pd.util.hash_pandas_object(df, index=True).values.tobytes()).hexdigest()


def materials() -> pd.DataFrame:
    return st.session_state.get("hyp_materials", pc.MATERIALS)


def weights() -> dict:
    return st.session_state.get("hyp_weights", pc.RISK_WEIGHTS)


def raw_xml() -> tuple[bytes, str, bool]:
    """(octets, nom du fichier, importé ?)"""
    if "plm_xml_bytes" in st.session_state:
        return (st.session_state["plm_xml_bytes"], st.session_state.get("plm_xml_name", "export.xml"), True)
    return DEFAULT_XML.read_bytes(), DEFAULT_XML.name, False


def ctx() -> dict:
    """Contexte partagé : modèle, composants enrichis, résumé validé, source."""
    raw, name, imported = raw_xml()
    h = hashlib.sha256(raw).hexdigest()
    m = _model(h, raw)
    mat = materials()
    w = weights()
    comp = _components(h, _hash_df(mat), tuple(sorted(w.items())), raw, mat)
    return dict(raw=raw, name=name, imported=imported, hash=h, m=m, comp=comp, mat=mat, weights=w)


def handle_upload(uploaded) -> None:
    """Valide un export (règles de plm_data) avant de le partager à toutes les pages."""
    if uploaded is None:
        return
    raw = uploaded.getvalue()
    token = (uploaded.name, hashlib.sha256(raw).hexdigest())
    if token == st.session_state.get("plm_upload_token"):
        return
    try:
        summary = analyse_xml(raw, uploaded.name)
    except (ValueError, UnicodeError) as exc:
        st.sidebar.error(f"Import refusé : {exc}")
        return
    st.session_state.update(plm_xml_bytes=raw, plm_xml_name=summary["filename"],
                            plm_upload_token=token, plm_summary=summary)
    st.session_state.pop(SEL, None)
    st.toast("Export importé : toutes les pages sont recalculées.")


def reset_to_demo() -> None:
    for k in ("plm_summary", "plm_xml_bytes", "plm_upload_token", "plm_xml_name", SEL):
        st.session_state.pop(k, None)
    st.session_state["upload_generation"] = st.session_state.get("upload_generation", 0) + 1


# --------------------------------------------------------------------------- #
# Pièce active (partagée entre pages)
# --------------------------------------------------------------------------- #
def selected(m: pc.PLMModel | None = None) -> str:
    m = m or ctx()["m"]
    x = st.session_state.get(SEL)
    if x not in m.products.index:
        x = m.root
        st.session_state[SEL] = x
    return x


def select(xml_id: str) -> None:
    st.session_state[SEL] = xml_id


def sync_widget(page: str, widget_key: str, to_widget, from_widget) -> None:
    """Avant création d'un widget : pousse la pièce active dans sa valeur si elle a changé ailleurs."""
    cur = st.session_state.get(SEL)
    if st.session_state.get(f"_seen_{page}") != cur or widget_key not in st.session_state:
        v = to_widget(cur)
        if v is not None:
            st.session_state[widget_key] = v
        st.session_state[f"_seen_{page}"] = cur


def sync_back(page: str, widget_value, from_widget) -> None:
    """Après le widget : si l'utilisateur a choisi une autre pièce, elle devient la pièce active."""
    x = from_widget(widget_value)
    if x and x != st.session_state.get(SEL):
        st.session_state[SEL] = x
    st.session_state[f"_seen_{page}"] = st.session_state.get(SEL)


def ei_of(m: pc.PLMModel, xml_id: str) -> str:
    """Pièce 3D -> son article d'ingénierie (parent EI) ; sinon l'objet lui-même."""
    if xml_id in m.products.index and m.products.loc[xml_id, "node_type"] == "Pièce 3D":
        for par in m.parents.get(xml_id, []):
            if m.products.loc[par, "node_type"] == "Pièce (EI)":
                return par
    return xml_id


# --------------------------------------------------------------------------- #
# Navigation
# --------------------------------------------------------------------------- #
def request_goto(page_key: str, xml_id: str | None = None) -> None:
    """Utilisable dans un callback : la bascule est faite par app.py au run suivant."""
    if xml_id:
        select(xml_id)
    st.session_state["_goto"] = page_key


def goto(page_key: str, xml_id: str | None = None) -> None:
    if xml_id:
        select(xml_id)
    if page_key in PAGES:
        st.switch_page(PAGES[page_key])


def nav_button(label: str, page_key: str, xml_id: str | None = None, key: str | None = None,
               primary: bool = False, help: str | None = None) -> None:
    st.button(label, key=key or f"nav_{page_key}_{xml_id}_{label}", on_click=request_goto,
              args=(page_key, xml_id), type="primary" if primary else "secondary", width="stretch", help=help)


# --------------------------------------------------------------------------- #
# Présentation commune
# --------------------------------------------------------------------------- #
# Palette commune (utilisée par le CSS, les graphes Plotly et Léo)
INK = "#0B2545"
PRIMARY = "#2453D6"
TEAL = "#14B8A6"
AMBER = "#F59E0B"
RED = "#E5484D"
VIOLET = "#8B5CF6"
SKY = "#38BDF8"
LIME = "#84CC16"
MUTED = "#64748B"
COLORWAY = [PRIMARY, TEAL, AMBER, RED, VIOLET, INK, SKY, LIME]
RISK_COLORS = {"Faible": TEAL, "Modéré": AMBER, "Élevé": RED}

GLOBAL_CSS = """
<style>
:root {--ink:#0B2545; --primary:#2453D6; --primary-soft:#E8EEFF; --teal:#14B8A6; --amber:#F59E0B;
  --red:#E5484D; --muted:#64748B; --line:#E3E8F0; --card:#FFFFFF;}
code, .xml-ref {font-family: 'IBM Plex Mono', Consolas, monospace !important;}
.block-container {max-width: 1440px; padding-top: 2.4rem; padding-bottom: 6rem;}
h1, h2, h3 {letter-spacing: -0.02em; color: var(--ink);}

/* bandeau de page : dégradé vivant */
.plm-hero {position: relative; overflow: hidden; color: #fff; border-radius: 18px; padding: 26px 30px 24px;
  margin-bottom: 22px; background: linear-gradient(120deg, #0B2545 0%, #133A7C 58%, #2453D6 118%);
  box-shadow: 0 14px 34px -14px rgba(11,37,69,.45);}
.plm-hero::before {content: ""; position: absolute; inset: 0; pointer-events: none; opacity: .18;
  background-image: linear-gradient(rgba(255,255,255,.35) 1px, transparent 1px),
                    linear-gradient(90deg, rgba(255,255,255,.35) 1px, transparent 1px);
  background-size: 34px 34px; mask-image: linear-gradient(100deg, transparent 35%, #000 100%);
  -webkit-mask-image: linear-gradient(100deg, transparent 35%, #000 100%);}
.plm-hero::after {content: ""; position: absolute; width: 520px; height: 520px; right: -140px; top: -260px;
  pointer-events: none; border-radius: 50%;
  background: radial-gradient(circle, rgba(110,149,255,.55), rgba(20,184,166,.18) 45%, transparent 70%);
  animation: plm-drift 14s ease-in-out infinite alternate;}
@keyframes plm-drift {from {transform: translate(0,0) scale(1);} to {transform: translate(-90px,60px) scale(1.15);}}
.plm-hero > * {position: relative; z-index: 1;}
.plm-hero .eyebrow {font-size: .72rem; letter-spacing: .16em; text-transform: uppercase; color: #A9C1F5; font-weight: 600;}
.plm-hero h1 {color: #fff !important; font-size: 1.85rem; margin: 6px 0 6px; padding: 0; font-weight: 650;}
.plm-hero p {color: #D3DEF3; margin: 0; font-size: .95rem; max-width: 900px;}

/* étiquettes source / hypothèse */
.plm-src {display: inline-block; font-size: .72rem; color: #475569; border: 1px solid var(--line);
  border-radius: 999px; padding: 2px 10px; margin: 2px 0 6px; background: #fff;}
.plm-hyp {display: inline-block; font-size: .72rem; color: #92400E; border: 1px solid #FCD9A5;
  border-radius: 999px; padding: 2px 10px; margin: 2px 0 6px; background: #FFF8EB;}

/* blocs de texte communs */
.note {background: #fff; border: 1px solid var(--line); border-left: 4px solid var(--primary); border-radius: 12px;
  padding: 14px 18px; margin: 10px 0 18px; line-height: 1.6;}
.note strong {color: var(--ink);}
.section {font-size: .8rem; font-weight: 650; color: var(--ink); text-transform: uppercase; letter-spacing: .08em;
  margin: 18px 0 10px; display: flex; align-items: center; gap: 8px;}
.section::before {content: ""; width: 4px; height: 14px; border-radius: 2px; background: var(--primary);}

/* métriques, onglets, graphes */
[data-testid="stMetric"] {background: var(--card); border: 1px solid var(--line); border-radius: 14px;
  padding: 14px 16px; transition: transform .18s ease, box-shadow .18s ease;}
[data-testid="stMetric"]:hover {transform: translateY(-2px); box-shadow: 0 10px 24px -14px rgba(11,37,69,.35);}
[data-testid="stMetricValue"] {color: var(--ink); font-weight: 650;}
[data-testid="stTabs"] button p {font-weight: 600;}
[data-testid="stPlotlyChart"] {background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 6px;}
.stButton button, .stDownloadButton button {transition: transform .12s ease, box-shadow .12s ease;}
.stButton button:hover, .stDownloadButton button:hover {transform: translateY(-1px);
  box-shadow: 0 6px 16px -10px rgba(36,83,214,.6);}

/* barre latérale */
.plm-side-part {font-size: .82rem; line-height: 1.5; padding: 10px 12px; border-radius: 10px;
  background: rgba(255,255,255,.06); border: 1px solid rgba(255,255,255,.14);}
.plm-side-part b {font-size: .9rem;}
.plm-brand {display: flex; align-items: center; gap: 10px; margin: 2px 0 4px;}
.plm-brand .logo {width: 34px; height: 34px; border-radius: 10px; display: grid; place-items: center;
  background: linear-gradient(135deg, #2453D6, #14B8A6); font-weight: 700; color: #fff; font-size: .95rem;}
.plm-brand .name {font-weight: 650; font-size: 1.05rem; color: #fff; line-height: 1.1;}
.plm-brand .sub {font-size: .72rem; color: #9FB3D3;}
</style>
"""


def hero(eyebrow: str, title: str, subtitle: str = "") -> None:
    st.markdown(f"<div class='plm-hero'><div class='eyebrow'>{html.escape(eyebrow)}</div>"
                f"<h1>{html.escape(title)}</h1><p>{html.escape(subtitle)}</p></div>", unsafe_allow_html=True)


def section(title: str) -> None:
    st.markdown(f"<div class='section'>{html.escape(title)}</div>", unsafe_allow_html=True)


def note(title: str, body_html: str) -> None:
    st.markdown(f"<div class='note'><strong>{html.escape(title)}</strong><br>{body_html}</div>",
                unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# Graphes : un seul style Plotly pour toute l'appli
# --------------------------------------------------------------------------- #
def _install_plotly_style() -> None:
    import plotly.graph_objects as go
    import plotly.io as pio
    from streamlit.delta_generator import DeltaGenerator

    if "plm" not in pio.templates:
        axis = dict(gridcolor="#E6EBF2", zerolinecolor="#CBD5E1", linecolor="#CBD5E1", ticks="",
                    title=dict(font=dict(size=12, color=MUTED)), tickfont=dict(size=11, color=MUTED))
        scene_axis = dict(backgroundcolor="#F3F6FB", gridcolor="#DCE3EE", zerolinecolor="#CBD5E1",
                          showbackground=True, title=dict(font=dict(size=11, color=MUTED)),
                          tickfont=dict(size=10, color=MUTED))
        pio.templates["plm"] = go.layout.Template(
            layout=dict(
                font=dict(family="IBM Plex Sans, Segoe UI, Arial, sans-serif", size=13, color="#1E293B"),
                colorway=COLORWAY, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                title=dict(font=dict(size=15, color=INK), x=0.01, xanchor="left"),
                xaxis=axis, yaxis=axis,
                scene=dict(xaxis=scene_axis, yaxis=scene_axis, zaxis=scene_axis),
                legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(size=12), title=dict(text="")),
                hoverlabel=dict(bgcolor=INK, bordercolor=INK,
                                font=dict(color="#FFFFFF", size=12, family="IBM Plex Sans, sans-serif")),
                colorscale=dict(sequential=[[0, "#E0E9FF"], [0.45, "#6E95FF"], [0.8, "#2453D6"], [1, INK]],
                                sequentialminus=[[0, INK], [1, "#E0E9FF"]],
                                diverging=[[0, TEAL], [0.5, "#F1F5F9"], [1, RED]]),
                margin=dict(l=10, r=10, t=36, b=10),
                transition=dict(duration=450, easing="cubic-in-out"),
                bargap=0.28, hovermode="closest",
            ),
            data=dict(bar=[go.Bar(marker=dict(line=dict(width=0), cornerradius=6))],
                      pie=[go.Pie(marker=dict(line=dict(color="#FFFFFF", width=2)))],
                      sunburst=[go.Sunburst(marker=dict(line=dict(color="#FFFFFF", width=1.5)))],
                      treemap=[go.Treemap(marker=dict(line=dict(color="#FFFFFF", width=2), cornerradius=6))]),
        )
    pio.templates.default = "plotly_white+plm"

    if getattr(DeltaGenerator.plotly_chart, "_plm", False):
        return
    original = DeltaGenerator.plotly_chart

    def plotly_chart(self, figure_or_data, *args, **kwargs):
        # thème Streamlit désactivé : le gabarit « plm » s'applique partout
        kwargs.setdefault("theme", None)
        cfg = dict(kwargs.get("config") or {})
        cfg.setdefault("displaylogo", False)
        cfg.setdefault("modeBarButtonsToRemove", ["select2d", "lasso2d", "autoScale2d"])
        kwargs["config"] = cfg
        return original(self, figure_or_data, *args, **kwargs)

    plotly_chart._plm = True
    DeltaGenerator.plotly_chart = plotly_chart
    st.plotly_chart = lambda *a, **k: plotly_chart(st._main, *a, **k)


_install_plotly_style()


def source(text: str) -> None:
    st.markdown(f"<span class='plm-src'>Source XML : {html.escape(text)}</span>", unsafe_allow_html=True)


def hypothesis(text: str) -> None:
    st.markdown(f"<span class='plm-hyp'>Hypothèse : {html.escape(text)}</span>", unsafe_allow_html=True)


def short(name: str) -> str:
    return (name or "").replace("ECAR-EI_", "").replace("ECAR-3D_", "").replace("ECAR-01_", "").strip()
