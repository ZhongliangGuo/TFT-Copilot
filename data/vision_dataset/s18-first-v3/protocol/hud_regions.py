"""Conservative image-only HUD proposals for full-health preparation screenshots.

No unit labels, renderer boxes or star labels are accepted as detector inputs.
Ambiguous cell ownership is deliberately left unresolved. This is a proposal
baseline, not a substitute for a trained HUD/footpoint association detector.
"""
import numpy as np
from scipy import ndimage


def detect_health_bars(image):
    a=np.asarray(image.convert('RGB'),dtype=np.int16)
    green=(a[:,:,1]>110)&(a[:,:,1]>a[:,:,0]*1.65)&(a[:,:,1]>a[:,:,2]*1.5)
    joined=ndimage.binary_closing(green,structure=np.ones((1,3)))
    labels,n=ndimage.label(joined)
    bars=[]
    for region in ndimage.find_objects(labels):
        if region is None:continue
        ys,xs=region;x,y,w,h=xs.start,ys.start,xs.stop-xs.start,ys.stop-ys.start
        if not (50<=w<=85 and 2<=h<=9 and 60<y<850):continue
        # HUD green strips have a long near-black border, unlike foliage.
        dark=np.max(a[max(0,y-3):min(1080,y+h+3),max(0,x-2):x+w+2],axis=2)<85
        if max(dark.mean(axis=1),default=0)<.65:continue
        bars.append(dict(id=len(bars)+1,barBBox=[x,y,w,h],center=[x+w/2,y+h/2],
                         badgeBBox=[max(0,x-38),max(0,y-11),40,26]))
    return bars


def associate_bar(center,bars):
    x,y=center
    eligible=[b for b in bars if abs(b['center'][0]-x)<=42 and 40<=y-b['center'][1]<=205]
    return dict(status='unique_candidate' if len(eligible)==1 else 'ambiguous' if eligible else 'not_found',
                candidates=[b['id'] for b in eligible],candidate=eligible[0] if len(eligible)==1 else None)


def assign_bars(slots,bars):
    assigned={s['key']:associate_bar(s['center'],bars) for s in slots}
    # A single HUD cannot confidently own two cells in the same column.
    uses={}
    for key,result in assigned.items():
        if result['candidate']:uses.setdefault(result['candidate']['id'],[]).append(key)
    for keys in uses.values():
        if len(keys)>1:
            for key in keys:assigned[key].update(status='shared_candidate',candidate=None)
    return assigned


def anchor_channel(size,anchor,sigma=10):
    from PIL import Image
    y,x=np.mgrid[:size[1],:size[0]]
    heat=np.exp(-((x-anchor[0])**2+(y-anchor[1])**2)/(2*sigma*sigma))
    return Image.fromarray(np.rint(heat*255).astype(np.uint8),'L')
