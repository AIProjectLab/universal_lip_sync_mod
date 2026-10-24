"""Conservative, skin-preserving elbow conversion for standard BBS player arms."""
from copy import deepcopy
import math


def _face_row(uv,fraction):
    u0,v0,u1,v1=uv
    v=v0+(v1-v0)*fraction
    return [u0,v-.04,u1,v+.04]


def split_cube(cube,cut):
    """Bake inflation before splitting, preserving the original UV slope."""
    if any(cube.get('rotate',[0,0,0])) or cube.get('meshes'):
        raise ValueError('Rotated arm pieces need a manually fitted elbow rig.')
    lower=deepcopy(cube);upper=deepcopy(cube)
    offset=float(cube.get('offset',0))
    bottom=cube['from'][1]-offset
    top=bottom+cube['size'][1]+2*offset
    if not bottom<cut<top:
        raise ValueError('The arm piece does not cross the elbow plane.')
    fraction=(top-cut)/(top-bottom)
    for piece,lo,hi in ((lower,bottom,cut),(upper,cut,top)):
        piece['from']=[cube['from'][0]-offset,lo,cube['from'][2]-offset]
        piece['size']=[cube['size'][0]+2*offset,hi-lo,cube['size'][2]+2*offset]
        piece.pop('offset',None)
        for face in ('front','back','left','right'):
            uv=deepcopy(cube['uvs'][face])
            seam=uv[1]+(uv[3]-uv[1])*fraction
            if piece is upper:uv[3]=seam
            else:uv[1]=seam
            piece['uvs'][face]=uv
    cap=_face_row(cube['uvs']['front'],fraction)
    upper['uvs']['bottom']=deepcopy(cap)
    lower['uvs']['top']=deepcopy(cap)
    return upper,lower


def split_player_arms(document,mapping):
    """Keep shoulder names and route wrist/item anchors through new forearms."""
    model=deepcopy(document)
    groups=model.get('model',{}).get('groups',{})
    if set(mapping)!=set(('left','right')) or len(set(mapping.values()))!=2:
        raise ValueError('Two different standard player shoulder bones are required.')
    elbows={side:side+'_forearm' for side in mapping}
    if any(name in groups for name in elbows.values()):
        raise ValueError('This model already has forearm bones. Use its existing elbow-enabled profile.')
    for side,shoulder in mapping.items():
        arm=groups.get(shoulder,{})
        cubes=arm.get('cubes',[])
        origin=arm.get('origin',[])
        if len(origin)!=3 or not math.isclose(origin[1],22) or arm.get('rotate') and any(arm['rotate']):
            raise ValueError('Automatic elbow conversion expects the standard player shoulder pivots.')
        if not cubes or any(len(c.get('size',[]))!=3 or c['size'][1]!=12 or c['size'][0] not in (3,4) or c['size'][2]!=4 or c.get('from',[None,None])[1]!=12 for c in cubes):
            raise ValueError('Automatic elbows support straight 12-pixel player arms. This rig needs manual splitting.')
        cut=18.
        uppers=[];lowers=[]
        for cube in cubes:
            upper,lower=split_cube(cube,cut)
            uppers.append(upper);lowers.append(lower)
        # A small inset core fills the outer crease on a bent square elbow.
        # At zero bend it is entirely inside the original arm volume.
        base=min(cubes,key=lambda c:c.get('offset',0))
        centre_x=base['from'][0]+base['size'][0]/2
        centre_z=base['from'][2]+base['size'][2]/2
        width=base['size'][0]-.04
        joint={'origin':[centre_x,cut,centre_z],
               'from':[centre_x-width/2,cut-1.98,centre_z-1.98],
               'size':[width,3.96,3.96],
               'uvs':{face:_face_row(base['uvs'][face] if face in ('front','back','left','right') else base['uvs']['front'],.5)
                      for face in ('front','back','left','right','top','bottom')}}
        arm['cubes']=uppers+[joint]
        forearm=elbows[side]
        groups[forearm]={'origin':[origin[0],cut,origin[2]],'parent':shoulder,'cubes':lowers}
        # Named wrist and held-item anchors must follow the lower segment.
        for name,child in groups.items():
            if name==forearm or child.get('parent')!=shoulder:continue
            point=child.get('origin',[0,99,0])
            if len(point)==3 and point[1]<=cut:
                child['parent']=forearm
    return model,elbows
