"""Profile-based BBS 2.3 dialogue builder; no UI and no implicit deployment."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import random
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import wave
import zipfile

import bbs_storage as bbs
from add_dialogue_blinks import EYE_BONES, make_blink_channels
from . import gestures as talking_gestures
from rhubarb_to_bbs import IDENTITY_BONE_TRANSFORM, MOUTH_BONES, DEFAULT_RHUBARB_PATH

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'studio_data'
# Rhubarb's documented sound meanings: E is rounded, F is puckered. The older
# experimental demo mapping remains untouched in rhubarb_to_bbs.py.
VISEME_MAP = dict(A='a', B='b', C='c', D='d', E='o', F='u', G='f', H='l', X='idle')


class StudioError(Exception):
    pass


class Cancelled(StudioError):
    pass


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def slug(text):
    return re.sub(r'[^a-z0-9_-]+', '_', text.lower()).strip('_')[:36] or 'dialogue'


def safe_relative(text):
    p = PurePosixPath(str(text).replace('\\', '/'))
    if not p.parts or p.is_absolute() or any(x in ('.', '..') or ':' in x for x in p.parts):
        raise StudioError(f'Unsafe asset path: {text}')
    return p


def json_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    os.replace(temporary, path)


def map_strings(value, operation):
    if isinstance(value, dict):
        return {k:map_strings(v, operation) for k,v in value.items()}
    if isinstance(value, list):
        return [map_strings(v, operation) for v in value]
    return operation(value) if isinstance(value, str) else value


def run(command, cancel=None):
    options = {'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
    process = subprocess.Popen([str(x) for x in command], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, **options)
    while True:
        if cancel is not None and cancel.is_set():
            process.terminate()
            try:
                process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill(); process.communicate()
            raise Cancelled('Cancelled. Your existing films were not changed.')
        try:
            out, err = process.communicate(timeout=.25)
            break
        except subprocess.TimeoutExpired:
            continue
    if process.returncode:
        raise StudioError(err.decode('utf-8', errors='replace')[-3500:] or 'Audio processing failed.')
    return out


class Assets:
    """Resolve the same local-first assets: namespace as BBS."""
    def __init__(self, instance):
        self.instance = Path(instance).resolve()
        self.local = self.instance/'config/bbs/assets'
        jars = list((self.instance/'mods').glob('bbs-*.jar'))
        self.jar = None
        for jar in jars:
            try:
                with zipfile.ZipFile(jar) as z:
                    meta = json.loads(z.read('fabric.mod.json'))
                if meta.get('id')=='bbs' and str(meta.get('version','')).startswith('2.3.'):
                    self.jar = jar
                    break
            except (KeyError, ValueError, zipfile.BadZipFile):
                continue
        if self.jar is None:
            raise StudioError('Choose an installation with BBS 2.3.x. Other BBS versions need a separate exporter.')

    def read(self, relative):
        p = safe_relative(relative)
        local = self.local/str(p)
        if local.is_file():
            return local.read_bytes()
        with zipfile.ZipFile(self.jar) as z:
            name = 'assets/bbs/assets/'+str(p)
            if name in z.namelist():
                return z.read(name)
        raise StudioError(f'BBS asset not found: assets:{p}')

    def model(self, identifier):
        prefix = 'models/'+str(safe_relative(identifier))+'/'
        files = {}
        with zipfile.ZipFile(self.jar) as z:
            base = 'assets/bbs/assets/'+prefix
            for name in z.namelist():
                if name.startswith(base) and not name.endswith('/'):
                    files[name[len(base):]] = z.read(name)
        directory = self.local/prefix
        if directory.is_dir():
            for path in directory.rglob('*'):
                if path.is_file():
                    files[path.relative_to(directory).as_posix()] = path.read_bytes()
        # The bundled, tested mouth can be attached to a new model for fitting.
        if not files and identifier=='player/mouth_dialogue':
            directory = ROOT/'lip_sync_studio/templates/mouth'
            if directory.is_dir():
                files = {p.relative_to(directory).as_posix():p.read_bytes()
                         for p in directory.rglob('*') if p.is_file()}
        if not files:
            raise StudioError(f'Model {identifier!r} is missing from this installation.')
        return files

    def bones(self, identifier):
        files = self.model(identifier)
        # 1. BBS cubic models (.bbs.json)
        for name, content in files.items():
            if name.endswith('.bbs.json'):
                data = json.loads(content)
                groups = list(data.get('model',{}).get('groups',{}))
                if groups:
                    return groups
        # 2. BOBJ models (.bobj)
        for name, content in files.items():
            if name.endswith('.bobj'):
                bones = []
                for line in content.decode('utf-8', errors='ignore').splitlines():
                    if line.startswith('arm_bone '):
                        parts = line.split()
                        if len(parts) > 1 and parts[1] not in bones:
                            bones.append(parts[1])
                if bones:
                    return bones
        # 3. Model configuration bones (config.json)
        for name, content in files.items():
            if name.endswith('config.json'):
                try:
                    data = json.loads(content)
                    if 'bones' in data and isinstance(data['bones'], dict):
                        return list(data['bones'].keys())
                except Exception:
                    pass
        raise StudioError(f'No head or armature controls found on {identifier!r}. Ask Antigravity for rig help.')


def read_film(path):
    try:
        film = bbs.load(path)
    except Exception as e:
        raise StudioError(f'Cannot read BBS film {Path(path).name}: {e}') from e
    plain = bbs.plain(film)
    if plain.get('bbs_format')!=3 or not plain.get('replays'):
        raise StudioError('Choose a BBS 2.3 film containing at least one character.')
    return film


def actors(path):
    return [r.get('label') or f'Character {i+1}'
            for i,r in enumerate(bbs.plain(read_film(path))['replays'])]


def roles(form):
    found = {}
    for i,part in enumerate(form.get('parts',[])):
        model = part.get('form',{}).get('model','')
        if model=='player/mouth_dialogue' or model.endswith('/mouth'):
            if 'mouth' in found:
                raise StudioError('This actor has several mouth attachments. Keep one mouth before importing.')
            found['mouth']=i
        elif model=='player/eyes_dialogue' or model.endswith('/eyes'):
            found['eyes']=i
    return found


def capture_profile(instance, film_path, actor_index, name, *, fitted=True,
                    attach_head=None, arm_bones=None, data_root=DATA):
    """Snapshot a fitted actor and its assets under unique profile model IDs."""
    assets = Assets(instance)
    template = read_film(film_path)
    try:
        actor_node = template['replays'][actor_index]
    except IndexError as e:
        raise StudioError('Choose a character from this film.') from e
    actor = bbs.plain(actor_node)
    form = actor.get('form',{})
    source_model = form.get('model')
    source_skin = form.get('texture')
    compatible_models=[];compatible_skins=[]
    if not source_model:
        raise StudioError('Choose a BBS model actor with a head attachment bone.')
    # A fitting scene already uses namespaced copies. Retain compatibility
    # with the original character model when importing its fitted result.
    if source_model.startswith('lip_sync_studio/'):
        parent_id=source_model.split('/')[1]
        parent_meta=Path(data_root)/'profiles'/parent_id/'profile.json'
        if parent_meta.is_file():
            parent=json.loads(parent_meta.read_text(encoding='utf-8'))
            compatible_models=list(dict.fromkeys(parent.get('compatible_models',[])+[source_model]))
            compatible_skins=list(dict.fromkeys(parent.get('compatible_skins',[])+[source_skin]))
            source_model=parent['source_model'];source_skin=parent.get('source_skin')
            if arm_bones is None:arm_bones=parent.get('arm_bones')
    if attach_head:
        if attach_head not in assets.bones(form['model']):
            raise StudioError(f'Bone {attach_head!r} was not found on {source_model}. Choose its real head bone.')
        if roles(form).get('mouth') is None:
            form.setdefault('parts',[]).append({
                'bone':attach_head,'useTarget':False,
                'transform':{'s':[1.,1.,1.],'r':[0.,0.,0.],'t':[0.,.05,.004]},
                'form':{'id':'bbs:model','model':'player/mouth_dialogue',
                        'texture':'assets:models/player/mouth_dialogue/texture.png',
                        'visible':True,'lighting':1.,'parts':[],'pose':{}},
            })
            fitted=False
        if roles(form).get('eyes') is None:
            try:
                assets.model('player/eyes_dialogue')
                form.setdefault('parts',[]).append({
                    'bone':attach_head,'useTarget':False,
                    'transform':{'s':[1.,1.,1.],'r':[0.,0.,0.],'t':[0.,0.,.004]},
                    'form':{'id':'bbs:model','model':'player/eyes_dialogue',
                            'texture':'assets:models/player/eyes_dialogue/texture.png',
                            'visible':True,'lighting':1.,'parts':[],'pose':{}},
                })
            except Exception:pass
            fitted=False
    binding = roles(form)
    if 'mouth' not in binding:
        raise StudioError('No dialogue mouth found. Prepare a fitting scene first, then import the fitted actor.')
    mouth_model = form['parts'][binding['mouth']]['form']['model']
    mouth_files = assets.model(mouth_model)
    try:
        poses = json.loads(mouth_files['poses.json'])
    except (KeyError, ValueError) as e:
        raise StudioError('The mouth needs a poses.json file containing its saved sound shapes.') from e
    for shape,key in VISEME_MAP.items():
        if key not in poses or set(MOUTH_BONES)-poses[key].get('pose',{}).keys():
            raise StudioError(f'The mouth is missing the complete {shape} sound pose ({key}).')
    profile_id = slug(name)+'_'+uuid.uuid4().hex[:8]
    directory = Path(data_root)/'profiles'/profile_id
    directory.mkdir(parents=True)
    model_map = {}
    model_files = {}

    def collect(f):
        if f.get('model'):
            old=f['model']
            if old not in model_map:
                role = next((role for role,index in binding.items()
                             if form['parts'][index].get('form') is f),None)
                new = f'lip_sync_studio/{profile_id}/'+(role or f'model_{len(model_map)}')
                model_map[old]=new
                model_files[old]=assets.model(old)
        for part in f.get('parts',[]):
            collect(part.get('form',{}))
    collect(form)
    extras = {}

    def rewrite(text):
        if text in model_map:
            return model_map[text]
        if text.startswith(('assets:','b.a:')):
            rel = text.split(':',1)[1]
            safe_relative(rel)
            for old,new in model_map.items():
                prefix='models/'+old+'/'
                if rel.startswith(prefix):
                    return 'assets:models/'+new+'/'+rel[len(prefix):]
            content = assets.read(rel)
            suffix = Path(rel).suffix
            target=f'lip_sync_studio/{profile_id}/extras/{hashlib.sha256(content).hexdigest()[:20]}{suffix}'
            extras[target]=content
            return 'assets:'+target
        if text.startswith(('http://','https://')):
            raise StudioError('This actor uses a web texture. Save it into BBS assets and select the local texture before importing.')
        return text

    form = map_strings(form,rewrite)
    for old,files in model_files.items():
        for relative,content in files.items():
            target=directory/'assets/models'/model_map[old]/str(safe_relative(relative))
            if relative.endswith('.json'):
                data=map_strings(json.loads(content),rewrite)
                content=(json.dumps(data,indent=2)+'\n').encode('utf-8')
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(content)
    for relative,content in extras.items():
        target=directory/'assets'/relative
        target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content)
    actor_node['form']=form
    bbs.save(directory/'template.dat',template)
    json_write(directory/'poses.json',poses)
    profile={
        'schema':1,'id':profile_id,'name':name,'fitted':fitted,
        'source_model':source_model,'source_skin':source_skin,
        'source_actor_index':actor_index,'source_film':str(Path(film_path).resolve()),
        'source_instance':str(Path(instance).resolve()),'bindings':binding,
        'compatible_models':compatible_models,'compatible_skins':compatible_skins,
        'viseme_map':VISEME_MAP,'asset_hashes':{
            p.relative_to(directory/'assets').as_posix():digest(p)
            for p in (directory/'assets').rglob('*') if p.is_file()},
    }
    if arm_bones is not None:
        names=assets.bones(actor['form']['model'])
        if set(arm_bones)!=set(('left','right')) or len(set(arm_bones.values()))!=2 or any(n not in names for n in arm_bones.values()):
            raise StudioError('Choose two different shoulder bones, one for each arm, or leave both blank.')
        profile['arm_bones']=arm_bones
    json_write(directory/'profile.json',profile)
    validate_profile(directory)
    return directory


def profile_model_groups(directory,meta=None):
    directory=Path(directory)
    meta=meta or json.loads((directory/'profile.json').read_text(encoding='utf-8'))
    actor=bbs.plain(read_film(directory/'template.dat'))['replays'][meta['source_actor_index']]
    model=directory/'assets/models'/actor['form']['model']
    groups={}
    for file in model.glob('*.bbs.json'):
        groups.update(json.loads(file.read_text(encoding='utf-8')).get('model',{}).get('groups',{}))
    for file in model.glob('*.bobj'):
        for line in file.read_text(encoding='utf-8',errors='ignore').splitlines():
            if line.startswith('arm_bone'):
                parts=line.split()
                if len(parts)>2:
                    groups[parts[1]]={'parent':parts[2],'origin':[0,0,0]}
                elif len(parts)>1:
                    groups[parts[1]]={'parent':'','origin':[0,0,0]}
    for file in model.glob('config.json'):
        try:
            cfg=json.loads(file.read_text(encoding='utf-8'))
            if 'bones' in cfg and isinstance(cfg['bones'],dict):
                for b,data in cfg['bones'].items():
                    if b not in groups:
                        groups[b]={'parent':data.get('parent',''),'origin':data.get('pivot',[0,0,0])}
        except Exception:pass
    return groups


def gesture_mapping(directory,meta=None):
    """Auto-map standard player shoulders for BBS cubic and BOBJ models."""
    directory=Path(directory)
    meta=meta or json.loads((directory/'profile.json').read_text(encoding='utf-8'))
    mapping=meta.get('arm_bones')
    if mapping is not None:
        if set(mapping)!=set(('left','right')) or len(set(mapping.values()))!=2:
            raise StudioError('This profile has invalid arm bones. Import the fitted actor with its two shoulder bones.')
        return mapping
    groups=profile_model_groups(directory,meta)
    for left,right in (('left_arm','right_arm'),('arm_left','arm_right')):
        if left in groups and right in groups:
            return {'left':left,'right':right}
    for name,x in (('left_arm',-6),('right_arm',6)):
        group=groups.get(name,{})
        if group.get('origin')==[x,22,0] and group.get('parent')=='body':
            return {'left':'left_arm','right':'right_arm'}
    return {}


def elbow_mapping(directory,meta=None):
    """Auto-map standard player elbow joints for BBS cubic and BOBJ models."""
    groups=profile_model_groups(directory,meta)
    shoulders=gesture_mapping(directory,meta)
    if not shoulders:return {}
    candidates=[
        ('low_left_arm','low_right_arm'),
        ('left_forearm','right_forearm'),
        ('left_elbow','right_elbow'),
        ('low_arm_left','low_arm_right')
    ]
    for left,right in candidates:
        if left in groups and right in groups:
            return {'left':left,'right':right}
    elbows={side:side+'_forearm' for side in shoulders}
    if len(elbows)==2 and all(name in groups for name in elbows.values()):
        for side,name in elbows.items():
            if groups[name].get('parent')==shoulders[side]:
                return elbows
    return {}


def create_elbow_profile(source,name=None,*,data_root=DATA):
    """Create a private split-arm model; never replace a fitted source profile."""
    from .rigging import split_player_arms
    source=Path(source)
    meta=validate_profile(source)
    if elbow_mapping(source,meta):
        raise StudioError('This character already has elbow joints. Select it and generate dialogue.')
    shoulders=gesture_mapping(source,meta)
    if not shoulders:
        raise StudioError('Map this character’s standard player shoulder bones before adding elbows. Other arm shapes need a manually fitted rig.')
    template=read_film(source/'template.dat')
    saved_actor=bbs.plain(template)['replays'][meta['source_actor_index']]
    root=saved_actor['form']['model']
    files=list((source/'assets/models'/root).glob('*.bbs.json'))
    if len(files)!=1:raise StudioError('Automatic elbow conversion needs one BBS cubic model file.')
    try:document,elbows=split_player_arms(json.loads(files[0].read_text(encoding='utf-8')),shoulders)
    except ValueError as error:raise StudioError(str(error)) from error
    name=name or meta['name']+' — elbows'
    identifier=slug(name)+'_'+uuid.uuid4().hex[:8]
    directory=Path(data_root)/'profiles'/identifier
    old_prefix='lip_sync_studio/'+meta['id']+'/'
    new_prefix='lip_sync_studio/'+identifier+'/'
    rewrite=lambda text:text.replace(old_prefix,new_prefix)
    for relative in meta['asset_hashes']:
        origin=source/'assets'/str(safe_relative(relative))
        # BBS uses the converted native model; an unconverted editor source
        # beside it would misleadingly describe the old one-piece arms.
        if origin.parent==files[0].parent and origin.suffix=='.bbmodel':continue
        destination=directory/'assets'/str(safe_relative(rewrite(relative)))
        destination.parent.mkdir(parents=True,exist_ok=True)
        if origin==files[0]:
            json_write(destination,map_strings(document,rewrite))
        elif origin.suffix=='.json':
            json_write(destination,map_strings(json.loads(origin.read_text(encoding='utf-8')),rewrite))
        else:shutil.copy2(origin,destination)
    template=map_strings(bbs.plain(template),rewrite)
    template['replays'][meta['source_actor_index']]['label']=name
    bbs.save(directory/'template.dat',bbs.node(template))
    shutil.copy2(source/'poses.json',directory/'poses.json')
    converted={**deepcopy(meta),'id':identifier,'name':name,'arm_bones':shoulders,
               'elbow_bones':elbows,'rig_adapter':'player_elbows_v1','base_profile_id':meta['id'],
               'compatible_models':list(dict.fromkeys(meta.get('compatible_models',[])+[root])),
               'compatible_skins':list(dict.fromkeys(meta.get('compatible_skins',[])+[saved_actor['form'].get('texture')])),
               'asset_hashes':{p.relative_to(directory/'assets').as_posix():digest(p)
                               for p in (directory/'assets').rglob('*') if p.is_file()}}
    json_write(directory/'profile.json',converted)
    validate_profile(directory)
    return directory


def validate_profile(directory):
    directory=Path(directory)
    p=json.loads((directory/'profile.json').read_text(encoding='utf-8'))
    if p.get('schema')!=1:
        raise StudioError('Unsupported character profile version.')
    for relative,expected in p['asset_hashes'].items():
        path=directory/'assets'/str(safe_relative(relative))
        if not path.is_file() or digest(path)!=expected:
            raise StudioError(f'Character profile asset changed or is missing: {relative}. Import the fitted actor again.')
    actor=bbs.plain(read_film(directory/'template.dat'))['replays'][p['source_actor_index']]
    parts=actor['form']['parts']
    for role,index in p['bindings'].items():
        part=parts[index]
        if not part.get('bone'):
            raise StudioError('The face attachment needs a head bone.')
        link=part['form'].get('texture','')
        if not link.startswith('assets:') or not (directory/'assets'/str(safe_relative(link[7:]))).is_file():
            raise StudioError(f'{role} texture is missing from this profile.')
    if 'eyes' in p['bindings']:
        eye=parts[p['bindings']['eyes']]['form']
        candidates=(directory/'assets/models'/eye['model']).glob('*.bbs.json')
        bones=set()
        for candidate in candidates:
            bones.update(json.loads(candidate.read_text(encoding='utf-8'))['model']['groups'])
        if set(EYE_BONES)-bones:
            raise StudioError('The eyelid rig needs LidLeft, LidRight, CreaseLeft and CreaseRight bones.')
    return p


def profile_list(data_root=DATA):
    result=[]
    for p in (Path(data_root)/'profiles').glob('*/profile.json'):
        try:
            meta=json.loads(p.read_text(encoding='utf-8'))
            result.append((p.parent,meta))
        except (ValueError,OSError):
            continue
    return sorted(result,key=lambda pair:pair[1]['name'].lower())


def normalize_audio(source,destination,cancel=None):
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:
        raise StudioError('FFmpeg was not found. Install FFmpeg or add its bin folder to PATH.')
    source=Path(source)
    if not source.is_file():
        raise StudioError('Choose an audio file that exists.')
    run([ffmpeg,'-nostdin','-y','-v','error','-i',source,'-map','0:a:0','-vn',
         '-ac','1','-ar','16000','-c:a','pcm_s16le',destination],cancel)
    with wave.open(str(destination),'rb') as audio:
        seconds=audio.getnframes()/audio.getframerate()
    if seconds<=0:
        raise StudioError('This file has no usable audio.')
    return seconds


def analyse(audio,recognizer,transcript,cache_root,cancel=None,progress=lambda s:None):
    if not DEFAULT_RHUBARB_PATH.is_file():
        raise StudioError('The bundled Rhubarb executable is missing.')
    signature=hashlib.sha256((digest(audio)+'\n'+recognizer+'\n'+transcript+'\n'+digest(DEFAULT_RHUBARB_PATH)).encode()).hexdigest()
    cache=Path(cache_root)/(signature+'.json')
    if cache.is_file():
        try:
            data=json.loads(cache.read_text(encoding='utf-8'))
            if data.get('signature')==signature:
                validate_cues(data['analysis'])
                progress('Using the matching audio analysis cache.')
                return data['analysis']
        except (ValueError,KeyError,StudioError):
            pass
    progress('Listening to the recording. Longer recordings take more time...')
    with tempfile.TemporaryDirectory(prefix='rhubarb_') as temporary:
        command=[DEFAULT_RHUBARB_PATH,'-f','json','-r',recognizer,'--extendedShapes','GHX']
        if transcript.strip():
            script=Path(temporary)/'dialogue.txt';script.write_text(transcript,encoding='utf-8')
            command.extend(['-d',script])
        command.append(audio)
        data=json.loads(run(command,cancel).decode('utf-8'))
    validate_cues(data)
    json_write(cache,{'signature':signature,'analysis':data})
    return data


def validate_cues(data):
    duration=data.get('metadata',{}).get('duration',0)
    if not isinstance(duration,(int,float)) or not math.isfinite(duration) or duration<=0:
        raise StudioError('Audio analysis has an invalid duration.')
    previous=0.
    for cue in data.get('mouthCues',[]):
        start,end=cue.get('start'),cue.get('end')
        if cue.get('value') not in VISEME_MAP or not all(isinstance(x,(int,float)) and math.isfinite(x) for x in (start,end)):
            raise StudioError('Audio analysis contains an invalid mouth cue.')
        if start<previous-1e-5 or end<=start or end>duration+.03:
            raise StudioError('Audio analysis contains overlapping or out-of-range cues.')
        previous=end


def pose_keys(data,poses,mapping=VISEME_MAP,transition=1.5):
    """Absolute fractional ticks prevent short cues accumulating timing drift."""
    validate_cues(data)
    end=data['metadata']['duration']*20
    targets={0.:'idle'}
    last_end=0.
    for cue in data['mouthCues']:
        start=round(cue['start']*20,4)
        if cue['start']>last_end+1e-5:
            targets[round(last_end*20,4)]='idle'
        targets[start]=mapping[cue['value']]
        last_end=cue['end']
    targets[round(last_end*20,4)]='idle'
    targets[round(end,4)]='idle'
    if math.ceil(end)>end:
        targets[float(math.ceil(end))]='idle'
    frames=[]
    for tick,key in sorted(targets.items()):
        if key not in poses:
            raise StudioError(f'Character profile is missing pose {key}.')
        pose=deepcopy(poses[key]['pose'])
        if frames and frames[-1]['value']==pose:
            continue
        if frames:
            hold=round(tick-min(transition,(tick-frames[-1]['tick'])/2),4)
            if hold>frames[-1]['tick'] and frames[-1]['value']!=pose:
                frames.append({'tick':hold,'value':deepcopy(frames[-1]['value'])})
        frames.append({'tick':tick,'value':pose})
    full_end=float(math.ceil(end))
    if frames[-1]['tick']<full_end:
        frames.append({'tick':full_end,'value':deepcopy(poses['idle']['pose'])})
    return frames


def blink_schedule(data,seed):
    rng=random.Random(seed)
    duration=data['metadata']['duration']
    pauses=[(c['start']+c['end'])/2 for c in data['mouthCues'] if c['value']=='X' and c['end']-c['start']>=.15]
    target=rng.uniform(1.8,3.)
    starts=[]
    while target+.3<duration:
        closest=min(pauses,key=lambda p:abs(p-target),default=target)
        chosen=closest if abs(closest-target)<.5 else target
        if not starts or chosen*20-starts[-1]>15:
            starts.append(round(chosen*20,4))
            if rng.random()<.1 and chosen+.65<duration:
                starts.append(round((chosen+.32)*20,4))
        target=chosen+rng.uniform(3.,5.5)
    return starts


def keyframe(tick,value):
    return {'tick':float(tick),'duration':0.,'interp':'cubic_out','color':16763461,'shape':'CIRCLE','value':deepcopy(value)}


def mouth_channels(frames,index):
    return {f'{index}/pose/{bone}':{'type':'pose_transform','keyframes':[
        keyframe(frame['tick'],{**deepcopy(IDENTITY_BONE_TRANSFORM),**frame['value'][bone]}) for frame in frames]}
        for bone in MOUTH_BONES}


def merge_channel(previous,new,start,end,neutral):
    old=[deepcopy(f) for f in previous.get('keyframes',[]) if f['tick']<start or f['tick']>end]
    by_tick={f['tick']:f for f in old}
    if start>0 and not any(t<start for t in by_tick):
        by_tick[0.]=keyframe(0.,neutral)
        if start>1.5:
            by_tick[start-1.5]=keyframe(start-1.5,neutral)
    for f in new['keyframes']:
        f=deepcopy(f);f['tick']=round(f['tick']+start,4)
        by_tick[f['tick']]=f
    return {'type':'pose_transform','keyframes':[by_tick[t] for t in sorted(by_tick)]}


def attach_profile(actor,profile_actor,profile):
    """Keep actor position/body motion and unrelated equipment attachments."""
    current=bbs.plain(actor['form'])
    saved=profile_actor['form']
    if current.get('model') not in (profile['source_model'],saved.get('model'),*profile.get('compatible_models',[])):
        raise StudioError('The selected scene actor uses a different model. Choose its matching character profile.')
    if current.get('texture') not in (profile.get('source_skin'),saved.get('texture'),*profile.get('compatible_skins',[])):
        raise StudioError('The selected actor has a different skin. Import a fitted profile for that skin first.')
    old_roles=roles(current)
    current.setdefault('parts',[])
    indices={}
    for role,source_index in profile['bindings'].items():
        source=deepcopy(saved['parts'][source_index])
        if role in old_roles:
            index=old_roles[role];current['parts'][index]=source
        else:
            index=len(current['parts']);current['parts'].append(source)
        indices[role]=index
    current['model']=saved['model']
    current['texture']=saved['texture']
    actor['form']=current
    return indices


def make_film(template,actor_index,profile,profile_actor,frames,analysis,audio_link,*,start_tick=0.,blinks=True,new_test=False,seed='',audio_title='Dialogue',gesture_style='off',arm_bones=None,gesture_audio=None,elbow_bones=None,allow_overlap=False):
    film=deepcopy(template)
    if new_test:
        film['replays']= [deepcopy(film['replays'][actor_index])]
        actor_index=0
    actor=film['replays'][actor_index]
    indices=attach_profile(actor,profile_actor,profile)
    duration=int(math.ceil(analysis['metadata']['duration']*20))
    end=start_tick+duration
    description=bbs.plain(film).get('description','')
    marker='\n[Lip Sync Studio metadata]\n'
    human_description=description.split(marker,1)[0]
    talks=[]
    if not new_test and marker in description:
        try:talks=json.loads(description.split(marker,1)[1]).get('talks',[])
        except ValueError:
            raise StudioError('This scene has damaged dialogue metadata. Use an earlier saved scene copy.')
        if not allow_overlap:
            for talk in talks:
                if talk['actor']==actor_index and start_tick<talk['end'] and end>talk['start']:
                    raise StudioError('This line overlaps an existing line for the same actor. Pick a later start time, or use the earlier source scene to replace that line.')
        else:
            talks=[t for t in talks if not (t.get('actor')==actor_index and start_tick<t.get('end',0) and end>t.get('start',0))]
    generated=mouth_channels(frames,indices['mouth'])
    if blinks and 'eyes' in indices:
        for name,channel in make_blink_channels(blink_schedule(analysis,seed),duration).items():
            generated[name.replace('1/pose/',str(indices['eyes'])+'/pose/',1)]=channel
    existing={} if new_test else bbs.plain(actor['animation'])
    gesture_events=[]
    if gesture_style!='off':
        if not arm_bones:
            raise StudioError('This character needs its shoulder bones mapped before using gestures. Turn gestures Off, or import the profile with its arm bones.')
        form=bbs.plain(actor['form'])
        if any(form.get(k) for k in ('ik_controls','ik_targets','physics_controls','physics_targets')):
            raise StudioError('This character has IK or physics controls. Turn gestures Off to keep that motion, or use a separate actor without those controls.')
        owned={bone for talk in talks if talk['actor']==actor_index for bone in talk.get('gesture_bones',[])}
        controlled={**arm_bones,**{side+'_elbow':bone for side,bone in (elbow_bones or {}).items()}}
        for bone in controlled.values():
            name='pose/'+bone
            conflicting=[k for k in existing if k=='pose' or k.startswith(name+'/') or (not allow_overlap and k==name and bone not in owned)]
            if conflicting or '$anim' in json.dumps(form.get('pose',{}).get(bone,{})):
                raise StudioError(f'{bone} already has authored animation. Turn gestures Off to keep it, or use a source scene without that arm animation.')
        arms,gesture_events=talking_gestures.channels(analysis,gesture_audio,arm_bones,form.get('pose',{}),gesture_style,seed,elbows=elbow_bones)
        generated.update(arms)
    for role,index in indices.items():
        existing.pop(f'{index}/pose',None)
    for name,channel in generated.items():
        neutral=channel['keyframes'][-1]['value']
        existing[name]=merge_channel(existing.get(name,{}),channel,start_tick,end,neutral)
    sections=[]
    for section in existing.pop('sections',[]):
        section=deepcopy(section)
        section['channels']=[c for c in section.get('channels',[]) if c not in generated]
        if section['channels']:
            sections.append(section)
    active_end=max(end,max((f['tick'] for name in generated for f in existing[name]['keyframes']),default=end))
    sections.append({'start':0.,'duration':float(active_end),'repeats':1,'accumulate':False,'channels':list(generated)})
    actor['animation']={**existing,'sections':sections}
    actor['label']=profile['name']
    camera=bbs.plain(film['camera'])
    if new_test:
        camera=[c for c in camera if c.get('type')!='bbs:audio']
        # A ready-to-play test keeps the saved camera/actor placement.
        if len(camera)==1:
            camera[0]['duration']=max(camera[0].get('duration',0),math.ceil(end))
    audio_layer=max([c.get('layer',0) for c in camera]+[0])+1
    camera.append({'type':'bbs:audio','enabled':True,'tick':int(start_tick),'duration':duration,
                   'layer':audio_layer,'audio':audio_link,'title':profile['name']+' - '+audio_title,
                   'offset':0,'volume':1.,'pitch':1.})
    film['camera']=camera
    talks.append({'actor':actor_index,'start':start_tick,'end':end,'audio':audio_link,'profile':profile['id'],
                  'gesture_style':gesture_style,'gesture_version':talking_gestures.VERSION,
                  'gesture_bones':list(controlled.values()) if gesture_style!='off' else [],
                  'elbows':bool(elbow_bones) and gesture_style!='off',
                  'gesture_events':gesture_events})
    film['description']=human_description+marker+json.dumps({'talks':talks},ensure_ascii=False)
    return film,generated,duration


@dataclass
class Job:
    audio: Path
    profile: Path
    instance: Path
    world: str
    scene: Path | None = None
    actor_index: int = 0
    start_seconds: float = 0.
    transcript: str = ''
    recognizer: str = 'pocketSphinx'
    blinks: bool = True
    gestures: str = 'off'
    data_root: Path = DATA


def build_job(job,progress=lambda s:None,cancel=None,analysis_override=None):
    profile=validate_profile(job.profile)
    if not profile.get('fitted'):
        raise StudioError('Finish this character’s fitting scene and import the saved actor before generating dialogue.')
    if not math.isfinite(job.start_seconds) or job.start_seconds<0 or abs(job.start_seconds*20-round(job.start_seconds*20))>1e-5:
        raise StudioError('Start time must be zero or a multiple of 0.05 seconds (one Minecraft tick).')
    assets=Assets(job.instance)
    world_path=assets.instance/'saves'/str(safe_relative(job.world))
    if not (world_path/'level.dat').is_file():
        raise StudioError('Choose an existing world from this installation.')
    job_id=slug(Path(job.audio).stem)+'_'+uuid.uuid4().hex[:10]
    directory=Path(job.data_root)/'jobs'/job_id
    directory.mkdir(parents=True)
    try:
        progress('Converting the audio into a BBS-compatible WAV...')
        audio=directory/'dialogue.wav'
        seconds=normalize_audio(job.audio,audio,cancel)
        result=analysis_override if analysis_override is not None else analyse(
            audio,job.recognizer,job.transcript,Path(job.data_root)/'analysis_cache',cancel,progress)
        validate_cues(result)
        if abs(seconds-result['metadata']['duration'])>.08:
            raise StudioError('The analysis duration does not match this audio. Generate fresh analysis for the selected recording.')
        if cancel is not None and cancel.is_set():
            raise Cancelled('Cancelled before building the film.')
        json_write(directory/'analysis.json',result)
        progress('Building mouth shapes, eye blinks and talking gestures...')
        poses=json.loads((Path(job.profile)/'poses.json').read_text(encoding='utf-8'))
        frames=pose_keys(result,poses,profile['viseme_map'])
        profile_template=read_film(Path(job.profile)/'template.dat')
        profile_actor=bbs.plain(profile_template)['replays'][profile['source_actor_index']]
        if job.scene:
            template=read_film(job.scene);actor_index=job.actor_index
        else:
            template=profile_template;actor_index=profile['source_actor_index']
        audio_relative=f'audio/lip_sync_studio/{profile["id"]}/{digest(audio)[:20]}.wav'
        film,generated,duration=make_film(template,actor_index,profile,profile_actor,frames,result,
            'assets:'+audio_relative,start_tick=round(job.start_seconds*20),blinks=job.blinks,
            new_test=job.scene is None,seed=digest(audio),audio_title=Path(job.audio).stem,
            gesture_style=job.gestures,arm_bones=gesture_mapping(job.profile,profile),gesture_audio=audio,
            elbow_bones=elbow_mapping(job.profile,profile))
        filename='talk_'+job_id
        bbs.save(directory/(filename+'.dat'),film)
        if bbs.load(directory/(filename+'.dat'))!=film:
            raise StudioError('The generated film failed its storage roundtrip check.')
        json_write(directory/'face_preset.json',generated)
        shutil.copytree(Path(job.profile)/'assets',directory/'assets')
        target=directory/'assets'/audio_relative
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(audio,target)
        manifest={'schema':1,'name':filename,'profile':profile['name'],'world':job.world,
            'instance':str(assets.instance),'audio_seconds':seconds,'ticks':duration,
            'start_tick':round(job.start_seconds*20),'audio':audio_relative,
            'film':filename+'.dat','source_scene':str(job.scene) if job.scene else None,
            'actor_index':job.actor_index if job.scene else 0,'blinks':job.blinks and 'eyes' in profile['bindings'],
            'gestures':job.gestures,
            'gesture_version':talking_gestures.VERSION,
            'elbows':bool(elbow_mapping(job.profile,profile)) and job.gestures!='off',
            'asset_hashes':{p.relative_to(directory/'assets').as_posix():digest(p)
                            for p in (directory/'assets').rglob('*') if p.is_file()},
            'in_game_reviewed':False}
        json_write(directory/'manifest.json',manifest)
        validate_package(directory)
        progress('Package checked. Ready to install into the selected world.')
        return directory
    except Exception as e:
        json_write(directory/'failed.json',{'error':str(e),'original_files_changed':False})
        raise


def validate_package(directory):
    directory=Path(directory)
    manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    film=bbs.plain(read_film(directory/manifest['film']))
    for relative,expected in manifest['asset_hashes'].items():
        path=directory/'assets'/str(safe_relative(relative))
        if not path.is_file() or digest(path)!=expected:
            raise StudioError(f'Output asset is missing or changed: {relative}')
    def check(text):
        if text.startswith('b.a:'):
            raise StudioError('Legacy b.a: asset link found; BBS needs assets:.')
        if text.startswith('assets:'):
            relative=str(safe_relative(text[7:]))
            if relative in manifest['asset_hashes']:
                return
            # Existing scene actors may legitimately refer to their own assets.
            Assets(manifest['instance']).read(relative)
    def walk(n):
        if isinstance(n,dict):
            for v in n.values():walk(v)
        elif isinstance(n,list):
            for v in n:walk(v)
        elif isinstance(n,str):check(n)
    walk(film)
    for actor in film['replays']:
        for key,channel in actor.get('animation',{}).items():
            if key=='sections':continue
            if not isinstance(channel,dict):continue
            frames=channel.get('keyframes',[])
            if any(a['tick']>=b['tick'] for a,b in zip(frames,frames[1:])):
                raise StudioError(f'Duplicate or reversed keyframe times: {key}')
    return manifest


def deployment_plan(directory,instance=None,world=None):
    directory=Path(directory)
    manifest=validate_package(directory)
    instance=Path(instance or manifest['instance']).resolve()
    Assets(instance)
    world=world or manifest['world']
    safe_relative(world)
    if not (instance/'saves'/world/'level.dat').is_file():
        raise StudioError('The target world does not exist.')
    copies=[]
    for relative,expected in manifest['asset_hashes'].items():
        destination=instance/'config/bbs/assets'/relative
        if destination.is_file():
            if digest(destination)!=expected:
                raise StudioError(f'An existing asset differs: {destination}. It was not overwritten.')
        else:
            copies.append((directory/'assets'/relative,destination))
    preset=instance/'config/bbs/settings/presets/keyframes'/(manifest['name']+'.json')
    film=instance/'saves'/world/'bbs/films'/manifest['film']
    for destination in (preset,film):
        if destination.exists():
            raise StudioError(f'This job is already installed: {destination.name}. Create a new job to make another version.')
    copies.extend([(directory/'face_preset.json',preset),(directory/manifest['film'],film)])
    return copies


def deploy(directory,instance=None,world=None,progress=lambda s:None):
    # Preflight every destination before writing. Film is published last, so a
    # failed asset copy never leaves a half-installed film in the Films list.
    plan=deployment_plan(directory,instance,world)
    created=[]
    try:
        for source,destination in plan:
            destination.parent.mkdir(parents=True,exist_ok=True)
            with destination.open('xb') as out, source.open('rb') as stream:
                created.append(destination)
                shutil.copyfileobj(stream,out)
            if digest(source)!=digest(destination):
                raise StudioError(f'Installed file failed verification: {destination}')
        progress('Installed and verified. Reopen the BBS Films list to see the new film.')
    except Exception:
        # Only exact files created by this transaction may be removed.
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise
    return plan[-1][1]


def make_fitting_package(profile_dir,world,*,data_root=DATA):
    profile=validate_profile(profile_dir)
    film=read_film(Path(profile_dir)/'template.dat')
    actor=deepcopy(film['replays'][profile['source_actor_index']])
    film['replays']=[actor]
    poses=json.loads((Path(profile_dir)/'poses.json').read_text(encoding='utf-8'))
    # Fixed calibration cycle: rest, closure, teeth, vowels, rounded lips, F/V, L.
    sequence=['idle','a','b','c','d','o','u','f','l','idle']
    frames=[{'tick':float(i*30),'value':poses[name]['pose']} for i,name in enumerate(sequence)]
    channels=mouth_channels(frames,profile['bindings']['mouth'])
    actor['animation']={**channels,'sections':[{'start':0.,'duration':300.,'channels':list(channels),'repeats':1,'accumulate':False}]}
    actor['label']='FIT FACE - '+profile['name']
    camera=[c for c in bbs.plain(film['camera']) if c.get('type')!='bbs:audio']
    if len(camera)==1:camera[0]['duration']=300
    film['camera']=camera
    name='fit_'+slug(profile['name'])+'_'+uuid.uuid4().hex[:8]
    directory=Path(data_root)/'jobs'/name;directory.mkdir(parents=True)
    bbs.save(directory/(name+'.dat'),film)
    shutil.copytree(Path(profile_dir)/'assets',directory/'assets')
    json_write(directory/'face_preset.json',channels)
    json_write(directory/'manifest.json',{'schema':1,'name':name,'film':name+'.dat',
        'instance':profile['source_instance'],'world':world,'asset_hashes':profile['asset_hashes'],
        'fitting_scene':True,'profile_directory':str(profile_dir),'in_game_reviewed':False})
    validate_package(directory)
    return directory
