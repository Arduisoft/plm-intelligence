"""Vue d'ensemble (d'après Page1) : le produit en chiffres, points de vigilance, accès aux analyses."""
from html import escape
import json

import plotly.express as px
import streamlit as st

import plm_core as pc
import shared as sh
import viz3d
from plm_data import analyse_xml

c = sh.ctx()
m, comp = c["m"], c["comp"]
P = m.products
data = analyse_xml(c["raw"], c["name"])
data["imported"] = c["imported"]


def format_mass(value, dec=1):
    try:
        return f"{float(value):,.{dec}f}".replace(",", " ").replace(".", ",") + " kg"
    except (ValueError, TypeError):
        return "Non fournie"


sh.hero("Product intelligence · " + (P.loc[m.root, "project"] or "PLM"),
        "Vue d'ensemble du produit",
        "Comprendre le produit, repérer les points de vigilance et préparer un changement, "
        "à partir du seul export XML de l'OEM.")

st.markdown(f"""<div style="background:#fff;border:1px solid #E3E8F0;border-left:4px solid #2453D6;padding:16px 22px;
border-radius:14px;margin:0 0 18px;overflow-wrap:anywhere"><div style="color:#64748B;font-size:.72rem;letter-spacing:.14em;
font-weight:600">{'EXPORT IMPORTÉ' if c['imported'] else 'EXPORT DE RÉFÉRENCE ECAR'}</div>
<div style="margin:6px 0 4px;color:#0B2545;font-size:1.25rem;font-weight:650">{escape(sh.short(data['name']))}</div>
<span style="color:#64748B;font-size:.85rem">{escape(data['filename'])} · révision {escape(data['revision'] or '—')}
· exporté de 3DEXPERIENCE le {escape(P.loc[m.root, 'created'][:10])}</span></div>""",
            unsafe_allow_html=True)

# ------------------------------------------------------------------ chiffres clés
st.subheader("Le produit en chiffres")
mass_total = P.loc[m.root, "mass_kg"]
metrics = [("Références produit", data["products"], "Éléments Product uniques"),
           ("Liens d'instance", data["instances"], "Liens parent → enfant (ProductInst)"),
           ("Pièces (EI)", len(comp), "Articles d'ingénierie EI (+ pièces 3D montées en direct)"),
           ("Masse déclarée", format_mass(mass_total), "WCGEquivalentComputedExt.V_WCG_Mass de la racine"),
           ("En conception", f"{data['inWork']}/{data['products']}", "Références à la maturité IN_WORK")]
for col, (name, val, help_text) in zip(st.columns(5), metrics):
    with col, st.container(border=True):
        st.metric(name, val, help=help_text)

# ------------------------------------------------------------------ sous-ensembles
st.subheader("Sous-ensembles principaux")
aliases = {"ECAR-EI_EMOTOR FRONT": "Moteur électrique", "ECAR-EI_REDUCER FRONT": "Réducteur",
           "ECAR-EI_HOUSING FRONT EDRIVE": "Carter du groupe motopropulseur"}
left, right = st.columns([1.5, 1])
with left:
    for offset in range(0, len(data["children"]), 3):
        for col, child in zip(st.columns(3), data["children"][offset:offset + 3]):
            sub = comp[comp.subassembly.str.contains(child["name"], regex=False)]
            with col, st.container(border=True):
                st.markdown(f"**{aliases.get(child['name'], sh.short(child['name']))}**")
                st.caption(f"Révision {child['revision'] or '—'}")
                st.metric("Masse déclarée", format_mass(child["mass"], 2))
                st.caption(f"{len(sub)} pièces · {int(sub.qty.sum())} occurrences · "
                           f"coût estimé* {sub.total_cost_eur.sum():,.0f} €".replace(",", " "))
                sh.nav_button("Explorer", "explorateur", child["id"], key=f"acc_exp_{child['id']}")
with right:
    fb = pc.flat_bom(m).merge(P[["mass_kg"]], left_on="xml_id", right_index=True)
    fb = fb[fb.level.between(1, 2)]
    fb["sub"] = fb.subassembly.map(lambda s: aliases.get(s, sh.short(s)))
    fb = fb[fb.level == 2].groupby(["sub", "name"], as_index=False).mass_kg.sum()
    fb["name"] = fb.name.map(sh.short)
    fig = px.sunburst(fb, path=["sub", "name"], values="mass_kg", color_discrete_sequence=sh.COLORWAY)
    fig.update_traces(hovertemplate="<b>%{label}</b><br>%{value:.2f} kg<extra></extra>",
                      insidetextorientation="radial")
    fig.update_layout(height=360, margin=dict(t=4, l=4, r=4, b=4))
    st.plotly_chart(fig, width="stretch")
    sh.source("masses déclarées des enfants de niveau 2")

# ------------------------------------------------------------------ structure 3D
st.subheader("La structure du produit en 3D")
g3, txt = st.columns([2.6, 1], gap="large")
with g3:
    st.plotly_chart(viz3d.bom_3d(m, highlight=sh.selected(m)), width="stretch", key="acc_bom3d")
with txt:
    sh.section("Comment lire")
    st.markdown("- **Centre** : le groupe motopropulseur complet.\n"
                "- **Chaque anneau** descend d'un niveau dans la nomenclature.\n"
                "- **Couleur** : le sous-ensemble d'appartenance.\n"
                "- **Taille** : la masse déclarée de la pièce.\n"
                "- **Anneau orange** : la pièce active.")
    st.caption("Faites pivoter à la souris, ou cliquez sur Tourner pour lancer la rotation.")
    sh.source("ProductInst Owned → Instancing, V_WCG_Mass")
    sh.nav_button("Explorer la nomenclature", "explorateur", key="acc_3d_exp", primary=True)

# ------------------------------------------------------------------ vigilance
st.subheader("Points à examiner")
a, b, cc = st.columns(3)
with a, st.container(border=True):
    st.markdown("**Composants les plus exposés**")
    for _, r in comp.head(5).iterrows():
        st.markdown(f"- {r.display_name} · **{r.risk_score:.0f}/100** · {r.family}")
    sh.hypothesis("score enrichi (matières, fournisseurs simulés)")
    sh.nav_button("Voir les risques", "risques", key="acc_risk")
with b, st.container(border=True):
    st.markdown("**Qualité des données**")
    dq = pc.data_quality(m)
    for _, r in dq[dq.severity.isin(["Élevée", "Moyenne"])].head(5).iterrows():
        color = "red" if r.severity == "Élevée" else "orange"
        st.markdown(f"- :{color}-badge[{r.severity}] {r.title}")
    if data["broken"] or data["cycles"]:
        st.error(f"{data['broken']} lien(s) non résolu(s) · {'cycle détecté' if data['cycles'] else 'aucun cycle'}")
    else:
        st.caption("Structure cohérente : aucun lien cassé, aucun cycle.")
    sh.nav_button("Voir le contrôle qualité", "qualite", key="acc_dq")
with cc, st.container(border=True):
    st.markdown("**Ce que l'export ne contient pas**")
    st.markdown("- aucun coût ni fournisseur\n- aucune géométrie ni image (36 fiches 3D sans fichier)\n"
                "- une seule maturité (IN_WORK) et un seul propriétaire\n- aucun historique de révision")
    st.caption("Ces manques sont complétés par des hypothèses explicites, modifiables dans la page Hypothèses.")
    sh.nav_button("Régler les hypothèses", "hypotheses", key="acc_hyp")

# ------------------------------------------------------------------ parcours
st.subheader("Poursuivre l'analyse")
for col, (title, desc, target) in zip(st.columns(4), [
        ("Comprendre", "Naviguer dans la nomenclature, comparer les versions.", "explorateur"),
        ("Identifier", "Classer les pièces critiques, expliquer chaque score.", "risques"),
        ("Décider", "Simuler un remplacement et chiffrer son impact.", "impact"),
        ("Demander à Léo", "Poser une question en langage naturel, sources citées.", "assistant")]):
    with col, st.container(border=True):
        st.markdown(f"**{title}**")
        st.caption(desc)
        sh.nav_button("Ouvrir", target, key=f"acc_go_{target}", primary=True)

with st.expander("Voir les sources et les règles de calcul"):
    st.write("Les compteurs portent sur les éléments Product et ProductInst. Le nombre de références en "
             "conception compte les Product dont Maturity vaut IN_WORK. Les masses des niveaux ne sont pas "
             "additionnées entre elles : cela compterait certaines pièces plusieurs fois.")
    st.code(f"/XPDMXML/ProductStructure/Product[@id='{data['root']}']\nMasse : WCGEquivalentComputedExt.V_WCG_Mass\n"
            "Lien parent : ProductInst/Owned\nLien enfant : ProductInst/Instancing", language="text")
    st.caption("Espace de noms : http://www.3ds.com/xsd/XPDMXML")
    st.text("SHA-256 : " + data["hash"])
    st.download_button("Télécharger la synthèse JSON", json.dumps(data, ensure_ascii=False, indent=2),
                       file_name="synthese_plm.json", mime="application/json")
st.caption("* Coûts et fournisseurs : hypothèses simulées, à remplacer par les données achats.")
