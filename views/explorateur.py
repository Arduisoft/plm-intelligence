"""
Page 2 : Explorateur Produit (Nomenclature BOM & Structure Hiérarchique)
Projet PLM - ESILV
"""

from __future__ import annotations

import hashlib
import html
import os
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import shared as sh
import viz3d

# ==============================================================================
# 1. RÉFÉRENTIEL MATÉRIAUX & COULEURS SOBRES
# ==============================================================================

MATERIAL_CATALOG: Dict[str, Dict[str, object]] = {
    "Bearing steel 100Cr6 (AISI 52100)": {
        "category": "Acier roulement (100Cr6)",
    },
    "Alloy steel 42CrMo4 (quenched and tempered)": {
        "category": "Acier allié (42CrMo4)",
    },
    "Aluminium alloy EN AW-6082-T6": {
        "category": "Aluminium usiné (6082)",
    },
    "Aluminium alloy EN AC-AlSi9Cu3(Fe) (HPDC)": {
        "category": "Fonte aluminium (AlSi9Cu3)",
    },
    "Aluminium alloy EN AC-AlSi10Mg (cast)": {
        "category": "Fonte aluminium (AlSi10Mg)",
    },
    "Electrical steel M235-35A (laminated)": {
        "category": "Acier électrique (M235)",
    },
    "Copper Cu-ETP (enamelled wire)": {
        "category": "Cuivre émaillé (Cu-ETP)",
    },
    "Ductile cast iron EN-GJS-600-3": {
        "category": "Fonte ductile (GJS-600)",
    },
    "Case-hardening steel 16MnCr5": {
        "category": "Acier cémentation (16MnCr5)",
    },
    "Case-hardening steel 20MnCr5": {
        "category": "Acier cémentation (20MnCr5)",
    },
    "FKM fluoroelastomer (steel-reinforced)": {
        "category": "Élastomère (FKM)",
    },
    "Structural steel S355J2": {
        "category": "Acier structure (S355J2)",
    },
    "Polyamide PA66-GF30": {
        "category": "Polymère (PA66-GF30)",
    },
    "Steel class 10.9 (zinc-flake coated)": {
        "category": "Visserie classe 10.9",
    },
}

INDUSTRIAL_PALETTE: List[str] = [
    "#1e293b",
    "#0369a1",
    "#334155",
    "#0f766e",
    "#475569",
    "#1d4ed8",
    "#64748b",
    "#0284c7",
]

MATURITY_REV_COLORS: Dict[str, str] = {
    "IN_WORK (Rév. A)": "#64748b",
    "IN_WORK (Rév. B)": "#0284c7",
    "IN_WORK (Rév. C)": "#0f766e",
    "IN_WORK (Rév. D)": "#1e3a8a",
}


def material_category(material_str: str) -> str:
    mat = material_str.strip()
    if not mat:
        return "Sous-ensemble"
    if mat in MATERIAL_CATALOG:
        return str(MATERIAL_CATALOG[mat]["category"])
    low = mat.lower()
    if "copper" in low:
        return f"Cuivre ({mat[:18]})"
    if "alum" in low or "alsi" in low:
        return f"Aluminium ({mat[:18]})"
    if "steel" in low:
        return f"Acier ({mat[:18]})"
    return mat[:25]


# ==============================================================================
# 2. MODÈLE DE DONNÉES & LECTURE DYNAMIQUE DU XML
# ==============================================================================

@dataclass
class PLMProduct:
    xml_id: str
    prd_id: str
    name: str
    description: str
    external_id: str
    revision_name: str
    revision_index: int
    revision_family: str
    maturity: str
    lifecycle: str
    time_created: str
    time_modified: str
    owner: str
    organization: str
    project: str
    security_level: str
    unit_mass_kg: float
    material: str
    supplier: str
    material_category: str
    unit_cost_eur: float
    is_3d_labeled: bool
    rep_3d_ids: List[str] = field(default_factory=list)
    raw_xml: str = ""


@dataclass
class BOMNode:
    node_uid: str
    product: PLMProduct
    parent_uid: Optional[str]
    parent_xml_id: Optional[str]
    level: int
    quantity: int
    instance_xml_ids: List[str]
    instance_codes: List[str]
    top_subassembly: str
    path_names: List[str]
    path_xml_ids: List[str]
    is_redundant_3d: bool = False
    is_direct_3d_anomaly: bool = False
    children: List["BOMNode"] = field(default_factory=list)

    @property
    def total_mass_kg(self) -> float:
        return round(self.product.unit_mass_kg * self.quantity, 3)

    @property
    def total_cost_eur(self) -> float:
        return round(self.product.unit_cost_eur * self.quantity, 2)


def discover_xml_files() -> List[Path]:
    base_dir = Path(__file__).resolve().parent
    found: List[Path] = []
    for p in base_dir.rglob("*.xml"):
        if ".git" not in p.parts and ".venv" not in p.parts:
            found.append(p)
    found.sort(
        key=lambda p: (
            0 if "xpdmxml" in p.name.lower() else (1 if "plm" in p.name.lower() else 2),
            -p.stat().st_mtime,
        )
    )
    return found


def find_default_xml_path() -> Optional[Path]:
    files = discover_xml_files()
    return files[0] if files else None


@st.cache_resource(show_spinner=False)  # classes définies dans une page : non picklables
def _parse_xml_bytes_cached(xml_bytes: bytes, content_hash: str, source_label: str,
                            enrich: Tuple[Tuple[str, str, float], ...] = ()) -> Dict[str, object]:
    """enrich : (xml_id, fournisseur, coût unitaire) issus de plm_core.components (source unique des hypothèses)."""
    enrich_map = {x: (sup, cost) for x, sup, cost in enrich}
    root = ET.fromstring(xml_bytes)

    ns_uri = ""
    if root.tag.startswith("{") and "}" in root.tag:
        ns_uri = root.tag.split("}")[0][1:]
    ns = {"x": ns_uri} if ns_uri else {}

    def qtag(tag_name: str) -> str:
        return f"x:{tag_name}" if ns_uri else tag_name

    header_info: Dict[str, str] = {}
    info_header = root.find(qtag("InfoHeader"), ns)
    if info_header is not None:
        for info in info_header.findall(qtag("Info"), ns):
            header_info[info.attrib.get("name", "")] = info.attrib.get("value", "")

    ps = root.find(qtag("ProductStructure"), ns)
    if ps is None:
        raise ValueError("Balise <ProductStructure> introuvable dans le fichier XML fourni.")

    root_ref = ps.attrib.get("rootRefs", "").strip()

    rep3d_by_owner: Dict[str, List[str]] = defaultdict(list)
    for rep in ps.findall(qtag("Rep3DAggr"), ns):
        owner_ref = (rep.findtext(qtag("Owned"), "", ns) or "").strip()
        rep_id = (rep.findtext(qtag("ID"), "", ns) or "").strip()
        rep_name = (rep.findtext(qtag("Name"), "", ns) or "").strip()
        if owner_ref:
            rep3d_by_owner[owner_ref].append(f"{rep_id} ({rep_name})")

    products: Dict[str, PLMProduct] = {}
    for p_elem in ps.findall(qtag("Product"), ns):
        xml_id = p_elem.attrib.get("id", "").strip()
        if not xml_id:
            continue
        prd_id = (p_elem.findtext(qtag("ID"), "", ns) or "").strip()
        name = (p_elem.findtext(qtag("Name"), "", ns) or xml_id).strip()
        desc = (p_elem.findtext(qtag("Description"), "", ns) or "").strip()
        uid_elem = p_elem.find(qtag("UniqueID"), ns)
        ext_id = uid_elem.attrib.get("External", "") if uid_elem is not None else ""
        rev_name = (p_elem.findtext(qtag("RevisionName"), "A", ns) or "A").strip()
        rev_idx_str = (p_elem.findtext(qtag("RevisionIndex"), "1", ns) or "1").strip()
        rev_idx = int(rev_idx_str) if rev_idx_str.isdigit() else 1
        rev_fam = (p_elem.findtext(qtag("RevisionFamily"), "", ns) or "").strip()
        maturity = (p_elem.findtext(qtag("Maturity"), "IN_WORK", ns) or "IN_WORK").strip()
        lifecycle = (p_elem.findtext(qtag("Lifecycle"), "", ns) or "").strip()
        t_created = (p_elem.findtext(qtag("TimeCreated"), "", ns) or "").strip()
        t_modified = (p_elem.findtext(qtag("TimeModified"), "", ns) or "").strip()
        owner = (p_elem.findtext(qtag("Owner"), "", ns) or "").strip()
        org = (p_elem.findtext(qtag("Organization"), "", ns) or "").strip()
        project = (p_elem.findtext(qtag("Project"), "", ns) or "").strip()
        sec_level = (p_elem.findtext(qtag("SecurityLevel"), "", ns) or "").strip()

        mass_str = (p_elem.findtext(qtag("WCGEquivalentComputedExt.V_WCG_Mass"), "0", ns) or "0").strip()
        try:
            unit_mass = float(mass_str)
        except ValueError:
            unit_mass = 0.0

        material = (p_elem.findtext(qtag("Material"), "", ns) or "").strip()
        mat_category = material_category(material)
        if xml_id in enrich_map:
            supplier, unit_cost = enrich_map[xml_id][0], round(float(enrich_map[xml_id][1]), 2)
        elif not material:
            supplier, unit_cost = "Interne (assemblage)", 0.0
        else:
            supplier, unit_cost = "Porté par l'article EI", 0.0
        is_3d_labeled = ("3D" in name.upper()) or (xml_id in rep3d_by_owner)

        raw_xml_str = ET.tostring(p_elem, encoding="unicode")
        if ns_uri:
            raw_xml_str = raw_xml_str.replace(f' xmlns="{ns_uri}"', "")

        products[xml_id] = PLMProduct(
            xml_id=xml_id,
            prd_id=prd_id,
            name=name,
            description=desc,
            external_id=ext_id,
            revision_name=rev_name,
            revision_index=rev_idx,
            revision_family=rev_fam,
            maturity=maturity,
            lifecycle=lifecycle,
            time_created=t_created,
            time_modified=t_modified,
            owner=owner,
            organization=org,
            project=project,
            security_level=sec_level,
            unit_mass_kg=unit_mass,
            material=material if material else "Non applicable (Assemblage)",
            supplier=supplier,
            material_category=mat_category,
            unit_cost_eur=unit_cost,
            is_3d_labeled=is_3d_labeled,
            rep_3d_ids=rep3d_by_owner.get(xml_id, []),
            raw_xml=raw_xml_str.strip(),
        )

    if not products:
        raise ValueError("Aucun élément <Product> trouvé dans le fichier XML.")

    raw_children_by_parent: Dict[str, Dict[str, Dict[str, List[str]]]] = defaultdict(
        lambda: defaultdict(lambda: {"inst_xml_ids": [], "inst_codes": []})
    )
    where_used: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
    all_child_ids = set()

    for inst in ps.findall(qtag("ProductInst"), ns):
        inst_xml_id = inst.attrib.get("id", "").strip()
        inst_code = (inst.findtext(qtag("ID"), "", ns) or "").strip()
        parent_id = (inst.findtext(qtag("Owned"), "", ns) or "").strip()
        child_id = (inst.findtext(qtag("Instancing"), "", ns) or "").strip()
        if parent_id in products and child_id in products:
            entry = raw_children_by_parent[parent_id][child_id]
            entry["inst_xml_ids"].append(inst_xml_id)
            entry["inst_codes"].append(inst_code)
            all_child_ids.add(child_id)

    for parent_id, child_dict in raw_children_by_parent.items():
        for child_id, info in child_dict.items():
            where_used[child_id].append((parent_id, len(info["inst_xml_ids"])))

    if not root_ref or root_ref not in products:
        roots_candidates = [pid for pid in products if pid not in all_child_ids]
        root_ref = roots_candidates[0] if roots_candidates else next(iter(products.keys()))

    flat_rows: List[Dict[str, object]] = []
    anomalies: List[Dict[str, str]] = []

    def build_node(
        xml_id: str,
        parent_uid: Optional[str],
        parent_xml_id: Optional[str],
        level: int,
        qty: int,
        inst_ids: List[str],
        inst_codes: List[str],
        top_sub: str,
        path_names: List[str],
        path_xml_ids: List[str],
    ) -> BOMNode:
        prod = products[xml_id]
        node_uid = f"{parent_uid}/{xml_id}" if parent_uid else xml_id
        current_top_sub = prod.name if level in (0, 1) else top_sub

        curr_path_names = path_names + [prod.name]
        curr_path_ids = path_xml_ids + [xml_id]

        is_redundant_3d = False
        is_direct_3d_anomaly = False
        if prod.is_3d_labeled and parent_xml_id and parent_xml_id in products:
            parent_prod = products[parent_xml_id]
            parent_children_count = len(raw_children_by_parent.get(parent_xml_id, {}))
            if (
                parent_children_count == 1
                and abs(parent_prod.unit_mass_kg - prod.unit_mass_kg) < 1e-4
                and qty == 1
            ):
                is_redundant_3d = True
            elif parent_children_count > 1:
                is_direct_3d_anomaly = True
                anomalies.append(
                    {
                        "xml_id": prod.xml_id,
                        "prd_id": prod.prd_id,
                        "name": prod.name,
                        "parent_xml_id": parent_prod.xml_id,
                        "parent_name": parent_prod.name,
                        "detail": (
                            f"Pièce 3D ({prod.name}) instanciée directement (x{qty}) sous l'assemblage "
                            f"{parent_prod.name} ({parent_prod.xml_id}) sans article d'ingénierie (ECAR-EI_*) intermédiaire."
                        ),
                    }
                )

        node = BOMNode(
            node_uid=node_uid,
            product=prod,
            parent_uid=parent_uid,
            parent_xml_id=parent_xml_id,
            level=level,
            quantity=qty,
            instance_xml_ids=inst_ids,
            instance_codes=inst_codes,
            top_subassembly=current_top_sub,
            path_names=curr_path_names,
            path_xml_ids=curr_path_ids,
            is_redundant_3d=is_redundant_3d,
            is_direct_3d_anomaly=is_direct_3d_anomaly,
        )

        child_dict = raw_children_by_parent.get(xml_id, {})
        for child_id, info in child_dict.items():
            if child_id in curr_path_ids:
                continue
            child_qty = len(info["inst_xml_ids"])
            child_node = build_node(
                xml_id=child_id,
                parent_uid=node_uid,
                parent_xml_id=xml_id,
                level=level + 1,
                qty=child_qty,
                inst_ids=info["inst_xml_ids"],
                inst_codes=info["inst_codes"],
                top_sub=current_top_sub,
                path_names=curr_path_names,
                path_xml_ids=curr_path_ids,
            )
            node.children.append(child_node)

        if prod.unit_mass_kg == 0.0 and node.children:
            non_red_children = [c for c in node.children if not c.is_redundant_3d]
            prod.unit_mass_kg = round(sum(c.total_mass_kg for c in non_red_children), 3)

        if node.children:
            non_red_children = [c for c in node.children if not c.is_redundant_3d]
            if prod.unit_cost_eur == 0.0 and non_red_children:
                prod.unit_cost_eur = round(sum(c.total_cost_eur for c in non_red_children), 2)

        flat_rows.append(
            {
                "node_uid": node_uid,
                "parent_uid": parent_uid if parent_uid else "",
                "xml_id": prod.xml_id,
                "prd_id": prod.prd_id,
                "name": prod.name,
                "display_label": f"{prod.name} (x{qty})" if qty > 1 else prod.name,
                "level": level,
                "quantity": qty,
                "unit_mass_kg": prod.unit_mass_kg,
                "total_mass_kg": node.total_mass_kg,
                "unit_cost_eur": prod.unit_cost_eur,
                "total_cost_eur": node.total_cost_eur,
                "revision_name": prod.revision_name,
                "maturity": prod.maturity,
                "maturity_and_rev": f"{prod.maturity} (Rév. {prod.revision_name})",
                "material": prod.material,
                "material_category": prod.material_category,
                "supplier": prod.supplier,
                "top_subassembly": current_top_sub,
                "is_3d_part": is_redundant_3d,
                "is_direct_3d_anomaly": is_direct_3d_anomaly,
                "nb_children": len(node.children),
                "path_str": " / ".join(curr_path_names),
                "path_ids_str": "/".join(curr_path_ids),
                "instance_xml_tag": (
                    f"<ProductInst id='{inst_ids[0]}'>"
                    if inst_ids
                    else f"<ProductStructure rootRefs='{root_ref}'>"
                ),
            }
        )
        return node

    root_node = build_node(
        xml_id=root_ref,
        parent_uid=None,
        parent_xml_id=None,
        level=0,
        qty=1,
        inst_ids=[],
        inst_codes=[],
        top_sub=products[root_ref].name,
        path_names=[],
        path_xml_ids=[],
    )

    flat_bom_df = pd.DataFrame(flat_rows)
    level1_subassemblies = [c.product.name for c in root_node.children]

    return {
        "source_label": source_label,
        "content_hash": content_hash[:10],
        "header_info": header_info,
        "root_ref": root_ref,
        "products": products,
        "root_node": root_node,
        "level1_subassemblies": level1_subassemblies,
        "flat_bom_df": flat_bom_df,
        "where_used": where_used,
        "anomalies": anomalies,
        "total_instances_count": len(ps.findall(qtag("ProductInst"), ns)),
        "total_rep3d_count": len(ps.findall(qtag("Rep3DAggr"), ns)),
    }


XML_SOURCE_URL: str = os.environ.get("PLM_XML_URL", "")


def load_plm_data(xml_source: Optional[str | bytes | Path] = None) -> Dict[str, object]:
    if xml_source is None:
        c = sh.ctx()
        enrich = tuple((r.xml_id, r.supplier, float(r.unit_cost_eur)) for r in c["comp"].itertuples())
        return _parse_xml_bytes_cached(c["raw"], c["hash"], c["name"], enrich)
    if xml_source is None:
        url_candidate = st.session_state.get("xml_url", "") or XML_SOURCE_URL
        if url_candidate:
            xml_source = url_candidate
        else:
            default_path = find_default_xml_path()
            if default_path is None:
                raise FileNotFoundError("Aucun fichier XML trouvé dans l'espace de travail.")
            xml_source = default_path

    if isinstance(xml_source, str) and xml_source.startswith(("http://", "https://")):
        req = urllib.request.Request(xml_source, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw_bytes = resp.read()
        label = xml_source.split("/")[-1] or "XML distant"
    elif isinstance(xml_source, (bytes, bytearray)):
        raw_bytes = bytes(xml_source)
        label = "Flux XML"
    elif isinstance(xml_source, Path) or (isinstance(xml_source, str) and os.path.exists(xml_source)):
        p = Path(xml_source)
        raw_bytes = p.read_bytes()
        label = p.name
    else:
        raw_bytes = str(xml_source).encode("utf-8")
        label = "XML"

    h = hashlib.sha256(raw_bytes).hexdigest()
    return _parse_xml_bytes_cached(raw_bytes, h, label)


# ==============================================================================
# 3. STYLE SOBRE INDUSTRIEL & ÉTIQUETTES DE TRAÇABILITÉ XML
# ==============================================================================

def apply_industrial_style() -> None:
    st.markdown(
        """
        <style>
        .block-container {
            padding-top: 1.4rem;
            padding-bottom: 2rem;
        }
        .xml-ref {
            font-family: 'Consolas', 'Courier New', monospace;
            font-size: 0.71rem;
            color: #475569;
            background-color: #f1f5f9;
            border: 1px solid #cbd5e1;
            padding: 1px 5px;
            border-radius: 3px;
            margin-left: 3px;
            white-space: nowrap;
        }
        .kv-list { display: flex; flex-direction: column; }
        .kv {
            display: grid;
            grid-template-columns: minmax(7.5rem, 38%) 1fr;
            column-gap: 12px;
            padding: 6px 0;
            border-bottom: 1px solid #f1f5f9;
            font-size: 0.88rem;
            line-height: 1.35;
        }
        .kv:last-child { border-bottom: none; }
        .kv-label { color: #64748b; }
        .kv-value { color: #0f172a; font-weight: 500; overflow-wrap: anywhere; min-width: 0; }
        .kv-sub { color: #64748b; font-weight: 400; }
        .kv-src {
            display: block;
            margin-top: 2px;
            font-family: 'Consolas', 'Courier New', monospace;
            font-size: 0.68rem;
            color: #94a3b8;
            font-weight: 400;
            overflow-wrap: anywhere;
        }
        .sa-eyebrow {font-size: .7rem; letter-spacing: .12em; text-transform: uppercase; color: #64748B;
            font-weight: 600;}
        .sa-name {font-weight: 650; color: #0B2545; font-size: 1rem; margin: 2px 0 6px; min-height: 2.6em;
            line-height: 1.3;}
        .sa-mass {font-size: 1.7rem; font-weight: 650; color: #2453D6; line-height: 1;}
        .sa-mass span {font-size: .9rem; color: #64748B; font-weight: 500;}
        .sa-meta {font-size: .78rem; color: #64748B; margin: 6px 0 8px;}
        .section-header {
            font-size: 0.78rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: #334155;
            border-bottom: 1px solid #e2e8f0;
            padding-bottom: 4px;
            margin-bottom: 10px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def xml_tag(label: str) -> str:
    escaped = label.replace("<", "&lt;").replace(">", "&gt;")
    return f'<span class="xml-ref" title="Balise source dans XPDMXML_ECAR_PLM.xml">{escaped}</span>'


def kv_row(label: str, value: object, src: str = "", sub: str = "") -> str:
    """Ligne libellé / valeur, avec la balise XML source en petit sous la valeur."""
    v = html.escape(str(value))
    if sub:
        v += f' <span class="kv-sub">{html.escape(sub)}</span>'
    if src:
        v += f'<span class="kv-src" title="Balise source dans XPDMXML_ECAR_PLM.xml">{html.escape(src)}</span>'
    return f'<div class="kv"><div class="kv-label">{html.escape(label)}</div><div class="kv-value">{v}</div></div>'


def kv_list(*rows: str) -> None:
    st.markdown('<div class="kv-list">' + "".join(rows) + "</div>", unsafe_allow_html=True)


def ptag(xid: str) -> str:
    return xml_tag('<Product id="' + xid + '">')


def rtag(xid: str) -> str:
    return xml_tag('<ProductStructure rootRefs="' + xid + '">')


def otag(xid: str) -> str:
    return xml_tag('<ProductInst Owned="' + xid + '">')


def sort_xml_ids(ids: List[str]) -> List[str]:
    def _key(val: str) -> Tuple[int, str]:
        num_part = val.replace("ID_", "")
        return (int(num_part), val) if num_part.isdigit() else (999999, val)

    return sorted(ids, key=_key)


def set_focused_subassembly_callback(xml_id: str, prd_id: str, target_subs: List[str]) -> None:
    st.session_state["selected_product_id"] = xml_id
    sh.select(xml_id)
    st.session_state["selected_prd_code"] = prd_id
    st.session_state["sidebar_subassemblies"] = list(target_subs)


def select_product_callback(xml_id: str, prd_id: str, target_page: Optional[str] = None) -> None:
    st.session_state["selected_product_id"] = xml_id
    st.session_state["selected_prd_code"] = prd_id
    sh.select(xml_id)  # pièce active partagée par toutes les pages
    if target_page == "Page 3":
        sh.request_goto("fiche", xml_id)
    elif target_page == "Page 5":
        sh.request_goto("impact", sh.ei_of(sh.ctx()["m"], xml_id))


# ==============================================================================
# 4. BARRE LATÉRALE COMMUNE (RECHERCHE GLOBALE DIRECTE + FILTRES GLOBAUX)
# ==============================================================================

def render_common_sidebar(plm_data: Optional[Dict[str, object]] = None) -> Dict[str, object]:
    if plm_data is None:
        plm_data = load_plm_data()

    with st.sidebar:
        products: Dict[str, PLMProduct] = plm_data["products"]  # type: ignore
        flat_df: pd.DataFrame = plm_data["flat_bom_df"]  # type: ignore
        root_ref: str = str(plm_data["root_ref"])
        subassemblies: List[str] = list(plm_data["level1_subassemblies"])  # type: ignore

        if (
            "selected_product_id" not in st.session_state
            or st.session_state["selected_product_id"] not in products
        ):
            st.session_state["selected_product_id"] = root_ref

        if "sidebar_subassemblies" not in st.session_state:
            st.session_state["sidebar_subassemblies"] = list(subassemblies)
        else:
            valid_subs = [s for s in st.session_state["sidebar_subassemblies"] if s in subassemblies]
            st.session_state["sidebar_subassemblies"] = valid_subs if valid_subs else list(subassemblies)

        st.markdown("**Recherche dans la nomenclature**")

        search_query = st.text_input(
            "Nom, matériau ou référence",
            value="",
            placeholder="Ex. : stator, cuivre, roulement…",
        )

        q_clean = search_query.strip().lower()
        if q_clean:
            matches = [
                p
                for p in products.values()
                if q_clean in f"{p.xml_id} {p.prd_id} {p.name} {p.material}".lower()
            ]
            st.caption(f"{len(matches)} composant(s) trouvé(s) :")
            for m in matches[:6]:
                st.button(
                    sh.short(m.name),
                    key=f"quick_search_{m.xml_id}",
                    width="stretch",
                    on_click=select_product_callback,
                    args=(m.xml_id, m.prd_id),
                )

        st.divider()
        st.markdown("**Filtres**")

        selected_subassemblies = st.multiselect(
            "Sous-ensemble",
            options=subassemblies,
            key="sidebar_subassemblies",
        )

        all_maturities = sorted(flat_df["maturity"].dropna().unique().tolist())
        selected_maturities = st.multiselect(
            "Maturité",
            options=all_maturities,
            default=all_maturities,
        )

        all_revisions = sorted(flat_df["revision_name"].dropna().unique().tolist())
        selected_revisions = st.multiselect(
            "Révision",
            options=all_revisions,
            default=all_revisions,
        )

        all_suppliers = sorted(flat_df["supplier"].dropna().unique().tolist())
        selected_suppliers = st.multiselect(
            "Fournisseur (hypothèse)",
            options=all_suppliers,
            default=all_suppliers,
        )

        include_3d_nodes = st.checkbox(
            "Inclure les pièces géométriques 3D",
            value=False,
            help="Affiche les représentations géométriques 3D enfants directs d'un article d'ingénierie (EI) de même masse.",
        )

    return {
        "plm_data": plm_data,
        "search_query": search_query.strip(),
        "selected_subassemblies": selected_subassemblies,
        "selected_maturities": selected_maturities,
        "selected_revisions": selected_revisions,
        "selected_suppliers": selected_suppliers,
        "include_3d_nodes": include_3d_nodes,
    }


# ==============================================================================
# 5. FILTRAGE & RENDU ARBRE / TABLEAU / GRAPHIQUE
# ==============================================================================

def node_matches_filters(node: BOMNode, filters: Dict[str, object]) -> bool:
    if node.is_redundant_3d and not filters["include_3d_nodes"] and node.level > 0:
        return False

    if node.level == 0:
        return True

    if node.top_subassembly not in filters["selected_subassemblies"]:  # type: ignore
        return False

    child_match = any(node_matches_filters(c, filters) for c in node.children)

    prod = node.product
    q = str(filters["search_query"]).lower()
    text_ok = (
        q in f"{prod.xml_id} {prod.prd_id} {prod.name} {prod.material} {prod.supplier}".lower()
        if q
        else True
    )
    mat_ok = prod.maturity in filters["selected_maturities"]  # type: ignore
    rev_ok = prod.revision_name in filters["selected_revisions"]  # type: ignore
    sup_ok = prod.supplier in filters["selected_suppliers"]  # type: ignore

    return (text_ok and mat_ok and rev_ok and sup_ok) or child_match


def render_bom_tree_node(
    node: BOMNode,
    filters: Dict[str, object],
    expand_all: bool,
) -> None:
    if not node_matches_filters(node, filters):
        return

    prod = node.product
    visible_children = [c for c in node.children if node_matches_filters(c, filters)]
    qty_str = f" (x{node.quantity})" if node.quantity > 1 else ""
    is_selected = st.session_state.get("selected_product_id") == prod.xml_id
    prefix = "[Actif] " if is_selected else ""

    title = f"{prefix}{sh.short(prod.name)}{qty_str}  ·  {node.total_mass_kg:.2f} kg  ·  rév. {prod.revision_name}"

    if visible_children:
        default_open = expand_all or (node.level <= 1) or bool(filters["search_query"])
        with st.expander(title, expanded=default_open):
            c1, c2 = st.columns([3.8, 1.2])
            with c1:
                st.markdown(
                    f"Masse **{node.total_mass_kg:.3f} kg** · coût estimé* **{node.total_cost_eur:,.2f} €** · "
                    f"{prod.maturity} · {len(visible_children)} élément(s)".replace(",", " ")
                )
            with c2:
                st.button(
                    "Ouvrir",
                    key=f"sel_node_{node.node_uid}",
                    width="stretch",
                    type="primary" if is_selected else "secondary",
                    on_click=select_product_callback,
                    args=(prod.xml_id, prod.prd_id),
                )

            for child in visible_children:
                render_bom_tree_node(child, filters, expand_all)
    else:
        c1, c2 = st.columns([3.8, 1.2])
        with c1:
            anomaly_note = " · montage 3D direct" if node.is_direct_3d_anomaly else ""
            st.markdown(
                f"**{sh.short(prod.name)}**{qty_str}{anomaly_note}  \n"
                f"<span style='color:#64748B;font-size:.85rem'>{node.total_mass_kg:.3f} kg · "
                f"{node.total_cost_eur:.2f} €* · {html.escape(prod.material)} · rév. {prod.revision_name}</span>",
                unsafe_allow_html=True,
            )
        with c2:
            st.button(
                "Ouvrir",
                key=f"sel_leaf_{node.node_uid}",
                width="stretch",
                type="primary" if is_selected else "secondary",
                on_click=select_product_callback,
                args=(prod.xml_id, prod.prd_id),
            )


def filter_flat_dataframe(
    flat_df: pd.DataFrame,
    filters: Dict[str, object],
) -> pd.DataFrame:
    df = flat_df.copy()

    if not filters["include_3d_nodes"]:
        df = df[~df["is_3d_part"]].copy()

    df = df[
        (df["level"] == 0)
        | (df["top_subassembly"].isin(filters["selected_subassemblies"]))  # type: ignore
    ].copy()

    q = str(filters["search_query"]).lower()
    matching_uids = set()
    for _, row in df.iterrows():
        if row["level"] == 0:
            matching_uids.add(row["node_uid"])
            continue
        haystack = f"{row['xml_id']} {row['prd_id']} {row['name']} {row['material']} {row['supplier']}".lower()
        text_ok = (q in haystack) if q else True
        mat_ok = row["maturity"] in filters["selected_maturities"]  # type: ignore
        rev_ok = row["revision_name"] in filters["selected_revisions"]  # type: ignore
        sup_ok = row["supplier"] in filters["selected_suppliers"]  # type: ignore
        if text_ok and mat_ok and rev_ok and sup_ok:
            parts = str(row["node_uid"]).split("/")
            for i in range(1, len(parts) + 1):
                matching_uids.add("/".join(parts[:i]))

    return df[df["node_uid"].isin(matching_uids)].copy()


def build_hierarchy_chart(
    flat_df: pd.DataFrame,
    filters: Dict[str, object],
    chart_type: str,
    size_metric: str,
    color_metric: str,
) -> Tuple[go.Figure, pd.DataFrame]:
    df = filter_flat_dataframe(flat_df, filters)
    parent_uids_present = set(df["parent_uid"].unique()) - {""}

    if size_metric == "Coût estimé (€)":
        val_col = "total_cost_eur"
    elif size_metric == "Nombre de pièces":
        val_col = "quantity"
    else:
        val_col = "total_mass_kg"

    leaf_values: Dict[str, float] = {}
    for _, row in df.iterrows():
        uid = str(row["node_uid"])
        leaf_values[uid] = max(float(row[val_col]), 0.001) if uid not in parent_uids_present else 0.0

    df_sorted = df.sort_values("level", ascending=False)
    computed_totals: Dict[str, float] = dict(leaf_values)
    for _, row in df_sorted.iterrows():
        uid = str(row["node_uid"])
        p_uid = str(row["parent_uid"])
        if p_uid and p_uid in computed_totals:
            computed_totals[p_uid] = round(computed_totals[p_uid] + computed_totals[uid], 4)

    df["chart_value"] = df["node_uid"].map(computed_totals)

    if color_metric == "Maturité et révision":
        color_col = "maturity_and_rev"
        color_map = MATURITY_REV_COLORS
    elif color_metric == "Maturité":
        color_col = "maturity"
        color_map = {
            "IN_WORK": sh.PRIMARY,
            "RELEASED": sh.TEAL,
            "FROZEN": sh.INK,
            "OBSOLETE": sh.RED,
        }
    elif color_metric == "Sous-ensemble":
        color_col = "top_subassembly"
        unique_subs = df["top_subassembly"].unique().tolist()
        color_map = {
            sub: sh.COLORWAY[i % len(sh.COLORWAY)]
            for i, sub in enumerate(unique_subs)
        }
    else:
        color_col = "material_category"
        color_map = {
            cat: sh.COLORWAY[i % len(sh.COLORWAY)]
            for i, cat in enumerate(df["material_category"].unique().tolist())
        }

    hover_template = (
        "<b>%{label}</b><br>"
        "Quantité : x%{customdata[2]}<br>"
        "Masse : %{customdata[3]:.3f} kg (unitaire %{customdata[4]:.3f} kg)<br>"
        "Coût estimé* : %{customdata[5]:.2f} €<br>"
        "Révision %{customdata[6]} · %{customdata[7]}<br>"
        "Matériau : %{customdata[8]}<br>"
        "Fournisseur* : %{customdata[9]}"
        "<extra></extra>"
    )

    custom_data_cols = [
        "xml_id",
        "prd_id",
        "quantity",
        "total_mass_kg",
        "unit_mass_kg",
        "total_cost_eur",
        "revision_name",
        "maturity",
        "material",
        "supplier",
    ]

    if chart_type == "Rectangles (treemap)":
        fig = px.treemap(
            df,
            ids="node_uid",
            names="display_label",
            parents="parent_uid",
            values="chart_value",
            color=color_col,
            color_discrete_map=color_map,
            custom_data=custom_data_cols,
            branchvalues="total",
            maxdepth=4,
        )
    else:
        fig = px.sunburst(
            df,
            ids="node_uid",
            names="display_label",
            parents="parent_uid",
            values="chart_value",
            color=color_col,
            color_discrete_map=color_map,
            custom_data=custom_data_cols,
            branchvalues="total",
            maxdepth=4,
        )

    fig.update_traces(hovertemplate=hover_template)
    fig.update_layout(margin=dict(t=10, l=10, r=10, b=10), height=520)
    return fig, df


# ==============================================================================
# 6. COMPARATEUR SYNTHÉTIQUE DES SOUS-ENSEMBLES & ÉTATS DE RÉVISION
# ==============================================================================

def render_subassembly_comparison(plm_data: Dict[str, object]) -> None:
    root_node: BOMNode = plm_data["root_node"]  # type: ignore
    flat_df: pd.DataFrame = plm_data["flat_bom_df"]  # type: ignore
    root_ref = str(plm_data["root_ref"])

    if not root_node.children:
        return

    total_motor_mass = max(root_node.product.unit_mass_kg, 0.001)
    total_motor_cost = max(root_node.product.unit_cost_eur, 0.01)

    rows = []
    for child in root_node.children:
        p = child.product
        sub_df = flat_df[
            (flat_df["top_subassembly"] == p.name)
            & (~flat_df["is_3d_part"])
            & (flat_df["level"] > 1)
        ]
        rev_counts = Counter(sub_df["revision_name"].tolist())
        rev_summary = ", ".join(f"Rév.{k}: {v}" for k, v in sorted(rev_counts.items()))

        mat_df = sub_df[sub_df["material"] != "Non applicable (Assemblage)"]
        if not mat_df.empty:
            top_mat = (
                mat_df.groupby("material_category")["total_mass_kg"]
                .sum()
                .sort_values(ascending=False)
                .index[0]
            )
        else:
            top_mat = "-"

        rows.append(
            {
                "Sous-ensemble": sh.short(p.name),
                "Révision": f"{p.revision_name} (indice {p.revision_index})",
                "Maturité": p.maturity,
                "Masse (kg)": round(p.unit_mass_kg, 2),
                "Part masse": f"{(p.unit_mass_kg / total_motor_mass) * 100:.1f} %",
                "Coût estimé* (€)": round(p.unit_cost_eur, 2),
                "Part coût": f"{(p.unit_cost_eur / total_motor_cost) * 100:.1f} %",
                "Pièces": len(sub_df),
                "Occurrences": int(sub_df["quantity"].sum()),
                "Versions internes": rev_summary,
                "Matériau principal": top_mat,
            }
        )

    comp_df = pd.DataFrame(rows)
    with st.expander("Comparer les sous-ensembles (masse, coût, versions)", expanded=False):
        st.dataframe(comp_df, width="stretch", hide_index=True)


# ==============================================================================
# 7. FIL D'ARIANE (BREADCRUMB) & FICHE DU COMPOSANT SÉLECTIONNÉ
# ==============================================================================

def render_breadcrumb(plm_data: Dict[str, object], selected_xml_id: str) -> None:
    products: Dict[str, PLMProduct] = plm_data["products"]  # type: ignore
    flat_df: pd.DataFrame = plm_data["flat_bom_df"]  # type: ignore

    occ_df = flat_df[flat_df["xml_id"] == selected_xml_id]
    if occ_df.empty:
        return

    first_path_ids = str(occ_df.iloc[0]["path_ids_str"]).split("/")
    cols = st.columns(len(first_path_ids) + 1)
    with cols[0]:
        st.caption("Chemin")
    for idx, pid in enumerate(first_path_ids):
        if pid in products:
            p = products[pid]
            is_last = idx == len(first_path_ids) - 1
            with cols[idx + 1]:
                st.button(
                    sh.short(p.name),
                    key=f"bc_{idx}_{pid}",
                    width="stretch",
                    type="primary" if is_last else "secondary",
                    on_click=select_product_callback,
                    args=(pid, p.prd_id),
                )


def render_selected_component_panel(
    plm_data: Dict[str, object],
    selected_xml_id: str,
) -> None:
    products: Dict[str, PLMProduct] = plm_data["products"]  # type: ignore
    flat_df: pd.DataFrame = plm_data["flat_bom_df"]  # type: ignore
    where_used: Dict[str, List[Tuple[str, int]]] = plm_data["where_used"]  # type: ignore
    root_ref: str = str(plm_data["root_ref"])

    if selected_xml_id not in products:
        selected_xml_id = root_ref
    prod = products[selected_xml_id]

    occ_df = flat_df[flat_df["xml_id"] == selected_xml_id]
    total_qty = int(occ_df["quantity"].sum()) if not occ_df.empty else 1
    paths = occ_df["path_str"].tolist() if not occ_df.empty else [prod.name]
    inst_tags = occ_df["instance_xml_tag"].unique().tolist() if not occ_df.empty else []

    st.divider()
    st.subheader(f"{sh.short(prod.name)}")

    render_breadcrumb(plm_data, selected_xml_id)

    col1, col2, col3 = st.columns([1.25, 1.45, 1.1])

    with col1:
        sh.section("Identité et cycle de vie")
        kv_list(
            kv_row("Désignation", prod.name),
            kv_row("Référence PLM", prod.prd_id),
            kv_row("Version", f"Rév. {prod.revision_name}", sub=f"(indice {prod.revision_index})"),
            kv_row("Maturité", prod.maturity),
            kv_row("Cycle de vie", prod.lifecycle),
            kv_row("Dernière modif.", prod.time_modified[:10]),
        )

    with col2:
        sh.section("Ingénierie et nomenclature")
        rep3d_str = f"{len(prod.rep_3d_ids)} associée(s)" if prod.rep_3d_ids else "aucune sur ce nœud"
        rows = [
            kv_row("Masse unitaire", f"{prod.unit_mass_kg:.3f} kg"),
            kv_row("Occurrences", f"×{total_qty}", sub=f"(cumul : {prod.unit_mass_kg * total_qty:.3f} kg)"),
            kv_row("Matériau", prod.material),
            kv_row("Fournisseur*", prod.supplier),
            kv_row("Coût estimé*", f"{prod.unit_cost_eur:.2f} €", sub=f"(cumul : {prod.unit_cost_eur * total_qty:.2f} €)"),
            kv_row("Représentation 3D", rep3d_str),
            kv_row("Propriétaire / projet", f"{prod.owner} / {prod.project}"),
        ]
        if prod.description:
            rows.append(kv_row("Description", prod.description))
        kv_list(*rows)

    with col3:
        sh.section("Position et actions")
        parents_info = where_used.get(selected_xml_id, [])
        if parents_info:
            p_list = ", ".join([f"{sh.short(products[pid].name)} (×{q})" for pid, q in parents_info])
            pos_row = kv_row("Utilisé dans", p_list)
        else:
            pos_row = kv_row("Position", "Produit racine")
        kv_list(pos_row, kv_row("Chemin complet", " › ".join(sh.short(x) for x in str(paths[0]).split(" / "))))
        st.write("")

        st.button(
            "Ouvrir la fiche complète",
            key=f"goto_p3_{prod.xml_id}",
            width="stretch",
            type="primary",
            on_click=select_product_callback,
            args=(prod.xml_id, prod.prd_id, "Page 3"),
        )
        st.button(
            "Simuler un remplacement",
            key=f"goto_p5_{prod.xml_id}",
            width="stretch",
            on_click=select_product_callback,
            args=(prod.xml_id, prod.prd_id, "Page 5"),
        )

    with st.expander("Voir le XML source de cette pièce"):
        st.code(prod.raw_xml, language="xml")


# ==============================================================================
# 8. RENDU DE LA PAGE 2
# ==============================================================================

def render_page2(
    plm_data: Optional[Dict[str, object]] = None,
    filters: Optional[Dict[str, object]] = None,
) -> None:
    apply_industrial_style()

    if filters is None:
        filters = render_common_sidebar(plm_data)
    if plm_data is None:
        plm_data = filters["plm_data"]  # type: ignore

    products: Dict[str, PLMProduct] = plm_data["products"]  # type: ignore
    root_node: BOMNode = plm_data["root_node"]  # type: ignore
    flat_df: pd.DataFrame = plm_data["flat_bom_df"]  # type: ignore
    root_ref: str = str(plm_data["root_ref"])
    all_subs: List[str] = list(plm_data["level1_subassemblies"])  # type: ignore
    anomalies: List[Dict[str, str]] = plm_data["anomalies"]  # type: ignore

    if (
        "selected_product_id" not in st.session_state
        or st.session_state["selected_product_id"] not in products
    ):
        st.session_state["selected_product_id"] = root_ref

    sh.hero("Comprendre · Explorateur", "Nomenclature du produit",
            "Arborescence, quantités, masses et versions de chaque sous-ensemble. "
            "Cliquez sur une pièce pour afficher sa fiche, Léo la suit automatiquement.")
    st.subheader("Principaux sous-ensembles")

    top_nodes = [root_node] + root_node.children
    m_cols = st.columns(min(len(top_nodes), 5))
    active_subs: List[str] = filters["selected_subassemblies"]  # type: ignore

    for idx, node in enumerate(top_nodes[: len(m_cols)]):
        p = node.product
        sub_df = (
            flat_df[(flat_df["top_subassembly"] == p.name) & (~flat_df["is_3d_part"])]
            if node.level > 0
            else flat_df[~flat_df["is_3d_part"]]
        )
        nb_insts = int(sub_df["quantity"].sum())
        is_focused = (
            (node.level == 0 and len(active_subs) == len(all_subs))
            or (node.level > 0 and active_subs == [p.name])
        )
        target_subs_for_btn = list(all_subs) if node.level == 0 else [p.name]

        with m_cols[idx]:
            with st.container(border=True):
                eyebrow = "Produit complet" if node.level == 0 else "Sous-ensemble"
                st.markdown(
                    f"<div class='sa-eyebrow'>{eyebrow}</div>"
                    f"<div class='sa-name'>{html.escape(sh.short(p.name))}</div>"
                    f"<div class='sa-mass'>{p.unit_mass_kg:.1f}<span> kg</span></div>"
                    f"<div class='sa-meta'>{nb_insts} pièces · {p.unit_cost_eur:,.0f} €* · rév. {p.revision_name}</div>"
                    .replace(",", " "),
                    unsafe_allow_html=True,
                )
                btn_label = "Tout afficher" if node.level == 0 else "Filtrer sur celui-ci"
                st.button(
                    btn_label,
                    key=f"focus_sub_{p.xml_id}",
                    width="stretch",
                    type="primary" if is_focused else "secondary",
                    on_click=set_focused_subassembly_callback,
                    args=(p.xml_id, p.prd_id, target_subs_for_btn),
                )

    render_subassembly_comparison(plm_data)

    st.write("")

    col_left, col_right = st.columns([1.05, 1.15], gap="large")

    filtered_table_df = filter_flat_dataframe(flat_df, filters)

    with col_left:
        st.subheader("Arborescence")
        c_mode, c_opt, c_dl = st.columns([1.3, 0.9, 1.1])
        with c_mode:
            left_view_mode = st.radio(
                "Mode d'affichage",
                options=["Arborescence", "Tableau BOM"],
                horizontal=True,
                label_visibility="collapsed",
            )
        with c_opt:
            expand_all = (
                st.checkbox("Tout déplier", value=False)
                if left_view_mode == "Arborescence"
                else False
            )
        with c_dl:
            csv_bytes = (
                filtered_table_df[
                    [
                        "xml_id",
                        "prd_id",
                        "name",
                        "top_subassembly",
                        "level",
                        "quantity",
                        "unit_mass_kg",
                        "total_mass_kg",
                        "total_cost_eur",
                        "revision_name",
                        "maturity",
                        "material",
                        "supplier",
                    ]
                ]
                .to_csv(index=False)
                .encode("utf-8-sig")
            )
            st.download_button(
                label="Exporter CSV",
                data=csv_bytes,
                file_name="nomenclature_bom_filtree.csv",
                mime="text/csv",
                width="stretch",
            )

        if left_view_mode == "Arborescence":
            with st.container(height=540, border=True):
                render_bom_tree_node(root_node, filters, expand_all=expand_all)
        else:
            table_display = filtered_table_df.assign(name=filtered_table_df["name"].map(sh.short))[
                ["name", "level", "quantity", "total_mass_kg", "total_cost_eur", "revision_name", "maturity",
                 "material", "supplier"]
            ].rename(
                columns={
                    "name": "Pièce",
                    "level": "Niveau",
                    "quantity": "Qté",
                    "total_mass_kg": "Masse (kg)",
                    "total_cost_eur": "Coût* (€)",
                    "revision_name": "Rév.",
                    "maturity": "Maturité",
                    "material": "Matériau",
                    "supplier": "Fournisseur*",
                }
            )
            st.caption("Cliquez sur une ligne pour ouvrir la fiche de la pièce.")
            table_event = st.dataframe(
                table_display,
                width="stretch",
                hide_index=True,
                height=500,
                on_select="rerun",
                selection_mode="single-row",
            )
            if (
                table_event
                and hasattr(table_event, "selection")
                and table_event.selection
                and table_event.selection.get("rows")
            ):
                row_idx = table_event.selection["rows"][0]
                clicked_id = str(filtered_table_df.iloc[row_idx]["xml_id"])
                if clicked_id in products and st.session_state.get("selected_product_id") != clicked_id:
                    st.session_state["selected_product_id"] = clicked_id
                    st.session_state["selected_prd_code"] = products[clicked_id].prd_id
                    st.rerun()

        if anomalies:
            with st.expander(f"Remarques structurelles ({len(anomalies)})"):
                for a in anomalies:
                    st.caption(f"- {sh.short(products[a['xml_id']].name) if a['xml_id'] in products else a['xml_id']} : "
                               f"{a['detail']}")

    with col_right:
        st.subheader("Visualisation")
        g1, g2, g3 = st.columns(3)
        with g1:
            chart_type = st.selectbox(
                "Type de graphique",
                ["Anneaux (sunburst)", "Rectangles (treemap)", "Structure 3D"],
                index=0,
            )
        with g2:
            size_metric = st.selectbox(
                "Taille selon",
                ["Masse (kg)", "Coût estimé (€)", "Nombre de pièces"],
                index=0,
            )
        with g3:
            color_metric = st.selectbox(
                "Couleur selon",
                ["Sous-ensemble", "Matériau", "Maturité et révision", "Maturité"],
                index=0,
            )

        fig, chart_df = build_hierarchy_chart(
            flat_df=flat_df,
            filters=filters,
            chart_type=chart_type,
            size_metric=size_metric,
            color_metric=color_metric,
        )
        if chart_type == "Structure 3D":
            st.plotly_chart(
                viz3d.bom_3d(sh.ctx()["m"], include_3d=bool(filters["include_3d_nodes"]),
                             highlight=st.session_state.get("selected_product_id"), height=520),
                width="stretch", key="bom_3d_chart",
            )
            st.caption("Taille = masse, couleur = sous-ensemble, anneau orange = pièce sélectionnée. "
                       "Les filtres de la barre latérale ne s'appliquent pas à cette vue.")

        selection_event = None if chart_type == "Structure 3D" else st.plotly_chart(
            fig,
            width="stretch",
            on_select="rerun",
            selection_mode="points",
            key="bom_hierarchy_chart",
        )

        if (
            selection_event
            and hasattr(selection_event, "selection")
            and selection_event.selection
            and selection_event.selection.get("points")
        ):
            pt = selection_event.selection["points"][0]
            custom = pt.get("customdata")
            if custom and len(custom) > 0:
                clicked_xml_id = str(custom[0])
                if (
                    clicked_xml_id in products
                    and st.session_state.get("selected_product_id") != clicked_xml_id
                ):
                    st.session_state["selected_product_id"] = clicked_xml_id
                    st.session_state["selected_prd_code"] = products[clicked_xml_id].prd_id

        available_chart_ids = chart_df["xml_id"].drop_duplicates().tolist()
        if available_chart_ids:
            curr_sel = st.session_state.get("selected_product_id", root_ref)
            if curr_sel not in available_chart_ids:
                curr_sel = available_chart_ids[0]
            chosen_from_chart = st.selectbox(
                "Pièce sélectionnée",
                options=available_chart_ids,
                index=available_chart_ids.index(curr_sel),
                format_func=lambda xid: (
                    f"{sh.short(products[xid].name)} · rév. {products[xid].revision_name} · "
                    f"{products[xid].unit_mass_kg:.3f} kg"
                ),
            )
            if chosen_from_chart != st.session_state.get("selected_product_id"):
                st.session_state["selected_product_id"] = chosen_from_chart
                st.session_state["selected_prd_code"] = products[chosen_from_chart].prd_id
                st.rerun()

    render_selected_component_panel(
        plm_data=plm_data,
        selected_xml_id=st.session_state.get("selected_product_id", root_ref),
    )


# ------------------------------------------------------------------ exécution dans l'appli commune
_plm = load_plm_data()
_sel = st.session_state.get(sh.SEL)
if st.session_state.get("_seen_p2") != _sel and _sel in _plm["products"]:
    st.session_state["selected_product_id"] = _sel
st.session_state["_seen_p2"] = _sel
_filters = render_common_sidebar(_plm)
render_page2(plm_data=_plm, filters=_filters)
if st.session_state.get("selected_product_id") not in (None, _sel):
    sh.select(st.session_state["selected_product_id"])
st.session_state["_seen_p2"] = st.session_state.get(sh.SEL)
