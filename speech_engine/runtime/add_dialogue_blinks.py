"""Add independent, skin-matched eyelids to the refined dialogue test copy."""
import json
from copy import deepcopy
from pathlib import Path
import zipfile

import bbs_storage as storage
from refine_dialogue_rig import face, write_json
from rhubarb_to_bbs import IDENTITY_BONE_TRANSFORM

ROOT = Path(__file__).resolve().parent
PACKAGE = ROOT / 'build/dialogue_refined'
EYES_ID = 'player/eyes_dialogue'
EYES_TEXTURE = 'assets:models/player/eyes_dialogue/texture.png'
EYE_BONES = ('LidLeft', 'LidRight', 'CreaseLeft', 'CreaseRight')


def eye_pose(closure, crease=0.0):
    result = {}
    for bone in EYE_BONES:
        value = deepcopy(IDENTITY_BONE_TRANSFORM)
        value['s'][1] = crease if bone.startswith('Crease') else closure
        result[bone] = value
    return result


def build_eyes():
    groups = {'EyesAnchor': {'origin': [0, 0, -4]}}
    # UVs sample the existing character's solid gold skin and darker eye rim.
    # The copied 64x64 skin is unchanged; no painted skin patch is introduced.
    skin_uv, crease_uv = [11, 10, 12, 11], [9, 11, 10, 12]
    for side, x in [('Left', -3.015), ('Right', .985)]:
        groups['Lid'+side] = {
            'origin': [0, 5.015, -4.025], 'parent': 'EyesAnchor',
            'cubes': [face(x, 2.985, 2.03, 2.03, -4.025, skin_uv)],
        }
        groups['Crease'+side] = {
            'origin': [0, 3.96, -4.03], 'parent': 'EyesAnchor',
            'cubes': [face(x+.165, 3.9, 1.7, .12, -4.03, crease_uv)],
        }
    return {'version': '0.7.2', 'animations': {}, 'model': {'groups': groups, 'texture': [64,64]}}


def blink_starts(cues, duration):
    desired = [2.2, 5.9, 10.7, 13.8, 18.6, 22.4, 27.2, 31.4, 35.8]
    pauses = [(c['start']+c['end'])/2 for c in cues
              if c['value']=='X' and c['end']-c['start'] >= .12]
    starts = []
    for target in desired:
        nearest = min(pauses, key=lambda p: abs(p-target), default=target)
        chosen = nearest if abs(nearest-target)<.65 else target
        if chosen+.26 < duration:
            starts.append(chosen*20)
    # One occasional double blink, rather than a rigid repeating cycle.
    if len(starts)>3:
        starts.append(starts[3]+6.0)
    return sorted(starts)


def make_blink_channels(starts, duration_ticks):
    channels = {}
    for bone in EYE_BONES:
        values = {0.0: 0.0, float(duration_ticks): 0.0}
        # A tiny left/right timing difference avoids a perfectly mechanical blink.
        stagger = .18 if bone.endswith('Right') else 0.0
        for start in starts:
            t = start+stagger
            if bone.startswith('Lid'):
                events = [(t,0), (t+1.4,1), (t+2.4,1), (t+5.2,0)]
            else:
                events = [(t,0), (t+1.1,0), (t+1.4,1), (t+2.4,1), (t+2.65,0), (t+5.2,0)]
            for tick, value in events:
                if tick<duration_ticks:
                    values[round(tick,4)] = float(value)
        frames = []
        for tick, value in sorted(values.items()):
            transform = deepcopy(IDENTITY_BONE_TRANSFORM)
            transform['s'][1] = value
            frames.append({'tick':tick,'duration':0.0,'interp':'cubic_out','color':6724044,
                           'shape':'CIRCLE','value':transform})
        channels['1/pose/'+bone] = {'type':'pose_transform','keyframes':frames}
    return channels


def main():
    source_film = PACKAGE / 'voice_refined_test.dat'
    film = storage.load(source_film)
    actor = film['replays'][0]
    if len(actor['form']['parts'].value)!=1:
        raise ValueError('Expected the single mouth attachment in the refined test')
    manifest = json.loads((PACKAGE/'manifest.json').read_text())
    cues = json.loads((ROOT/'voice_output.json').read_text())['mouthCues']
    starts = blink_starts(cues, manifest['audio_seconds'])
    channels = make_blink_channels(starts, manifest['ticks'])
    eyes_dir = PACKAGE/'assets/models'/EYES_ID
    write_json(eyes_dir/'model.bbs.json',build_eyes())
    write_json(eyes_dir/'config.json',{'texture':EYES_TEXTURE,'culling':False})
    write_json(eyes_dir/'poses.json',{
        'open': {'static':False,'pose':eye_pose(0)},
        'closed': {'static':False,'pose':eye_pose(1,1)},
    })
    jar = Path(r'C:\Users\shafe\curseforge\minecraft\Instances\BBS 1.21.1\mods\bbs-2.3.3-1.21.1.jar')
    with zipfile.ZipFile(jar) as archive:
        skin = archive.read('assets/bbs/assets/models/player/steve/steve.png')
    (eyes_dir/'texture.png').write_bytes(skin)
    part = deepcopy(actor['form']['parts'][0])
    part['bone'] = 'head'
    part['transform'] = {'s':[1.0,1.0,1.0],'t':[0.0,0.0,.004],'r':[0.0,0.0,0.0]}
    part['form']['model'] = EYES_ID
    part['form']['texture'] = EYES_TEXTURE
    part['form']['pose'] = eye_pose(0)
    actor['form']['parts'].value.append(part)
    animation = actor['animation']
    for name, channel in channels.items():
        animation[name] = channel
    all_channels = [name for name in animation.value if name!='sections']
    animation['sections'][0]['channels'] = all_channels
    actor['label'] = 'Dialogue refined - natural blinks'
    film['description'] = 'Full voice dialogue with independent gold eyelids, staggered blinks and an occasional double blink.'
    output = PACKAGE/'voice_blink_test.dat'
    storage.save(output,film)
    assert storage.load(output)==film
    preset = storage.plain(animation)
    del preset['sections']
    write_json(PACKAGE/'voice_blink_pose_preset.json',preset)
    write_json(PACKAGE/'blink_manifest.json',{
        **manifest,'film':output.name,'eyes_model':EYES_ID,
        'blink_count':len(starts),'blink_starts_seconds':[round(t/20,3) for t in starts],
        'eyelid_close_ticks':1.4,'eyelid_hold_ticks':1.0,'eyelid_open_ticks':2.8,
        'left_right_stagger_ticks':.18,'bone_channels':len(all_channels),
        'in_game_preview_verified':False,
    })
    print(f'Built {output}: {len(starts)} blinks; mouth/audio preserved; {len(all_channels)} total bone channels')


if __name__=='__main__':
    main()
