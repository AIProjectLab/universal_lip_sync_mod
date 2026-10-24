from copy import deepcopy
import json
import math
import unittest

import bbs_storage as b
from lip_sync_studio import engine as e, gestures as g, rigging as r
import test_lip_sync_studio as fixtures


def standard_model():
    groups={'body':{'origin':[0,23,1]}}
    for side,x in (('left',-6),('right',6)):
        cube={'origin':[x,18,0],'from':[x-2,12,-2],'size':[4,12,4],
              'uvs':{face:[4.,20.,8.,32.] for face in ('front','back','left','right')}}
        cube['uvs'].update(top=[8.,20.,4.,16.],bottom=[12.,16.,8.,20.])
        outer=deepcopy(cube);outer['offset']=.25
        groups[side+'_arm']={'origin':[x,22,0],'parent':'body','cubes':[cube,outer]}
        groups[side+'_arm_end']={'origin':[x,12,0],'parent':side+'_arm'}
        groups[side+'_arm_item']={'origin':[x,11,1],'parent':side+'_arm'}
    return {'model':{'texture':[64,64],'groups':groups}}


MAPPING={'left':'left_arm','right':'right_arm'}
ELBOWS={'left':'left_forearm','right':'right_forearm'}


class ElbowGeometryTests(unittest.TestCase):
    def test_split_preserves_inflated_bounds_and_side_texture_coordinates(self):
        for cube in standard_model()['model']['groups']['left_arm']['cubes']:
            upper,lower=r.split_cube(cube,18.)
            offset=cube.get('offset',0)
            bottom=cube['from'][1]-offset;top=bottom+cube['size'][1]+offset*2
            self.assertEqual(lower['from'][1],bottom)
            self.assertEqual(lower['from'][1]+lower['size'][1],upper['from'][1])
            self.assertEqual(upper['from'][1]+upper['size'][1],top)
            self.assertEqual(upper['uvs']['top'],cube['uvs']['top'])
            self.assertEqual(lower['uvs']['bottom'],cube['uvs']['bottom'])
            for y in (bottom+.3,17.7,18.3,top-.3):
                piece=lower if y<18 else upper
                for face in ('front','back','left','right'):
                    v0,v1=cube['uvs'][face][1::2]
                    old_v=v0+(v1-v0)*(top-y)/(top-bottom)
                    pv0,pv1=piece['uvs'][face][1::2]
                    ptop=piece['from'][1]+piece['size'][1]
                    new_v=pv0+(pv1-pv0)*(ptop-y)/piece['size'][1]
                    self.assertAlmostEqual(old_v,new_v)

    def test_joints_and_hand_anchors_follow_the_forearms(self):
        source=standard_model();before=deepcopy(source)
        result,elbows=r.split_player_arms(source,MAPPING)
        self.assertEqual(source,before)
        self.assertEqual(elbows,ELBOWS)
        groups=result['model']['groups']
        for side,x in (('left',-6),('right',6)):
            self.assertEqual(groups[side+'_forearm']['origin'],[x,18.,0])
            self.assertEqual(groups[side+'_forearm']['parent'],side+'_arm')
            for child in ('_arm_end','_arm_item'):
                self.assertEqual(groups[side+child]['parent'],side+'_forearm')
            joint=groups[side+'_arm']['cubes'][-1]
            self.assertTrue(all(size<4 for size in joint['size']))
        with self.assertRaises(ValueError):r.split_player_arms(result,MAPPING)

    def test_unusual_arm_geometry_requires_manual_conversion(self):
        source=standard_model()
        source['model']['groups']['left_arm']['cubes'][0]['size'][1]=10
        with self.assertRaisesRegex(ValueError,'manual splitting'):
            r.split_player_arms(source,MAPPING)


class ElbowGestureTests(unittest.TestCase):
    def test_elbows_bend_and_recover_with_coordinated_chest_height(self):
        analysis={'metadata':{'duration':8.},'mouthCues':[{'start':0.,'end':8.,'value':'C'}]}
        for style in ('subtle','expressive'):
            channels,events=g.channels(analysis,None,MAPPING,{},style,'elbows',elbows=ELBOWS)
            self.assertEqual(len(channels),4)
            for event in events:
                for side in MAPPING:
                    if event['side'] not in (side,'both'):continue
                    peak=event['peak']*20
                    upper=next(k for k in channels['pose/'+MAPPING[side]]['keyframes'] if abs(k['tick']-peak)<.001)
                    lower=next(k for k in channels['pose/'+ELBOWS[side]]['keyframes'] if abs(k['tick']-peak)<.001)
                    x,_,z=upper['value']['r'];bend=lower['value']['r'][0]
                    self.assertGreater(bend,math.radians(45))
                    self.assertLess(bend,math.radians(75))
                    height=22.-(4.*math.cos(x)+6.*math.cos(x+bend))*math.cos(z)
                    self.assertGreater(height,18.)
                    self.assertLess(height,22.)
            for side in MAPPING:
                upper=channels['pose/'+MAPPING[side]]['keyframes']
                lower=channels['pose/'+ELBOWS[side]]['keyframes']
                self.assertEqual([k['tick'] for k in upper],[k['tick'] for k in lower])
                self.assertEqual(lower[0]['value']['r'],[0.,0.,0.])
                self.assertEqual(lower[-1]['value']['r'],[0.,0.,0.])


class ElbowProfileTests(unittest.TestCase):
    setUp=fixtures.StudioTests.setUp
    tearDown=fixtures.StudioTests.tearDown

    def add_arms(self):
        form=self.profile_actor['form']
        file=next((self.profile/'assets/models'/form['model']).glob('*.bbs.json'))
        model=json.loads(file.read_text(encoding='utf-8'))
        model['model']['groups'].update(standard_model()['model']['groups'])
        e.json_write(file,model)
        self.meta['asset_hashes'][file.relative_to(self.profile/'assets').as_posix()]=e.digest(file)
        e.json_write(self.profile/'profile.json',self.meta)

    def test_conversion_owns_new_assets_preserves_source_and_reuses_original_scene(self):
        self.add_arms()
        before={str(p):e.digest(p) for p in self.profile.rglob('*') if p.is_file()}
        output=e.create_elbow_profile(self.profile,data_root=self.data)
        self.assertEqual(before,{str(p):e.digest(p) for p in self.profile.rglob('*') if p.is_file()})
        meta=e.validate_profile(output)
        self.assertEqual(e.elbow_mapping(output),ELBOWS)
        actor=b.plain(e.read_film(output/'template.dat'))['replays'][0]
        self.assertNotEqual(actor['form']['model'],self.profile_actor['form']['model'])
        scene_actor=deepcopy(e.read_film(self.profile/'template.dat')['replays'][0])
        e.attach_profile(scene_actor,actor,meta)
        self.assertEqual(b.plain(scene_actor)['form']['model'],actor['form']['model'])
        self.assertEqual(json.loads((output/'poses.json').read_text()),self.poses)
        self.assertEqual(e.digest(output/'assets'/actor['form']['texture'][7:]),
                         e.digest(self.profile/'assets'/self.profile_actor['form']['texture'][7:]))
        with self.assertRaisesRegex(e.StudioError,'already has elbow'):
            e.create_elbow_profile(output,data_root=self.data)

    def test_authored_forearm_animation_is_protected(self):
        analysis={'metadata':{'duration':2.},'mouthCues':[{'start':0.,'end':2.,'value':'C'}]}
        scene=deepcopy(self.film)
        scene['replays'][0]['animation']['pose/left_forearm']={'type':'pose_transform','keyframes':[]}
        with self.assertRaisesRegex(e.StudioError,'authored animation'):
            e.make_film(scene,0,self.meta,self.profile_actor,e.pose_keys(analysis,self.poses),analysis,
                        'assets:test.wav',gesture_style='expressive',arm_bones=MAPPING,elbow_bones=ELBOWS)
