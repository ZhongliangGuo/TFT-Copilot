"""Portable 1920x1080 ally preparation-stage preprocessing; no GT required."""
import argparse
import json
from pathlib import Path
import numpy as np
from PIL import Image
from hud_stage import get_slots, locate, save_crops


def preprocess(image, out, occupied=None, mode='ally'):
    if image.size != (1920, 1080):
        raise ValueError('Expected an uncropped 1920x1080 screenshot; do not resize other aspect ratios.')
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    rows = []
    for slot in get_slots(mode):
        key = slot['key']; cx, cy = slot['center']
        x, y = round(cx-90), round(cy-165)
        fx, fy = round(cx-65), round(cy-95)
        image.crop((x,y,x+180,y+205)).save(out/(key+'.png'))
        image.crop((fx,fy,fx+130,fy+120)).save(out/(key+'_focus.png'))
        yy, xx = np.mgrid[:205,:180]
        anchor = np.rint(np.exp(-((xx-(cx-x))**2+(yy-(cy-y))**2)/200)*255).astype('uint8')
        Image.fromarray(anchor).save(out/(key+'_anchor.png'))
        rows.append(dict(cell=key,image=key+'.png',focus=key+'_focus.png',anchor=key+'_anchor.png',
                         bbox=[x,y,180,205],target_point=[cx-x,cy-y]))
    (out/'identity.json').write_text(json.dumps(rows,indent=2),encoding='utf8')
    if occupied is not None:
        save_crops(image,locate(image,get_slots(mode),occupied),out/'hud')
    return rows


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('image',type=Path);p.add_argument('--out',required=True,type=Path)
    p.add_argument('--occupied',type=Path,help='JSON list of nonempty slot keys predicted by identity model')
    a=p.parse_args()
    preprocess(Image.open(a.image).convert('RGB'),a.out,
               json.loads(a.occupied.read_text('utf8')) if a.occupied else None)
