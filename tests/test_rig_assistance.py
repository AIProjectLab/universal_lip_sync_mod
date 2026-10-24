"""The Codex round trip is local, scoped and cannot replace unrelated film data."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import parse_qs, urlparse
import unittest

import bbs_storage as b
from lip_sync_studio import engine as e, rig_assistance as r
import test_lip_sync_studio as fixtures


class RigAssistanceTests(unittest.TestCase):
    def setUp(self):
        fixtures.StudioTests.setUp(self)
        other=deepcopy(self.film['replays'][0]);other['label']='Other actor'
        self.film['replays'].value.append(other)
        b.save(self.source,self.film)
        self.source_hash=e.digest(self.source)
        self.jar_hash=e.digest(self.instance/'mods/bbs-test.jar')
        self.bundle=self.root/'rig help with spaces'

    def tearDown(self):fixtures.StudioTests.tearDown(self)

    def prepare(self):
        return r.prepare_bundle(self.instance,self.source,0,self.bundle,reason='Fit custom face')

    def returned(self):
        self.prepare()
        return r.finalize_result(self.bundle)

    def test_bundle_is_self_contained_and_documented_link_prefills_without_running(self):
        out=self.prepare()
        url=urlparse(out['codex_url']);query=parse_qs(url.query)
        self.assertEqual((url.scheme,url.netloc),('codex','new'))
        self.assertEqual(Path(query['path'][0]),self.bundle)
        self.assertIn('TASK.md',query['prompt'][0])
        self.assertTrue((self.bundle/'sandbox-instance/config/bbs/assets/models/test/body/body.bbs.json').is_file())
        self.assertTrue((self.bundle/'runtime/lip_sync_studio/templates/mouth/poses.json').is_file())
        self.assertTrue((self.bundle/'runtime/bbs_storage.py').is_file())
        self.assertEqual(e.digest(self.source),self.source_hash)
        self.assertEqual(e.digest(self.instance/'mods/bbs-test.jar'),self.jar_hash)
        self.assertFalse((self.bundle/'result.json').exists())
        self.assertTrue(out['requires_visual_review'])
        with self.assertRaisesRegex(e.StudioError,'already exists'):self.prepare()

    def test_fitted_return_preserves_scene_and_namespaces_assets(self):
        out=self.returned()
        self.assertEqual(out['status'],'validated')
        self.assertTrue(out['requires_visual_review'])
        fitted=b.plain(e.read_film(out['result_film']))
        original=b.plain(self.film)
        self.assertEqual(fitted['camera'],original['camera'])
        self.assertEqual(fitted['replays'][1],original['replays'][1])
        for key,value in original['replays'][0].items():
            if key!='form':self.assertEqual(fitted['replays'][0][key],value)
        self.assertTrue(fitted['replays'][0]['form']['model'].startswith('lip_sync_studio/'))
        self.assertEqual(e.digest(self.source),self.source_hash)
        self.assertEqual(e.digest(self.instance/'mods/bbs-test.jar'),self.jar_hash)

    def test_missing_not_ready_and_wrong_actor_returns_are_rejected(self):
        self.prepare()
        with self.assertRaisesRegex(e.StudioError,'not returned'):r.validate_result(self.bundle)
        e.json_write(self.bundle/'result.json',{'schema':1,'status':'needs_input','message':'Need a front view'})
        with self.assertRaisesRegex(e.StudioError,'front view'):r.validate_result(self.bundle)
        r.finalize_result(self.bundle)
        data=json.loads((self.bundle/'result.json').read_text())
        data['source_actor_fingerprint']='wrong';e.json_write(self.bundle/'result.json',data)
        with self.assertRaisesRegex(e.StudioError,'different character'):r.validate_result(self.bundle)

    def test_result_paths_cannot_escape_the_bundle(self):
        self.returned()
        data=json.loads((self.bundle/'result.json').read_text())
        for invalid in (str(self.source),'../source.dat'):
            data['fitted_snapshot']=invalid;e.json_write(self.bundle/'result.json',data)
            with self.assertRaisesRegex(e.StudioError,'outside'):r.validate_result(self.bundle)

    def test_changed_input_and_unrelated_actor_edits_are_rejected(self):
        self.prepare()
        working=e.read_film(self.bundle/'working.dat');working['replays'][1]['label']='Changed other actor'
        b.save(self.bundle/'working.dat',working)
        with self.assertRaisesRegex(e.StudioError,'outside the selected'):r.finalize_result(self.bundle)
        b.save(self.bundle/'input.dat',working)
        with self.assertRaisesRegex(e.StudioError,'snapshot changed'):r.validate_result(self.bundle)

    def test_changed_private_assets_are_rejected(self):
        out=self.returned()
        profile=Path(out['profile_directory']);asset=next((profile/'assets').rglob('*.png'))
        asset.write_bytes(b'changed')
        with self.assertRaisesRegex(e.StudioError,'hashes'):r.validate_result(self.bundle)

    def test_asset_manifest_cannot_escape_through_parent_segments(self):
        out=self.returned();profile=Path(out['profile_directory'])
        meta=json.loads((profile/'profile.json').read_text())
        meta['asset_hashes']['../../input.dat']=self.source_hash
        e.json_write(profile/'profile.json',meta)
        with self.assertRaises(e.StudioError):r.validate_result(self.bundle)

    def test_return_cannot_change_camera_after_profile_capture(self):
        out=self.returned();path=Path(out['result_film'])
        fitted=e.read_film(path);fitted['camera'][0]['x']=900.
        b.save(path,fitted)
        with self.assertRaisesRegex(e.StudioError,'outside the selected'):r.validate_result(self.bundle)

    def test_missing_sound_pose_is_not_ready_even_with_valid_asset_hashes(self):
        out=self.returned();profile=Path(out['profile_directory'])
        poses=json.loads((profile/'poses.json').read_text());del poses['a']
        e.json_write(profile/'poses.json',poses)
        with self.assertRaisesRegex(e.StudioError,'mouth sound poses'):r.validate_result(self.bundle)

    def test_copied_runtime_can_finalize_in_an_isolated_python_process(self):
        self.prepare()
        command='''from pathlib import Path
import sys
bundle=Path.cwd()
sys.path.insert(0,str(bundle/'runtime'))
from lip_sync_studio.rig_assistance import finalize_result
result=finalize_result(bundle)
assert result['status']=='validated'
assert result['requires_visual_review'] is True
print('isolated return validated')
'''
        completed=subprocess.run([sys.executable,'-I','-c',command],cwd=self.bundle,
                                 capture_output=True,text=True,timeout=30)
        self.assertEqual(completed.returncode,0,completed.stdout+completed.stderr)
        self.assertIn('isolated return validated',completed.stdout)
        self.assertEqual(r.validate_result(self.bundle)['status'],'validated')


if __name__=='__main__':unittest.main()
