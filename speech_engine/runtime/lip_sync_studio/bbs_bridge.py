"""One-request worker for the BBS companion add-on.

The editor owns its live film. This process reads a copied snapshot and returns
a new native film for the editor to apply; it never saves over an open film.
Only namespaced model/audio assets are published to the installation.
"""
from __future__ import annotations

from copy import deepcopy
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
import uuid
import wave

from . import engine as e

SCHEMA=1
MAX_REQUEST_BYTES=2*1024*1024
MAX_SNAPSHOT_BYTES=256*1024*1024


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),
                                    ensure_ascii=False,allow_nan=False).encode('utf-8')).hexdigest()


def _absolute(value,label,*,file=False):
    if not isinstance(value,str) or not value.strip() or not Path(value).is_absolute():
        raise e.StudioError(label+' must be an absolute local path.')
    path=Path(value).resolve()
    if file and not path.is_file():raise e.StudioError(label+' was not found: '+path.name)
    return path


def _integer(value,label,minimum=0):
    if type(value) is not int or not minimum<=value<=2147483647:
        raise e.StudioError(label+' must be a whole number of Minecraft ticks.')
    return value


def _flag(request,key,default=False):
    value=request.get(key,default)
    if type(value) is not bool:raise e.StudioError(key+' must be true or false.')
    return value


def _read_snapshot(path):
    if path.stat().st_size>MAX_SNAPSHOT_BYTES:raise e.StudioError('The editor snapshot is too large.')
    try:
        with gzip.open(path,'rb') as stream:raw=stream.read(MAX_SNAPSHOT_BYTES+1)
    except (OSError,EOFError) as error:
        raise e.StudioError('The editor snapshot is not a valid compressed BBS film.') from error
    if len(raw)>MAX_SNAPSHOT_BYTES:raise e.StudioError('The expanded editor snapshot is too large.')
    try:film=e.bbs.decode(raw)
    except Exception as error:raise e.StudioError('The editor snapshot could not be decoded.') from error
    plain=e.bbs.plain(film)
    if plain.get('bbs_format')!=3 or not isinstance(plain.get('replays'),list) or not plain['replays']:
        raise e.StudioError('The editor must supply a BBS 2.3 film containing its selected actor.')
    return film


class FileCancellation:
    def __init__(self,path):self.path=Path(path)
    def is_set(self):return self.path.exists()
    def check(self):
        if self.is_set():raise e.Cancelled('Cancelled. The open film was not changed.')


class ProfileAssets:
    """The profile's private snapshot, independent of installed copies."""
    def __init__(self,profile):self.root=Path(profile)/'assets'
    def read(self,relative):return (self.root/str(e.safe_relative(relative))).read_bytes()
    def model(self,identifier):
        directory=self.root/'models'/str(e.safe_relative(identifier))
        return {p.relative_to(directory).as_posix():p.read_bytes() for p in directory.rglob('*') if p.is_file()}


def _body_signature(assets,form):
    """Compare actual model/skin bytes after normalizing private model IDs.

    Profile capture reformats JSON and rewrites asset links. Those differences
    must not make a valid fit unusable; geometry/skin content changes must.
    """
    model=form.get('model')
    if not model:raise e.StudioError('This actor needs a local BBS model.')
    files=assets.model(model)
    if not files:raise e.StudioError('The character model has no local files.')
    def normalize(value):
        if value==model:return '<body-model>'
        if value.startswith(('assets:','b.a:')):
            relative=value.split(':',1)[1]
            prefix='models/'+model+'/'
            if relative.startswith(prefix):return '<model-file>/'+relative[len(prefix):]
            return '<asset-bytes>/'+hashlib.sha256(assets.read(relative)).hexdigest()
        return value
    content={}
    for name,raw in sorted(files.items()):
        if name.endswith('.json'):
            content[name]=e.map_strings(json.loads(raw),normalize)
        else:content[name]={'sha256':hashlib.sha256(raw).hexdigest()}
    texture=form.get('texture')
    if texture:
        if not texture.startswith(('assets:','b.a:')):
            raise e.StudioError('Save this character’s texture as a local BBS asset before fitting it.')
        skin=hashlib.sha256(assets.read(texture.split(':',1)[1])).hexdigest()
    else:skin=None
    return fingerprint({'files':content,'skin':skin})


def _face_signature(assets,form):
    roles=e.roles(form)
    result={}
    for role,index in roles.items():
        part=form['parts'][index]
        result[role]={'bone':part.get('bone'),'useTarget':part.get('useTarget',False),
                      'transform':part.get('transform',{}),
                      'assets':_body_signature(assets,part['form'])}
    return fingerprint(result)


def _profile_actor(profile,meta):
    return e.bbs.plain(e.read_film(Path(profile)/'template.dat'))['replays'][meta['source_actor_index']]


def _find_profile(assets,actor,data_root):
    form=actor.get('form',{})
    model=form.get('model','');skin=form.get('texture')
    body=_body_signature(assets,form)
    face=_face_signature(assets,form) if e.roles(form) else None
    found=[]
    for directory,unvalidated in e.profile_list(data_root=data_root):
        private_prefix='lip_sync_studio/'+str(unvalidated.get('id',''))+'/'
        direct=bool(unvalidated.get('id')) and model.startswith(private_prefix)
        try:
            meta=e.validate_profile(directory);saved=_profile_actor(directory,meta)['form']
            models=(meta.get('source_model'),saved.get('model'),*meta.get('compatible_models',[]))
            skins=(meta.get('source_skin'),saved.get('texture'),*meta.get('compatible_skins',[]))
            if model not in models or skin not in skins:continue
            # A draft remains a draft, even after the user moves its mouth in BBS.
            if direct and not meta.get('fitted'):
                return directory,meta
            reference=_body_signature(ProfileAssets(directory),saved)
            if body!=reference:
                # Elbow conversion intentionally changes the geometry. Only an
                # explicitly recorded pre-conversion fingerprint permits reuse.
                if body!=meta.get('bridge_source_body_signature'):continue
            if face is not None and face!=_face_signature(ProfileAssets(directory),saved):continue
            found.append((directory,meta,direct))
        except (e.StudioError,OSError,ValueError,KeyError):
            if direct:raise e.StudioError('This character’s saved profile changed or is missing files. Set up the character again.')
    if not found:return None,None
    found.sort(key=lambda item:(bool(item[1].get('fitted')),item[2],bool(item[1].get('bridge_fingerprint'))),reverse=True)
    return found[0][:2]


def _mouth_ready(assets,form):
    try:
        roles=e.roles(form)
        if 'mouth' not in roles:return False
        mouth=form['parts'][roles['mouth']]['form']
        poses=json.loads(assets.model(mouth['model'])['poses.json'])
        return all(not (set(e.MOUTH_BONES)-poses[key].get('pose',{}).keys()) for key in e.VISEME_MAP.values())
    except (e.StudioError,OSError,ValueError,KeyError,TypeError):return False


def _capabilities(profile,meta,assets,actor):
    form=actor.get('form',{})
    if profile:
        return {'mouth':'mouth' in meta.get('bindings',{}),'eyes':'eyes' in meta.get('bindings',{}),
                'arms':bool(e.gesture_mapping(profile,meta)),'elbows':bool(e.elbow_mapping(profile,meta))}
    try:
        bones=assets.bones(form['model'])
        arms=('left_arm' in bones and 'right_arm' in bones) or ('arm_left' in bones and 'arm_right' in bones)
        elbows=arms and any(b in bones for b in ('low_left_arm','left_forearm','left_elbow','low_arm_left'))
    except Exception:arms=elbows=False
    return {'mouth':_mouth_ready(assets,form),'eyes':'eyes' in e.roles(form),'arms':arms,'elbows':elbows}


def _setup_defaults(actor,bones,profile=None,meta=None):
    form=actor.get('form',{});roles=e.roles(form)
    if 'mouth' not in roles and profile:
        form=_profile_actor(profile,meta)['form'];roles=e.roles(form)
    part=form.get('parts',[])[roles['mouth']] if 'mouth' in roles else {}
    transform=part.get('transform',{});t=transform.get('t',[0.,.05,.004]);s=transform.get('s',[1.,1.,1.])
    head=part.get('bone') or next((bone for bone in bones if bone.lower()=='head'),'')
    return {'head_bone':head,**dict(zip(('tx','ty','tz'),t)),**dict(zip(('sx','sy','sz'),s)),
            'elbows':bool(profile and e.elbow_mapping(profile,meta))}


def _clone_profile(profile,meta,snapshot,index,data_root,name):
    """Copy a known fit for editing, keeping its custom face and eyelids."""
    identifier=e.slug(name)+'_'+uuid.uuid4().hex[:8]
    directory=Path(data_root)/'profiles'/identifier
    old_prefix='lip_sync_studio/'+meta['id']+'/'
    new_prefix='lip_sync_studio/'+identifier+'/'
    rewrite=lambda text:text.replace(old_prefix,new_prefix)
    for relative in meta['asset_hashes']:
        origin=Path(profile)/'assets'/str(e.safe_relative(relative))
        destination=directory/'assets'/str(e.safe_relative(rewrite(relative)))
        destination.parent.mkdir(parents=True,exist_ok=True)
        if origin.suffix=='.json':e.json_write(destination,e.map_strings(json.loads(origin.read_text(encoding='utf-8')),rewrite))
        else:shutil.copy2(origin,destination)
    saved=e.map_strings(_profile_actor(profile,meta),rewrite)
    cloned={**deepcopy(meta),'id':identifier,'name':name,'source_actor_index':index,
            'compatible_models':list(dict.fromkeys(meta.get('compatible_models',[])+[_profile_actor(profile,meta)['form']['model']])),
            'compatible_skins':list(dict.fromkeys(meta.get('compatible_skins',[])+[_profile_actor(profile,meta)['form'].get('texture')]))}
    result=deepcopy(snapshot)
    e.attach_profile(result['replays'][index],saved,cloned)
    e.bbs.save(directory/'template.dat',result)
    shutil.copy2(Path(profile)/'poses.json',directory/'poses.json')
    cloned['asset_hashes']={p.relative_to(directory/'assets').as_posix():e.digest(p) for p in (directory/'assets').rglob('*') if p.is_file()}
    e.json_write(directory/'profile.json',cloned)
    return directory


def install_assets(instance,files,cancel=None):
    """Publish only verified new files; refuse collisions and roll back our files.

    files maps safe asset-relative paths to (source_file, expected_sha256).
    Hard-link publication is atomic and never replaces an existing destination.
    """
    root=(Path(instance)/'config/bbs/assets').resolve()
    instance=Path(instance).resolve()
    try:root.relative_to(instance)
    except ValueError as error:raise e.StudioError('The BBS assets folder resolves outside the selected installation.') from error
    plan=[]
    for relative,(source,expected) in files.items():
        source=Path(source)
        if not source.is_file() or e.digest(source)!=expected:raise e.StudioError('A generated asset changed before installation: '+relative)
        destination=root/str(e.safe_relative(relative))
        try:destination.resolve().relative_to(root)
        except ValueError as error:raise e.StudioError('An asset path resolves outside the BBS assets folder.') from error
        if destination.exists():
            if not destination.is_file() or e.digest(destination)!=expected:
                raise e.StudioError('An existing asset differs and was not overwritten: '+relative)
        else:plan.append((source,destination,expected,relative))
    created=[];temporary=[]
    try:
        for source,destination,expected,relative in plan:
            if cancel:cancel.check()
            destination.parent.mkdir(parents=True,exist_ok=True)
            staged=destination.with_name('.'+destination.name+'.'+uuid.uuid4().hex+'.partial')
            temporary.append(staged)
            with staged.open('xb') as output,source.open('rb') as content:shutil.copyfileobj(content,output)
            if e.digest(staged)!=expected:raise e.StudioError('Asset staging failed verification: '+relative)
            try:
                os.link(staged,destination)
                created.append((destination,expected))
            except FileExistsError:
                if not destination.is_file() or e.digest(destination)!=expected:
                    raise e.StudioError('An asset changed while publishing: '+relative)
            staged.unlink();temporary.remove(staged)
        if cancel:cancel.check()
    except Exception:
        for destination,expected in reversed(created):
            if destination.is_file() and e.digest(destination)==expected:destination.unlink()
        raise
    finally:
        for staged in temporary:staged.unlink(missing_ok=True)
    return [str(destination) for destination,_ in created]


def _profile_files(profile,meta):
    return {relative:(Path(profile)/'assets'/str(e.safe_relative(relative)),expected)
            for relative,expected in meta['asset_hashes'].items()}


class Worker:
    def __init__(self,request,job_directory,*,data_root=None):
        if not isinstance(request,dict) or request.get('schema')!=SCHEMA:
            raise e.StudioError('Unsupported BBS lip-sync request format.')
        self.request=request;self.job=Path(job_directory).resolve();self.job.mkdir(parents=True,exist_ok=True)
        self.instance=_absolute(request.get('instance'),'Minecraft installation')
        self.assets=e.Assets(self.instance)
        self.snapshot=_absolute(request.get('snapshot'),'Editor snapshot',file=True)
        if self.snapshot in (self.job/'result.dat',self.job/'rollback.dat'):
            raise e.StudioError('The input snapshot must have its own file name.')
        self.film=_read_snapshot(self.snapshot);self.plain=e.bbs.plain(self.film)
        self.index=_integer(request.get('actor_index'),'Actor index')
        if self.index>=len(self.plain['replays']):raise e.StudioError('The selected actor is missing from the editor snapshot.')
        self.actor=self.plain['replays'][self.index]
        if not self.actor.get('form',{}).get('model'):
            raise e.StudioError('Choose a local BBS model actor. This actor has no model rig to animate.')
        self.start=_integer(request.get('start_tick',0),'Start time')
        self.data=Path(data_root or request.get('data_root') or e.DATA).resolve()
        self.cancel=FileCancellation(self.job/'cancel.flag')
        self.before_hash=fingerprint(self.actor)

    def status(self,message,state='working'):
        e.json_write(self.job/'status.json',{'schema':SCHEMA,'state':state,'message':str(message)})

    def _profile(self):return _find_profile(self.assets,self.actor,self.data)

    def _response(self,**values):
        return {'schema':SCHEMA,'ok':True,'action':self.request['action'],
                'actor_index':self.index,'target_actor_index':self.index,
                'before_actor_hash':self.before_hash,'before_snapshot_sha256':e.digest(self.snapshot),**values}

    def _finish_film(self,film,profile,meta,extra_files=None,**values):
        self.cancel.check()
        # The exact source bytes are a restore point; this never alters them.
        restore=self.job/'rollback.dat'
        if restore.exists() and e.digest(restore)!=e.digest(self.snapshot):
            raise e.StudioError('This job folder belongs to a different snapshot. Start a new job.')
        if not restore.exists():shutil.copy2(self.snapshot,restore)
        result=self.job/'result.dat';temporary=self.job/('result.'+uuid.uuid4().hex+'.tmp')
        e.bbs.save(temporary,film)
        if e.bbs.load(temporary)!=film:raise e.StudioError('Generated film failed its native storage roundtrip.')
        self.cancel.check()
        files=_profile_files(profile,meta);files.update(extra_files or {})
        installed=install_assets(self.instance,files,self.cancel)
        os.replace(temporary,result)
        return self._response(result_film=str(result),rollback_film=str(restore),
            profile_directory=str(profile),fitted=bool(meta.get('fitted')),
            capabilities=_capabilities(profile,meta,self.assets,self.actor),
            asset_hashes={key:pair[1] for key,pair in files.items()},installed_assets=installed,**values)

    def inspect(self):
        profile,meta=self._profile()
        try:bones=self.assets.bones(self.actor['form']['model'])
        except e.StudioError:bones=[]
        ready=bool(meta.get('fitted')) if meta else _mouth_ready(self.assets,self.actor['form'])
        return self._response(fitted=ready,profile_directory=str(profile) if profile else None,
            capabilities=_capabilities(profile,meta,self.assets,self.actor),bones=bones,
            setup_defaults=_setup_defaults(self.actor,bones,profile,meta),
            visual_review_required=bool(meta and not meta.get('fitted')),
            message='Character ready for lip sync.' if ready else 'Set up this character once, preview the fit, then save it.')

    def setup(self):
        setup=self.request.get('setup',{})
        if not isinstance(setup,dict):raise e.StudioError('Setup options must be an object.')
        confirmed=_flag(setup,'visual_confirmed')
        elbows=_flag(setup,'elbows')
        profile,meta=self._profile()
        try:bones=self.assets.bones(self.actor['form']['model'])
        except e.StudioError:bones=[]
        defaults=_setup_defaults(self.actor,bones,profile,meta)
        head=setup.get('head_bone',defaults['head_bone'])
        if not isinstance(head,str) or not head:raise e.StudioError('Select the real head attachment for this character, or ask Antigravity for rig help.')
        components={}
        for key in ('tx','ty','tz','sx','sy','sz'):
            value=setup.get(key,defaults[key])
            if type(value) not in (float,int) or not math.isfinite(value):raise e.StudioError('Mouth placement values must be finite numbers.')
            if key.startswith('s') and value<=0:raise e.StudioError('Mouth size must be greater than zero.')
            if abs(value)>10000:raise e.StudioError('Mouth placement is outside the supported range.')
            components[key]=float(value)
        self.status('Saving a private character rig for preview…')
        name=str(setup.get('name') or self.actor.get('label') or 'BBS character').strip()[:120]
        before_body=_body_signature(self.assets,self.actor['form'])
        if profile:
            profile=_clone_profile(profile,meta,self.film,self.index,self.data,name)
        else:
            has_mouth='mouth' in e.roles(self.actor['form'])
            if bones and head not in bones:raise e.StudioError('The chosen head bone was not found on this character.')
            profile=e.capture_profile(self.instance,self.snapshot,self.index,name,fitted=confirmed,
                attach_head=None if has_mouth else head,data_root=self.data)
        meta=e.validate_profile(profile)
        template=e.read_film(profile/'template.dat')
        target=template['replays'][self.index]
        part=e.bbs.plain(target['form'])['parts'][meta['bindings']['mouth']]
        part['bone']=head
        transform=deepcopy(part.get('transform',{}))
        transform['t']=[components[key] for key in ('tx','ty','tz')]
        transform['s']=[components[key] for key in ('sx','sy','sz')]
        part['transform']=transform
        target['form']['parts'].value[meta['bindings']['mouth']]=e.bbs.node(part)
        if 'eyes' not in meta.get('bindings', {}):
            try:
                src_eyes = self.assets.model('player/eyes_dialogue')
                dst_eyes = profile / f'assets/models/lip_sync_studio/{meta["id"]}/eyes'
                dst_eyes.mkdir(parents=True, exist_ok=True)
                for item in ('model.bbs.json', 'poses.json', 'texture.png'):
                    (dst_eyes / item).write_bytes(src_eyes[item])
                cfg_content = json.dumps({'texture': f'assets:models/lip_sync_studio/{meta["id"]}/eyes/texture.png', 'culling': False}, indent=2)
                (dst_eyes / 'config.json').write_text(cfg_content, encoding='utf-8')
                for item in ('config.json', 'model.bbs.json', 'poses.json', 'texture.png'):
                    rel = f'models/lip_sync_studio/{meta["id"]}/eyes/{item}'
                    meta['asset_hashes'][rel] = e.digest(dst_eyes / item)
                eyes_part = {
                    'bone': head, 'useTarget': 0,
                    'transform': {'s': [1., 1., 1.], 't': [0., 0., 0.004], 'r': [0., 0., 0.]},
                    'form': {
                        'id': 'bbs:model', 'model': f'lip_sync_studio/{meta["id"]}/eyes',
                        'texture': f'assets:models/lip_sync_studio/{meta["id"]}/eyes/texture.png',
                        'visible': 1, 'lighting': 1., 'parts': [], 'pose': {}
                    }
                }
                plain_target = e.bbs.plain(target)
                plain_target['form'].setdefault('parts', []).append(eyes_part)
                target['form'] = e.bbs.node(plain_target['form'])
                meta['bindings']['eyes'] = len(plain_target['form']['parts']) - 1
            except Exception:
                pass
        e.bbs.save(profile/'template.dat',template)
        meta.update(fitted=confirmed,bridge_source_body_signature=before_body,
                    bridge_fingerprint=fingerprint({'body':before_body,'face':_face_signature(ProfileAssets(profile),e.bbs.plain(target['form']))}),
                    bridge_visual_reviewed=confirmed,source_instance=str(self.instance))
        e.json_write(profile/'profile.json',meta)
        if elbows and not e.elbow_mapping(profile,meta):
            profile=e.create_elbow_profile(profile,data_root=self.data);meta=e.validate_profile(profile)
        saved=_profile_actor(profile,meta)
        output=deepcopy(self.film);e.attach_profile(output['replays'][self.index],saved,meta)
        return self._finish_film(output,profile,meta,visual_review_required=not confirmed,
             setup_defaults=_setup_defaults(saved,bones,profile,meta),
             message='Character setup saved.' if confirmed else 'Preview the mouth in BBS. Save the setup after checking its placement and colour.')

    def _generate_profile(self):
        profile,meta=self._profile()
        if profile:
            if not meta.get('fitted'):raise e.StudioError('Preview this character’s mouth and click Save setup before generating lip sync.')
            return profile,meta
        if not _mouth_ready(self.assets,self.actor['form']):
            raise e.StudioError('This character needs a mouth setup. Choose 1-Click Auto-Fit or Ask Antigravity first.')
        # A fitted mouth already present in the live actor is authoritative. It
        # is captured anew when its transform/assets differ from an older fit.
        profile=e.capture_profile(self.instance,self.snapshot,self.index,
            self.actor.get('label') or 'BBS character',data_root=self.data)
        meta=e.validate_profile(profile)
        meta['bridge_source_body_signature']=_body_signature(self.assets,self.actor['form'])
        meta['bridge_fingerprint']=_face_signature(self.assets,self.actor['form'])
        e.json_write(profile/'profile.json',meta)
        return profile,meta

    def _audio_segment(self):
        source=_absolute(self.request.get('audio'),'Recording',file=True)
        reuse=_flag(self.request,'audio_reuse')
        offset=_integer(self.request.get('audio_offset_ticks',0),'Audio offset')
        duration=_integer(self.request.get('audio_duration_ticks',0),'Audio duration')
        link=self.request.get('audio_link')
        if reuse:
            candidates=[]
            for i,clip in enumerate(self.plain.get('camera',[])):
                if clip.get('type')=='bbs:audio' and clip.get('tick',0)==self.start and (not link or clip.get('audio')==link):
                    candidates.append((i,clip))
            selected=self.request.get('audio_clip_index')
            if selected is not None:
                selected=_integer(selected,'Audio clip index')
                candidates=[pair for pair in candidates if pair[0]==selected]
            if len(candidates)!=1:raise e.StudioError('The selected audio clip changed or is ambiguous. Select one clip and try again.')
            clip=candidates[0][1];link=clip.get('audio')
            if not isinstance(link,str) or not link.startswith('assets:'):raise e.StudioError('The selected clip needs a local assets: recording.')
            if clip.get('pitch',1)!=1:raise e.StudioError('Changed audio pitch is not supported for automatic lip sync. Use a normal-speed recording.')
            if not clip.get('enabled',True):raise e.StudioError('Enable this audio clip before generating lip sync.')
            actual_offset=_integer(clip.get('offset',0),'Selected clip offset')
            actual_duration=_integer(clip.get('duration',0),'Selected clip duration',minimum=1)
            if 'audio_offset_ticks' in self.request and offset!=actual_offset:raise e.StudioError('The selected clip offset changed. Reopen Lip sync.')
            if 'audio_duration_ticks' in self.request and duration!=actual_duration:raise e.StudioError('The selected clip duration changed. Reopen Lip sync.')
            offset,duration=actual_offset,actual_duration
            if hashlib.sha256(self.assets.read(link[7:])).hexdigest()!=e.digest(source):
                raise e.StudioError('The selected clip and recording file do not match.')
        self.status('Converting the recording…')
        normalized=self.job/'normalized.wav';seconds=e.normalize_audio(source,normalized,self.cancel)
        if normalized.stat().st_size>MAX_SNAPSHOT_BYTES:raise e.StudioError('The recording is too long for one dialogue job. Split it into shorter lines.')
        if offset==0 and duration==0:return normalized,seconds,reuse,link
        segment=self.job/'dialogue.wav'
        with wave.open(str(normalized),'rb') as incoming:
            rate=incoming.getframerate();start=round(offset*rate/20)
            if start>=incoming.getnframes():raise e.StudioError('The clip starts after the end of the recording.')
            length=min(incoming.getnframes()-start,round(duration*rate/20) if duration else incoming.getnframes()-start)
            incoming.setpos(start)
            with wave.open(str(segment),'wb') as outgoing:
                outgoing.setparams(incoming.getparams())
                remaining=length
                while remaining:
                    self.cancel.check();block=incoming.readframes(min(remaining,16000*10))
                    if not block:break
                    outgoing.writeframesraw(block);remaining-=len(block)//2
        return segment,length/rate,reuse,link

    def generate(self):
        profile,meta=self._generate_profile()
        audio,seconds,reuse,old_link=self._audio_segment()
        recognizer='pocketSphinx' if self.request.get('language','English')=='English' else 'phonetic'
        transcript=self.request.get('transcript','')
        if not isinstance(transcript,str) or len(transcript)>1000000:raise e.StudioError('The transcript must be plain text of a reasonable length.')
        analysis=e.analyse(audio,recognizer,transcript,self.data/'analysis_cache',self.cancel,self.status)
        e.validate_cues(analysis)
        if abs(seconds-analysis['metadata']['duration'])>.08:raise e.StudioError('Audio analysis duration does not match the selected recording segment.')
        style=self.request.get('gestures','off')
        if style not in ('off','subtle','expressive'):raise e.StudioError('Choose Off, Subtle or Expressive gestures.')
        blinks=_flag(self.request,'blinks',True)
        poses=json.loads((profile/'poses.json').read_text(encoding='utf-8'))
        frames=e.pose_keys(analysis,poses,meta['viseme_map'])
        audio_relative='audio/lip_sync_studio/'+meta['id']+'/'+e.digest(audio)[:20]+'.wav'
        audio_link=old_link if reuse else 'assets:'+audio_relative
        self.status('Building synchronized mouth, eyes and gestures…')
        result,generated,ticks=e.make_film(self.film,self.index,meta,_profile_actor(profile,meta),frames,analysis,audio_link,
            start_tick=self.start,blinks=blinks,new_test=False,seed=e.digest(audio),audio_title=Path(self.request['audio']).stem,
            gesture_style=style,arm_bones=e.gesture_mapping(profile,meta),gesture_audio=audio,
            elbow_bones=e.elbow_mapping(profile,meta),allow_overlap=True)
        # Preserve every native type tag on existing clips, and do not create a
        # second audio track when the dialog used the selected BBS clip.
        if reuse:result['camera']=deepcopy(self.film['camera'])
        else:
            extra=deepcopy(result['camera'].value[-1])
            result['camera']=deepcopy(self.film['camera']);result['camera'].value.append(extra)
        original_actor=self.film['replays'][self.index]
        if 'label' in original_actor.value:result['replays'][self.index]['label']=deepcopy(original_actor['label'])
        else:result['replays'][self.index].value.pop('label',None)
        e.json_write(self.job/'analysis.json',analysis)
        e.json_write(self.job/'generated_channels.json',generated)
        extra_files={} if reuse else {audio_relative:(audio,e.digest(audio))}
        return self._finish_film(result,profile,meta,extra_files=extra_files,audio_reused=reuse,
            generated_channels=list(generated),ticks=ticks,start_tick=self.start,
            visual_review_required=False,message='Lip sync generated. Preview it in BBS before recording.')

    def assist_prepare(self):
        from .rig_assistance import prepare_bundle
        result=prepare_bundle(self.instance,self.snapshot,self.index,self.job/'rig-help',
            reason=str(self.request.get('reason','Please fit this character for lip sync.')),
            character_name=self.actor.get('label'))
        return self._response(**{key:value for key,value in result.items() if key!='schema'},
                              visual_review_required=True)

    def assist_resume(self):
        from .rig_assistance import validate_result,fingerprint as rig_fingerprint
        bundle=_absolute(self.request.get('bundle_directory'),'Antigravity task folder')
        result=validate_result(bundle)
        if result['target_actor_index']!=self.index or result['source_actor_fingerprint']!=rig_fingerprint(self.actor):
            raise e.StudioError('The live character changed while Antigravity was working. Create a new rig-help task from the current character.')
        source=Path(result['profile_directory']);source_meta=e.validate_profile(source)
        destination=self.data/'profiles'/source_meta['id']
        if destination.exists():
            meta=e.validate_profile(destination)
            if meta['asset_hashes']!=source_meta['asset_hashes']:raise e.StudioError('A different profile already uses this Antigravity result ID.')
        else:
            shutil.copytree(source,destination)
            meta=source_meta
            meta.update(source_instance=str(self.instance),source_film=str(self.snapshot),fitted=False,
                        bridge_visual_reviewed=False,
                        bridge_source_body_signature=_body_signature(self.assets,self.actor['form']))
            e.json_write(destination/'profile.json',meta)
        film=e.read_film(result['result_film'])
        return self._finish_film(film,destination,meta,visual_review_required=True,
            message='Antigravity rig files checked. Preview the mouth in BBS, then Save setup before continuing.')

    def run(self):
        action=self.request.get('action')
        if action not in ('inspect','setup','generate','assist_prepare','assist_resume'):
            raise e.StudioError('Unknown BBS lip-sync action.')
        self.cancel.check();self.status('Checking the selected character…')
        result=getattr(self,action)()
        self.status(result.get('message') or 'Complete.','complete')
        return result


def process_request(request,job_directory,*,data_root=None):
    """Useful for tests/hosts; raises StudioError without displaying a GUI."""
    return Worker(request,job_directory,data_root=data_root).run()


def main(argv=None):
    argv=sys.argv[1:] if argv is None else argv
    if len(argv)!=1:
        print('Usage: python -m lip_sync_studio.bbs_bridge ABSOLUTE_REQUEST_JSON',file=sys.stderr);return 2
    job=None;action='unknown'
    try:
        path=_absolute(argv[0],'Request file',file=True);job=path.parent
        if path.stat().st_size>MAX_REQUEST_BYTES:raise e.StudioError('The request is too large.')
        request=json.loads(path.read_text(encoding='utf-8-sig'));action=request.get('action','unknown')
        response=process_request(request,job)
        e.json_write(job/'response.json',response)
        return 0
    except Exception as error:
        message=str(error) or type(error).__name__
        if job:
            state='cancelled' if isinstance(error,e.Cancelled) else 'error'
            e.json_write(job/'status.json',{'schema':SCHEMA,'state':state,'message':message})
            e.json_write(job/'response.json',{'schema':SCHEMA,'ok':False,'action':action,'error':message,
                                           'message':message,'cancelled':state=='cancelled'})
        print(message,file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())
