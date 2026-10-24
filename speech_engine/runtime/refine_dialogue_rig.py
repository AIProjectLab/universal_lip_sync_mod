"""Build a separate, skin-independent BBS dialogue rig and full-audio test film.

Reuses the existing audio alignment and saved camera/actor placement. It never
overwrites the source film or changes Minecraft's bundled player/mouth model.
"""
import argparse
import json
from copy import deepcopy
from pathlib import Path
import shutil
import zipfile

import bbs_storage as storage
import rhubarb_to_bbs as lip_sync

ROOT = Path(__file__).resolve().parent
MODEL_ID = 'player/mouth_dialogue'
TEXTURE = 'assets:models/player/mouth_dialogue/texture.png'


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=4) + '\n', encoding='utf-8')


def face(x, y, width, height, z, uv):
    return {
        'origin': [0, y, z], 'from': [x, y, z],
        'size': [width, height, 0],
        'uvs': {side: uv for side in ('front', 'back', 'left', 'right', 'top', 'bottom')},
    }


def build_model(poses):
    model = json.loads((ROOT / 'assets/bbs/assets/models/player/mouth/model.bbs.json').read_text())
    # Remove joystick-driven stock animation: it must not compete with dialogue.
    model['animations'] = {}
    g = model['model']['groups']
    dark = [1, 0, 5, 3]
    teeth = [1, 4, 5, 5]
    # A narrow neutral line replaces the stock rectangle of Steve skin/beard.
    g['Closed']['cubes'] = [face(-1, .8, 2, .16, -4.03, dark)]
    for name in ('ClosedLeft', 'ClosedRight', 'InsideLeft', 'InsideRight'):
        g[name]['cubes'] = []
    # Chamfered corners keep the aperture from looking like a rectangular sticker.
    g['Inside']['cubes'] = [
        face(-.8, .4375, 1.6, .1875, -4.01, dark),
        face(-1, .625, 2, .75, -4.01, dark),
        face(-.8, 1.375, 1.6, .1875, -4.01, dark),
    ]
    g['Up']['origin'] = [0, 1.15, -4.02]
    g['Up']['cubes'] = [face(-.75, 1.15, 1.5, .18, -4.02, teeth)]
    g['Down']['origin'] = [0, 1.2, -4.02]
    g['Down']['cubes'] = [face(-.7, 1.2, 1.4, .14, -4.02, teeth)]
    g['Tongue']['origin'] = [0, .7, -4.015]
    g['Tongue']['cubes'] = [face(-.45, .7, .9, .22, -4.015, [2, 8, 4, 9])]
    # These local refinements are specific to the new tooth/lip geometry.
    poses['ee']['pose']['Up']['t'][1] = .05
    poses['ee']['pose']['Down']['t'][1] = -.15
    poses['f']['pose']['Closed']['t'][1] = .3
    poses['b']['pose']['Up']['t'][1] = .1
    poses['b']['pose']['Down']['t'][1] = -.1
    poses['d']['pose']['Tongue']['t'][1] = -.15
    for rounded in ('o', 'u'):
        for tooth in ('Up', 'Down'):
            poses[rounded]['pose'][tooth]['s'] = [0.0, 0.0, 0.0]
    return model


def articulate(frames, transition_ticks=1.5):
    """Hold readable targets, then make a short eased transition into the next.

    Targets remain on their original cue ticks. This adds anticipation before
    the target instead of slowly melting toward it over an entire vowel.
    """
    result = []
    for i, frame in enumerate(frames):
        if i:
            previous = frames[i - 1]
            hold = frame['tick'] - transition_ticks
            if hold > previous['tick'] and previous['value'] != frame['value']:
                key = deepcopy(previous)
                key['tick'] = hold
                result.append(key)
        result.append(deepcopy(frame))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-film', type=Path, required=True)
    parser.add_argument('--bbs-jar', type=Path, required=True)
    parser.add_argument('--cues', type=Path, default=ROOT / 'voice_output.json')
    parser.add_argument('--out', type=Path, default=ROOT / 'build/dialogue_refined')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    source = storage.load(args.source_film)
    assert storage.decode(storage.encode(source)) == source, 'Typed BBS1 roundtrip failed'
    film = deepcopy(source)
    poses = json.loads((ROOT / 'bbs_poses.json').read_text())
    model = build_model(poses)
    model_dir = args.out / 'assets/models' / MODEL_ID
    write_json(model_dir / 'model.bbs.json', model)
    write_json(model_dir / 'poses.json', poses)
    write_json(model_dir / 'config.json', {'texture': TEXTURE, 'culling': False})
    with zipfile.ZipFile(args.bbs_jar) as jar:
        (model_dir / 'texture.png').write_bytes(jar.read('assets/bbs/assets/models/player/mouth/texture.png'))

    cues = json.loads(args.cues.read_text())
    timed, stats = lip_sync.convert_cues_to_drift_free_ticks(cues['mouthCues'], cues['metadata']['duration'])
    frames = articulate(lip_sync.generate_bbs_pose_json(timed, poses)['0/pose']['keyframes'])
    channels = {}
    for bone in lip_sync.MOUTH_BONES:
        bone_frames = []
        for frame in frames:
            key = deepcopy(frame)
            key['value'] = key['value']['pose'][bone]
            bone_frames.append(key)
        channels[f'0/pose/{bone}'] = {'type': 'pose_transform', 'keyframes': bone_frames}
    duration = int(stats['total_audio_ticks'])
    section = {'start': 0.0, 'duration': float(duration), 'repeats': 1, 'accumulate': False, 'channels': list(channels)}

    actor = film['replays'][0]
    part = actor['form']['parts'][0]
    if part['form']['model'].value != 'player/mouth':
        raise ValueError('Source actor must have player/mouth as its first attachment')
    part['form']['model'] = MODEL_ID
    part['form']['texture'] = TEXTURE
    # BBS 2.3 ValuePoseTree stores bone names directly (not the legacy wrapper).
    part['form']['pose'] = poses['idle']['pose']
    # Body-part translations use blocks; 0.05 raises the mouth by 0.8 model pixels.
    # The head attachment matrix includes a 180-degree Y rotation before the
    # child model's rotation. Positive attachment Z moves its face planes forward.
    part['transform'] = {'s': [1.0, 1.0, 1.0], 't': [0.0, .05, .004], 'r': [0.0, 0.0, 0.0]}
    part['bone'] = 'head'
    actor['label'] = 'Dialogue refined - full voice'
    # Remove the composite channel as well as the old bone tracks. Each bone
    # now has one authoritative channel, with no stale depth-scale edits.
    actor['animation'] = {**channels, 'sections': [section]}
    actor['sections'] = []
    actor['layers'] = []
    for clip in film['camera'].value:
        if clip['type'].value == 'bbs:audio':
            clip['audio'] = 'assets:audio/voice.wav'
            clip['tick'] = 0
            clip['duration'] = duration
            clip['offset'] = 0
        elif clip['type'].value == 'bbs:movement':
            clip['duration'] = duration
    film['description'] = 'Refined skin-independent mouth; matched voice audio; short eased transitions and readable holds.'
    output_film = args.out / 'voice_refined_test.dat'
    storage.save(output_film, film)
    assert storage.load(output_film) == film, 'Output film did not roundtrip'
    write_json(args.out / 'voice_refined_pose_preset.json', channels)
    shutil.copy2(ROOT / 'voice.wav', args.out / 'voice.wav')
    write_json(args.out / 'manifest.json', {
        'source_film': str(args.source_film), 'model': MODEL_ID,
        'film': output_film.name, 'audio': 'voice.wav', 'audio_seconds': cues['metadata']['duration'],
        'ticks': duration, 'cue_keyframes': len(timed), 'keyframes_per_bone': len(frames),
        'bone_channels': len(channels), 'transition_ticks': 1.5,
        'source_unchanged': True, 'in_game_preview_verified': False,
    })
    print(f'Built {output_film}: {duration} ticks, {len(channels)} channels, {len(frames)} keys per bone')


if __name__ == '__main__':
    main()
