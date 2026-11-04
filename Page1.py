"""Lancement : python -m streamlit run app.py"""
from copy import deepcopy
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import streamlit as st
from plm_data import analyse_xml, DEMO


def afficher_accueil():
    """Page autonome ; les autres pages peuvent lire session_state['plm_summary']."""
    st.caption('PRODUCT INTELLIGENCE / ECAR_PLM')
    st.title('Vue d’ensemble')
    st.write('Comprendre le produit, repérer les points de vigilance et préparer un changement.')

    if 'plm_summary' not in st.session_state:
        st.session_state['plm_summary'] = deepcopy(DEMO)
    uploaded = st.file_uploader('Importer un export XPDMXML', type=['xml'],
                                max_upload_size=10,
                                key=f"xml_upload_{st.session_state.get('upload_generation', 0)}",
                                help='10 Mo maximum. Le fichier est analysé en mémoire par Python.')
    if uploaded is not None:
        raw = uploaded.getvalue()
        token = (uploaded.name, sha256(raw).hexdigest())
        if token != st.session_state.get('plm_upload_token'):
            try:
                summary = analyse_xml(raw, uploaded.name)
            except (ValueError, UnicodeError) as exc:
                st.error(f'Import refusé : {exc} Les données précédentes sont conservées.')
            else:
                st.session_state['plm_summary'] = summary
                # Pour l’intégration : les autres pages peuvent parser cet export.
                st.session_state['plm_xml_bytes'] = raw
                st.session_state['plm_upload_token'] = token
                st.success('Import réussi : les indicateurs ont été recalculés.')
    else:
        st.session_state.pop('plm_upload_token', None)

    data = st.session_state['plm_summary']
    label = 'Export importé' if data['imported'] else 'Données de démonstration'
    st.markdown(f'''<div class="product-banner"><small>{label.upper()}</small>
        <h3>{escape(data['name'])}</h3>
        <span>{escape(data['filename'])} · Révision {escape(data['revision'] or '—')}</span>
        </div>''', unsafe_allow_html=True)
    if data['imported']:
        st.caption(f"Import : {data['importDate']} · La date d’import n’est pas la date d’export.")
    else:
        st.caption('Valeurs relevées dans l’export ECAR. Importez votre fichier pour recalculer les résultats.')

    st.subheader('Le produit en chiffres')
    metrics = [('Références produit', data['products'], 'Références uniques de tout l’export'),
               ('Liens d’instance', data['instances'], 'Liens parent → enfant de tout l’export'),
               ('Sous-ensembles directs', len(data['children']), 'Liens enfants de la racine'),
               ('En conception', data['inWork'], 'Références à la maturité IN_WORK')]
    for column, (name, count, help_text) in zip(st.columns(4), metrics):
        with column:
            with st.container(border=True):
                st.metric(name, count, help=help_text)

    ratio = round(100 * data['inWork'] / data['products']) if data['products'] else 0
    st.info(f"{len(data['children'])} sous-ensemble(s) direct(s) · "
            f"{ratio} % des références en conception · "
            f"Masse déclarée : {format_mass(data['mass'])} (unité à confirmer)")
    st.subheader('Sous-ensembles principaux')
    st.caption('Enfants directs du produit racine · masses déclarées, sans unité supposée')
    aliases = {'ECAR-EI_EMOTOR FRONT': 'Moteur électrique',
               'ECAR-EI_REDUCER FRONT': 'Réducteur',
               'ECAR-EI_HOUSING FRONT EDRIVE': 'Carter du groupe motopropulseur'}
    for offset in range(0, len(data['children']), 3):
        for col, child in zip(st.columns(3), data['children'][offset:offset + 3]):
            with col:
                with st.container(border=True):
                    st.markdown(f"**{aliases.get(child['name'], child['name'])}**")
                    st.caption(child['name'])
                    st.metric('Masse déclarée', format_mass(child['mass']))
                    st.caption(f"{child['id']} · Révision {child['revision'] or '—'}")
                    with st.expander('Source de cette valeur'):
                        st.text(f"Product[@id='{child['id']}'] / WCGEquivalentComputedExt.V_WCG_Mass")
    if not data['children']:
        st.info('Aucun enfant direct pour cette racine.')
    st.caption('Les masses des différents niveaux ne sont pas additionnées : cela compterait certaines pièces plusieurs fois.')

    left, right = st.columns([1.1, 1])
    with left:
        with st.container(border=True):
            st.subheader('Points à examiner')
            if data['inWork']:
                st.warning(f"{data['inWork']} référence(s) en conception : vérifier leur validation avant un changement.")
            else:
                st.info('Aucune référence IN_WORK. Les autres états ne sont pas assimilés automatiquement à une validation.')
            st.write('**Masse :** confirmer l’unité avant toute comparaison ou simulation.')
            st.write('**Conséquences financières :** compléter les coûts et fournisseurs avant de chiffrer un changement.')
            st.caption('Ces points signalent des vérifications à effectuer, pas des défauts mécaniques établis.')

    with right:
        with st.container(border=True):
            st.subheader('Qualité des données')
            if data['broken'] or data['cycles']:
                st.error(f"{data['broken']} lien(s) non résolu(s) · "
                         + ('cycle détecté' if data['cycles'] else 'aucun cycle détecté'))
            else:
                st.success('Structure cohérente : aucun lien cassé, aucun cycle détecté.')
            st.warning('Unité de masse à confirmer.')
            if data['extraBusinessFields']:
                st.info('Des champs de coût ou fournisseur ont été détectés : leur interprétation reste à confirmer.')
            else:
                st.info('Coûts et fournisseurs non disponibles pour cette analyse.')
            st.caption(f"{data['representations']} représentations 3D décrites. Aucun fichier géométrique affiché.")

    st.subheader('Poursuivre l’analyse')
    for col, title, target in zip(st.columns(3),
            ['Explorer le produit', 'Examiner les risques', 'Simuler un changement'],
            ['pages/2_Explorateur.py', 'pages/4_Risques.py', 'pages/5_Impact.py']):
        with col:
            available = (Path(__file__).resolve().parent / target).is_file()
            if st.button(title, disabled=not available, width='stretch',
                         help=None if available else 'Disponible après intégration de la page du groupe.'):
                st.switch_page(target)
            if not available:
                st.caption('Page du groupe à raccorder')

    with st.expander('Voir les sources et les règles de calcul'):
        st.write('Les compteurs portent sur les éléments Product et ProductInst. '
                 'Le nombre de références en conception compte les Product dont Maturity vaut IN_WORK.')
        st.code(f"/XPDMXML/ProductStructure/Product[@id='{data['root']}']\n"
                'Masse : WCGEquivalentComputedExt.V_WCG_Mass\n'
                'Lien parent : ProductInst/Owned\nLien enfant : ProductInst/Instancing', language='text')
        st.caption('Espace de noms : http://www.3ds.com/xsd/XPDMXML')
        if data.get('hash'):
            st.text('SHA-256 : ' + data['hash'])
        else:
            st.info('Empreinte disponible après import du fichier réel.')
        st.download_button('Télécharger la synthèse JSON',
                           json.dumps(data, ensure_ascii=False, indent=2),
                           file_name='synthese_plm.json', mime='application/json')
    st.caption('Les faits XML, résultats calculés et hypothèses métier doivent rester distincts.')


def format_mass(value):
    try:
        return f'{float(value):,.3f}'.replace(',', ' ').replace('.', ',')
    except (ValueError, TypeError):
        return 'Non fournie'


def main():
    st.set_page_config(page_title='PLM Intelligence — Accueil', page_icon='⚙️', layout='wide')
    st.markdown('''<style>
        .block-container {max-width:1450px;padding-top:2rem;}
        h1,h2,h3 {letter-spacing:-.035em;}
        .product-banner {background:#eaf0e4;border:1px solid #dce5d4;
            padding:24px;border-radius:12px;margin:16px 0;overflow-wrap:anywhere;}
        .product-banner small {color:#657b57;letter-spacing:2px;}
        .product-banner h3 {margin:10px 0;color:#25362e!important;font-size:1.4rem;}
        .product-banner span {color:#62745b;font-size:13px;}
        </style>''', unsafe_allow_html=True)
    with st.sidebar:
        st.title('⚙️ PLM Intelligence')
        st.caption('ESILV · Équipe ECAR')
        st.divider()
        st.markdown('**Vue d’ensemble**')
        st.caption('Comprendre → Examiner → Décider')
        st.divider()
        if st.button('Revenir aux données de démonstration'):
            for key in ('plm_summary', 'plm_xml_bytes', 'plm_upload_token'):
                st.session_state.pop(key, None)
            # Incrémenter la clé réinitialise aussi le widget d’import.
            st.session_state['upload_generation'] = st.session_state.get('upload_generation', 0) + 1
            st.rerun()
        st.caption('Le fichier importé est transmis au serveur Streamlit et analysé en mémoire, sans sauvegarde automatique.')
    afficher_accueil()


if __name__ == '__main__':
    main()
