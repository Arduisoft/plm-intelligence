"""PLM Intelligence : point d'entrée unique.

Lancement :  python -m streamlit run app.py
"""
import streamlit as st

import shared as sh

st.set_page_config(page_title="PLM Intelligence", page_icon=":material/precision_manufacturing:", layout="wide",
                   initial_sidebar_state="expanded")
st.markdown(sh.GLOBAL_CSS, unsafe_allow_html=True)

import leo  # noqa: E402  (après set_page_config)

V = sh.ROOT_DIR / "views"
sh.PAGES.update({
    "accueil": st.Page(V / "accueil.py", title="Vue d'ensemble", icon=":material/dashboard:", default=True),
    "explorateur": st.Page(V / "explorateur.py", title="Explorateur BOM", icon=":material/account_tree:"),
    "fiche": st.Page(V / "fiche.py", title="Fiche composant", icon=":material/description:"),
    "risques": st.Page(V / "risques.py", title="Risques", icon=":material/warning:"),
    "impact": st.Page(V / "impact.py", title="Analyse d'impact", icon=":material/call_split:"),
    "comparaison": st.Page(V / "comparaison.py", title="Comparer deux exports", icon=":material/difference:"),
    "qualite": st.Page(V / "qualite.py", title="Qualité des données", icon=":material/fact_check:"),
    "assistant": st.Page(V / "assistant.py", title="Léo, assistant PLM", icon=":material/smart_toy:"),
    "hypotheses": st.Page(V / "hypotheses.py", title="Hypothèses", icon=":material/tune:"),
})
P = sh.PAGES
nav = st.navigation({
    "Comprendre": [P["accueil"], P["explorateur"], P["fiche"]],
    "Identifier les risques": [P["risques"], P["qualite"]],
    "Évaluer un changement": [P["impact"], P["comparaison"]],
    "Outils": [P["assistant"], P["hypotheses"]],
})
page_key = next((k for k, v in P.items() if v is nav or v.title == nav.title), "accueil")

# Bascule demandée par un bouton (les callbacks ne peuvent pas changer de page eux-mêmes)
target = st.session_state.pop("_goto", None)
if target in P and P[target] is not nav:
    st.switch_page(P[target])

# ------------------------------------------------------------------ barre latérale commune
with st.sidebar:
    st.markdown("<div class='plm-brand'><div class='logo'>PI</div><div><div class='name'>PLM Intelligence</div>"
                "<div class='sub'>ESILV · équipe ECAR</div></div></div>", unsafe_allow_html=True)
    with st.expander("Source des données", expanded=False, icon=":material/upload_file:"):
        up = st.file_uploader("Importer un export XPDMXML", type=["xml"],
                              key=f"xml_upload_{st.session_state.get('upload_generation', 0)}",
                              help="10 Mo maximum, DTD et entités refusées. Analysé en mémoire.")
        sh.handle_upload(up)
        if "plm_xml_bytes" in st.session_state:
            st.button("Revenir à l'export ECAR", on_click=sh.reset_to_demo, width="stretch")
    c = sh.ctx()
    st.caption(f"{'Export importé' if c['imported'] else 'Export ECAR'} · {c['name']} · "
               f"{len(c['m'].products)} objets")
    active_slot = st.container()
    st.divider()

# Léo : bulle flottante, avant la page pour rester visible même si la page s'interrompt (st.stop)
if page_key != "assistant":
    leo.launcher(page_key)

nav.run()

# ------------------------------------------------------------------ pièce active (après la page)
m = c["m"]
P_ = m.products
cur = sh.selected(m)
ids = list(P_.sort_values("display_name").index)
ids.remove(m.root)
ids = [m.root] + ids


def _on_global():
    sh.select(st.session_state["global_sel"])


if st.session_state.get("_seen_global") != cur or "global_sel" not in st.session_state:
    st.session_state["global_sel"] = cur
    st.session_state["_seen_global"] = cur
with active_slot:
    st.selectbox("Pièce active (partagée par toutes les pages)", ids, key="global_sel", on_change=_on_global,
                 format_func=lambda x: f"{sh.short(P_.loc[x, 'name'])} · rév. {P_.loc[x, 'revision']}")
    r = P_.loc[cur]
    mass = f"{r.mass_kg:.3f} kg" if r.mass_kg is not None else "masse non fournie"
    st.markdown(f"<div class='plm-side-part'><b>{sh.short(r['name'])}</b><br>"
                f"{r.node_type} · {r.maturity} · {mass}</div>", unsafe_allow_html=True)
    a, b = st.columns(2)
    with a:
        sh.nav_button("Fiche", "fiche", cur, key="side_fiche")
    with b:
        sh.nav_button("Impact", "impact", sh.ei_of(m, cur), key="side_impact")
st.session_state["_seen_global"] = st.session_state.get(sh.SEL)
