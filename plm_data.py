"""Analyse XPDMXML réutilisable indépendamment de Streamlit."""
from collections import Counter, defaultdict
from datetime import datetime
from hashlib import sha256
from pathlib import Path
import xml.etree.ElementTree as ET

MAX_SIZE = 10 * 1024 * 1024

def analyse_xml(raw, filename='export.xml'):
    """Analyse un XPDMXML ; ne suppose aucune unité, aucun coût ou fournisseur."""
    if len(raw) > MAX_SIZE:
        raise ValueError('Le fichier dépasse 10 Mo.')
    # Normaliser avant le contrôle afin de couvrir UTF-16 et UTF-32.
    if raw.startswith((b'\xff\xfe\x00\x00', b'\x00\x00\xfe\xff')):
        text = raw.decode('utf-32')
    elif raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        text = raw.decode('utf-16')
    else:
        text = raw.decode('utf-8-sig')
    if '\x00' in text or '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
        raise ValueError('Les DTD et entités XML ne sont pas acceptées.')
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError('XML mal formé.') from exc
    for element in root.iter():
        element.tag = element.tag.rsplit('}', 1)[-1]
    if root.tag != 'XPDMXML':
        raise ValueError('Le document doit être un XPDMXML.')
    structure = root.find('ProductStructure')
    if structure is None:
        raise ValueError('Section ProductStructure absente.')
    elements = list(structure)
    ids = [e.get('id') for e in elements]
    if any(not value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError('Identifiant absent ou dupliqué.')
    products = {e.get('id'): e for e in structure.findall('Product')}
    roots = structure.get('rootRefs', '').split()
    if len(roots) != 1 or roots[0] not in products:
        raise ValueError('Une racine produit unique et résolue est nécessaire.')
    def value(e, field):
        return (e.findtext(field) or '').strip()
    adjacency = defaultdict(list)
    instances = structure.findall('ProductInst')
    representations = structure.findall('Rep3DAggr')
    broken = 0
    for instance in instances:
        parent, child = value(instance, 'Owned'), value(instance, 'Instancing')
        if parent not in products or child not in products:
            broken += 1
        else:
            adjacency[parent].append(child)
    broken += sum(value(e, 'Owned') not in products for e in representations)
    # Parcours itératif : préserver les répétitions et détecter les cycles.
    colors, cycles = {}, False
    for start in products:
        if colors.get(start):
            continue
        stack = [(start, False)]
        while stack:
            current, finished = stack.pop()
            if finished:
                colors[current] = 2
                continue
            if colors.get(current) == 1:
                cycles = True
                continue
            if colors.get(current) == 2:
                continue
            colors[current] = 1
            stack.append((current, True))
            stack.extend((child, False) for child in adjacency[current])
    product = products[roots[0]]
    return {
        'filename': Path(filename.replace('\\', '/')).name[:200],
        'hash': sha256(raw).hexdigest(), 'root': roots[0],
        'name': value(product, 'Name') or roots[0],
        'revision': value(product, 'RevisionName'),
        'products': len(products), 'instances': len(instances),
        'inWork': Counter(value(e, 'Maturity') for e in products.values())['IN_WORK'],
        'representations': len(representations), 'broken': broken, 'cycles': cycles,
        'mass': value(product, 'WCGEquivalentComputedExt.V_WCG_Mass'),
        'imported': True, 'importDate': datetime.now().astimezone().strftime('%d/%m/%Y %H:%M'),
        'extraBusinessFields': any(
            any(word in child.tag.lower() for word in ('supplier', 'cost', 'price', 'fournisseur'))
            for e in elements for child in e),
        'children': [{
            'id': child, 'name': value(products[child], 'Name') or child,
            'revision': value(products[child], 'RevisionName'),
            'mass': value(products[child], 'WCGEquivalentComputedExt.V_WCG_Mass'),
        } for child in adjacency[roots[0]]],
    }


DEMO = {'filename': 'XPDMXML_ECAR_PLM.xml', 'root': 'ID_0', 'name': 'ECAR-EI_410KW_EDRIVE_FRONT', 'revision': 'D', 'products': 79, 'instances': 112, 'inWork': 79, 'representations': 36, 'broken': 0, 'cycles': False, 'mass': '100.100', 'imported': False, 'extraBusinessFields': False, 'children': [{'id': 'ID_1', 'name': 'ECAR-EI_EMOTOR FRONT', 'revision': 'C', 'mass': '39.730'}, {'id': 'ID_17', 'name': 'ECAR-EI_REDUCER FRONT', 'revision': 'D', 'mass': '29.850'}, {'id': 'ID_51', 'name': 'ECAR-EI_HOUSING FRONT EDRIVE', 'revision': 'C', 'mass': '30.520'}]}
