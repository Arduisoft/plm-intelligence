"""Hypothèses : tout ce qui n'est PAS dans l'XML, modifiable et appliqué à toutes les pages."""
import pandas as pd
import streamlit as st

import plm_core as pc
import shared as sh

sh.hero("Outils · Hypothèses", "Ce que l'export ne dit pas",
        "Prix matières, risques d'approvisionnement, fournisseurs et pondérations du score. "
        "Toute modification est appliquée immédiatement à toutes les pages.")

st.warning("Ces valeurs sont des **hypothèses de démonstration**. Elles ne viennent pas de l'OEM et doivent être "
           "remplacées par les données achats du fournisseur avant toute décision.")

st.subheader("Référentiel matières")
st.caption("€/kg brut, kgCO₂e/kg, risque d'approvisionnement et volatilité notés de 1 à 5. "
           "Prix de fin 2026 issus de sources publiques (colonne source).")
edited = st.data_editor(
    sh.materials().reset_index(), hide_index=True, width="stretch", key="mat_editor", num_rows="fixed",
    disabled=["material", "source"],
    column_config={
        "material": "Matériau", "eur_per_kg": st.column_config.NumberColumn("€/kg", min_value=0.0, format="%.2f"),
        "co2_per_kg": st.column_config.NumberColumn("kgCO₂e/kg", min_value=0.0, format="%.1f"),
        "supply_risk": st.column_config.NumberColumn("Risque appro", min_value=1, max_value=5, step=1),
        "price_volatility": st.column_config.NumberColumn("Volatilité", min_value=1, max_value=5, step=1),
        "note": st.column_config.TextColumn("Remarque", width="large"),
        "source": st.column_config.TextColumn("Source", width="medium")})
new_mat = edited.set_index("material")
_num = ["eur_per_kg", "co2_per_kg", "supply_risk", "price_volatility"]
_old = sh.materials()
if st.session_state.get("mat_editor", {}).get("edited_rows") and not (
        new_mat[_num].astype(float).round(4).equals(_old[_num].astype(float).round(4))
        and new_mat.note.equals(_old.note)):
    st.session_state["hyp_materials"] = new_mat
    st.rerun()

st.subheader("Pondérations du score de risque enrichi")
w = dict(sh.weights())
cols = st.columns(4)
new_w = {}
for i, (k, v) in enumerate(w.items()):
    with cols[i % 4]:
        new_w[k] = st.slider(k.capitalize(), 0.0, 0.5, float(v), 0.05, key=f"w_{k}")
total = sum(new_w.values())
if total == 0:
    st.error("Au moins une pondération doit être positive.")
else:
    st.caption(f"Somme : {total:.2f} (le score est normalisé, la somme n'a pas besoin de valoir 1).")
    if new_w != w:
        st.session_state["hyp_weights"] = new_w
        st.rerun()

st.subheader("Fournisseurs simulés par famille de pièces")
st.dataframe(pc.SUPPLIERS.assign(families=pc.SUPPLIERS.families.str.join(", ")).rename(columns={
    "supplier_id": "ID", "supplier": "Fournisseur (fictif)", "country": "Pays", "families": "Familles",
    "qualified_sources": "Sources qualifiées", "rating": "Note qualité", "lead_time_wk": "Délai (sem.)"}),
    hide_index=True, width="stretch")
a, b = st.columns(2)
with a:
    st.markdown("**Criticité fonctionnelle par famille (1 à 5)**")
    st.dataframe(pd.Series(pc.FAMILY_CRITICALITY, name="criticité").rename_axis("famille").reset_index(),
                 hide_index=True, width="stretch")
with b:
    st.markdown("**Facteur de transformation (coût pièce / coût matière)**")
    st.dataframe(pd.Series(pc.PROCESS_FACTOR, name="facteur").rename_axis("famille").reset_index(),
                 hide_index=True, width="stretch")

if "hyp_materials" in st.session_state or "hyp_weights" in st.session_state:
    if st.button("Restaurer les valeurs par défaut", type="primary"):
        for k in ("hyp_materials", "hyp_weights", "mat_editor"):
            st.session_state.pop(k, None)
        for k in list(st.session_state):
            if str(k).startswith("w_"):
                del st.session_state[k]
        st.rerun()

with st.expander("Sources des prix et des notes de risque"):
    st.markdown(
        "- Cuivre et aluminium : niveaux LME et prévisions 2026 de Standard Chartered, "
        "[oilprice.com, 21/10/2026](https://oilprice.com/Metals/Commodities/Aluminium-Gives-Up-Its-War-Premium-Copper-Stays-Elevated.amp.html)\n"
        "- Acier électrique : indice de prix T2 2026, "
        "[IMARC](https://www.imarcgroup.com/news/electrical-steel-price-index-q2-2026)\n"
        "- Restriction PFAS (joints FKM) : calendrier ECHA/SEAC 2026, "
        "[Certivo](https://www.certivo.com/blog-details/eu-pfas-restriction-under-reach-2026-echa-consultation-update)\n"
        "- Autres matières : ordres de grandeur, à confirmer par les achats.")
