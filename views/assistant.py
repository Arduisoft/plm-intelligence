"""Léo en pleine page : même conversation que la bulle flottante, avec sources et tableaux détaillés."""
import streamlit as st

import leo
import shared as sh

sh.hero("Outils · Léo", "Léo, votre assistant PLM",
        "Posez vos questions en langage naturel. Léo s'appuie sur les calculs de l'appli, sur la page que vous "
        "consultiez et sur la pièce active, et cite les objets PLM utilisés.")

st.markdown(leo.LEO_CSS, unsafe_allow_html=True)
main, side = st.columns([2.4, 1], gap="large")

with side:
    with st.container(border=True):
        sh.section("Contexte")
        leo._context_chips("assistant")
        st.caption("Léo suit la pièce active : sélectionnez une pièce dans n'importe quelle page, "
                   "puis revenez ici ou utilisez la bulle Léo en bas à droite.")
    with st.container(border=True):
        sh.section("Connexion Gemini")
        leo.settings_panel()

SUGGESTIONS = ["Quels sont les sous-ensembles majeurs du moteur ?", "Quels composants sont critiques ?",
               "Si on remplace le stator core, quel est l'impact ?", "Où sont les dépendances fournisseurs ?",
               "Quelles pièces sont en cuivre ?", "Combien coûte le produit ?",
               "La qualité des données est-elle suffisante ?", "Explique-moi la pièce active."]

with main:
    hist = st.session_state.setdefault(leo.HIST, [])
    if not hist:
        sh.section("Pour commencer")
        cols = st.columns(2)
        for i, q in enumerate(SUGGESTIONS):
            if cols[i % 2].button(q, key=f"sugg_{i}", width="stretch"):
                st.session_state["_pending_q"] = q
                st.rerun()
    leo._render_history(hist, compact=False, nav_prefix="leo_page")
    if hist and st.button("Effacer la conversation", icon=":material/delete:"):
        st.session_state[leo.HIST] = []
        st.rerun()

q = st.chat_input("Posez une question sur le produit, une pièce, un risque, un changement…")
q = q or st.session_state.pop("_pending_q", None)
if q:
    leo.ask(q, "assistant")
    st.rerun()
