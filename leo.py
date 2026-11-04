"""Léo : assistant conversationnel de l'appli, contextuel (page ouverte + pièce active).

Moteur : API Gemini de Google (offre gratuite, clé à créer sur https://aistudio.google.com/apikey).
Ordre de recherche de la clé :
  1. clé saisie dans l'interface pour la session en cours ;
  2. variable d'environnement GEMINI_API_KEY ou GOOGLE_API_KEY ;
  3. .streamlit/secrets.toml (GEMINI_API_KEY) ;
  4. fichier local .secrets/gemini_api_key (créé par le bouton « Enregistrer », ignoré par git).
Sans clé, Léo répond quand même avec le moteur d'intentions d'assistant_core (réponses calculées).
"""
from __future__ import annotations

import html
import json
import os
import re
import urllib.error
import urllib.request

import pandas as pd
import streamlit as st

import assistant_core as ac
import plm_core as pc
import shared as sh

KEY_FILE = sh.ROOT_DIR / ".secrets" / "gemini_api_key"
# Ordre de préférence (offre gratuite, testé le 04/11/2026) : en cas de saturation, Léo passe au suivant.
# Les modèles 2.x ne sont plus ouverts aux nouvelles clés.
MODELS = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash",
          "gemini-3.5-flash-lite", "gemini-flash-latest"]
API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
HIST = "leo_hist"

PAGE_NAMES = {"accueil": "Vue d'ensemble", "explorateur": "Explorateur BOM", "fiche": "Fiche composant",
              "risques": "Risques", "impact": "Analyse d'impact", "comparaison": "Comparaison d'exports",
              "qualite": "Qualité des données", "assistant": "Léo", "hypotheses": "Hypothèses"}

SYSTEM = (
    "Tu es Léo, l'assistant PLM intégré à l'application « PLM Intelligence ». L'utilisateur travaille pour un "
    "fournisseur de moteurs électriques et analyse un export XPDMXML de 3DEXPERIENCE (nomenclature d'un groupe "
    "motopropulseur électrique). Réponds en français, de façon claire et concise (listes courtes, gras pour les "
    "chiffres clés, 180 mots maximum sauf demande explicite). Appuie-toi UNIQUEMENT sur le CONTEXTE fourni : "
    "page ouverte, pièce active et résultats calculés. N'invente aucune donnée. Les coûts, fournisseurs, pays et "
    "scores de risque enrichis sont des HYPOTHÈSES simulées : signale-le quand tu les utilises. Si le contexte ne "
    "permet pas de répondre, dis-le et propose la page de l'appli où chercher. Utilise des noms de pièces lisibles "
    "plutôt que des identifiants techniques, sauf si on te les demande."
)


# --------------------------------------------------------------------------- #
# Clé API
# --------------------------------------------------------------------------- #
def _read_key_file() -> str:
    try:
        return KEY_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def api_key() -> tuple[str, str]:
    """(clé, origine) ; clé vide si aucune n'est configurée."""
    k = st.session_state.get("leo_session_key", "")
    if k:
        return k, "session"
    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        if os.environ.get(var):
            return os.environ[var].strip(), f"variable {var}"
    try:
        if "GEMINI_API_KEY" in st.secrets:
            return str(st.secrets["GEMINI_API_KEY"]).strip(), "secrets.toml"
    except Exception:  # pas de secrets.toml
        pass
    k = _read_key_file()
    return (k, "fichier .secrets/gemini_api_key") if k else ("", "")


def save_key(key: str) -> None:
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_text(key.strip(), encoding="utf-8")
    gi = KEY_FILE.parent / ".gitignore"          # double sécurité si le .gitignore racine est modifié
    if not gi.exists():
        gi.write_text("*\n", encoding="utf-8")


def delete_key() -> None:
    st.session_state.pop("leo_session_key", None)
    try:
        KEY_FILE.unlink()
    except OSError:
        pass


def masked(key: str) -> str:
    return f"{key[:4]}…{key[-4:]}" if len(key) > 10 else "••••"


# --------------------------------------------------------------------------- #
# Contexte : ce que l'utilisateur regarde
# --------------------------------------------------------------------------- #
def _fmt(v, nd=2, unit=""):
    try:
        return f"{float(v):,.{nd}f}".replace(",", " ") + (f" {unit}" if unit else "")
    except (TypeError, ValueError):
        return "non renseigné"


def part_context(m: pc.PLMModel, comp: pd.DataFrame, xid: str) -> str:
    P = m.products
    if xid not in P.index:
        return ""
    r = P.loc[xid]
    lines = [f"PIÈCE ACTIVE : {sh.short(r['name'])} (nom complet {r['name']}, réf. {r.plm_id}, id XML {xid})",
             f"- type : {r.node_type} · famille : {r.family} · fonction : {r.function}",
             f"- révision {r.revision} (indice {r.rev_index}) · maturité {r.maturity} · cycle de vie {r.lifecycle}",
             f"- masse unitaire : {_fmt(r.mass_kg, 3, 'kg')} · matériau : {r.material or 'non renseigné'}"]
    if r.description:
        lines.append(f"- description : {r.description}")
    paths = pc.where_used(m, xid)
    if paths:
        lines.append("- position : " + " > ".join(sh.short(P.loc[p, "name"]) for p in max(paths, key=len)))
        lines.append(f"- utilisée dans {len(paths)} chemin(s) de la nomenclature")
    kids = m.children.get(xid, [])
    if kids:
        names = sorted({sh.short(P.loc[c, "name"]) for c, _ in kids})
        lines.append(f"- contient {len(kids)} occurrence(s) : {', '.join(names[:12])}")
    ci = comp.set_index("xml_id")
    target = sh.ei_of(m, xid)
    if target in ci.index:
        c = ci.loc[target]
        fs = sorted(((k[2:], v) for k, v in c.items() if str(k).startswith("f_")), key=lambda kv: -kv[1])[:4]
        lines.append(f"- HYPOTHÈSES : fournisseur {c.supplier} ({c.country}), coût unitaire {_fmt(c.unit_cost_eur, 2, '€')}, "
                     f"quantité {int(c.qty)}, score de risque enrichi {c.risk_score:.0f}/100 ({c.risk_level}) ; "
                     "facteurs dominants : " + ", ".join(f"{k} {v}/5" for k, v in fs))
    return "\n".join(lines)


def page_context(page: str, m: pc.PLMModel, comp: pd.DataFrame) -> str:
    P = m.products
    root = P.loc[m.root]
    out = [f"PAGE OUVERTE : {PAGE_NAMES.get(page, page)}",
           f"PRODUIT : {root['name']} (rév. {root.revision}, {_fmt(root.mass_kg, 1, 'kg')}, {len(P)} objets PLM, "
           f"{len(comp)} pièces d'ingénierie)"]
    kids = list(dict.fromkeys(c for c, _ in m.children.get(m.root, [])))
    out.append("SOUS-ENSEMBLES : " + "; ".join(
        f"{sh.short(P.loc[k, 'name'])} {_fmt(P.loc[k, 'mass_kg'], 1, 'kg')}" for k in kids))

    if page in ("accueil", "risques", "assistant"):
        out.append("PIÈCES LES PLUS EXPOSÉES (score enrichi, hypothèses) : " + "; ".join(
            f"{r.display_name} {r.risk_score:.0f}/100 ({r.family}, {r.material})" for _, r in comp.head(8).iterrows()))
    if page == "risques":
        sup = comp.groupby("supplier").total_cost_eur.sum().sort_values(ascending=False)
        out.append("COÛT PAR FOURNISSEUR (hypothèse) : " + "; ".join(f"{k} {_fmt(v, 0, '€')}" for k, v in sup.head(6).items()))
        mono = comp[comp.single_source].supplier.unique()
        out.append(f"FOURNISSEURS MONO-SOURCE (hypothèse) : {', '.join(mono) or 'aucun'}")
    if page in ("accueil", "qualite"):
        dq = pc.data_quality(m)
        out.append("CONSTATS QUALITÉ : " + "; ".join(f"[{r.severity}] {r.title}" for _, r in dq.head(8).iterrows()))
    if page == "explorateur":
        fam = comp.groupby("family").agg(n=("xml_id", "count"), masse=("mass_total_kg", "sum"))
        out.append("FAMILLES DE PIÈCES : " + "; ".join(f"{k} ({int(v.n)} pièces, {_fmt(v.masse, 1, 'kg')})"
                                                       for k, v in fam.sort_values("masse", ascending=False).iterrows()))
    if page == "hypotheses":
        mat = sh.materials()
        out.append("PRIX MATIÈRES (€/kg) : " + "; ".join(f"{k} {_fmt(v, 2)}" for k, v in mat.eur_per_kg.items()))
        out.append("PONDÉRATIONS DU RISQUE : " + ", ".join(f"{k} {v}" for k, v in sh.weights().items()))
    extra = st.session_state.get(f"leo_ctx_{page}")
    if extra:
        out.append("RÉSULTATS AFFICHÉS SUR LA PAGE :\n" + str(extra))
    sel = st.session_state.get(sh.SEL)
    if sel:
        out.append(part_context(m, comp, sel))
    return "\n".join(out)


def set_page_context(page: str, text: str) -> None:
    """Une page peut publier ce qu'elle affiche (résultat d'impact, écarts…) pour que Léo le commente."""
    st.session_state[f"leo_ctx_{page}"] = text


# --------------------------------------------------------------------------- #
# Réponse
# --------------------------------------------------------------------------- #
class LeoError(Exception):
    def __init__(self, msg: str, retry_other_model: bool = False):
        super().__init__(msg)
        self.retry_other_model = retry_other_model


def _gemini(key: str, model: str, contents: list[dict], timeout: int = 40) -> str:
    # thinkingBudget 0 : réponse directe (~1,5 s) au lieu d'un raisonnement qui consomme les jetons
    cfg = {"temperature": 0.35, "maxOutputTokens": 1200, "thinkingConfig": {"thinkingBudget": 0}}
    body = {"systemInstruction": {"parts": [{"text": SYSTEM}]}, "contents": contents, "generationConfig": cfg}
    req = urllib.request.Request(API.format(model=model), data=json.dumps(body).encode("utf-8"),
                                 headers={"x-goog-api-key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("error", {}).get("message", "")
        except Exception:
            msg = ""
        if e.code in (400, 401, 403) and "key" in msg.lower():
            raise LeoError("clé API refusée par Google (vérifiez-la dans les réglages de Léo)") from e
        if e.code == 400 and "thinking" in msg.lower():   # modèle sans réglage de raisonnement
            body["generationConfig"].pop("thinkingConfig", None)
            req = urllib.request.Request(API.format(model=model), data=json.dumps(body).encode("utf-8"),
                                         headers={"x-goog-api-key": key, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.load(r)
        elif e.code == 429:
            raise LeoError("quota gratuit atteint pour le moment", retry_other_model=True) from e
        elif e.code in (404, 500, 503, 504):              # modèle retiré ou saturé : on essaie le suivant
            raise LeoError(f"{model} indisponible ({e.code})", retry_other_model=True) from e
        else:
            raise LeoError(f"erreur {e.code} de l'API Gemini {msg[:120]}") from e
    except TimeoutError as e:
        raise LeoError(f"{model} trop lent", retry_other_model=True) from e
    except urllib.error.URLError as e:
        raise LeoError("API Gemini injoignable (réseau ou proxy)") from e
    cands = data.get("candidates") or []
    text = "".join(p.get("text", "") for p in (cands[0].get("content", {}).get("parts", []) if cands else [])).strip()
    if not text:
        raise LeoError(f"réponse vide de {model}", retry_other_model=True)
    return text


def ask_gemini(key: str, contents: list[dict]) -> tuple[str, str]:
    """Essaie le modèle choisi puis les suivants de la liste. Renvoie (texte, modèle utilisé)."""
    chosen = st.session_state.get("leo_model", MODELS[0])
    order = [chosen] + [m for m in MODELS if m != chosen]
    errors = []
    for model in order:
        try:
            return _gemini(key, model, contents), model
        except LeoError as e:
            errors.append(str(e))
            if not e.retry_other_model:
                raise
    raise LeoError("aucun modèle Gemini disponible : " + " ; ".join(errors[:3]))


def reply(question: str, page: str, history: list[dict] | None = None) -> dict:
    """Répond à une question en tenant compte de la page et de la pièce active.

    Renvoie dict(text, sources, focus, table, mode) ; mode = « gemini », « calcul » ou « calcul (erreur …) ».
    """
    c = sh.ctx()
    m, comp = c["m"], c["comp"]
    try:
        calc = ac.answer(m, comp, question, c["mat"], selected=st.session_state.get(sh.SEL))
    except Exception:  # question hors des intentions connues : on garde le contexte de page
        calc = ac.Answer(text="Je n'ai pas de calcul direct pour cette question. Voici le contexte :\n\n"
                              + offline_summary(page), intent="recherche")
    out = dict(text=calc.text, sources=calc.sources, focus=calc.focus, table=calc.table, mode="calcul")
    key, _ = api_key()
    if not key:
        return out
    ctx = page_context(page, m, comp)
    calc_txt = calc.text if calc.intent != "recherche" or not calc.sources.empty else ""
    prompt = (f"CONTEXTE :\n{ctx}\n\nCALCUL DE L'APPLI POUR CETTE QUESTION :\n{calc_txt or '(aucun)'}\n\n"
              f"QUESTION : {question}")
    contents = []
    for h in (history or [])[-6:]:
        contents.append({"role": "user" if h["role"] == "user" else "model", "parts": [{"text": h["text"][:1500]}]})
    contents.append({"role": "user", "parts": [{"text": prompt}]})
    try:
        out["text"], used = ask_gemini(key, contents)
        out["mode"] = "gemini"
        out["model"] = used
    except LeoError as e:
        out["mode"] = f"calcul ({e})"
    return out


def offline_summary(page: str) -> str:
    """Résumé sans LLM : contexte mis en forme."""
    c = sh.ctx()
    out: list[str] = []
    for line in page_context(page, c["m"], c["comp"]).splitlines():
        mt = re.match(r"^([A-ZÀ-ÖØ-Þ' ()-]{4,}|[A-ZÀ-ÖØ-Þ' -]{4,} \([^)]*\)) : (.*)$", line)
        if not mt:
            out.append(line if line.startswith("- ") else f"- {line}")
            continue
        label = mt.group(1).strip().capitalize()
        items = [x.strip() for x in mt.group(2).split("; ") if x.strip()]
        out.append("")
        if len(items) > 1:
            out.append(f"**{label}**")
            out += [f"- {x}" for x in items[:6]]
        else:
            out.append(f"**{label}** : {mt.group(2)}")
    return "\n".join(out).strip()


def quick_prompts(page: str) -> list[tuple[str, str]]:
    m = sh.ctx()["m"]
    sel = sh.selected(m)
    name = sh.short(m.products.loc[sel, "name"]) if sel in m.products.index else "la pièce active"
    return [("Résumer cette page", f"Résume ce que montre la page « {PAGE_NAMES.get(page, page)} » et ce qu'il faut en retenir."),
            (f"Expliquer {name[:26]}", f"Explique-moi la pièce {name} : son rôle, sa place dans le produit et ses points de vigilance."),
            ("Que faire ensuite ?", "D'après ce que je regarde, quelles sont les 3 prochaines actions utiles ?")]


# --------------------------------------------------------------------------- #
# Interface
# --------------------------------------------------------------------------- #
LEO_CSS = """
<style>
.st-key-leo_fab {position: fixed; right: 26px; bottom: 22px; z-index: 999990; width: auto !important;
  background: #0B2545; border-radius: 18px; padding: 10px 12px 12px;
  box-shadow: 0 18px 40px -12px rgba(11,37,69,.55); border: 1px solid #24467A; max-width: 300px;}
.st-key-leo_fab .leo-head {display: flex; align-items: center; gap: 9px; color: #fff; margin-bottom: 2px;}
.st-key-leo_fab .leo-sub {color: #9FB3D3; font-size: .72rem; line-height: 1.25;}
.st-key-leo_fab button {background: rgba(255,255,255,.08) !important; color: #E6ECF5 !important;
  border: 1px solid rgba(255,255,255,.16) !important; font-size: .8rem !important; min-height: 2rem !important;
  justify-content: flex-start !important;}
.st-key-leo_fab button:hover {background: #2453D6 !important; border-color: #2453D6 !important;}
.leo-avatar {width: 30px; height: 30px; border-radius: 50%; display: grid; place-items: center; flex: none;
  background: conic-gradient(from 210deg, #2453D6, #14B8A6, #6E95FF, #2453D6); color: #fff; font-weight: 700;
  font-size: .85rem; box-shadow: 0 0 0 3px rgba(110,149,255,.25); animation: leo-pulse 2.8s ease-in-out infinite;}
@keyframes leo-pulse {0%,100% {box-shadow: 0 0 0 3px rgba(110,149,255,.25);} 50% {box-shadow: 0 0 0 7px rgba(110,149,255,.08);}}
.leo-chip {display: inline-block; font-size: .74rem; padding: 2px 10px; border-radius: 999px; margin: 0 6px 6px 0;
  background: #E8EEFF; color: #1E3A8A; border: 1px solid #CBD8FF;}
.leo-mode {font-size: .7rem; color: #64748B; margin-top: 2px;}
@media (max-width: 760px) {.st-key-leo_fab {right: 10px; bottom: 10px; max-width: 220px;}}
</style>
"""


def _render_history(hist: list[dict], compact: bool = True, nav_prefix: str = "leo") -> None:
    m = sh.ctx()["m"]
    for i, msg in enumerate(hist):
        with st.chat_message(msg["role"], avatar=None if msg["role"] == "user" else ":material/smart_toy:"):
            st.markdown(msg["text"])
            if msg["role"] != "assistant":
                continue
            if msg.get("mode") == "gemini" and msg.get("model"):
                st.markdown(f"<div class='leo-mode'>{html.escape(msg['model'])}</div>", unsafe_allow_html=True)
            elif msg.get("mode") and msg["mode"] != "gemini":
                st.markdown(f"<div class='leo-mode'>Réponse calculée par l'appli · {html.escape(msg['mode'])}</div>",
                            unsafe_allow_html=True)
            if compact:
                continue
            if msg.get("table") is not None:
                with st.expander("Tableau détaillé"):
                    st.dataframe(msg["table"], hide_index=True, width="stretch")
            src = msg.get("sources")
            if src is not None and not src.empty:
                with st.expander(f"Objets PLM utilisés ({len(src)})"):
                    st.dataframe(src.rename(columns={"xml_id": "ID XML", "plm_id": "Référence", "name": "Nom",
                                                     "revision": "Rév.", "champ": "Attributs utilisés"}),
                                 hide_index=True, width="stretch")
            if msg.get("focus") in m.products.index:
                a, b, _ = st.columns([1, 1, 2])
                with a:
                    sh.nav_button("Ouvrir la fiche", "fiche", msg["focus"], key=f"{nav_prefix}_f_{i}")
                with b:
                    sh.nav_button("Analyse d'impact", "impact", sh.ei_of(m, msg["focus"]), key=f"{nav_prefix}_i_{i}")


def ask(question: str, page: str) -> None:
    hist = st.session_state.setdefault(HIST, [])
    prev = list(hist)
    hist.append(dict(role="user", text=question))
    with st.spinner("Léo réfléchit…"):
        if question.startswith("Résume ce que montre la page") and not api_key()[0]:
            ans = dict(text="Voici les éléments clés de la page :\n\n" + offline_summary(page), mode="calcul",
                       sources=None, focus=None, table=None)
        else:
            ans = reply(question, page, prev)
    hist.append(dict(role="assistant", **ans))
    if ans.get("focus"):
        sh.select(ans["focus"])


def settings_panel(in_dialog: bool = False) -> None:
    key, origin = api_key()
    if key:
        st.success(f"Gemini connecté ({origin}) · clé {masked(key)}")
    else:
        st.info("Aucune clé : Léo répond avec le moteur de calcul de l'appli. Pour des réponses rédigées, créez "
                "une clé gratuite sur aistudio.google.com/apikey.")
    cur = st.session_state.get("leo_model", MODELS[0])
    st.session_state["leo_model"] = st.selectbox(
        "Modèle Gemini", MODELS, index=MODELS.index(cur) if cur in MODELS else 0,
        help="Modèle préféré. S'il est saturé ou indisponible, Léo essaie automatiquement les suivants.")
    with st.form("leo_key_form", clear_on_submit=True, border=False):
        new = st.text_input("Clé API Gemini", type="password", placeholder="AIza…")
        remember = st.checkbox("Enregistrer sur ce poste (fichier .secrets/, ignoré par git)", value=True)
        if st.form_submit_button("Utiliser cette clé", type="primary") and new.strip():
            if remember:
                save_key(new)
                st.session_state.pop("leo_session_key", None)
            else:
                st.session_state["leo_session_key"] = new.strip()
            st.rerun(scope="fragment" if in_dialog else "app")
    if key and origin in ("session", "fichier .secrets/gemini_api_key"):
        if st.button("Oublier la clé"):
            delete_key()
            st.rerun(scope="fragment" if in_dialog else "app")


def _context_chips(page: str) -> None:
    m = sh.ctx()["m"]
    sel = st.session_state.get(sh.SEL)
    chips = [f"Page : {PAGE_NAMES.get(page, page)}"]
    if sel in m.products.index:
        chips.append(f"Pièce : {sh.short(m.products.loc[sel, 'name'])}")
    chips.append("Gemini" if api_key()[0] else "Mode calcul")
    st.markdown("".join(f"<span class='leo-chip'>{html.escape(c)}</span>" for c in chips), unsafe_allow_html=True)


@st.dialog("Léo · assistant PLM", width="large")
def leo_dialog(page: str) -> None:
    pending = st.session_state.pop("leo_pending", None)
    if pending:
        ask(pending, page)
    _context_chips(page)
    hist = st.session_state.get(HIST, [])
    with st.container(height=420, border=False):
        if not hist:
            st.caption("Léo connaît la page ouverte et la pièce que vous avez sélectionnée. "
                       "Posez une question ou choisissez une suggestion.")
        _render_history(hist[-12:], compact=True)
    cols = st.columns(3)
    for col, (label, q) in zip(cols, quick_prompts(page)):
        if col.button(label, key=f"leo_dq_{label}", width="stretch"):
            ask(q, page)
            st.rerun(scope="fragment")
    with st.form("leo_form", clear_on_submit=True, border=False):
        a, b = st.columns([5, 1])
        q = a.text_input("Question", placeholder="Demandez à Léo…", label_visibility="collapsed")
        sent = b.form_submit_button("Envoyer", type="primary", width="stretch")
    if sent and q.strip():
        ask(q.strip(), page)
        st.rerun(scope="fragment")
    with st.expander("Réglages de Léo"):
        settings_panel(in_dialog=True)
        if hist and st.button("Effacer la conversation"):
            st.session_state[HIST] = []
            st.rerun(scope="fragment")


def launcher(page: str) -> None:
    """Bulle flottante en bas à droite, présente sur toutes les pages."""
    st.markdown(LEO_CSS, unsafe_allow_html=True)
    m = sh.ctx()["m"]
    sel = sh.selected(m)
    where = sh.short(m.products.loc[sel, "name"]) if sel in m.products.index else ""
    with st.container(key="leo_fab"):
        st.markdown(f"<div class='leo-head'><div class='leo-avatar'>L</div><div><b>Léo</b>"
                    f"<div class='leo-sub'>{html.escape(PAGE_NAMES.get(page, page))}"
                    f"{(' · ' + html.escape(where[:28])) if where else ''}</div></div></div>",
                    unsafe_allow_html=True)
        prompts = quick_prompts(page)
        clicked = None
        for label, q in prompts[:2]:
            if st.button(label, key=f"leo_fab_{label}", width="stretch"):
                clicked = q
        if st.button("Discuter avec Léo", key="leo_fab_open", width="stretch"):
            clicked = ""
    if clicked is not None:
        if clicked:
            st.session_state["leo_pending"] = clicked
        leo_dialog(page)
