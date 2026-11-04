"""
PLM Intelligence Challenge, page 3 : Fiche composant.

Lancement : python -m streamlit run Page3.py

Conventions partagées avec les autres pages (st.session_state) :
    "plm_selected_part"  référence (balise <ID>, ex. prd-12535825-00000048) de la pièce affichée
    "plm_impact_part"    référence de la pièce à simuler sur la page Impact

La fonction family_of(name) peut être réutilisée par les autres pages pour regrouper les pièces par type.
"""

from __future__ import annotations

import collections
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import streamlit as st

import shared as sh

# ----------------------------------------------------------------------------
# Paramètres
# ----------------------------------------------------------------------------
RENDER_PATH = sh.ROOT_DIR / "assets" / "edrive_render.png"  # extrait de la présentation du sujet (PLM_ESILV_V2.pptx)
NS = "{http://www.3ds.com/xsd/XPDMXML}"
NAVY = sh.INK

_v = tuple(int(x) for x in st.__version__.split(".")[:2])
FULL = {"width": "stretch"} if _v >= (1, 46) else {"use_container_width": True}

MAIN_FIELDS = {
    "Name": "Nom",
    "ID": "Référence",
    "Description": "Description",
    "RevisionName": "Révision",
    "RevisionIndex": "Index de révision",
    "Maturity": "Maturité",
    "WCGEquivalentComputedExt.V_WCG_Mass": "Masse (kg)",
    "Material": "Matériau",
    "Project": "Projet",
    "TimeCreated": "Création",
    "TimeModified": "Dernière modification",
}
TECH_FIELDS = {
    "UniqueID": "Identifiant unique (XID)",
    "RevisionFamily": "Famille de révision",
    "Lifecycle": "Cycle de vie",
    "Owner": "Propriétaire",
    "Organization": "Organisation",
    "SecurityLevel": "Niveau de sécurité",
}
LABELS = {**MAIN_FIELDS, **TECH_FIELDS}
# Exclus de la comparaison : identifiants propres à chaque objet et dates liées à la génération du fichier
NON_COMPARABLE = {"UniqueID", "RevisionFamily", "TimeCreated", "TimeModified"}

MATURITY = {
    "IN_WORK": ("En cours de conception", "#B9770E", "rgba(185,119,14,0.12)"),
    "PRIVATE": ("Privé", "#5D6D7E", "rgba(93,109,126,0.12)"),
    "FROZEN": ("Figé", "#2471A3", "rgba(36,113,163,0.12)"),
    "RELEASED": ("Validé", "#1E8449", "rgba(30,132,73,0.12)"),
    "OBSOLETE": ("Obsolète", "#5D6D7E", "rgba(93,109,126,0.12)"),
}

CSS = """
<style>
h1 {font-weight: 700; letter-spacing: -0.5px;}
.subtitle {color: #6B7785; margin-top: -0.6rem; margin-bottom: 1.4rem;}
.part-name {font-size: 1.6rem; font-weight: 650; color: #0B2545; margin: 0 0 0.4rem 0; word-break: break-word;}
.part-desc {color: #6B7785; font-style: italic; margin-top: 0.6rem;}
.tag {display: inline-block; padding: 2px 10px; border-radius: 6px; font-size: 0.8rem;
      font-weight: 600; margin: 0 6px 4px 0;}
.kind-note {font-size: 0.82rem; color: #6B7785; margin-top: 0.3rem;}
.grid {display: grid; grid-template-columns: repeat(auto-fit, minmax(118px, 1fr));
       gap: 14px; margin-top: 1rem;}
.cell {border-left: 3px solid #2453D6; padding: 2px 0 2px 12px;}
.cell .k {font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.6px; color: #6B7785;}
.cell .v {font-size: 0.98rem; font-weight: 600; word-break: break-word; margin-top: 2px;}
.cell .v.muted {color: #9AA5B1; font-weight: 500;}
.visual {border-radius: 10px; background: rgba(36,83,214,0.05); display: flex; align-items: center;
         justify-content: center; height: 190px; overflow: hidden;}
.visual svg {width: 150px; height: 150px;}
.visual-note {font-size: 0.72rem; color: #9AA5B1; margin-top: 0.4rem; line-height: 1.3;}
.src {font-size: 0.75rem; color: #9AA5B1; margin-top: 0.6rem;}
.section {font-size: 1.05rem; font-weight: 700; color: #2453D6; text-transform: uppercase;
          letter-spacing: 0.8px; margin-bottom: 0.6rem;}
.rev {display: inline-block; width: 34px; height: 34px; line-height: 31px; text-align: center;
      border-radius: 50%; font-weight: 700; margin-right: 4px;
      border: 1.5px dashed #C5CED8; color: #9AA5B1;}
.rev.on {background: #2453D6; border: 1.5px solid #2453D6; color: white;}
.rev-line {display: inline-block; width: 22px; height: 2px; background: #C5CED8;
           vertical-align: middle; margin-right: 4px;}
.crumb {display: inline-block; padding: 3px 10px; border-radius: 6px; margin: 2px 0;
        background: rgba(36,83,214,0.07); font-size: 0.88rem;}
.crumb.on {background: #2453D6; color: white; font-weight: 600;}
.sep {color: #9AA5B1; margin: 0 6px;}
.check {padding: 10px 14px; border-radius: 8px; font-size: 0.88rem; margin-top: 0.8rem;}
.check.ok {background: rgba(30,132,73,0.08); color: #1E8449;}
.check.ko {background: rgba(185,119,14,0.10); color: #B9770E;}
</style>
"""


# ----------------------------------------------------------------------------
# Familles de pièces, déduites des noms du XML (l'ordre des règles compte)
# ----------------------------------------------------------------------------
FAMILY_RULES = [
    (r"410KW", "Groupe motopropulseur"),
    (r"EMOTOR", "Moteur électrique"),
    (r"REDUCER", "Réducteur"),
    (r"DIFFERENTIAL_ASSEMBLY", "Différentiel"),
    (r"_BEA\d", "Roulement"),
    (r"GEAR|BEVEL", "Engrenage"),
    (r"SCREW", "Vis"),
    (r"[_ ]PIN$", "Goupille"),
    (r"SHAFT|[_ ](HSH|ISH|LSH)$", "Arbre"),
    (r"COIL", "Bobinage"),
    (r"STATOR", "Stator"),
    (r"COOLING", "Refroidissement"),
    (r"JOINT", "Joint d'étanchéité"),
    (r"PIPE", "Raccord de tuyauterie"),
    (r"HOUSING|CASING|CASTING|SUPPORT|PLATE|[_ ]MOTOR$", "Carter et supports"),
]


def family_of(name: str) -> str:
    n = (name or "").upper().strip()
    for pattern, fam in FAMILY_RULES:
        if re.search(pattern, n):
            return fam
    return "Composant"


# ----------------------------------------------------------------------------
# Illustrations génériques par famille (schémas SVG dessinés en code)
# ----------------------------------------------------------------------------
def _gear_path(cx, cy, r, teeth, depth):
    pts = []
    for i in range(teeth * 4):
        a = 2 * math.pi * i / (teeth * 4)
        rr = r + depth if i % 4 in (1, 2) else r
        pts.append(f"{cx + rr * math.cos(a):.1f},{cy + rr * math.sin(a):.1f}")
    return "M" + " L".join(pts) + " Z"


def _svg(body: str) -> str:
    return (f"<svg viewBox='0 0 120 120' xmlns='http://www.w3.org/2000/svg' fill='none' stroke='{NAVY}' "
            f"stroke-width='2.2' stroke-linejoin='round' stroke-linecap='round'>{body}</svg>")


LIGHT = "fill='rgba(36,83,214,0.10)'"


def family_svg(fam: str) -> str:
    if fam == "Roulement":
        balls = "".join(f"<circle cx='{60 + 31 * math.cos(2 * math.pi * i / 10):.1f}' "
                        f"cy='{60 + 31 * math.sin(2 * math.pi * i / 10):.1f}' r='6' {LIGHT}/>" for i in range(10))
        return _svg(f"<circle cx='60' cy='60' r='45'/><circle cx='60' cy='60' r='38'/>"
                    f"<circle cx='60' cy='60' r='24'/><circle cx='60' cy='60' r='16'/>{balls}")
    if fam == "Engrenage":
        return _svg(f"<path d='{_gear_path(60, 60, 38, 14, 8)}' {LIGHT}/>"
                    f"<circle cx='60' cy='60' r='14'/><rect x='56' y='42' width='8' height='6'/>")
    if fam == "Réducteur":
        return _svg(f"<path d='{_gear_path(42, 66, 27, 11, 6)}' {LIGHT}/><circle cx='42' cy='66' r='8'/>"
                    f"<path d='{_gear_path(87, 40, 17, 8, 5)}' {LIGHT}/><circle cx='87' cy='40' r='5'/>")
    if fam == "Différentiel":
        return _svg(f"<circle cx='60' cy='60' r='46'/><path d='{_gear_path(60, 34, 13, 8, 4)}' {LIGHT}/>"
                    f"<path d='{_gear_path(60, 86, 13, 8, 4)}' {LIGHT}/>"
                    f"<path d='{_gear_path(34, 60, 11, 7, 4)}' {LIGHT}/><path d='{_gear_path(86, 60, 11, 7, 4)}' {LIGHT}/>")
    if fam == "Vis":
        threads = "".join(f"<line x1='48' y1='{y}' x2='72' y2='{y + 5}'/>" for y in range(44, 100, 8))
        return _svg(f"<path d='M38 14 L82 14 L88 26 L82 38 L38 38 L32 26 Z' {LIGHT}/>"
                    f"<rect x='48' y='38' width='24' height='68'/>{threads}")
    if fam == "Goupille":
        return _svg(f"<rect x='16' y='48' width='88' height='24' rx='8' {LIGHT}/>"
                    "<line x1='28' y1='48' x2='28' y2='72'/><line x1='92' y1='48' x2='92' y2='72'/>")
    if fam == "Arbre":
        return _svg(f"<rect x='8' y='52' width='26' height='16' {LIGHT}/><rect x='34' y='42' width='22' height='36'/>"
                    f"<rect x='56' y='47' width='38' height='26' {LIGHT}/><rect x='94' y='54' width='18' height='12'/>"
                    "<line x1='62' y1='52' x2='88' y2='52'/>")
    if fam == "Stator":
        teeth = ""
        for i in range(18):
            a = 2 * math.pi * i / 18
            teeth += (f"<line x1='{60 + 40 * math.cos(a):.1f}' y1='{60 + 40 * math.sin(a):.1f}' "
                      f"x2='{60 + 26 * math.cos(a):.1f}' y2='{60 + 26 * math.sin(a):.1f}' stroke-width='4'/>")
        return _svg(f"<circle cx='60' cy='60' r='50' {LIGHT}/><circle cx='60' cy='60' r='40'/>"
                    f"<circle cx='60' cy='60' r='22'/>{teeth}")
    if fam == "Bobinage":
        turns = "".join(f"<ellipse cx='{x}' cy='60' rx='7' ry='32'/>" for x in range(30, 96, 9))
        return _svg(f"<rect x='22' y='26' width='76' height='68' rx='6' {LIGHT}/>{turns}")
    if fam == "Refroidissement":
        return _svg(f"<rect x='14' y='22' width='92' height='76' rx='10' {LIGHT}/>"
                    "<path d='M26 38 H92 Q100 38 100 46 Q100 54 92 54 H28 Q20 54 20 62 Q20 70 28 70 H92 "
                    "Q100 70 100 78 Q100 86 92 86 H26'/>")
    if fam == "Joint d'étanchéité":
        return _svg(f"<circle cx='60' cy='60' r='44' {LIGHT}/><circle cx='60' cy='60' r='30' fill='white'/>"
                    "<circle cx='60' cy='60' r='37' stroke-dasharray='4 4'/>")
    if fam == "Raccord de tuyauterie":
        return _svg(f"<path d='M20 30 H62 Q90 30 90 58 V100' stroke-width='16' stroke='rgba(36,83,214,0.18)'/>"
                    "<path d='M20 22 H62 Q98 22 98 58 V100 M20 38 H62 Q82 38 82 58 V100'/>"
                    "<line x1='20' y1='18' x2='20' y2='42'/><line x1='78' y1='100' x2='102' y2='100'/>")
    if fam == "Carter et supports":
        bolts = "".join(f"<circle cx='{x}' cy='{y}' r='3.5'/>" for x, y in
                        [(24, 28), (96, 28), (24, 92), (96, 92), (60, 22), (60, 98)])
        return _svg(f"<rect x='14' y='16' width='92' height='88' rx='12' {LIGHT}/>"
                    f"<circle cx='60' cy='60' r='22'/><circle cx='60' cy='60' r='12'/>{bolts}")
    if fam in ("Moteur électrique", "Groupe motopropulseur"):
        fins = "".join(f"<line x1='{x}' y1='30' x2='{x}' y2='90'/>" for x in range(30, 86, 8))
        return _svg(f"<rect x='18' y='26' width='74' height='68' rx='8' {LIGHT}/>{fins}"
                    "<rect x='92' y='52' width='20' height='16'/><rect x='8' y='40' width='10' height='40'/>")
    return _svg(f"<path d='M60 14 L102 36 L102 84 L60 106 L18 84 L18 36 Z' {LIGHT}/>"
                "<path d='M18 36 L60 58 L102 36 M60 58 V106'/>")


# ----------------------------------------------------------------------------
# Lecture du XML
# ----------------------------------------------------------------------------
def _read_fields(elem: ET.Element) -> dict:
    fields = {}
    for child in elem:
        tag = child.tag.replace(NS, "")
        if len(child):
            continue
        if child.attrib and not (child.text or "").strip():
            fields[tag] = child.attrib.get("XID") or child.attrib.get("External", "")
        else:
            fields[tag] = (child.text or "").strip()
    return fields


@st.cache_data
def load_data(raw: bytes) -> dict:
    root = ET.fromstring(raw)
    ps = root.find(NS + "ProductStructure")

    products = {}
    for p in ps.findall(NS + "Product"):
        f = _read_fields(p)
        f["xml_id"] = p.get("id")
        products[p.get("id")] = f

    links = []
    for inst in ps.findall(NS + "ProductInst"):
        f = _read_fields(inst)
        links.append({"xml_id": inst.get("id"), "parent": f.get("Owned"), "child": f.get("Instancing")})

    reps = collections.defaultdict(list)
    for r in ps.findall(NS + "Rep3DAggr"):
        f = _read_fields(r)
        f["xml_id"] = r.get("id")
        reps[f.get("Owned")].append(f)

    return {"products": products, "links": links, "reps": dict(reps)}


def paths_to_root(xid: str, parents: dict) -> list[list[str]]:
    parent_ids = sorted({l["parent"] for l in parents.get(xid, [])})
    if not parent_ids:
        return [[xid]]
    return [path + [xid] for pid in parent_ids for path in paths_to_root(pid, parents)]


def grouped(links: list[dict], key: str) -> list[tuple[str, int, list[str]]]:
    acc = collections.OrderedDict()
    for l in links:
        acc.setdefault(l[key], []).append(l["xml_id"])
    return [(k, len(v), v) for k, v in acc.items()]


def to_float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def kind_of(xid: str, children: dict) -> str:
    """Assemblage : plusieurs sous éléments. Article d'ingénierie : enveloppe autour d'une seule pièce 3D."""
    distinct = {l["child"] for l in children.get(xid, [])}
    if not distinct:
        return "Pièce 3D"
    if len(distinct) == 1 and not children.get(next(iter(distinct))):
        return "Article d'ingénierie"
    return "Assemblage"


# ----------------------------------------------------------------------------
# Affichage
# ----------------------------------------------------------------------------
def table_height(n: int) -> int:
    return 35 * (n + 1) + 3


def src(text: str):
    st.markdown(f"<div class='src'>Source : {text}</div>", unsafe_allow_html=True)


def cell(k: str, v: str | None) -> str:
    if v:
        return f"<div class='cell'><div class='k'>{k}</div><div class='v'>{v}</div></div>"
    return f"<div class='cell'><div class='k'>{k}</div><div class='v muted'>Non renseigné</div></div>"


def label(p: dict) -> str:
    return f"{p.get('Name') or '(sans nom)'}   |   rév. {p.get('RevisionName', '?')}   |   {p.get('ID')}"


def select_part(ref: str):
    st.session_state["plm_selected_part"] = ref


def enum_fr(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " et " + items[-1]


def comparison_table(a: dict, b: dict, name_a: str, name_b: str) -> pd.DataFrame:
    rows = []
    for k, lab in LABELS.items():
        if k in NON_COMPARABLE:
            continue
        va, vb = a.get(k, ""), b.get(k, "")
        rows.append({"Attribut": lab, name_a: va or "(vide)", name_b: vb or "(vide)",
                     "Statut": "Identique" if va == vb else "Différent"})
    return pd.DataFrame(rows)


def show_diff(df: pd.DataFrame):
    n = int((df["Statut"] == "Différent").sum())
    st.write(f"{n} attribut(s) différent(s) sur {len(df)} comparés.")
    if st.toggle("Afficher uniquement les différences", key="fc_only_diff"):
        df = df[df["Statut"] == "Différent"]

    def hl(row):
        s = "background-color: rgba(185,119,14,0.14); font-weight: 600" if row["Statut"] == "Différent" else ""
        return [s] * len(row)

    try:
        st.dataframe(df.style.apply(hl, axis=1), hide_index=True, height=table_height(len(df)), **FULL)
    except Exception:
        st.dataframe(df, hide_index=True, height=table_height(len(df)), **FULL)
    st.caption("Les dates de création et de modification ne sont pas comparées : "
               "elles correspondent à la génération du fichier et n'ont pas de sens métier.")


# ----------------------------------------------------------------------------
# Page
# ----------------------------------------------------------------------------
st.markdown(CSS, unsafe_allow_html=True)

# Indique au navigateur que la page est en français pour bloquer la traduction automatique de Chrome
NO_TRANSLATE_JS = """<script>
const d = window.parent.document;
d.documentElement.lang = "fr";
d.documentElement.setAttribute("translate", "no");
d.documentElement.classList.add("notranslate");
if (!d.querySelector('meta[name="google"]')) {
  const m = d.createElement("meta"); m.name = "google"; m.content = "notranslate"; d.head.appendChild(m);
}
</script>"""
try:
    import streamlit.components.v1 as components
    components.html(NO_TRANSLATE_JS, height=0)
except Exception:
    pass

sh.hero("Comprendre · Fiche composant", "Fiche composant",
        "Identité, propriétés, révisions et position d'une pièce dans la structure du produit.")

_c = sh.ctx()
data = load_data(_c["raw"])
_comp = _c["comp"].set_index("xml_id")
P = data["products"]
children = collections.defaultdict(list)
parents = collections.defaultdict(list)
for l in data["links"]:
    children[l["parent"]].append(l)
    parents[l["child"]].append(l)
by_ref = {p["ID"]: p for p in P.values()}

refs = sorted(by_ref, key=lambda r: (by_ref[r].get("Name", "").lower(), r))
# synchronisation avec la pièce active de l'appli (clé partagée sel_xml)
sh.sync_widget("p3", "plm_selected_part", lambda x: P[x]["ID"] if x in P else None, None)
if st.session_state.get("plm_selected_part") not in by_ref:
    roots = [p["ID"] for x, p in P.items() if x not in parents]
    st.session_state["plm_selected_part"] = roots[0] if roots else refs[0]
st.selectbox("Rechercher une pièce par son nom ou sa référence", refs,
             key="plm_selected_part", format_func=lambda r: label(by_ref[r]))
sh.sync_back("p3", st.session_state["plm_selected_part"], lambda r: by_ref[r]["xml_id"])

part = by_ref[st.session_state["plm_selected_part"]]
xid = part["xml_id"]
kids = children.get(xid, [])
dads = parents.get(xid, [])
mass = to_float(part.get("WCGEquivalentComputedExt.V_WCG_Mass"))
kind = kind_of(xid, children)
fam = family_of(part.get("Name"))

# ---------------------------------------------------------------- Identité
with st.container(border=True):
    col_img, col_info = st.columns([1.1, 4])

    with col_img:
        if fam == "Groupe motopropulseur" and RENDER_PATH.exists():
            st.image(str(RENDER_PATH), **FULL)
            st.markdown("<div class='visual-note'>Rendu du groupe motopropulseur complet. "
                        "Source : présentation du sujet (PLM_ESILV_V2.pptx).</div>", unsafe_allow_html=True)
        else:
            st.markdown(f"<div class='visual'>{family_svg(fam)}</div>"
                        f"<div class='visual-note'>Illustration générique de la famille « {fam} », "
                        f"pas la pièce réelle : l'export ne contient aucune image.</div>", unsafe_allow_html=True)

    with col_info:
        top_left, top_right = st.columns([3, 1.4])
        with top_left:
            mat_txt, mat_fg, mat_bg = MATURITY.get(
                part.get("Maturity"), (part.get("Maturity", "Inconnue"), "#5D6D7E", "rgba(93,109,126,0.12)"))
            note = ""
            if kind == "Article d'ingénierie":
                inner = P[kids[0]["child"]].get("Name")
                note = (f"<div class='kind-note'>Enveloppe de gestion 3DEXPERIENCE autour d'une pièce 3D unique : "
                        f"{inner}.</div>")
            st.markdown(
                f"<div class='part-name'>{part.get('Name') or '(sans nom)'}</div>"
                f"<span class='tag' style='color:{mat_fg};background:{mat_bg}'>{mat_txt}</span>"
                f"<span class='tag' style='color:{NAVY};background:rgba(36,83,214,0.10)'>{kind}</span>"
                f"<span class='tag' style='color:#5D6D7E;background:rgba(93,109,126,0.10)'>{fam}</span>"
                + note
                + (f"<div class='part-desc'>{part['Description']}</div>" if part.get("Description") else ""),
                unsafe_allow_html=True)
        with top_right:
            _target = sh.ei_of(_c["m"], xid)
            if _target in _comp.index:
                sh.nav_button("Simuler un remplacement", "impact", _target, key="btn_impact", primary=True)
            else:
                st.caption("Assemblage : l'analyse d'impact se fait sur ses pièces.")
            sh.nav_button("Voir dans l'explorateur", "explorateur", xid, key="btn_explo")

        st.markdown(
            "<div class='grid'>"
            + cell("Référence", part.get("ID"))
            + cell("Révision courante", f"{part.get('RevisionName', '?')} (index {part.get('RevisionIndex', '?')})")
            + cell("Masse", f"{mass:.3f} kg" if mass is not None else None)
            + cell("Matériau", part.get("Material"))
            + cell("Fournisseur*", _comp.loc[_target, "supplier"] if _target in _comp.index else None)
            + cell("Coût unitaire*", f"{_comp.loc[_target, 'unit_cost_eur']:.2f} €" if _target in _comp.index else None)
            + cell("Score de risque*", f"{_comp.loc[_target, 'risk_score']:.0f}/100" if _target in _comp.index else None)
            + "</div>", unsafe_allow_html=True)
        src(f"Product {xid}. La famille est déduite du nom de la pièce. * Fournisseur, coût et risque : "
            "hypothèses simulées (page Hypothèses), absentes de l'export de l'OEM.")

# ---------------------------------------------------------------- Propriétés
with st.container(border=True):
    st.markdown("<div class='section'>Propriétés</div>", unsafe_allow_html=True)

    def shown(k):
        v = part.get(k) or "(vide)"
        if k == "Maturity" and v in MATURITY:
            v = f"{MATURITY[v][0]} ({v})"
        return v

    main = pd.DataFrame([{"Attribut": lab, "Valeur": shown(k)} for k, lab in MAIN_FIELDS.items()])
    st.dataframe(main, hide_index=True, height=table_height(len(main)), **FULL)
    with st.expander("Attributs techniques"):
        tech = pd.DataFrame([{"Attribut": lab, "Valeur": part.get(k) or "(vide)", "Balise XML": k}
                             for k, lab in TECH_FIELDS.items()])
        st.dataframe(tech, hide_index=True, height=table_height(len(tech)), **FULL)
    for rep in data["reps"].get(xid, []):
        st.write(f"Représentation 3D associée : {rep.get('Name')}, révision {rep.get('RevisionName')}.")
        src(f"Rep3DAggr {rep['xml_id']}")
    src(f"Product {xid}")

# ---------------------------------------------------------------- Révisions
with st.container(border=True):
    st.markdown("<div class='section'>Révisions</div>", unsafe_allow_html=True)
    try:
        n_rev = int(part.get("RevisionIndex", "1"))
    except ValueError:
        n_rev = 1
    current = part.get("RevisionName", "")
    letters = [chr(ord("A") + i) for i in range(n_rev)]
    st.markdown("<span class='rev-line'></span>".join(
        f"<span class='rev{' on' if l == current else ''}'>{l}</span>" for l in letters), unsafe_allow_html=True)
    previous = [l for l in letters if l != current]
    if not previous:
        st.caption("Première révision de la pièce.")
    elif len(previous) == 1:
        st.caption(f"Révision courante : {current}. L'index {n_rev} indique qu'une révision antérieure "
                   f"({previous[0]}) a existé. Elle est déduite de la numérotation de 3DEXPERIENCE "
                   "et ne figure pas dans l'export, qui ne contient que la révision courante.")
    else:
        st.caption(f"Révision courante : {current}. L'index {n_rev} indique que {len(previous)} révisions "
                   f"antérieures ({enum_fr(previous)}) ont existé. Elles sont déduites de la numérotation de "
                   "3DEXPERIENCE et ne figurent pas dans l'export, qui ne contient que la révision courante.")
    src(f"RevisionName et RevisionIndex de Product {xid} (révisions en pointillés : déduites)")

    st.markdown("**Comparer avec une autre pièce**")
    # Pièce proposée par défaut : la pièce 3D d'un article d'ingénierie, ou l'article d'ingénierie d'une pièce 3D
    suggested = []
    if kind == "Article d'ingénierie":
        suggested = [P[kids[0]["child"]]["ID"]]
    elif kind == "Pièce 3D":
        suggested = [P[l["parent"]]["ID"] for l in dads if kind_of(l["parent"], children) == "Article d'ingénierie"]
    same_name = [r for r in refs if by_ref[r].get("Name") == part.get("Name") and r != part["ID"]]
    first = list(dict.fromkeys(suggested + same_name))
    others = first + [r for r in refs if r not in first and r != part["ID"]]
    if st.session_state.get("fc_last_part") != part["ID"] or st.session_state.get("fc_other") not in others:
        st.session_state["fc_other"] = others[0]
        st.session_state["fc_last_part"] = part["ID"]
    if suggested:
        st.caption("La pièce proposée par défaut est l'autre face du même objet (article d'ingénierie et pièce 3D).")
    elif same_name:
        st.caption(f"{len(same_name)} autre(s) pièce(s) portent le même nom et sont proposées en premier.")
    other = by_ref[st.selectbox("Pièce à comparer", others, format_func=lambda r: label(by_ref[r]),
                                key="fc_other", label_visibility="collapsed")]
    show_diff(comparison_table(part, other, f"Pièce affichée (rév. {current})",
                               f"{other.get('Name')} (rév. {other.get('RevisionName')})"))
    src(f"Product {xid} et Product {other['xml_id']}")

# ---------------------------------------------------------------- Position
with st.container(border=True):
    st.markdown("<div class='section'>Position dans le produit</div>", unsafe_allow_html=True)

    paths = paths_to_root(xid, parents)
    for path in paths:
        st.markdown("<span class='sep'>›</span>".join(
            f"<span class='crumb{' on' if n == xid else ''}'>{P[n].get('Name')}</span>" for n in path),
            unsafe_allow_html=True)
    if len(paths) > 1:
        st.caption(f"Cette pièce est utilisée à {len(paths)} endroits de la structure.")
    src("liens Owned et Instancing des ProductInst")

    st.write("")
    col_up, col_down = st.columns(2)
    with col_up:
        st.markdown("**Utilisée dans**")
        if not dads:
            st.caption("Produit racine, il n'est utilisé dans aucun autre assemblage.")
        for pid, qty, inst in grouped(dads, "parent"):
            p = P[pid]
            st.button(f"{p.get('Name')}   (quantité {qty})", key=f"up_{pid}",
                      on_click=select_part, args=(p["ID"],), **FULL)
    with col_down:
        st.markdown("**Contient**")
        if not kids:
            st.caption("Pièce 3D, elle ne contient aucun sous composant.")
        total, complete = 0.0, True
        for cid, qty, inst in grouped(kids, "child"):
            c = P[cid]
            m = to_float(c.get("WCGEquivalentComputedExt.V_WCG_Mass"))
            if m is None:
                complete = False
            else:
                total += m * qty
            st.button(f"{c.get('Name')}   (quantité {qty})", key=f"down_{cid}",
                      on_click=select_part, args=(c["ID"],), **FULL)

    # Contrôle de masse réservé aux vrais assemblages (pour une enveloppe, il serait forcément vrai)
    if kind == "Assemblage" and mass is not None and complete:
        gap = mass - total
        ok = abs(gap) <= max(0.001, 0.005 * mass)
        st.markdown(
            f"<div class='check {'ok' if ok else 'ko'}'>"
            f"{'Masse cohérente' if ok else 'Écart de masse à vérifier'} : masse déclarée {mass:.3f} kg, "
            f"somme des sous composants {total:.3f} kg (écart {gap:+.3f} kg).</div>",
            unsafe_allow_html=True)