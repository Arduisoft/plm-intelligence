"""Comparer deux exports successifs (versions, maturités, masses, matériaux, quantités)."""
import pandas as pd
import plotly.express as px
import streamlit as st

import plm_core as pc
import leo
import shared as sh
from plm_data import analyse_xml

c = sh.ctx()
sh.hero("Évaluer · Comparaison", "Qu'est-ce qui a changé entre deux exports ?",
        "L'export ne contient que la révision courante : on compare donc deux livraisons successives de l'OEM. "
        "Clé de rapprochement : RevisionFamily, stable d'une révision à l'autre.")

st.markdown(f"**Export de référence (V1)** : `{c['name']}`, l'export actif de l'appli.")
mode = st.radio("Export à comparer (V2)", ["Importer un second export", "Générer un V2 de démonstration"],
                horizontal=True, index=1)
raw2, log = None, []
if mode == "Importer un second export":
    up = st.file_uploader("Export V2 (XPDMXML)", type=["xml"], key="cmp_upload")
    if up is not None:
        try:
            analyse_xml(up.getvalue(), up.name)
            raw2 = up.getvalue()
        except (ValueError, UnicodeError) as exc:
            st.error(f"Import refusé : {exc}")
else:
    raw2, log = pc.simulate_next_export(c["raw"])
    st.info("V2 **simulé** à partir du V1 pour démontrer la fonction : "
            + " ; ".join(log) + ".")
    st.download_button("Télécharger ce V2 simulé", raw2, file_name="XPDMXML_ECAR_PLM_V2_simule.xml",
                       mime="application/xml")

if raw2 is None:
    st.stop()

a, b = c["m"], pc.load_model_bytes(raw2)
diff = pc.diff_models(a, b)
diff[["before", "after"]] = diff[["before", "after"]].astype(str)
leo.set_page_context("comparaison", "\n".join(
    f"- {r.change} · {r['name']} · {r.field} : {r.before} → {r.after}" for _, r in diff.head(25).iterrows()))

k = st.columns(4)
k[0].metric("Objets V1 → V2", f"{len(a.products)} → {len(b.products)}")
k[1].metric("Modifications", int((diff.change == "Modifié").sum()))
k[2].metric("Ajouts", int((diff.change == "Ajouté").sum()))
k[3].metric("Suppressions", int((diff.change == "Supprimé").sum()))

if diff.empty:
    st.success("Aucune différence détectée.")
    st.stop()

left, right = st.columns([1.6, 1])
with left:
    st.dataframe(diff.rename(columns={"change": "Type", "name": "Objet", "field": "Attribut", "before": "V1",
                                      "after": "V2", "plm_id": "ID PLM"}).drop(columns=["xml_id_v1", "xml_id_v2"]),
                 hide_index=True, width="stretch",
                 column_config={"V1": st.column_config.TextColumn(), "V2": st.column_config.TextColumn()})
    sh.source("Product (RevisionName, Maturity, Mass, Material, Description) et ProductInst des deux exports")
with right:
    cnt = diff.assign(field=diff.field.str.replace(r"Quantité dans .*", "Quantité", regex=True).replace("", "Objet")) \
        .groupby("field").size().reset_index(name="n")
    fig = px.bar(cnt.sort_values("n"), x="n", y="field", orientation="h", text="n", color="field",
                 labels={"n": "Changements", "field": ""}, color_discrete_sequence=sh.COLORWAY)
    fig.update_traces(textposition="outside")
    fig.update_layout(height=280, margin=dict(t=10, l=0, r=0, b=0), showlegend=False)
    st.plotly_chart(fig, width="stretch")

# lecture métier
st.subheader("Ce que ces changements impliquent")
comp_b = pc.components(b, sh.weights(), sh.materials())
comp_a = c["comp"]
ma = a.products.maturity.value_counts()
mb = b.products.maturity.value_counts()
mat_tbl = pd.DataFrame({"V1": ma, "V2": mb}).fillna(0).astype(int)
x, y = st.columns(2)
with x:
    st.markdown("**Maturités**")
    st.dataframe(mat_tbl, width="stretch")
    rel = diff[(diff.field == "Maturité") & diff.after.isin(["RELEASED", "FROZEN"])]
    if len(rel):
        st.caption(f"{len(rel)} pièce(s) figée(s) ou libérée(s) : toute modification ultérieure exigera une "
                   "nouvelle révision et un ECR/ECO.")
with y:
    st.markdown("**Effet sur les indicateurs du fournisseur***")
    d_cost = comp_b.total_cost_eur.sum() - comp_a.total_cost_eur.sum()
    d_mass = (b.products.loc[b.root, "mass_kg"] or 0) - (a.products.loc[a.root, "mass_kg"] or 0)
    st.metric("Coût matière estimé / produit", f"{comp_b.total_cost_eur.sum():,.2f} €".replace(",", " "),
              f"{d_cost:+.2f} €", delta_color="inverse")
    st.metric("Masse déclarée de la racine", f"{b.products.loc[b.root, 'mass_kg']:.3f} kg", f"{d_mass:+.3f} kg",
              delta_color="inverse")
    st.caption("La masse racine n'est pas recalculée automatiquement par l'OEM dans le V2 simulé : "
               "un écart entre la somme des pièces et la racine apparaîtra dans la page Qualité.")
    sh.hypothesis("coûts recalculés avec les mêmes prix matières")
