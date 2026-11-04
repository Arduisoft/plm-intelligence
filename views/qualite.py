"""Qualité des données : contrôles structurels (plm_data) + contrôles métier (plm_core)."""
import pandas as pd
import plotly.express as px
import streamlit as st

import plm_core as pc
import shared as sh
from plm_data import analyse_xml

c = sh.ctx()
m = c["m"]
P = m.products
summary = analyse_xml(c["raw"], c["name"])
dq = pc.data_quality(m)

sh.hero("Identifier · Qualité des données", "Peut-on faire confiance à cet export ?",
        "Chaque constat est relié aux objets XML concernés. Un défaut de données n'est pas un défaut mécanique.")

sev_icon = {"Élevée": ":red-badge[Élevée]", "Moyenne": ":orange-badge[Moyenne]", "Faible": ":blue-badge[Faible]", "Info": ":gray-badge[Info]"}
k = st.columns(5)
k[0].metric("Liens cassés", summary["broken"])
k[1].metric("Cycles", "oui" if summary["cycles"] else "non")
for i, sev in enumerate(["Élevée", "Moyenne", "Faible"], start=2):
    k[i].metric(f"Constats {sev.lower()}s", int((dq.severity == sev).sum()))

st.subheader("Constats")
for i, r in dq.iterrows():
    with st.expander(f"{sev_icon.get(r.severity, '')} [{r.category}] {r.title}", expanded=r.severity == "Élevée"):
        st.write(r.detail)
        ids = list(dict.fromkeys(x for x in r.xml_ids if x in P.index))[:12]
        if ids:
            st.dataframe(P.loc[ids, ["plm_id", "name", "revision", "maturity", "mass_kg", "material"]]
                         .rename(columns={"plm_id": "ID PLM", "name": "Nom", "revision": "Rév.",
                                          "maturity": "Maturité", "mass_kg": "Masse kg", "material": "Matériau"}),
                         width="stretch")
            cols = st.columns(min(4, len(ids)))
            for col, x in zip(cols, ids[:4]):
                with col:
                    sh.nav_button(f"Fiche {sh.short(P.loc[x, 'name'])[:22]}", "fiche", x, key=f"dq_{i}_{x}")
        if len(r.xml_ids) > 12:
            st.caption(f"… et {len(r.xml_ids) - 12} autres objets.")

st.subheader("Cohérence des masses (assemblage = somme des enfants ?)")
mr = pc.mass_rollup(m)
mr["statut"] = mr.gap_kg.abs().map(lambda g: "Cohérent" if g <= 0.01 else "Écart")
st.dataframe(mr.rename(columns={"name": "Assemblage", "declared_kg": "Déclarée kg", "children_sum_kg": "Somme enfants kg",
                                "gap_kg": "Écart kg", "n_children": "Occurrences"}),
             hide_index=True, width="stretch")
sh.source("WCGEquivalentComputedExt.V_WCG_Mass, ProductInst Owned → Instancing")
st.caption("Les masses sont cohérentes arithmétiquement, mais voir le constat sur le stator : une dent seule et "
           "une bobine seule, modélisées pour le maillage, sont comptées en plus du noyau et des bobines complets.")

st.subheader("Complétude des attributs")
fields = ["description", "material", "mass_kg", "revision", "maturity", "lifecycle", "owner"]
comp_rate = pd.DataFrame([dict(attribut=f, renseigné=round(100 * (P[f].notna() & (P[f].astype(str) != "")).mean(), 1),
                               valeurs_distinctes=P[f].nunique()) for f in fields])
a, b = st.columns([1.3, 1])
with a:
    fig = px.bar(comp_rate.sort_values("renseigné"), x="renseigné", y="attribut", orientation="h",
                 range_x=[0, 105], text="renseigné", color="renseigné",
                 color_continuous_scale=[sh.RED, sh.AMBER, sh.TEAL], range_color=[0, 100],
                 labels={"renseigné": f"% des {len(P)} objets renseignés", "attribut": ""})
    fig.update_traces(texttemplate="%{text:.0f} %", textposition="outside")
    fig.update_layout(height=320, margin=dict(t=10, l=0, r=0, b=0), coloraxis_showscale=False)
    st.plotly_chart(fig, width="stretch")
with b:
    st.dataframe(comp_rate, hide_index=True, width="stretch")
    st.caption("Un attribut avec une seule valeur distincte (maturité, propriétaire, cycle de vie) "
               "ne permet pas de discriminer les pièces.")

with st.expander("En-tête de l'export (InfoHeader)"):
    st.dataframe(pd.DataFrame([(k_, v) for k_, v in m.header.items() if not k_.startswith("_")],
                              columns=["Info", "Valeur"]), hide_index=True, width="stretch")
st.download_button("Exporter le rapport qualité (CSV)",
                   dq.drop(columns=["xml_ids"]).to_csv(index=False).encode("utf-8-sig"),
                   file_name="qualite_donnees_plm.csv", mime="text/csv")
