"""Second-stage HUD crops from full RGB and first-stage occupied slot keys.

No renderer boxes or ground-truth identity/star/item labels enter this module.
Legacy v2 compilation is deliberately separate and remains reproducible.
"""
import argparse
import json
import math
import sys
from pathlib import Path
from PIL import Image
from hud_regions import detect_health_bars

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PROFILE = dict(id='s18-fixed-1920-v4', pitch=45.552433094138294, fov=32.339498296693534,
               horizontalTolerance=20, verticalRange=[40,240], equipmentLiftHypotheses=[0,28],
               equipmentOffsetSize=[-4,10,74,26], starOffsetSize=[-38,-11,40,26])


def overlap(a, b):
    return min(a[0]+a[2], b[0]+b[2]) > max(a[0], b[0]) and min(a[1]+a[3], b[1]+b[3]) > max(a[1], b[1])


def equipment_box(bar):
    x, y, _, _ = bar['barBBox']
    return [x-4, y+10, 74, 26]


def assign_occupied(slots, bars, occupied, perspective=True, mode='ally'):
    """Reciprocal unique candidates; never break ties with arbitrary nearest bar.

    Calibrated vertical projection accommodates measured HUD displacement.
    Every eligible edge participates in the reverse conflict check, including
    edges from slots with multiple candidates. This prevents a unique slot
    from claiming a bar also eligible for an ambiguous neighbor.
    """
    occupied = set(occupied)
    known = {s['key'] for s in slots}
    if occupied - known:
        raise ValueError('Unknown occupied slots: '+str(sorted(occupied-known)))
    # Vertical world lines converge at this pixel for the fixed calibrated
    # camera. HUD is additionally raised 28 screen pixels when equipped.
    # Both hypotheses are tested; no equipment label is used.
    vanishing_y = 540 + 540 / (math.tan(math.radians(PROFILE['fov']/2))*math.tan(math.radians(PROFILE['pitch'])))
    ymax, dxtol = (180, 12) if mode == 'enemy' else (240, 20)  # 敌方上半屏透视压缩, 血条更近更密, 收紧判定
    def eligible(x, y, b):
        bx, by = b['center']
        if not 40 <= y-by <= ymax: return False
        if not perspective: return abs(bx-x)<=65
        expected = [960+(x-960)*(by+shift-vanishing_y)/(y-vanishing_y)+1 for shift in (0,28)]
        return min(abs(bx-ex) for ex in expected) <= dxtol
    edges = {}
    for s in slots:
        x, y = s['center']
        edges[s['key']] = [b for b in bars if eligible(x,y,b)] if s['key'] in occupied else []
    owners = {}
    for key, candidates in edges.items():
        for b in candidates:
            owners.setdefault(b['id'], []).append(key)
    result = {}
    for s in slots:
        key = s['key']; candidates = edges[key]; candidate = None
        if key not in occupied: status = 'empty'
        elif not candidates: status = 'not_found'
        elif len(candidates) > 1: status = 'ambiguous'
        elif len(owners[candidates[0]['id']]) > 1: status = 'shared_candidate'
        else: status = 'unique_candidate'; candidate = candidates[0]
        result[key] = dict(status=status, candidates=[b['id'] for b in candidates], candidate=candidate)
    return result


def locate(image, slots, occupied, mode='ally'):
    if image.size != (1920, 1080):
        raise ValueError('Expected full 1920x1080 screenshot')
    bars = detect_health_bars(image)
    assigned = assign_occupied(slots, bars, occupied, mode=mode)
    for result in assigned.values():
        b = result['candidate']
        if b is None: continue
        # Merged/occluded or depleted strips cannot establish an intact row.
        # Keep them in the assignment graph so they still block conflicts.
        if not (62 <= b['barBBox'][2] <= 70 and 2 <= b['barBBox'][3] <= 6):
            result['status'] = 'irregular_health_bar'
            continue
        regions = dict(stars=b['badgeBBox'], equipment=equipment_box(b))
        result['regions'] = {}
        for task, box in regions.items():
            conflicts = [other['id'] for other in bars if other['id'] != b['id'] and any(overlap(box, foreign) for foreign in (other['barBBox'], other['badgeBBox'], equipment_box(other)))]
            x, y, w, h = box
            status = 'out_of_frame' if x < 0 or y < 0 or x+w > image.width or y+h > image.height else 'neighbor_hud_overlap' if conflicts else 'ready'
            result['regions'][task] = dict(bbox=box, status=status, conflicts=conflicts)
    return dict(schemaVersion=4, resolution=[1920,1080], profile=PROFILE, occupiedInputSource='first-stage predictions',
                contract='full-health preparation RGB; reciprocal unique ownership; unresolved is not empty equipment',
                hudCandidates=bars, slots=assigned)


def save_crops(image, manifest, output):
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    previous = output/'manifest.json'
    if previous.exists():
        old = json.loads(previous.read_text('utf8'))
        for result in old.get('slots',{}).values():
            for region in result.get('regions',{}).values():
                if region.get('file'):
                    owned = (output/region['file']).resolve()
                    if output.resolve() in owned.parents and owned.suffix == '.png' and owned.is_file():
                        owned.unlink()
    # Also remove same-slot files left by older diagnostic versions which
    # omitted failed regions from their manifest before cleanup was added.
    known_keys={s['key'] for s in get_slots()}
    for key in manifest['slots']:
        if key not in known_keys:
            raise ValueError('Unexpected slot key in crop manifest')
        for task in ('stars','equipment'):
            owned=(output/task/(key+'.png')).resolve()
            if output.resolve() in owned.parents and owned.is_file():owned.unlink()
    for key, result in manifest['slots'].items():
        for task, region in result.get('regions', {}).items():
            if region['status'] != 'ready': continue
            x, y, w, h = region['bbox']; filename = task+'/'+key+'.png'
            (output/task).mkdir(exist_ok=True)
            image.crop((x,y,x+w,y+h)).save(output/filename)
            region['file'] = filename
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')


def get_slots(mode='ally'):
    c = json.loads((HERE / 'regions.json').read_text('utf8'))
    slots = [dict(key=f"board_{s['row']}_{s['col']}", center=s['center']) for s in c['cells' if mode=='ally' else 'enemyCells']]
    return slots+[dict(key=f'bench_0_{i}',center=center) for i,center in enumerate(c['benchCenters' if mode=='ally' else 'enemyBenchCenters'])]


def supervise(manifest, objects, mode='ally'):
    """Check labels AFTER RGB localization; never relocate a proposed crop.

    For the next synthetic compiler only. Not an inference-time filter.
    Empty equipment lists are valid negatives only for verified clean rows.
    """
    def key(o):
        col=o['col'] if mode=='ally' else (8-o['col'] if o['bench'] else 6-o['col'])
        row=0 if o['bench'] else o['row']-4 if mode=='ally' else 3-o['row']
        return ('bench' if o['bench'] else 'board')+f'_{row}_{col}'
    targets={key(o):o for o in objects if o['kind']!='tactician'}
    def contains(a,b):
        return a[0]<=b[0] and a[1]<=b[1] and b[0]+b[2]<=a[0]+a[2] and b[1]+b[3]<=a[1]+a[3]
    for cell,result in manifest['slots'].items():
        obj=targets.get(cell);candidate=result['candidate']
        hb=obj.get('healthBarBBox') if obj else None
        verified=bool(candidate and hb and abs(candidate['barBBox'][0]-(hb[0]+24))<=2 and abs(candidate['barBBox'][1]-(hb[1]+11))<=2)
        for task,region in result.get('regions',{}).items():
            reasons=[];box=region['bbox']
            if region['status']!='ready':reasons.append(region['status'])
            if not verified:reasons.append('wrong_or_missing_owner')
            if obj and obj.get('fullyOccluded'):reasons.append('target_fully_occluded')
            own=[];foreign=[]
            if obj:
                if task=='equipment':
                    own=[i['bbox'] for i in obj.get('equipment',[]) if i.get('bbox')]
                    if any(not i.get('bbox') for i in obj.get('equipment',[])):reasons.append('missing_item_box')
                else:
                    own=[obj['starBadgeBBox']] if obj.get('starBadgeBBox') else []
                    if not own:reasons.append('missing_star_badge')
                for other in objects:
                    if other['instanceId']==obj['instanceId']:continue
                    foreign.extend(i['bbox'] for i in other.get('equipment',[]) if i.get('bbox'))
                    if other.get('starBadgeBBox'):foreign.append(other['starBadgeBBox'])
                    if other.get('healthBarBBox'):foreign.append(other['healthBarBBox'])
            if any(not contains(box,b) for b in own):reasons.append('target_clipped')
            if any(overlap(box,b) for b in foreign):reasons.append('foreign_hud_in_crop')
            supervision=dict(eligible=not reasons,reasons=reasons,source='synthetic GT verification only; crop unchanged')
            if not reasons:
                if task=='stars':supervision['label']=obj['star']
                else:supervision['objects']=[dict(itemId=i['itemId'],bbox=[i['bbox'][0]-box[0],i['bbox'][1]-box[1],i['bbox'][2],i['bbox'][3]]) for i in obj.get('equipment',[])]
            region['supervision']=supervision
    return manifest


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('image',type=Path)
    p.add_argument('--occupied',required=True,type=Path,help='JSON list of nonempty slot keys from identity classifier')
    p.add_argument('--out',required=True,type=Path)
    p.add_argument('--mode',choices=['ally','enemy'],default='ally')
    a = p.parse_args(); image = Image.open(a.image).convert('RGB')
    occupied = json.loads(a.occupied.read_text('utf8'))
    if not isinstance(occupied,list) or not all(isinstance(k,str) for k in occupied):
        p.error('--occupied must contain a JSON list of slot key strings')
    save_crops(image,locate(image,get_slots(a.mode),occupied),a.out)
