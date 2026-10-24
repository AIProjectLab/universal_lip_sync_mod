from copy import deepcopy
import math
from pathlib import Path
import tempfile
import unittest
import wave
from array import array

import bbs_storage as b
from lip_sync_studio import gestures as g
from lip_sync_studio import engine as e
import test_lip_sync_studio as fixtures


class GestureTests(unittest.TestCase):
    def setUp(self):
        self.analysis={'metadata':{'duration':8.},'mouthCues':[
            {'start':0.,'end':3.,'value':'C'},
            {'start':3.,'end':5.,'value':'X'},
            {'start':5.,'end':8.,'value':'D'}]}
        self.mapping={'left':'left_arm','right':'right_arm'}

    def test_pauses_are_still_and_beats_do_not_overlap(self):
        events=g.schedule(self.analysis,[.5]*400,'subtle','same audio')
        self.assertGreaterEqual(len(events),2)
        self.assertTrue(all(a['end']<b['start'] for a,b in zip(events,events[1:])))
        self.assertTrue(all(ev['end']<=3.12 or ev['start']>=5. for ev in events))
        self.assertEqual(events,g.schedule(self.analysis,[.5]*400,'subtle','same audio'))
        self.assertEqual(g.schedule(self.analysis,[0.]*400,'expressive','same audio'),[])

    def test_rotation_radians_rest_pose_and_no_distortion(self):
        rest={'left_arm':{'s':[1.1,1.,1.],'r':[.1,.2,-.1],'t':[.2,0.,0.]}}
        channels,events=g.channels(self.analysis,None,self.mapping,rest,'expressive','seed')
        for name,ch in channels.items():
            keys=ch['keyframes']
            self.assertEqual(keys[0]['value'],keys[-1]['value'])
            self.assertEqual(keys[0]['tick'],0.)
            self.assertEqual(keys[-1]['tick'],160.)
            self.assertTrue(all(a['tick']<b['tick'] for a,b in zip(keys,keys[1:])))
            for frame in keys:
                self.assertEqual(frame['interp'],'cubic_inout')
                self.assertTrue(all(abs(v)<math.pi/2 for v in frame['value']['r']))
                self.assertEqual(frame['value']['s'],keys[0]['value']['s'])
                self.assertEqual(frame['value']['t'],keys[0]['value']['t'])
        self.assertEqual(channels['pose/left_arm']['keyframes'][0]['value'],rest['left_arm'])

    def test_wav_energy_supplies_emphasis_and_silence_is_zero(self):
        root=e.ROOT/'build/studio_test_temp';root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temp:
            path=Path(temp)/'pulse.wav'
            with wave.open(str(path),'wb') as wav:
                wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000)
                values=array('h',[0]*16000+[int(14000*math.sin(i*.1)) for i in range(16000)])
                wav.writeframes(values.tobytes())
            envelope=g.energy_envelope(path)
            self.assertEqual(len(envelope),100)
            self.assertEqual(max(envelope[:50]),0.)
            self.assertGreater(min(envelope[50:]),.2)

    def test_active_hands_reach_chest_height_in_the_player_rig(self):
        # Standard shoulders are y=22, with wrist centres at y=12 at rest.
        # Check geometric height, not just the presence of nonzero rotations.
        for style in ('subtle','expressive'):
            channels,events=g.channels(self.analysis,None,self.mapping,{},style,'chest')
            self.assertTrue(events)
            for event in events:
                for side,bone in self.mapping.items():
                    if event['side'] not in (side,'both'):continue
                    key=next(k for k in channels['pose/'+bone]['keyframes']
                             if abs(k['tick']-event['peak']*20)<.001)
                    x,_,z=key['value']['r']
                    height=22.-10.*math.cos(x)*math.cos(z)
                    self.assertGreaterEqual(height,18.,(style,event,height))

    def test_long_phrase_keeps_a_speaking_pose_between_accents(self):
        analysis={'metadata':{'duration':10.},'mouthCues':[{'start':0.,'end':10.,'value':'C'}]}
        channels,events=g.channels(analysis,None,self.mapping,{},'expressive','hold')
        self.assertGreater(len(events),2)
        primary=channels['pose/left_arm']['keyframes']
        self.assertTrue(all(k['value']['r'][0]>.6 for k in primary if 6.4<=k['tick']<=195.))
        self.assertEqual(primary[-1]['value']['r'],[0.,0.,0.])

    def test_mislabeled_silence_has_no_speaking_pose(self):
        root=e.ROOT/'build/studio_test_temp';root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temp:
            path=Path(temp)/'silence.wav'
            with wave.open(str(path),'wb') as wav:
                wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000)
                wav.writeframes(bytes(16000*8*2))
            channels,events=g.channels(self.analysis,path,self.mapping,{},'expressive','quiet')
            self.assertEqual(events,[])
            self.assertTrue(all(k['value']['r']==[0.,0.,0.] for ch in channels.values() for k in ch['keyframes']))


class GestureSceneTests(unittest.TestCase):
    setUp=fixtures.StudioTests.setUp
    tearDown=fixtures.StudioTests.tearDown
    def test_authored_arm_animation_protected_and_gesture_lines_merge(self):
        analysis={'metadata':{'duration':3.},'mouthCues':[{'start':0.,'end':3.,'value':'C'}]}
        frames=e.pose_keys(analysis,self.poses)
        kwargs=dict(gesture_style='subtle',arm_bones={'left':'left_arm','right':'right_arm'})
        scene=deepcopy(self.film)
        scene['replays'][0]['animation']['pose/left_arm']={'type':'pose_transform','keyframes':[]}
        with self.assertRaisesRegex(e.StudioError,'authored animation'):
            e.make_film(scene,0,self.meta,self.profile_actor,frames,analysis,'assets:a.wav',**kwargs)
        self.assertIn('pose/left_arm',b.plain(scene)['replays'][0]['animation'])
        first,_,_=e.make_film(self.film,0,self.meta,self.profile_actor,frames,analysis,'assets:a.wav',start_tick=40.,**kwargs)
        second,_,_=e.make_film(first,0,self.meta,self.profile_actor,frames,analysis,'assets:b.wav',start_tick=140.,**kwargs)
        first_keys=b.plain(first)['replays'][0]['animation']['pose/left_arm']['keyframes']
        second_keys=b.plain(second)['replays'][0]['animation']['pose/left_arm']['keyframes']
        self.assertEqual(first_keys,[k for k in second_keys if k['tick']<=100.])
        self.assertEqual(b.plain(second)['camera'][-1]['tick'],140)
        self.assertEqual(b.plain(second)['replays'][0]['props'],b.plain(self.film)['replays'][0]['props'])

    def test_missing_mapping_and_ik_protected(self):
        data={'metadata':{'duration':1.},'mouthCues':[]}
        frames=e.pose_keys(data,self.poses)
        with self.assertRaisesRegex(e.StudioError,'shoulder bones'):
            e.make_film(self.film,0,self.meta,self.profile_actor,frames,data,'assets:a.wav',gesture_style='subtle')
        scene=deepcopy(self.film);scene['replays'][0]['form']['ik_targets']={'arm':{'enabled':True}}
        with self.assertRaisesRegex(e.StudioError,'IK or physics'):
            e.make_film(scene,0,self.meta,self.profile_actor,frames,data,'assets:a.wav',gesture_style='subtle',
                        arm_bones={'left':'left_arm','right':'right_arm'})

