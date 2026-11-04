"""
plm_core.py — Moteur d'analyse PLM (parsing XPDMXML 3DEXPERIENCE, enrichissement,
risques, analyse d'impact, recherche de sources).

Toutes les fonctions renvoient des DataFrames ou des dicts traçables : chaque ligne
porte l'identifiant XML (`xml_id`, ex. ID_12) et l'ID PLM (`plm_id`, ex.
prd-12535825-00000052) de l'objet d'origine, pour pouvoir citer la source.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

NS = "{http://www.3ds.com/xsd/XPDMXML}"
DATA_DIR = Path(__file__).parent / "data"
DEFAULT_XML = DATA_DIR / "XPDMXML_ECAR_PLM.xml"


# --------------------------------------------------------------------------- #
# 1. Parsing
# --------------------------------------------------------------------------- #
def _t(e, tag):
    x = e.find(NS + tag)
    return (x.text or "").strip() if x is not None and x.text else ""


@dataclass
class PLMModel:
    header: dict
    products: pd.DataFrame          # 1 ligne par référence (Product)
    instances: pd.DataFrame         # 1 ligne par occurrence (ProductInst)
    reps: pd.DataFrame              # représentations 3D
    children: dict = field(default_factory=dict)   # parent xml_id -> [(child xml_id, inst xml_id)]
    parents: dict = field(default_factory=dict)    # child xml_id -> [parent xml_id]
    root: str = "ID_0"


def load_model(path: str | Path = DEFAULT_XML) -> PLMModel:
    root = ET.parse(path).getroot()
    header = {i.get("name"): i.get("value") for i in root.iter(NS + "Info")}
    ps = root.find(NS + "ProductStructure")

    prods = []
    for p in ps.findall(NS + "Product"):
        mass = _t(p, "WCGEquivalentComputedExt.V_WCG_Mass")
        prods.append(dict(
            xml_id=p.get("id"), plm_id=_t(p, "ID"), name=_t(p, "Name"),
            description=_t(p, "Description"), revision=_t(p, "RevisionName"),
            rev_index=int(_t(p, "RevisionIndex") or 0), maturity=_t(p, "Maturity"),
            lifecycle=_t(p, "Lifecycle"), owner=_t(p, "Owner"), project=_t(p, "Project"),
            created=_t(p, "TimeCreated"), modified=_t(p, "TimeModified"),
            mass_kg=float(mass) if mass else None, material=_t(p, "Material"),
            revision_family=_t(p, "RevisionFamily"),
        ))
    products = pd.DataFrame(prods).set_index("xml_id", drop=False)

    insts = []
    for i in ps.findall(NS + "ProductInst"):
        insts.append(dict(
            inst_id=i.get("id"), inst_name=_t(i, "ID"), parent=_t(i, "Owned"),
            child=_t(i, "Instancing"), order=float(_t(i, "ChildOrder") or 0),
            translation=_t(i.find(NS + "RelativeTransformation3D"), "Translation3D")
            if i.find(NS + "RelativeTransformation3D") is not None else "",
        ))
    instances = pd.DataFrame(insts)
    # occurrences orphelines (référence absente de l'export) : écartées, mais conservées pour le contrôle
    known = set(products.index)
    dangling = instances[~instances.parent.isin(known) | ~instances.child.isin(known)]
    instances = instances.drop(dangling.index).reset_index(drop=True)
    header["_dangling_instances"] = len(dangling)

    reps = pd.DataFrame([dict(
        rep_id=r.get("id"), plm_id=_t(r, "ID"), name=_t(r, "Name"),
        revision=_t(r, "RevisionName"), owner_product=_t(r, "Owned"),
    ) for r in ps.findall(NS + "Rep3DAggr")])

    children, parents = defaultdict(list), defaultdict(list)
    for _, r in instances.sort_values("order").iterrows():
        children[r.parent].append((r.child, r.inst_id))
        parents[r.child].append(r.parent)

    m = PLMModel(header, products, instances, reps, dict(children), dict(parents),
                 header.get("SourceObjectRef", "ID_0"))
    _classify(m)
    return m


# --------------------------------------------------------------------------- #
# 2. Classification : niveau, type de nœud, famille fonctionnelle
# --------------------------------------------------------------------------- #
FAMILY_RULES = [  # (regex sur le nom, famille, sous-ensemble fonctionnel)
    (r"BEA\d", "Roulement", "Guidage"),
    (r"GEAR|BEVEL|PIN\b|_PIN|ISH\b|_ISH|SIDE SHAFT|SideShaft", "Engrenage / arbre", "Transmission"),
    (r"HSH\b|_HSH$|_HSH\b", "Arbre rotor", "Rotor"),
    (r"Stator Core|STATOR Core", "Tôles stator", "Stator"),
    (r"Coil", "Bobinage cuivre", "Stator"),
    (r"COOLING|Cooling", "Refroidissement", "Thermique"),
    (r"DIFFERENTIAL_CASTING|DifferentialCasing", "Carter différentiel", "Transmission"),
    (r"housing|support|plate bearing|EI_motor$|3D_motor$", "Carter / structure", "Structure"),
    (r"[Ss]crew|SCREW", "Visserie", "Fixation"),
    (r"joint", "Joint d'étanchéité", "Étanchéité"),
    (r"pipe", "Tuyauterie", "Thermique"),
]

# Criticité fonctionnelle (1–5) — hypothèse d'ingénierie, modifiable dans l'app
FAMILY_CRITICALITY = {
    "Tôles stator": 5, "Bobinage cuivre": 5, "Arbre rotor": 5, "Roulement": 4,
    "Engrenage / arbre": 4, "Carter différentiel": 3, "Carter / structure": 3,
    "Refroidissement": 4, "Joint d'étanchéité": 3, "Tuyauterie": 2, "Visserie": 2,
    "Assemblage": 3,
}


def _family(name: str) -> tuple[str, str]:
    for rx, fam, func in FAMILY_RULES:
        if re.search(rx, name):
            return fam, func
    return "Assemblage", "Assemblage"


def _classify(m: PLMModel):
    p = m.products
    p["node_type"] = [
        "Assemblage" if pid in m.children and any(c for c, _ in m.children[pid]
                                                   if m.products.loc[c, "name"].startswith("ECAR-EI"))
        else ("Pièce (EI)" if n.startswith("ECAR-EI") else "Pièce 3D")
        for pid, n in zip(p.xml_id, p.name)
    ]
    fams = [_family(n) for n in p.name]
    p["family"] = [f if t != "Assemblage" else "Assemblage" for (f, _), t in zip(fams, p.node_type)]
    p["function"] = [g if t != "Assemblage" else "Assemblage" for (_, g), t in zip(fams, p.node_type)]
    p["has_3d_rep"] = p.xml_id.isin(set(m.reps.owner_product))
    p["display_name"] = (p.name.str.replace(r"^ECAR-(EI|3D)[_ ]*", "", regex=True)
                         .str.strip() + " (rév. " + p.revision + ")")


# --------------------------------------------------------------------------- #
# 3. Nomenclature à plat (BOM explosée), chemins et sous-ensemble de rattachement
# --------------------------------------------------------------------------- #
def flat_bom(m: PLMModel) -> pd.DataFrame:
    rows = []

    # quantité : chaque instance compte 1 ; on agrège ensuite
    def walk2(pid, level, path, top):
        p = m.products.loc[pid]
        rows.append(dict(xml_id=pid, plm_id=p.plm_id, name=p["name"], level=level,
                         path=" / ".join(path + [p["name"]]),
                         subassembly=top if top else p["name"],
                         parent_name=path[-1] if path else ""))
        for c, _ in m.children.get(pid, []):
            walk2(c, level + 1, path + [p["name"]], top if top else (p["name"] if level == 1 else None))
    walk2(m.root, 0, [], None)
    df = pd.DataFrame(rows)
    df.loc[df.level == 0, "subassembly"] = "(racine)"
    return df


def usage(m: PLMModel) -> pd.DataFrame:
    """Quantité totale de chaque référence dans le produit racine (cas d'emploi)."""
    fb = flat_bom(m)
    u = fb.groupby("xml_id").agg(qty=("path", "count"),
                                 subassemblies=("subassembly", lambda s: ", ".join(sorted(set(s)))),
                                 paths=("path", list)).reset_index()
    return u


# --------------------------------------------------------------------------- #
# 4. Enrichissement simulé (hypothèses explicites, à remplacer par données achats)
# --------------------------------------------------------------------------- #
MATERIALS = pd.DataFrame([
    # matériau, €/kg brut, kgCO2e/kg, risque appro (1-5), volatilité prix (1-5), remarque
    ("Copper Cu-ETP (enamelled wire)", 10.5, 4.0, 4, 5, "Cuivre : cours LME très volatil, forte demande e-mobilité"),
    ("Electrical steel M235-35A (laminated)", 3.0, 2.6, 4, 3, "Acier électrique NO : peu de producteurs qualifiés"),
    ("Case-hardening steel 20MnCr5", 1.6, 2.0, 2, 2, "Acier de cémentation courant"),
    ("Case-hardening steel 16MnCr5", 1.6, 2.0, 2, 2, "Acier de cémentation courant"),
    ("Bearing steel 100Cr6 (AISI 52100)", 1.9, 2.1, 3, 2, "Roulements : marché concentré"),
    ("Alloy steel 42CrMo4 (quenched and tempered)", 1.8, 2.1, 2, 2, "Acier allié courant"),
    ("Steel class 10.9 (zinc-flake coated)", 2.0, 2.3, 2, 2, "Visserie standard"),
    ("Aluminium alloy EN AC-AlSi9Cu3(Fe) (HPDC)", 2.6, 6.5, 3, 4, "Aluminium : prix énergie + sanctions"),
    ("Aluminium alloy EN AC-AlSi10Mg (cast)", 2.8, 7.5, 3, 4, "Aluminium primaire"),
    ("Aluminium alloy EN AW-6082-T6", 3.2, 8.5, 3, 4, "Aluminium corroyé"),
    ("Ductile cast iron EN-GJS-600-3", 1.2, 1.8, 2, 2, "Fonderie fonte"),
    ("Structural steel S355J2", 1.0, 1.9, 1, 2, "Acier de construction"),
    ("FKM fluoroelastomer (steel-reinforced)", 40.0, 10.0, 4, 3, "Fluoropolymère : restriction PFAS UE en préparation"),
    ("Polyamide PA66-GF30", 4.5, 7.0, 3, 3, "PA66 : historique de pénuries"),
    # matériaux de substitution (non présents dans l'export, proposés pour les scénarios)
    ("Aluminium winding wire (enamelled)", 3.5, 8.0, 2, 4, "Substitut du cuivre : section ×1,6 pour même résistance"),
    ("HNBR elastomer (steel-reinforced)", 18.0, 6.0, 2, 2, "Substitut sans PFAS au FKM (tenue thermique moindre)"),
    ("Aluminium alloy EN AC-AlSi9Cu3(Fe) recycled (HPDC)", 2.4, 2.0, 2, 3, "Alu secondaire bas carbone"),
], columns=["material", "eur_per_kg", "co2_per_kg", "supply_risk", "price_volatility", "note"]).set_index("material")

# multiplicateur coût pièce / coût matière brute, par famille (hypothèse)
PROCESS_FACTOR = {
    "Engrenage / arbre": 6.0, "Arbre rotor": 5.0, "Roulement": 8.0, "Carter / structure": 3.0,
    "Tôles stator": 2.5, "Bobinage cuivre": 2.0, "Visserie": 4.0, "Carter différentiel": 3.0,
    "Joint d'étanchéité": 5.0, "Tuyauterie": 3.0, "Refroidissement": 4.0,
}

# Fournisseurs fictifs par famille
SUPPLIERS = pd.DataFrame([
    ("SUP-01", "RollTech GmbH", "Allemagne", ["Roulement"], 1, 4.2, 10),
    ("SUP-02", "PrecisionGear SAS", "France", ["Engrenage / arbre", "Arbre rotor"], 2, 4.0, 12),
    ("SUP-03", "AluCast Iberia", "Espagne", ["Carter / structure", "Refroidissement"], 1, 3.4, 14),
    ("SUP-04", "LamiCore Steel AB", "Suède", ["Tôles stator"], 1, 4.5, 16),
    ("SUP-05", "WindCo Electric", "Chine", ["Bobinage cuivre"], 1, 3.6, 20),
    ("SUP-06", "FixPro SpA", "Italie", ["Visserie"], 3, 4.1, 4),
    ("SUP-07", "SealFlex GmbH", "Allemagne", ["Joint d'étanchéité"], 1, 3.9, 8),
    ("SUP-08", "PolyForm Sp. z o.o.", "Pologne", ["Tuyauterie"], 2, 3.8, 6),
    ("SUP-09", "Fonderie du Nord", "France", ["Carter différentiel"], 2, 3.2, 10),
], columns=["supplier_id", "supplier", "country", "families", "qualified_sources", "rating", "lead_time_wk"])

COUNTRY_RISK = {"Chine": 4, "Pologne": 2, "Espagne": 2, "Italie": 2, "France": 1,
                "Allemagne": 1, "Suède": 1}


def supplier_for(family: str) -> dict:
    for _, s in SUPPLIERS.iterrows():
        if family in s.families:
            return s.to_dict()
    return {}


# --------------------------------------------------------------------------- #
# 5. Table des composants enrichie + score de risque
# --------------------------------------------------------------------------- #
RISK_WEIGHTS = {  # somme = 1
    "criticité fonctionnelle": 0.20, "risque appro matière": 0.20, "source unique": 0.15,
    "risque pays": 0.10, "volatilité prix": 0.10, "instabilité design (révisions)": 0.10,
    "maturité non libérée": 0.05, "qualité de données": 0.10,
}


def components(m: PLMModel, weights: dict | None = None, mat: pd.DataFrame | None = None) -> pd.DataFrame:
    """Une ligne par pièce EI (référence achetée/fabriquée) avec coût, CO2, fournisseur, risque."""
    weights = weights or RISK_WEIGHTS
    mat = MATERIALS if mat is None else mat
    p = m.products
    u = usage(m).set_index("xml_id")
    dq = data_quality(m)
    dq_ids = set(x for _, r in dq.iterrows() if r.severity in ("Élevée", "Moyenne")
                 and r.category != "Cycle de vie" for x in r.xml_ids)

    rows = []
    for pid, r in p.iterrows():
        # on garde les pièces "EI" + les pièces 3D instanciées directement (sans enveloppe EI)
        direct3d = r.node_type == "Pièce 3D" and any(
            m.products.loc[par, "node_type"] == "Assemblage" for par in m.parents.get(pid, []))
        if r.node_type != "Pièce (EI)" and not direct3d:
            continue
        fam = r.family
        mt = mat.loc[r.material] if r.material in mat.index else None
        qty = int(u.loc[pid, "qty"]) if pid in u.index else 0
        unit_cost = (r.mass_kg or 0) * (mt.eur_per_kg if mt is not None else 0) * PROCESS_FACTOR.get(fam, 3)
        sup = supplier_for(fam)
        f = {
            "criticité fonctionnelle": FAMILY_CRITICALITY.get(fam, 3),
            "risque appro matière": mt.supply_risk if mt is not None else 3,
            "source unique": 5 if sup.get("qualified_sources", 2) == 1 else (3 if sup.get("qualified_sources") == 2 else 1),
            "risque pays": COUNTRY_RISK.get(sup.get("country"), 2) + 1,
            "volatilité prix": mt.price_volatility if mt is not None else 3,
            "instabilité design (révisions)": min(5, r.rev_index + 1),
            "maturité non libérée": 5 if r.maturity != "RELEASED" else 1,
            "qualité de données": 5 if pid in dq_ids else 1,
        }
        score = sum(weights[k] * (v - 1) / 4 for k, v in f.items()) / sum(weights.values()) * 100
        rows.append(dict(
            xml_id=pid, plm_id=r.plm_id, name=r["name"], display_name=r.display_name,
            revision=r.revision, rev_index=r.rev_index, maturity=r.maturity,
            family=fam, function=r.function, material=r.material or "(non renseigné)",
            mass_kg=r.mass_kg, qty=qty, mass_total_kg=(r.mass_kg or 0) * qty,
            subassembly=u.loc[pid, "subassemblies"] if pid in u.index else "",
            unit_cost_eur=round(unit_cost, 2), total_cost_eur=round(unit_cost * qty, 2),
            co2_kg=round((r.mass_kg or 0) * qty * (mt.co2_per_kg if mt is not None else 0), 2),
            supplier=sup.get("supplier", "?"), supplier_id=sup.get("supplier_id", ""),
            country=sup.get("country", ""), lead_time_wk=sup.get("lead_time_wk"),
            single_source=sup.get("qualified_sources", 2) == 1,
            risk_score=round(score, 1), **{f"f_{k}": v for k, v in f.items()},
        ))
    df = pd.DataFrame(rows).sort_values("risk_score", ascending=False).reset_index(drop=True)
    df["risk_level"] = pd.cut(df.risk_score, [-1, 40, 60, 101], labels=["Faible", "Moyen", "Élevé"])
    return df


# --------------------------------------------------------------------------- #
# 6. Qualité des données
# --------------------------------------------------------------------------- #
def mass_rollup(m: PLMModel) -> pd.DataFrame:
    rows = []
    for pid, ch in m.children.items():
        p = m.products.loc[pid]
        if p.node_type != "Assemblage":
            continue
        s = sum(m.products.loc[c, "mass_kg"] or 0 for c, _ in ch)
        rows.append(dict(xml_id=pid, plm_id=p.plm_id, name=p["name"], declared_kg=p.mass_kg,
                         children_sum_kg=round(s, 3), gap_kg=round((p.mass_kg or 0) - s, 3),
                         n_children=len(ch)))
    return pd.DataFrame(rows)


def data_quality(m: PLMModel) -> pd.DataFrame:
    p = m.products
    issues = []

    def add(sev, cat, title, detail, ids):
        issues.append(dict(severity=sev, category=cat, title=title, detail=detail,
                           xml_ids=list(ids),
                           plm_ids=", ".join(p.loc[i, "plm_id"] for i in ids if i in p.index)))

    # a) représentations partielles comptées dans la masse
    partial = p[p.description.str.contains("single|only", case=False) |
                ((p.name.str.contains("Coil$")) & p.node_type.eq("Pièce (EI)"))]
    stator = p[p.name.eq("ECAR-EI_STATOR")]
    if len(stator):
        sid = stator.index[0]
        ch = [c for c, _ in m.children.get(sid, [])]
        part_ids = [c for c in ch if p.loc[c, "name"] in ("ECAR-EI_STATOR Core Tooth", "ECAR-EI_STATOR Coil")]
        extra = sum(p.loc[c, "mass_kg"] for c in part_ids)
        add("Élevée", "Masse", "Double comptage probable dans le stator",
            f"Le stator contient le noyau complet (360°) et les bobines complètes, mais aussi une dent "
            f"seule et une bobine seule (« only a single tooth → required for meshing »). Leur masse "
            f"({extra:.2f} kg) est incluse dans les {p.loc[sid,'mass_kg']:.2f} kg déclarés du stator.",
            [sid] + part_ids)

    # b) homonymes avec révisions différentes
    ei = p[p.node_type.eq("Pièce (EI)")]
    for n, g in ei.groupby("name"):
        if g.revision.nunique() > 1:
            add("Moyenne", "Identification", f"{len(g)} références homonymes « {n} »",
                "Même nom, révisions et masses différentes ("
                + ", ".join(f"rév. {r.revision} = {r.mass_kg} kg" for _, r in g.sort_values('revision').iterrows())
                + "). Risque de confusion en commande ou en change request : seul l'ID PLM les distingue.",
                g.index)

    # c) pièce 3D instanciée sans enveloppe EI
    for pid, r in p[p.node_type.eq("Pièce 3D")].iterrows():
        if any(p.loc[par, "node_type"] == "Assemblage" for par in m.parents.get(pid, [])):
            add("Moyenne", "Structure", f"{r['name']} instanciée directement dans un assemblage",
                "Toutes les autres pièces passent par une référence d'ingénierie « EI » qui porte la "
                "pièce 3D ; celle-ci est rattachée directement. Incohérence de modélisation.",
                [pid] + m.parents.get(pid, []))

    # d) écarts de masse entre assemblage et somme des enfants
    mr = mass_rollup(m)
    for _, r in mr[mr.gap_kg.abs() > 0.01].iterrows():
        add("Moyenne", "Masse", f"Écart de masse sur {r['name']}",
            f"Déclaré {r.declared_kg} kg, somme des enfants {r.children_sum_kg} kg (écart {r.gap_kg} kg).",
            [r.xml_id])

    # d2) complétude fonctionnelle : un moteur électrique sans rotor
    if not p.name.str.contains("ROTOR|Rotor|MAGNET|Magnet", regex=True).any():
        em = p[p.name.str.contains("EMOTOR")]
        add("Élevée", "Complétude", "Aucun rotor dans la nomenclature du moteur",
            "Le sous-ensemble moteur contient stator, arbre, refroidissement et un roulement, mais ni "
            "tôles rotor ni aimants. Soit l'OEM les gère hors de cet export, soit la nomenclature est "
            "incomplète : point à clarifier, car les aimants (terres rares) sont le premier risque "
            "d'approvisionnement d'un moteur électrique.", list(em.index))

    # e) maturité
    if p.maturity.nunique() == 1:
        add("Élevée", "Cycle de vie", f"100 % des objets en {p.maturity.iloc[0]}",
            f"Aucune des {len(p)} références n'est libérée (RELEASED). Lancer outillage ou "
            "approvisionnement sur ces données expose à des modifications non maîtrisées.", list(p.index))

    # f) descriptions vides
    empty = p[p.description.eq("")]
    add("Faible", "Complétude", f"{len(empty)}/{len(p)} descriptions vides",
        "Le champ Description n'est renseigné que pour " + str(len(p) - len(empty)) +
        " objets : la recherche plein texte et le chatbot s'appuient donc surtout sur les noms.",
        list(empty.index))

    # g) conventions de nommage
    bad = p[p.name.str.contains(r"ECAR-(?:EI|3D) |ECAR-EI_ |_01_")]
    add("Faible", "Nommage", f"{len(bad)} noms hors convention",
        "Espaces au lieu de « _ », espaces parasites ou préfixes « 01_ » incohérents (ex. "
        + ", ".join(bad.name.head(4)) + ").", list(bad.index))

    # h) horodatage
    add("Info", "Historique", "Toutes les dates de création/modification sont du 14/10/2026",
        "L'export est un instantané : il ne contient pas l'historique des révisions précédentes. "
        "La comparaison de versions doit se faire entre deux exports successifs.", [])
    sev_order = {"Élevée": 0, "Moyenne": 1, "Faible": 2, "Info": 3}
    df = pd.DataFrame(issues)
    return df.sort_values("severity", key=lambda s: s.map(sev_order)).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# 7. Analyse d'impact d'un changement
# --------------------------------------------------------------------------- #
def where_used(m: PLMModel, pid: str) -> list[list[str]]:
    """Tous les chemins racine → pid."""
    paths = []

    def up(x, acc):
        ps = m.parents.get(x, [])
        if not ps:
            paths.append(list(reversed(acc)))
            return
        for par in set(ps):
            up(par, acc + [par])
    up(pid, [pid])
    return paths


def change_impact(m: PLMModel, comp: pd.DataFrame, pid: str, new_material: str | None = None,
                  new_mass: float | None = None, new_supplier: str | None = None,
                  mat: pd.DataFrame | None = None) -> dict:
    """Impact d'un remplacement de composant (matériau, masse, fournisseur)."""
    MATERIALS_ = MATERIALS if mat is None else mat
    c = comp.set_index("xml_id").loc[pid]
    mat_new = new_material or (c.material if c.material in MATERIALS_.index else None)
    mass_new = new_mass if new_mass is not None else c.mass_kg
    fam = c.family
    mt = MATERIALS_.loc[mat_new] if mat_new in MATERIALS_.index else None
    unit_new = mass_new * (mt.eur_per_kg if mt is not None else 0) * PROCESS_FACTOR.get(fam, 3)
    co2_new = mass_new * c.qty * (mt.co2_per_kg if mt is not None else 0)
    paths = where_used(m, pid)
    assemblies = sorted({x for path in paths for x in path[:-1]})
    # rattrapage : la référence 3D sous la pièce EI
    rep3d = [cc for cc, _ in m.children.get(pid, [])]
    siblings = comp[(comp.material == c.material) & (comp.xml_id != pid)]
    same_sup = comp[(comp.supplier == c.supplier) & (comp.xml_id != pid)]
    return dict(
        component=c, paths=paths, assemblies=assemblies, rep3d=rep3d,
        delta_unit_cost=unit_new - c.unit_cost_eur, delta_total_cost=(unit_new - c.unit_cost_eur) * c.qty,
        delta_mass_total=(mass_new - c.mass_kg) * c.qty, delta_co2=co2_new - c.co2_kg,
        new_unit_cost=unit_new, siblings_same_material=siblings, same_supplier=same_sup,
        new_supplier=new_supplier, new_material=mat_new, new_mass=mass_new,
        new_co2=co2_new,
    )


def price_shock(comp: pd.DataFrame, shocks: dict[str, float], mat: pd.DataFrame | None = None) -> pd.DataFrame:
    """Hausse (%) du prix de matières → surcoût par composant (part matière du coût)."""
    MATERIALS_ = MATERIALS if mat is None else mat
    df = comp.copy()
    df["material_cost_eur"] = [
        r.mass_total_kg * MATERIALS_.loc[r.material, "eur_per_kg"] if r.material in MATERIALS_.index else 0
        for _, r in df.iterrows()]
    df["shock_pct"] = df.material.map(lambda x: shocks.get(x, 0.0))
    df["extra_cost_eur"] = df.material_cost_eur * df.shock_pct / 100
    return df[df.extra_cost_eur > 0].sort_values("extra_cost_eur", ascending=False)


# --------------------------------------------------------------------------- #
# 8. Recherche plein texte (pour le chatbot : contexte + sources citées)
# --------------------------------------------------------------------------- #
SYNONYMS = {
    "cuivre": "copper", "bobine": "coil", "bobines": "coil", "bobinage": "coil", "roulement": "bea",
    "roulements": "bea", "engrenage": "gear", "pignon": "gear", "roue": "gear", "carter": "housing",
    "carters": "housing", "vis": "screw", "visserie": "screw", "refroidissement": "cooling",
    "arbre": "shaft", "aluminium": "alumin", "alu": "alumin", "acier": "steel", "fonte": "cast iron",
    "différentiel": "differential", "differentiel": "differential", "joint": "joint",
    "tuyau": "pipe", "noyau": "core", "dent": "tooth", "réducteur": "reducer", "reducteur": "reducer",
    "moteur": "emotor", "tôle": "electrical steel", "tôles": "electrical steel",
}
STOPWORDS = {"les", "des", "est", "quel", "quels", "quelle", "quelles", "pour", "dans", "avec", "une",
             "sont", "que", "qui", "the", "what", "which", "are", "and", "par", "sur", "composant",
             "composants", "pièce", "pièces", "piece", "pieces", "si", "on", "of", "is", "existe",
             "existent", "remplace", "remplacé", "remplacée", "impact", "impactés", "affectés",
             "component", "replaced", "replace", "affected", "critique", "critiques", "fournisseur",
             "fournisseurs", "supplier", "suppliers", "version", "versions", "révision", "révisions",
             "propriétés", "properties", "dépend", "dépendent", "depend", "coût", "coûts", "cost"}


def _tokens(query: str) -> list[str]:
    toks = []
    for t in re.split(r"[^\wÀ-ÿ]+", query.lower()):
        if len(t) <= 2 or t in STOPWORDS:
            continue
        toks.append(SYNONYMS.get(t, t))
    return toks


def search(m: PLMModel, comp: pd.DataFrame, query: str, k: int = 8) -> pd.DataFrame:
    toks = _tokens(query)
    p = m.products.copy()
    c = comp.set_index("xml_id")
    p["supplier"] = p.xml_id.map(c.supplier).fillna("")
    p["subasm"] = p.xml_id.map(c.subassembly).fillna("")
    p["family_"] = p.family
    hay = (p.name + " " + p.description + " " + p.material + " " + p.family_ + " " +
           p.function + " " + p.supplier + " " + p["subasm"] + " " + p.plm_id + " rév " + p.revision).str.lower()
    p["score"] = [sum(tok in h for tok in toks) + (0.5 if t == "Pièce (EI)" else 0)
                  for h, t in zip(hay, p.node_type)]
    p.loc[[not any(tok in h for tok in toks) for h in hay], "score"] = 0
    return p[p.score > 0].sort_values(["score", "mass_kg"], ascending=[False, False]).head(k)


# --------------------------------------------------------------------------- #
# 9. Comparaison de deux exports
# --------------------------------------------------------------------------- #
def diff_models(a: PLMModel, b: PLMModel) -> pd.DataFrame:
    """Compare deux exports. Clé = RevisionFamily (stable entre révisions d'un même objet)."""
    pa = a.products.set_index("revision_family")
    pb = b.products.set_index("revision_family")
    rows = []
    for k in sorted(set(pa.index) | set(pb.index)):
        if k not in pb.index:
            r = pa.loc[k]
            rows.append(dict(change="Supprimé", name=r["name"], field="", before="présent", after="",
                             plm_id=r.plm_id, xml_id_v1=r.xml_id, xml_id_v2=""))
            continue
        if k not in pa.index:
            r = pb.loc[k]
            rows.append(dict(change="Ajouté", name=r["name"], field="", before="", after="présent",
                             plm_id=r.plm_id, xml_id_v1="", xml_id_v2=r.xml_id))
            continue
        ra, rb = pa.loc[k], pb.loc[k]
        for f, lab in [("revision", "Révision"), ("maturity", "Maturité"), ("mass_kg", "Masse (kg)"),
                       ("material", "Matériau"), ("description", "Description")]:
            if str(ra[f]) != str(rb[f]):
                rows.append(dict(change="Modifié", name=rb["name"], field=lab, before=ra[f], after=rb[f],
                                 plm_id=rb.plm_id, xml_id_v1=ra.xml_id, xml_id_v2=rb.xml_id))
    # nombre d'occurrences par lien parent → enfant
    def links(m):
        p = m.products
        return m.instances.assign(pk=lambda d: d.parent.map(p.revision_family),
                                  ck=lambda d: d.child.map(p.revision_family),
                                  pn=lambda d: d.parent.map(p["name"]),
                                  cn=lambda d: d.child.map(p["name"])
                                  ).groupby(["pk", "ck", "pn", "cn"]).size()
    la, lb = links(a), links(b)
    for key in sorted(set(la.index) | set(lb.index)):
        qa, qb = int(la.get(key, 0)), int(lb.get(key, 0))
        if qa != qb and qa and qb:
            rows.append(dict(change="Modifié", name=key[3], field=f"Quantité dans {key[2]}",
                             before=qa, after=qb, plm_id="", xml_id_v1="", xml_id_v2=""))
    return pd.DataFrame(rows, columns=["change", "name", "field", "before", "after", "plm_id",
                                       "xml_id_v1", "xml_id_v2"])


if __name__ == "__main__":
    m = load_model()
    comp = components(m)
    print(comp[["display_name", "family", "qty", "total_cost_eur", "supplier", "risk_score"]].head(12))
    print(mass_rollup(m))
    print(data_quality(m)[["severity", "title"]])
