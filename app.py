"""PLM Risk Intelligence — page 4. Scores explicables, sans données inventées."""
from collections import defaultdict
from pathlib import Path
import xml.etree.ElementTree as ET
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="PLM • Risk Intelligence", page_icon="🛡️", layout="wide", initial_sidebar_state="expanded")
st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap');
html,body,[class*="css"],.stApp{font-family:'DM Sans',sans-serif}
.stApp{background:#f4f7fb;color:#17263c}.block-container{padding-top:1.6rem;max-width:1450px}
h1,h2,h3{color:#14263f;letter-spacing:-.025em}h1{font-weight:750!important}
[data-testid="stSidebar"]{background:#101d33}[data-testid="stSidebar"] *{color:#e7edf7!important}
[data-testid="stSidebar"] input{color:#17263c!important}
[data-testid="stMetric"]{background:white;border:1px solid #e3eaf2;border-radius:17px;padding:17px;box-shadow:0 5px 18px #152f5410}
[data-testid="stMetricLabel"]{color:#62738a!important}
[data-testid="stMetricValue"]{color:#182d48!important}
[data-testid="stTabs"] button{font-weight:650}
[data-testid="stAlert"]{border-radius:14px}
.hero{background:linear-gradient(115deg,#11223b,#214568);border-radius:22px;padding:28px 32px;color:#fff;margin-bottom:22px;box-shadow:0 12px 28px #11223b18}
.hero h1{color:white!important;font-size:2.25rem;margin:0 0 8px}.hero p{color:#c6d6e8;margin:0}
.eyebrow{color:#8ecce6;letter-spacing:2px;font-size:.72rem;font-weight:700;margin-bottom:8px}
.note{background:#fff;border:1px solid #e1e8f1;border-left:4px solid #3485b9;border-radius:13px;padding:16px 19px;margin:10px 0 18px;line-height:1.65}
.note strong{color:#183a5a}.subtle{color:#65778e;font-size:.91rem}
.section{font-size:1.15rem;font-weight:750;margin:20px 0 8px}
</style>""", unsafe_allow_html=True)

@st.cache_data(show_spinner=False)
def parse_xml(data:bytes):
    root=ET.fromstring(data)
    def tag(e):return e.tag.rsplit('}',1)[-1]
    structure=next((e for e in root if tag(e)=='ProductStructure'),None)
    if structure is None:raise ValueError('ProductStructure introuvable')
    def field(e,n):return next(((x.text or '').strip() for x in e if tag(x)==n),'')
    products={};instances=[]
    for e in structure:
        if tag(e)=='Product':
            pid=e.attrib.get('id','')
            if not pid:continue
            products[pid]={'xml_id':pid,'reference':field(e,'ID'),'name':field(e,'Name') or field(e,'ID') or pid,
                           'maturity':field(e,'Maturity') or 'NON RENSEIGNÉ','revision':field(e,'RevisionName'),
                           'source':f"Product[@id='{pid}']"}
        elif tag(e)=='ProductInst':
            instances.append({'instance_id':e.attrib.get('id',''),'parent':field(e,'Owned'),
                              'child':field(e,'Instancing'),'name':field(e,'ID')})
    instances=[i for i in instances if i['parent'] in products and i['child'] in products]
    parents=defaultdict(set);children=defaultdict(set)
    for i in instances:parents[i['child']].add(i['parent']);children[i['parent']].add(i['child'])
    return products,instances,dict(parents),dict(children)

def traverse(node,edges):
    found=set();todo=list(edges.get(node,[]))
    while todo:
        x=todo.pop()
        if x not in found and x!=node:found.add(x);todo.extend(edges.get(x,[]))
    return found

def compute(products,instances,parents,children,weights):
    counts={}
    for i in instances:counts[i['child']]=counts.get(i['child'],0)+1
    rows=[]
    for pid,p in products.items():
        rows.append({**p,'ancetres':len(traverse(pid,parents)),'parents':len(parents.get(pid,set())),
                     'occurrences':counts.get(pid,0),'terminal':not bool(children.get(pid))})
    df=pd.DataFrame(rows)
    if df.empty:return df
    max_a=max(int(df.ancetres.max()),1);max_p=max(int(df.parents.max()),1)
    df['dep_norm']=df.ancetres/max_a
    df['reuse_norm']=df.parents/max_p
    final={'RELEASED','FROZEN','APPROVED','VALIDATED'}
    df['non_final']=~df.maturity.str.upper().isin(final)
    total=sum(weights)
    df['score']=(100*(weights[0]*df.dep_norm+weights[1]*df.reuse_norm+weights[2]*df.non_final.astype(int))/total).round(1)
    df['score_sans_maturite']=(100*(.7*df.dep_norm+.3*df.reuse_norm)).round(1)
    df['niveau']=pd.cut(df.score,bins=[-0.1,35,65,100],labels=['Faible','Modéré','Élevé'],include_lowest=True)
    df['raison']=df.apply(lambda r:f"{int(r.ancetres)} assemblage(s) ancêtre(s), {int(r.parents)} parent(s) direct(s), maturité {r.maturity}",axis=1)
    return df.sort_values(['score','ancetres','parents'],ascending=False).reset_index(drop=True)

def box_note(title,body):
    st.markdown(f'<div class="note"><strong>{title}</strong><br>{body}</div>',unsafe_allow_html=True)

def plot_style(fig,height=410):
    fig.update_layout(height=height,paper_bgcolor='rgba(0,0,0,0)',plot_bgcolor='rgba(0,0,0,0)',
        font=dict(family='DM Sans',color='#354961'),margin=dict(l=12,r=12,t=40,b=20),
        legend_title_text='',hoverlabel=dict(bgcolor='white'),xaxis=dict(gridcolor='#e9eef5'),yaxis=dict(gridcolor='#e9eef5'))
    return fig

st.sidebar.markdown('## 🛡️ PLM Intelligence')
st.sidebar.caption('MODULE 04 · RISK INTELLIGENCE')
up=st.sidebar.file_uploader('Importer un export XML',type=['xml'])
try:
    xml=up.getvalue() if up else (Path(__file__).parent/'XPDMXML_ECAR_PLM.xml').read_bytes()
    products,instances,parents,children=parse_xml(xml)
except Exception as exc:
    st.error(f'Lecture XML impossible : {exc}');st.stop()
if not products:st.warning('Aucune référence produit dans cet export.');st.stop()
st.sidebar.markdown('### ⚖️ Pondérations')
a=st.sidebar.slider('Propagation vers les assemblages',0,100,55,5)
b=st.sidebar.slider('Réutilisation dans plusieurs parents',0,100,25,5)
c=st.sidebar.slider('Maturité non finalisée',0,100,20,5)
if a+b+c==0:st.sidebar.error('Au moins une pondération doit être positive.');st.stop()
st.sidebar.caption(f'Poids effectifs : {a/(a+b+c):.0%} / {b/(a+b+c):.0%} / {c/(a+b+c):.0%}')
df=compute(products,instances,parents,children,(a,b,c))
st.sidebar.markdown('### 🔎 Explorer')
query=st.sidebar.text_input('Nom ou référence',placeholder='Rechercher une pièce…')
mat=st.sidebar.multiselect('Maturité',sorted(df.maturity.unique()),default=sorted(df.maturity.unique()))
levels=st.sidebar.multiselect('Priorité', ['Élevé','Modéré','Faible'],default=['Élevé','Modéré','Faible'])
leaves=st.sidebar.checkbox('Pièces terminales uniquement')
filtered=df[df.maturity.isin(mat)&df.niveau.astype(str).isin(levels)].copy()
if query:filtered=filtered[filtered.name.str.contains(query,case=False,regex=False)|filtered.reference.str.contains(query,case=False,regex=False)]
if leaves:filtered=filtered[filtered.terminal]

st.markdown('<div class="hero"><div class="eyebrow">PRODUCT LIFECYCLE MANAGEMENT / 04</div><h1>Risk Intelligence</h1><p>Prioriser les composants à examiner · Comprendre les dépendances · Justifier chaque décision</p></div>',unsafe_allow_html=True)
all_nonfinal=int(df.non_final.sum());multi=int((df.parents>1).sum());high=int((df.niveau=='Élevé').sum())
k1,k2,k3,k4=st.columns(4)
k1.metric('Références analysées',f'{len(df)}')
k2.metric('Relations d’instance',f'{len(instances)}')
k3.metric('Priorité élevée (modèle)',f'{high}')
k4.metric('Parents multiples',f'{multi}')
box_note('Lecture rapide',f"Le modèle classe <b>{high} référence(s)</b> en priorité élevée avec les pondérations actuelles. <b>{multi} référence(s)</b> sont utilisées par plusieurs parents directs. <b>{all_nonfinal}/{len(df)}</b> ont un état de maturité considéré comme non finalisé. Ces constats décrivent un <b>besoin de revue</b>, pas une panne avérée.")
if df.maturity.nunique()==1:
    st.info(f"La maturité est identique pour toutes les références ({df.maturity.iloc[0]}). Ce critère ajoute une constante au score et ne départage donc pas les pièces : privilégier les dépendances pour interpréter le classement.")

t1,t2,t3,t4=st.tabs(['📊 Vue d’ensemble','🏆 Priorités & explications','🔬 Analyse d’une pièce','🧭 Méthode & sensibilité'])
with t1:
    left,right=st.columns([1.25,1])
    with left:
        st.markdown('<div class="section">Carte des dépendances structurelles</div>',unsafe_allow_html=True)
        if not filtered.empty:
            fig=px.scatter(filtered,x='parents',y='ancetres',size='score',color='niveau',
                color_discrete_map={'Faible':'#23a58b','Modéré':'#e6a73d','Élevé':'#d75c65'},
                category_orders={'niveau':['Faible','Modéré','Élevé']},
                hover_name='name',hover_data={'reference':True,'score':True,'source':True,'parents':True,'ancetres':True},
                size_max=34,labels={'parents':'Parents directs distincts','ancetres':'Assemblages ancêtres distincts','niveau':'Priorité'})
            fig.update_traces(marker=dict(line=dict(width=1,color='white'),opacity=.85))
            st.plotly_chart(plot_style(fig,460),use_container_width=True)
        else:st.warning('Aucune référence pour ces filtres.')
    with right:
        st.markdown('<div class="section">Répartition des niveaux</div>',unsafe_allow_html=True)
        cnt=filtered.niveau.value_counts().reindex(['Faible','Modéré','Élevé'],fill_value=0).reset_index()
        cnt.columns=['niveau','nombre']
        fig=px.pie(cnt,names='niveau',values='nombre',hole=.67,color='niveau',
            color_discrete_map={'Faible':'#23a58b','Modéré':'#e6a73d','Élevé':'#d75c65'})
        fig.update_traces(textinfo='percent+label',sort=False)
        st.plotly_chart(plot_style(fig,460),use_container_width=True)
    box_note('Comment lire ces graphiques ?',"Chaque point correspond à une <b>référence produit</b>. Plus il est haut, plus un changement pourrait remonter vers des assemblages distincts ; plus il est à droite, plus il est partagé entre plusieurs parents. La taille et la couleur reflètent le <b>score heuristique</b>, et non une probabilité de défaillance. Les seuils 35/65 sont des repères de présentation, pas des normes métier.")
    st.markdown('<div class="section">Les 12 références les mieux classées</div>',unsafe_allow_html=True)
    top=filtered.head(12).sort_values('score')
    if not top.empty:
        fig=px.bar(top,x='score',y='name',orientation='h',color='score',color_continuous_scale=['#91d8cf','#e8bc64','#cf5967'],
                   hover_data=['reference','raison'],labels={'score':'Score de priorité / 100','name':''})
        fig.update_layout(coloraxis_showscale=False)
        st.plotly_chart(plot_style(fig,max(350,35*len(top)+110)),use_container_width=True)
        leader=filtered.iloc[0]
        box_note('Point d’attention principal',f"<b>{leader['name']}</b> ({leader['reference'] or leader['xml_id']}) arrive en tête avec <b>{leader['score']:.1f}/100</b>. Son classement s'explique par <b>{leader['ancetres']} assemblage(s) ancêtre(s)</b> et <b>{leader['parents']} parent(s) direct(s)</b>. Une modification de cette référence justifie une revue des assemblages amont avant validation.")
with t2:
    st.markdown('### Classement transparent des références')
    st.caption('Classement recalculé immédiatement selon les curseurs et les filtres. Un score élevé signifie « examiner en priorité ».')
    view=filtered[['name','reference','revision','maturity','ancetres','parents','occurrences','score','niveau','raison','source']].rename(columns={
        'name':'Composant','reference':'Référence','revision':'Révision','maturity':'Maturité','ancetres':'Assemblages amont',
        'parents':'Parents directs','occurrences':'Occurrences','score':'Score / 100','niveau':'Priorité','raison':'Explication','source':'Source XML'})
    st.dataframe(view,hide_index=True,use_container_width=True,height=460,
        column_config={'Score / 100':st.column_config.ProgressColumn('Score / 100',min_value=0,max_value=100,format='%.1f')})
    st.download_button('⬇️ Exporter le classement et les justifications',filtered.to_csv(index=False).encode('utf-8-sig'),file_name='plm_risk_intelligence_classement.csv',mime='text/csv')
    if not filtered.empty:
        n_high=int((filtered.niveau=='Élevé').sum());n_mid=int((filtered.niveau=='Modéré').sum())
        box_note('Interprétation du classement',f"Dans la sélection affichée : <b>{n_high} priorité(s) élevée(s)</b> et <b>{n_mid} priorité(s) modérée(s)</b>. Examiner d'abord les références qui cumulent une forte propagation amont et une réutilisation directe. Une priorité élevée <b>n’implique pas</b> que la pièce soit défectueuse.")
with t3:
    if filtered.empty:st.warning('Aucune référence sélectionnable. Modifie les filtres.')
    else:
        ids=filtered.xml_id.tolist()
        chosen=st.selectbox('Référence à analyser',ids,format_func=lambda x:f"{products[x]['name']} · {products[x]['reference'] or x}")
        r=df.set_index('xml_id').loc[chosen]
        st.markdown(f"### {r['name']}")
        st.caption(f"Référence : {r['reference'] or 'non renseignée'} · Révision : {r['revision'] or 'non renseignée'} · Maturité : {r['maturity']}")
        x1,x2,x3=st.columns(3)
        x1.metric('Score de priorité',f"{r['score']:.1f}/100")
        x2.metric('Assemblages ancêtres',int(r['ancetres']))
        x3.metric('Parents directs',int(r['parents']))
        parts=pd.DataFrame({'Critère':['Propagation amont','Réutilisation directe','Maturité non finalisée'],
            'Contribution':[100*a/(a+b+c)*r['dep_norm'],100*b/(a+b+c)*r['reuse_norm'],100*c/(a+b+c)*int(r['non_final'])]})
        fig=px.bar(parts,x='Contribution',y='Critère',orientation='h',color='Critère',
            color_discrete_sequence=['#2876a7','#26a69a','#e5a644'],labels={'Contribution':'Points dans le score','Critère':''})
        fig.update_layout(showlegend=False,xaxis_range=[0,100])
        st.plotly_chart(plot_style(fig,300),use_container_width=True)
        reasons=[]
        if r['ancetres']>0:reasons.append(f"sa modification pourrait concerner <b>{int(r['ancetres'])} référence(s) d’assemblage amont</b>")
        else:reasons.append('aucun assemblage ancêtre n’est identifié dans cet export')
        if r['parents']>1:reasons.append(f"elle est <b>partagée entre {int(r['parents'])} parents directs</b>")
        elif r['parents']==1:reasons.append('elle possède un parent direct')
        else:reasons.append('aucun parent direct n’est identifié')
        reasons.append(f"son état <b>{r['maturity']}</b> est {'considéré comme non finalisé' if r['non_final'] else 'considéré comme finalisé'} par la règle choisie")
        box_note('Pourquoi ce score ?', ' ; '.join(reasons).capitalize()+'. Le score reflète une <b>priorité de revue technique</b>, pas un risque quantifié de panne.')
        ancestors=traverse(chosen,parents)
        if ancestors:
            st.markdown('#### Assemblages amont potentiellement concernés')
            st.dataframe(pd.DataFrame([{'Nom':products[k]['name'],'Référence':products[k]['reference'],'ID XML':k} for k in sorted(ancestors)]),hide_index=True,use_container_width=True)
        else:st.info('Aucun assemblage amont identifié : racine ou référence isolée.')
        st.markdown('#### Traçabilité XML')
        st.code(f"ProductStructure/Product[@id='{chosen}']",language='text')
        linked=[i for i in instances if i['parent']==chosen or i['child']==chosen]
        if linked:st.dataframe(pd.DataFrame([{'ProductInst/@id':i['instance_id'],'Owned (parent)':i['parent'],'Instancing (enfant)':i['child']} for i in linked]),hide_index=True,use_container_width=True)
        st.markdown('#### Action recommandée')
        st.write('Vérifier la configuration et les interfaces des assemblages amont, puis confirmer auprès des équipes conception et qualité si une modification nécessite une revue ou une revalidation. Cette recommandation est une démarche de contrôle, pas un diagnostic de défaut.')
with t4:
    st.markdown('### Modèle de scoring : entièrement explicable')
    st.latex(r"S_i=100\,\frac{w_a\,A_i/A_{max}+w_p\,P_i/P_{max}+w_m\,M_i}{w_a+w_p+w_m}")
    st.markdown("""**A** = nombre d'assemblages ancêtres distincts (propagation potentielle). **P** = nombre de parents directs distincts (réutilisation). **M** = 1 si maturité non finalisée, sinon 0. Les maxima sont calculés sur l'export entier pour que les filtres ne changent pas les scores.

**Hypothèse de maturité :** `RELEASED`, `FROZEN`, `APPROVED`, `VALIDATED` sont traités comme finalisés ; tous les autres états sont traités comme non finalisés. À valider selon le référentiel PLM réel.

**Limites :** les liens `ProductInst/Owned` et `ProductInst/Instancing` décrivent des dépendances structurelles, pas des probabilités de défaillance. Le XML ne fournit pas de base fiable pour estimer des coûts, des fournisseurs ou des probabilités de panne. Les seuils de priorité (35 et 65) sont indicatifs.
""")
    st.markdown('### Test de sensibilité : avec ou sans maturité')
    comparison=df[['name','score','score_sans_maturite']].copy()
    comparison['rang_actuel']=comparison.score.rank(ascending=False,method='min').astype(int)
    comparison['rang_structure']=comparison.score_sans_maturite.rank(ascending=False,method='min').astype(int)
    comparison['écart_de_rang']=comparison.rang_structure-comparison.rang_actuel
    st.dataframe(comparison.sort_values('rang_actuel').head(20),hide_index=True,use_container_width=True)
    if df.maturity.nunique()==1:
        box_note('Résultat de sensibilité','Toutes les références partagent le même état de maturité : la maturité ne peut pas modifier l’ordre du classement tant que les deux pondérations structurelles restent proportionnelles. Elle peut en revanche modifier les valeurs absolues et les catégories de priorité.')
    else:
        box_note('Comment utiliser ce test ?','Compare le rang actuel au rang fondé uniquement sur les dépendances (70 % propagation, 30 % réutilisation). Les écarts montrent la sensibilité du classement au critère de maturité et au choix des poids.')
    st.markdown('### Vérification des données et sources')
    st.write(f"{len(products)} références `Product`, {len(instances)} relations valides `ProductInst`, {len(set(df.maturity))} état(s) de maturité distinct(s).")
    st.code("ProductStructure/Product[@id='…']\nProductStructure/ProductInst/Owned → parent\nProductStructure/ProductInst/Instancing → enfant",language='text')
    st.warning('Aucune interprétation ne doit être présentée comme une analyse de fiabilité physique ou financière sans enrichissement et validation métier.')

st.divider()
st.caption('PLM Risk Intelligence · Démonstrateur hackathon · Résultats calculés depuis le XML chargé · Indice heuristique de priorité structurelle')
