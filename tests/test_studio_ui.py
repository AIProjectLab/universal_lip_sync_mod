"""Workflow regressions behind the guided desktop UI; all files stay in a temporary instance."""
from copy import deepcopy
import json
from pathlib import Path
import tkinter as tk
import time
import unittest
from unittest.mock import patch
import wave

import bbs_storage as b
from lip_sync_studio import app as ui, engine as e
import test_lip_sync_studio as fixtures


class UIWorkflowTests(unittest.TestCase):
    def setUp(self):
        fixtures.StudioTests.setUp(self)
        self.films=self.instance/'saves/world/bbs/films';self.films.mkdir(parents=True)
        self.scene_file=self.films/'scene.dat';b.save(self.scene_file,self.film)
        self.audio=self.root/'line.wav'
        with wave.open(str(self.audio),'wb') as stream:
            stream.setparams((1,2,16000,0,'NONE','not compressed'));stream.writeframes(b'\0\0'*16000)
        listing=e.profile_list
        self.patches=[patch.object(ui,'KNOWN_BBS_INSTANCES',[self.instance]),
                      patch.object(e,'DATA',self.data),
                      patch.object(e,'profile_list',lambda:listing(data_root=self.data))]
        for p in self.patches:p.start()
        self.tk=tk.Tk();self.tk.withdraw();self.app=ui.App(self.tk);self.tk.update_idletasks()

    def tearDown(self):
        self.app.dispose()
        for p in reversed(self.patches):p.stop()
        fixtures.StudioTests.tearDown(self)

    def ready(self):
        self.app.audio.set(str(self.audio));self.tk.update_idletasks()
        self.assertEqual(self.app.readiness_error(),'')

    def test_quick_test_ignores_unused_scene_and_existing_scene_requires_matching_skin(self):
        self.ready()
        self.app.scene.set('missing');self.app.actor.set('missing')
        self.assertEqual(self.app.readiness_error(),'')
        self.app.use_scene.set(True);self.app.toggle_scene();self.tk.update_idletasks()
        self.assertIn('Choose a saved scene',self.app.readiness_error())
        self.app.scene.set('scene');self.app.refresh_actors()
        self.assertEqual(self.app.readiness_error(),'')
        self.app.scene_actors[0]['form']['texture']='assets:another.png'
        self.assertIn('different model or skin',self.app.readiness_error())
        self.app.use_scene.set(False);self.app.toggle_scene()
        self.assertEqual(self.app.readiness_error(),'')
        self.assertEqual(self.app.scene_fields.winfo_manager(),'')

    def test_missing_audio_and_bad_start_time_explain_why_create_is_disabled(self):
        self.assertIn('recording',self.app.review.get())
        self.assertTrue(self.app.generate.instate(['disabled']))
        self.ready()
        for invalid in ('nan','inf','-1','word'):
            self.app.start.set(invalid);self.app.refresh_review()
            self.assertIn('start time',self.app.review.get())
            self.assertTrue(self.app.generate.instate(['disabled']))
        self.app.start.set('2.5');self.app.refresh_review()
        self.assertFalse(self.app.generate.instate(['disabled']))
        self.app.install.set(False);self.app.refresh_review()
        self.assertIn('not installed',self.app.review.get())

    def test_new_and_fitted_characters_have_distinct_next_actions(self):
        self.app.load_character_source(self.source)
        self.assertEqual(self.app.fit_mode.get(),'fitted')
        self.assertTrue(self.app.prepare_button.instate(['disabled']))
        self.assertFalse(self.app.import_button.instate(['disabled']))
        unfitted=deepcopy(self.film);unfitted['replays'][0]['form']['parts']=[]
        source=self.root/'new.dat';b.save(source,unfitted)
        self.app.load_character_source(source)
        self.assertEqual(self.app.fit_mode.get(),'new')
        self.assertFalse(self.app.prepare_button.instate(['disabled']))
        self.assertTrue(self.app.import_button.instate(['disabled']))
        self.assertFalse(self.app.rig_section.opened)
        with patch.object(e.Assets,'bones',return_value=['skull']):self.app.load_bones()
        self.assertEqual(self.app.head_bone.get(),'')
        self.assertTrue(self.app.rig_section.opened)
        self.assertTrue(self.app.prepare_button.instate(['disabled']))

    def test_fitting_completion_selects_the_installed_fit_file_for_the_next_step(self):
        package=e.make_fitting_package(self.profile,'world',data_root=self.data)
        film=e.deploy(package)
        self.app.finish_result('fit',package,film)
        self.assertEqual(Path(self.app.source_film.get()),film)
        self.assertEqual(self.app.fit_mode.get(),'fitted')
        self.assertIn('save in BBS',self.app.fit_instructions.get())
        self.assertEqual(self.app.character_name.get(),'Alice')
        self.assertFalse(self.app.import_button.instate(['disabled']))

    def test_next_line_uses_output_scene_and_end_time_and_clears_the_old_recording(self):
        package=self.root/'output';package.mkdir()
        e.json_write(package/'manifest.json',{'instance':str(self.instance),'world':'world',
                    'name':'scene','actor_index':0,'start_tick':20,'ticks':40})
        self.app.last_package=package;self.app.last_film=self.scene_file;self.app.last_category='job'
        self.app.audio.set(str(self.audio));self.app.transcript.insert('1.0','Old line')
        self.app.use_last_scene()
        self.assertTrue(self.app.use_scene.get());self.assertEqual(self.app.scene.get(),'scene')
        self.assertEqual(self.app.start.get(),'3.0')
        self.assertEqual(self.app.audio.get(),'');self.assertEqual(self.app.transcript.get('1.0','end').strip(),'')

    def test_options_are_forwarded_to_the_same_engine_and_controls_lock_during_work(self):
        self.ready();self.app.transcript.insert('1.0','Spoken words')
        self.app.language.set('Other language');self.app.start.set('1.5');self.app.install.set(False)
        captured=[]
        def operation_sync(operation):
            operation(lambda text:None)
        with patch.object(self.app,'start_work',operation_sync),patch.object(e,'build_job',side_effect=lambda job,*args:captured.append(job) or self.root):
            self.app.generate_job()
        job=captured[0]
        self.assertEqual(job.recognizer,'phonetic');self.assertEqual(job.transcript,'Spoken words')
        self.assertEqual(job.start_seconds,1.5);self.assertFalse(job.blinks);self.assertEqual(job.gestures,'off')
        self.app.busy=True;self.app.lock_inputs();self.app.cancel_button.configure(state='normal')
        self.assertTrue(self.app.instance_box.instate(['disabled']))
        self.assertTrue(self.app.profile_box.instate(['disabled']))
        self.assertFalse(self.app.cancel_button.instate(['disabled']))
        self.app.busy=False;self.app.unlock_inputs();self.app.refresh_review()
        self.assertTrue(self.app.instance_box.instate(['readonly']))

    def test_legacy_profile_labels_migrate_to_friendly_names(self):
        self.app.profile.set('Alice  ['+self.meta['id'][-4:]+']');self.app.refresh_profiles()
        self.assertEqual(self.app.profile.get(),'Alice')
        self.app.save_state()
        stored=json.loads((self.data/'preferences.json').read_text())
        self.assertEqual(stored['profile_id'],self.meta['id'])

    def test_create_action_stays_visible_at_minimum_window_size_in_both_modes(self):
        self.ready();self.tk.geometry('1000x760');self.tk.deiconify();self.tk.update()
        for scene_mode in (False,True):
            self.app.use_scene.set(scene_mode);self.app.toggle_scene();self.tk.update()
            action_bottom=self.app.generate.winfo_rooty()+self.app.generate.winfo_height()
            page_bottom=self.app.build_page.winfo_rooty()+self.app.build_page.winfo_height()
            self.assertLessEqual(action_bottom,page_bottom)
            self.assertGreater(self.app.generate.winfo_height(),30)

    def test_real_package_job_completes_through_the_background_ui_without_installing(self):
        self.ready();self.app.install.set(False)
        build=e.build_job
        def temporary_build(job,*args):
            job.data_root=self.data
            return build(job,*args)
        before=e.digest(self.scene_file)
        with patch.object(e,'build_job',temporary_build),patch.object(self.app,'error') as error:
            self.app.generate_job()
            self.assertTrue(self.app.busy)
            deadline=time.monotonic()+30
            while self.app.busy and time.monotonic()<deadline:
                self.tk.update();time.sleep(.025)
            self.assertFalse(self.app.busy,'The background job did not finish')
            error.assert_not_called()
        self.assertEqual(self.app.last_category,'job')
        self.assertTrue((self.app.last_package/'manifest.json').is_file())
        self.assertIsNone(self.app.last_film)
        self.assertIn('Not installed',self.app.result.get())
        self.assertFalse(self.app.open_button.instate(['disabled']))
        self.assertTrue(self.app.next_line_button.instate(['disabled']))
        self.assertEqual(e.digest(self.scene_file),before)
        self.assertEqual(list(self.films.glob('*.dat')),[self.scene_file])

    def test_already_fitted_body_without_a_cubic_bone_list_can_still_be_imported(self):
        model=self.instance/'config/bbs/assets/models/test/mesh';model.mkdir(parents=True)
        (model/'mesh.obj').write_text('o body\nv 0 0 0\n')
        e.json_write(model/'config.json',{'texture':'assets:models/test/body/skin.png'})
        film=deepcopy(self.film);film['replays'][0]['form']['model']='test/mesh'
        path=self.root/'mesh_fitted.dat';b.save(path,film)
        self.app.load_character_source(path)
        self.assertTrue(self.app._source_valid)
        self.assertEqual(self.app.fit_mode.get(),'fitted')
        self.assertFalse(self.app.import_button.instate(['disabled']))
        self.assertTrue(self.app.prepare_button.instate(['disabled']))
        captured=[]
        with patch.object(self.app,'start_work',lambda operation:captured.append(operation(lambda text:None))):
            self.app.import_character()
        self.assertTrue(e.validate_profile(captured[0][1])['fitted'])


if __name__=='__main__':unittest.main()
