"""Assistant PLM : questions en langage naturel -> réponses calculées sur le modèle, sources citées.

Fonctionne sans LLM (routage par intention + fonctions de plm_core). Si une clé API est configurée,
un LLM reformule la réponse en s'appuyant UNIQUEMENT sur le contexte calculé et les sources.
Variables d'environnement :
  ANTHROPIC_API_KEY (+ PLM_LLM_MODEL)            -> API Anthropic
  LLM_BASE_URL + LLM_API_KEY (+ LLM_MODEL)        -> toute API compatible OpenAI (/chat/completions)
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass, field

import pandas as pd

import plm_core as pc


@dataclass
class Answer:
    text: str
    sources: pd.DataFrame = field(default_factory=pd.DataFrame)  # xml_id, plm_id, name, revision, champ
    intent: str = "recherche"
    focus: str | None = None            # pièce principale de la réponse (pour boutons)
    table: pd.DataFrame | None = None


def _short(n: str) -> str:
    return re.sub(r"^ECAR-(EI|3D|01)[_ ]*", "", n or "").strip()


def _src(m: pc.PLMModel, ids, champ: str) -> pd.DataFrame:
    P = m.products
    ids = [i for i in dict.fromkeys(ids) if i in P.index]
    return pd.DataFrame([dict(xml_id=i, plm_id=P.loc[i, "plm_id"], name=P.loc[i, "name"],
                              revision=P.loc[i, "revision"], champ=champ) for i in ids])


INTENTS = [
    ("impact", r"rempla|chang|impact|modifi|substitu|replace|si on |what if|affect"),
    ("fournisseurs", r"fournisseur|supplier|dépendan|depend|source unique|mono.?source|pays|sourcing"),
    ("risques", r"critique|risque|critical|risk|vigilance|fragile|priorit"),
    ("qualite", r"qualit|anomal|incohéren|incoheren|erreur|manqu|quality|fiable|doublon"),
    ("versions", r"version|révision|revision|maturit|lifecycle|cycle de vie|released|in_work"),
    ("cout", r"co[uû]t|prix|cost|price|€|cher"),
    ("masse", r"masse|poids|lourd|weight|\bkg\b"),
    ("materiaux", r"matéria|materia|matière|matiere|cuivre|copper|alumin|acier|steel|fonte|pfas|fkm"),
    ("ou_utilise", r"où|utilisé dans|where.?used|contient|parent"),
    ("structure", r"sous.?ensemble|subassembl|structure|compos|majeur|principa|nomenclature|bom|arbo"),
]


def detect_intent(q: str) -> str:
    ql = q.lower()
    for name, rx in INTENTS:
        if re.search(rx, ql):
            return name
    return "recherche"


def resolve_part(m: pc.PLMModel, comp: pd.DataFrame, q: str, prefer_ei: bool = True) -> str | None:
    """Pièce la plus pertinente pour la question (recherche plein texte de plm_core)."""
    q2 = re.sub(r"\b(impact|remplace\w*|change\w*|modifi\w*|si on)\b", " ", q, flags=re.I)
    res = pc.search(m, comp, q2, k=10)
    if res.empty:
        return None
    if prefer_ei:
        ei = res[res.node_type.isin(["Pièce (EI)", "Assemblage"])]
        if not ei.empty:
            best = ei.score.iloc[0]
            asm = ei[(ei.node_type == "Assemblage") & (ei.score >= best - 0.5)]
            return asm.index[0] if len(asm) else ei.index[0]
    return res.index[0]


def answer(m: pc.PLMModel, comp: pd.DataFrame, q: str, mat: pd.DataFrame | None = None,
           selected: str | None = None) -> Answer:
    P = m.products
    intent = detect_intent(q)
    ci = comp.set_index("xml_id")
    part = resolve_part(m, comp, q)
    if part is None and selected and re.search(r"cette pièce|this part|la pièce active|celle-ci", q.lower()):
        part = selected

    if intent == "structure":
        kids = list(dict.fromkeys(c for c, _ in m.children.get(m.root, [])))
        lines = [f"**{P.loc[m.root, 'name']}** (rév. {P.loc[m.root, 'revision']}, {P.loc[m.root, 'mass_kg']:.1f} kg) "
                 f"se décompose en **{len(kids)} sous-ensembles** :"]
        rows = []
        for k in kids:
            sub = [c for c, _ in m.children.get(k, [])]
            names = sorted({_short(P.loc[c, "name"]) for c in sub})
            lines.append(f"- **{_short(P.loc[k, 'name'])}** · rév. {P.loc[k, 'revision']} · {P.loc[k, 'mass_kg']:.2f} kg · "
                         f"{len(sub)} occurrences : {', '.join(names[:8])}{'…' if len(names) > 8 else ''}")
            rows.append(k)
        return Answer("\n".join(lines), _src(m, [m.root] + rows, "Name, RevisionName, Mass, ProductInst"), intent)

    if intent == "risques":
        top = comp.head(6)
        lines = ["Les composants les plus critiques selon le score enrichi* (0 à 100) :"]
        for _, r in top.iterrows():
            fs = {k[2:]: v for k, v in r.items() if str(k).startswith("f_")}
            main = sorted(fs.items(), key=lambda kv: -kv[1])[:3]
            lines.append(f"- **{r.display_name}** · {r.risk_score:.0f}/100 · {r.family} · "
                         + ", ".join(f"{k} {v}/5" for k, v in main))
        lines.append("\nLa maturité est identique pour toutes les pièces (IN_WORK) : elle ne départage pas. "
                     "Ce qui pèse : criticité fonctionnelle, tension sur la matière (cuivre, acier électrique) "
                     "et fournisseur mono-source.\n\n*Score basé sur des hypothèses (page Hypothèses).")
        return Answer("\n".join(lines), _src(m, top.xml_id, "Name, Material, RevisionIndex"), intent,
                      focus=top.xml_id.iloc[0], table=top[["display_name", "family", "material", "risk_score"]])

    if intent == "fournisseurs":
        sup = comp.groupby(["supplier", "country", "single_source"]).agg(
            pieces=("xml_id", "count"), cout=("total_cost_eur", "sum")).reset_index().sort_values("cout", ascending=False)
        sup["part"] = (100 * sup.cout / sup.cout.sum()).round(1)
        mono = sup[sup.single_source]
        lines = [f"{len(sup)} fournisseurs simulés* couvrent les {len(comp)} pièces.",
                 f"**{len(mono)} sont mono-source** : " + ", ".join(
                     f"{r.supplier} ({r.country}, {r.part} % du coût)" for _, r in mono.iterrows()) + ".",
                 f"Le premier fournisseur pèse **{sup.part.iloc[0]} %** du coût matière estimé ({sup.supplier.iloc[0]})."]
        if part and part in ci.index:
            r = ci.loc[part]
            lines.append(f"\nPour **{r.display_name}** : {r.supplier} ({r.country}), délai {r.lead_time_wk} semaines.")
        lines.append("\n*Aucun fournisseur n'est présent dans l'export de l'OEM : affectation par famille de pièce.")
        ids = comp[comp.supplier.isin(mono.supplier)].xml_id
        return Answer("\n".join(lines), _src(m, ids, "Name (famille → fournisseur simulé)"), intent, table=sup)

    if intent == "impact" and part:
        target = part
        if P.loc[target, "node_type"] == "Pièce 3D":
            target = next((p for p in m.parents.get(target, []) if p in ci.index), target)
        paths = pc.where_used(m, target)
        assemblies = sorted({x for p in paths for x in p[:-1]}, key=lambda x: len(m.parents.get(x, [])))
        lines = [f"Remplacer **{_short(P.loc[target, 'name'])}** (rév. {P.loc[target, 'revision']}, "
                 f"{P.loc[target, 'plm_id']}) toucherait :"]
        lines += [f"- {' › '.join(_short(P.loc[x, 'name']) for x in p)}" for p in paths[:4]]
        lines.append(f"\n**{len(assemblies)} assemblage(s) à réviser** jusqu'au produit final.")
        if target in ci.index:
            imp = pc.change_impact(m, comp, target, mat=mat)
            r = ci.loc[target]
            sib = imp["siblings_same_material"]
            lines.append(f"Quantité par produit : **{int(r.qty)}** · coût unitaire estimé* {r.unit_cost_eur:.2f} € · "
                         f"fournisseur* {r.supplier} ({r.country}).")
            if len(sib):
                lines.append(f"{len(sib)} autre(s) pièce(s) utilisent le même matériau ({r.material}) : "
                             + ", ".join(sib.display_name.head(5)) + ".")
            same = imp["same_supplier"]
            if len(same):
                lines.append(f"Chez le même fournisseur : {', '.join(same.display_name.head(5))}.")
        lines.append("\nToutes les pièces sont IN_WORK : le changement peut entrer dans la révision en cours, "
                     "sans conflit de maturité. Simulation détaillée dans la page Analyse d'impact.")
        return Answer("\n".join(lines), _src(m, [target] + assemblies, "ProductInst Owned → Instancing"), intent,
                      focus=target)

    if intent == "versions":
        if part and not re.search(r"toutes|global|répartition|distribution|du produit|de l'export", q.lower()):
            r = P.loc[part]
            homo = P[(P.name == r["name"]) & (P.index != part)]
            lines = [f"**{_short(r['name'])}** est en révision **{r.revision}** (index {r.rev_index}), maturité "
                     f"**{r.maturity}**, cycle de vie {r.lifecycle}."]
            if r.rev_index > 1:
                lines.append(f"L'index {r.rev_index} indique {r.rev_index - 1} révision(s) antérieure(s), absentes de "
                             "l'export (seule la révision courante est livrée).")
            if len(homo):
                lines.append(f"Attention : {len(homo)} autre(s) référence(s) portent le même nom avec les révisions "
                             + ", ".join(homo.revision) + " : ce sont des pièces différentes, distinguées par l'ID PLM.")
            return Answer("\n".join(lines), _src(m, [part] + list(homo.index), "RevisionName, RevisionIndex, Maturity"),
                          intent, focus=part)
        rv = P.revision.value_counts().sort_index()
        mt = P.maturity.value_counts()
        lines = ["Répartition des révisions : " + ", ".join(f"rév. {k} : {v}" for k, v in rv.items()) + ".",
                 "Maturités : " + ", ".join(f"{k} : {v}" for k, v in mt.items()) + ".",
                 f"Les révisions les plus avancées (D) : " + ", ".join(
                     sorted({_short(n) for n in P[P.revision == P.revision.max()].name})) + ".",
                 "Aucune pièce n'est libérée (RELEASED) : tout est encore en conception."]
        return Answer("\n".join(lines), _src(m, P[P.revision == P.revision.max()].index, "RevisionName, Maturity"),
                      intent)

    if intent == "qualite":
        dq = pc.data_quality(m)
        lines = ["Constats de qualité des données :"]
        ids = []
        for _, r in dq.head(6).iterrows():
            lines.append(f"- **[{r.severity}] {r.title}** : {r.detail}")
            ids += r.xml_ids[:3]
        return Answer("\n".join(lines), _src(m, ids, "contrôles qualité"), intent)

    if intent == "cout":
        top = comp.sort_values("total_cost_eur", ascending=False).head(6)
        tot = comp.total_cost_eur.sum()
        lines = [f"Coût matière et transformation estimé* : **{tot:,.0f} € par produit**.".replace(",", " "),
                 "Les postes principaux :"]
        lines += [f"- {r.display_name} : {r.total_cost_eur:,.2f} € ({100 * r.total_cost_eur / tot:.0f} %)".replace(",", " ")
                  for _, r in top.iterrows()]
        lines.append("\n*Masse × prix matière × facteur de transformation : hypothèses, l'export ne contient aucun coût.")
        return Answer("\n".join(lines), _src(m, top.xml_id, "Mass, Material"), intent, focus=top.xml_id.iloc[0])

    if intent == "masse":
        if part and not re.search(r"plus lourd|top|principal|heaviest", q.lower()):
            r = P.loc[part]
            return Answer(f"**{_short(r['name'])}** : {r.mass_kg} kg (attribut WCGEquivalentComputedExt.V_WCG_Mass).",
                          _src(m, [part], "WCG_Mass"), intent, focus=part)
        top = comp.sort_values("mass_total_kg", ascending=False).head(6)
        lines = [f"Masse totale déclarée : **{P.loc[m.root, 'mass_kg']} kg**. Pièces les plus lourdes (quantités incluses) :"]
        lines += [f"- {r.display_name} : {r.mass_total_kg:.2f} kg" for _, r in top.iterrows()]
        return Answer("\n".join(lines), _src(m, top.xml_id, "WCG_Mass × quantité"), intent)

    if intent == "materiaux":
        kw = {"cuivre": "copper", "copper": "copper", "alu": "alumin", "acier": "steel", "steel": "steel",
              "fonte": "cast iron", "fkm": "fkm", "pfas": "fkm", "électrique": "electrical"}
        key = next((v for k, v in kw.items() if k in q.lower()), None)
        sel = comp[comp.material.str.lower().str.contains(key)] if key else comp
        g = sel.groupby("material").agg(pieces=("xml_id", "count"), masse=("mass_total_kg", "sum")).reset_index()
        lines = [f"{len(sel)} pièce(s) concernée(s) :"]
        for _, r in g.sort_values("masse", ascending=False).iterrows():
            note = mat.loc[r.material, "note"] if mat is not None and r.material in mat.index else ""
            lines.append(f"- **{r.material}** : {r.pieces} pièce(s), {r.masse:.2f} kg. {note}")
        return Answer("\n".join(lines), _src(m, sel.xml_id, "Material"), intent)

    if intent == "ou_utilise" and part:
        paths = pc.where_used(m, part)
        qty = int(ci.loc[part, "qty"]) if part in ci.index else len(paths)
        lines = [f"**{_short(P.loc[part, 'name'])}** apparaît **{qty} fois** dans le produit, via {len(paths)} chemin(s) :"]
        lines += [f"- {' › '.join(_short(P.loc[x, 'name']) for x in p)}" for p in paths[:6]]
        kids = list(dict.fromkeys(c for c, _ in m.children.get(part, [])))
        if kids:
            lines.append("Contient : " + ", ".join(_short(P.loc[c, "name"]) for c in kids[:10]) + ".")
        return Answer("\n".join(lines), _src(m, [part] + [x for p in paths for x in p], "ProductInst"), intent, focus=part)

    res = pc.search(m, comp, q, k=6)
    if res.empty:
        return Answer("Je n'ai trouvé aucun objet correspondant dans l'export. Essayez avec un nom de pièce "
                      "(stator, roulement, carter…), un matériau ou une référence prd-…", intent="recherche")
    lines = ["Objets correspondants dans l'export :"]
    lines += [f"- **{_short(r['name'])}** · rév. {r.revision} · {r.node_type} · {r.mass_kg} kg · {r.material or '—'}"
              for _, r in res.iterrows()]
    return Answer("\n".join(lines), _src(m, res.index, "recherche plein texte"), "recherche", focus=res.index[0])


# --------------------------------------------------------------------------- #
# Reformulation optionnelle par LLM (ne peut utiliser que le contexte calculé)
# --------------------------------------------------------------------------- #
SYSTEM = ("Tu es l'assistant PLM d'un fournisseur de moteurs électriques. Réponds en français, brièvement, "
          "UNIQUEMENT à partir du CONTEXTE fourni (calculé sur l'export XML de l'OEM). Cite les ID PLM (prd-…) "
          "des objets utilisés. Signale clairement ce qui est une hypothèse (coûts, fournisseurs). "
          "Si le contexte ne permet pas de répondre, dis-le. N'invente aucune donnée.")


def llm_available() -> str | None:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("LLM_BASE_URL") and os.environ.get("LLM_API_KEY"):
        return "openai"
    return None


def llm_rephrase(question: str, ans: Answer, history: list[dict] | None = None, timeout: int = 30) -> str:
    provider = llm_available()
    ctx = ans.text + "\n\nSOURCES :\n" + (ans.sources.to_csv(index=False) if not ans.sources.empty else "aucune")
    user = f"QUESTION : {question}\n\nCONTEXTE :\n{ctx}"
    if provider == "anthropic":
        body = dict(model=os.environ.get("PLM_LLM_MODEL", "claude-sonnet-5-5"), max_tokens=800, system=SYSTEM,
                    messages=[{"role": "user", "content": user}])
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
                                     headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                                              "anthropic-version": "2023-06-01", "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return "".join(b.get("text", "") for b in json.load(r)["content"])
    if provider == "openai":
        body = dict(model=os.environ.get("LLM_MODEL", "gpt-4o-mini"),
                    messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}])
        req = urllib.request.Request(os.environ["LLM_BASE_URL"].rstrip("/") + "/chat/completions",
                                     data=json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {os.environ['LLM_API_KEY']}",
                                              "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)["choices"][0]["message"]["content"]
    return ans.text
