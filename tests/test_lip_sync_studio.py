"""Regression tests for real failures seen during in-game dialogue testing."""
from copy import deepcopy
import json
import math
from pathlib import Path
import tempfile
import unittest
import wave
import zipfile

import bbs_storage as b
from lip_sync_studio import engine as e


class StudioTests(unittest.TestCase):
    def setUp(self):
        temporary_root=(e.ROOT/'build/studio_test_temp').resolve()
        temporary_root.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=temporary_root)
        self.root=Path(self.temp.name)
        assert self.root.resolve().parent==temporary_root
        self.instance=self.root/'instance'
        (self.instance/'mods').mkdir(parents=True)
        (self.instance/'saves/world').mkdir(parents=True)
        (self.instance/'saves/world/level.dat').write_bytes(b'world')
        original=e.ROOT/'lip_sync_studio/templates/mouth'
        with zipfile.ZipFile(self.instance/'mods/bbs-test.jar','w') as z:
            z.writestr('fabric.mod.json',json.dumps({'id':'bbs','version':'2.3.3'}))
            z.writestr('assets/bbs/assets/models/test/body/body.bbs.json',json.dumps({
                'model':{'groups':{'head':{'origin':[0,24,0]}},'texture':[16,16]}}))
            z.writestr('assets/bbs/assets/models/test/body/config.json',json.dumps({'texture':'assets:models/test/body/skin.png'}))
            z.writestr('assets/bbs/assets/models/test/body/skin.png',(original/'texture.png').read_bytes())
            for p in original.iterdir():
                z.writestr('assets/bbs/assets/models/player/mouth_dialogue/'+p.name,p.read_bytes())
        part={'bone':'head','transform':{'s':[1.,1.,1.],'t':[0.,.05,.004],'r':[0.,0.,0.]},
              'form':{'id':'bbs:model','model':'player/mouth_dialogue','texture':'assets:models/player/mouth_dialogue/texture.png','parts':[]}}
        self.actor={'label':'Alice','form':{'id':'bbs:model','model':'test/body','texture':'assets:models/test/body/skin.png','parts':[part]},
                    'props':{'x':{'$anim':{'v':15.,'track':{'type':'double','keyframes':[{'tick':0.,'value':15.}]}}}},
                    'animation':{'sections':[]},'sections':[],'layers':[]}
        self.film=b.node({'bbs_format':3,'replays':[self.actor],
             'camera':[{'type':'bbs:movement','tick':0,'duration':20,'layer':0,'x':10.5,'y':6.,'z':2.}],
             'relative_coordinates':{'x':250.,'y':0.,'z':-450.}})
        self.source=self.root/'source.dat';b.save(self.source,self.film)
        self.data=self.root/'data'
        self.profile=e.capture_profile(self.instance,self.source,0,'Alice',data_root=self.data)
        self.meta=e.validate_profile(self.profile)
        self.profile_actor=b.plain(e.read_film(self.profile/'template.dat'))['replays'][0]
        self.poses=json.loads((self.profile/'poses.json').read_text())

    def tearDown(self):
        self.temp.cleanup()

    def test_profile_owns_assets_and_keeps_fitted_depth(self):
        form=self.profile_actor['form']
        self.assertTrue(form['model'].startswith('lip_sync_studio/'))
        self.assertEqual(form['parts'][0]['transform']['t'][2],.004)
        self.assertIn(form['texture'][7:],self.meta['asset_hashes'])
        self.assertEqual(b.load(self.source),self.film)

    def test_dense_cues_do_not_accumulate_drift(self):
        cues=[{'start':i*.01,'end':(i+1)*.01,'value':'A' if i%2 else 'C'} for i in range(100)]
        data={'metadata':{'duration':1.0},'mouthCues':cues}
        frames=e.pose_keys(data,self.poses)
        self.assertEqual(frames[-1]['tick'],20.)
        self.assertTrue(all(x['tick']<y['tick'] for x,y in zip(frames,frames[1:])))
        self.assertTrue(any(not f['tick'].is_integer() for f in frames))
        self.assertEqual(frames[-1]['value'],self.poses['idle']['pose'])

    def test_fractional_audio_tail_is_not_cut_off(self):
        data={'metadata':{'duration':1.037},'mouthCues':[{'start':0.,'end':1.037,'value':'D'}]}
        frames=e.pose_keys(data,self.poses)
        self.assertEqual(frames[-1]['tick'],21.)
        film,_,duration=e.make_film(self.film,0,self.meta,self.profile_actor,frames,data,
                                   'assets:audio/test.wav',new_test=True)
        self.assertEqual(duration,21)
        self.assertEqual(b.plain(film)['camera'][-1]['duration'],21)

    def test_dialogue_keeps_other_actor_scene_and_unrelated_attachment(self):
        scene=deepcopy(self.film)
        extra=deepcopy(scene['replays'][0]);extra['label']='Bob'
        scene['replays'].value.append(extra)
        helmet=deepcopy(scene['replays'][0]['form']['parts'][0])
        helmet['form']['model']='prop/helmet'
        scene['replays'][0]['form']['parts'].value.insert(0,helmet)
        data={'metadata':{'duration':1.},'mouthCues':[{'start':0.,'end':1.,'value':'C'}]}
        out,generated,_=e.make_film(scene,0,self.meta,self.profile_actor,e.pose_keys(data,self.poses),data,
             'assets:audio/test.wav',start_tick=60.)
        result=b.plain(out)
        self.assertEqual(out['replays'][1],extra)
        self.assertEqual(out['replays'][0]['props'],scene['replays'][0]['props'])
        self.assertEqual(out['relative_coordinates'],scene['relative_coordinates'])
        self.assertTrue(all(name.startswith('1/pose/') for name in generated))
        self.assertEqual(result['camera'][0],b.plain(scene)['camera'][0])
        self.assertEqual(result['camera'][-1]['tick'],60)
        self.assertEqual(result['replays'][0]['animation']['1/pose/Open']['keyframes'][0]['value']['s'][1],0)
        # A second line preserves the first line and its audio.
        second,_,_=e.make_film(out,0,self.meta,self.profile_actor,e.pose_keys(data,self.poses),data,
             'assets:audio/second.wav',start_tick=120.)
        self.assertEqual(len(b.plain(second)['camera']),3)
        first_keys=[f for f in result['replays'][0]['animation']['1/pose/Open']['keyframes'] if f['tick']<=80]
        second_keys=[f for f in b.plain(second)['replays'][0]['animation']['1/pose/Open']['keyframes'] if f['tick']<=80]
        self.assertEqual(first_keys,second_keys)

    def test_wrong_character_or_skin_is_rejected(self):
        actor=deepcopy(self.film['replays'][0]);actor['form']['model']='different/model'
        with self.assertRaises(e.StudioError):e.attach_profile(actor,self.profile_actor,self.meta)

    def test_same_actor_overlap_rejected_and_description_preserved(self):
        source=deepcopy(self.film);source['description']='My original scene notes'
        data={'metadata':{'duration':1.},'mouthCues':[{'start':0.,'end':1.,'value':'C'}]}
        frames=e.pose_keys(data,self.poses)
        first,_,_=e.make_film(source,0,self.meta,self.profile_actor,frames,data,'assets:audio/one.wav',start_tick=40.)
        self.assertTrue(b.plain(first)['description'].startswith('My original scene notes'))
        with self.assertRaises(e.StudioError):
            e.make_film(first,0,self.meta,self.profile_actor,frames,data,'assets:audio/two.wav',start_tick=50.)

    def test_unicode_character_and_model_names_roundtrip(self):
        profile=e.capture_profile(self.instance,self.source,0,'Character — علی',data_root=self.data)
        self.assertEqual(e.validate_profile(profile)['name'],'Character — علی')
        value=b.node({'name':'Character — علی U0001f642'})
        self.assertEqual(b.decode(b.encode(value)),value)
        actor=deepcopy(self.film['replays'][0]);actor['form']['texture']='assets:other_skin.png'
        with self.assertRaises(e.StudioError):e.attach_profile(actor,self.profile_actor,self.meta)

    def test_negative_paths_and_changed_profile_assets_rejected(self):
        for path in ('../escape','/absolute','C:/escape','folder/../../escape'):
            with self.assertRaises(e.StudioError):e.safe_relative(path)
        relative=next(iter(self.meta['asset_hashes']))
        (self.profile/'assets'/relative).write_bytes(b'changed')
        with self.assertRaises(e.StudioError):e.validate_profile(self.profile)

    def test_fitting_profile_requires_human_fit_and_resolves_original_model(self):
        raw=deepcopy(self.film);raw['replays'][0]['form']['parts']=[]
        path=self.root/'raw.dat';b.save(path,raw)
        prepared=e.capture_profile(self.instance,path,0,'New character',attach_head='head',data_root=self.data)
        self.assertFalse(e.validate_profile(prepared)['fitted'])
        package=e.make_fitting_package(prepared,'world',data_root=self.data)
        e.deploy(package)
        installed=self.instance/'saves/world/bbs/films'/json.loads((package/'manifest.json').read_text())['film']
        fitted=e.capture_profile(self.instance,installed,0,'Fitted character',data_root=self.data)
        self.assertEqual(e.validate_profile(fitted)['source_model'],'test/body')

    def test_full_job_from_real_wav_with_silence_and_nonoverwriting_deploy(self):
        audio=self.root/'quiet.wav'
        with wave.open(str(audio),'wb') as wav:
            wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000)
            wav.writeframes(b'\0\0'*16000)
        result={'metadata':{'duration':1.},'mouthCues':[{'start':0.,'end':1.,'value':'X'}]}
        job=e.Job(audio,self.profile,self.instance,'world',data_root=self.data)
        package=e.build_job(job,analysis_override=result)
        manifest=e.validate_package(package)
        self.assertEqual(manifest['world'],'world')
        film=e.deploy(package)
        self.assertEqual(film.parent,self.instance/'saves/world/bbs/films')
        self.assertTrue(film.is_file())
        self.assertEqual(b.load(self.source),self.film)
        with self.assertRaises(e.StudioError):e.deploy(package)
        self.assertTrue(film.is_file())


if __name__=='__main__':unittest.main()
