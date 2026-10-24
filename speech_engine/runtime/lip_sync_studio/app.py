"""Guided desktop interface for the profile-based BBS dialogue engine."""
from __future__ import annotations
import json
import math
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from . import engine
from rhubarb_to_bbs import KNOWN_BBS_INSTANCES

BG='#13202b'


class Disclosure(ttk.Frame):
    """Keep optional detail out of the main path without removing its controls."""
    def __init__(self,parent,title,*,opened=False):
        super().__init__(parent)
        self.title=title
        self.opened=False
        self.button=ttk.Button(self,style='Link.TButton',command=self.toggle)
        self.button.pack(anchor='w')
        self.body=ttk.Frame(self,padding=(2,4,2,4))
        self.set_open(opened)

    def set_open(self,opened):
        self.opened=bool(opened)
        self.button.configure(text=('▾  ' if self.opened else '▸  ')+self.title)
        if self.opened:self.body.pack(fill='x')
        else:self.body.pack_forget()

    def toggle(self):self.set_open(not self.opened)


class App:
    def __init__(self,root):
        self.root=root
        root.title('Lip Sync Studio — BBS dialogue')
        root.geometry('1140x900');root.minsize(1000,760)
        self.events=queue.Queue();self.cancel=threading.Event();self.busy=False
        self.last_package=None;self.last_film=None;self.last_category=None
        self.instances={p.name:p for p in KNOWN_BBS_INSTANCES if p.is_dir()}
        self.profiles={};self.scene_paths={};self.scene_actors=[];self.source_names=[]
        self.audio=tk.StringVar();self.profile=tk.StringVar();self.instance=tk.StringVar()
        self.world=tk.StringVar();self.scene=tk.StringVar();self.actor=tk.StringVar()
        self.use_scene=tk.BooleanVar(value=False);self.start=tk.StringVar(value='0')
        self.language=tk.StringVar(value='English');self.blinks=tk.BooleanVar(value=True)
        self.gestures=tk.StringVar(value='Expressive');self.install=tk.BooleanVar(value=True)
        self.status=tk.StringVar(value='Follow the numbered cards to create dialogue, or open Characters & fitting to add someone new.')
        self.source_film=tk.StringVar();self.source_actor=tk.StringVar()
        self.character_name=tk.StringVar();self.head_bone=tk.StringVar(value='')
        self.left_arm=tk.StringVar();self.right_arm=tk.StringVar()
        self.profile_info=tk.StringVar();self.result=tk.StringVar()
        self.review=tk.StringVar();self.source_info=tk.StringVar(value='Choose a BBS film containing your character.')
        self.fit_mode=tk.StringVar(value='new');self.fit_instructions=tk.StringVar()
        self.character_ready=tk.StringVar();self.elbow_hint=tk.StringVar()
        self._source_valid=False;self._source_has_mouth=False;self._bone_names=[]
        self._active_profile_id=None;self._profile_allowed=None;self._profile_error=''
        self._gesture_preference='Expressive';self._blink_preference=True
        self._auto_name='';self._pending_fit=None;self._review_pending=False
        self._review_after=None;self._poll_after=None;self._closing=False
        self.scroll_canvases=[];self._locked_states=[]
        self.style()
        outer=ttk.Frame(root,padding=(18,12));outer.pack(fill='both',expand=True)
        header=ttk.Frame(outer);header.pack(fill='x',pady=(0,10))
        ttk.Label(header,text='Lip Sync Studio',font=('Segoe UI',22,'bold')).pack(side='left')
        ttk.Label(header,text='Reusable characters. Voice, face and gestures together.',
                  style='Muted.TLabel').pack(side='left',padx=18)
        # Shared context is visible on every tab; fitting has no hidden dependency.
        location=ttk.LabelFrame(outer,text='Minecraft location — used for dialogue and character fitting',padding=(10,6))
        location.pack(fill='x',pady=(0,10))
        ttk.Label(location,text='Installation').grid(row=0,column=0,padx=(0,8))
        self.instance_box=ttk.Combobox(location,textvariable=self.instance,values=list(self.instances),state='readonly')
        self.instance_box.grid(row=0,column=1,sticky='ew')
        ttk.Button(location,text='Find installation…',command=self.pick_instance).grid(row=0,column=2,padx=8)
        ttk.Label(location,text='World').grid(row=0,column=3,padx=(10,8))
        self.world_box=ttk.Combobox(location,textvariable=self.world,state='readonly',width=22)
        self.world_box.grid(row=0,column=4,sticky='ew')
        location.columnconfigure(1,weight=2);location.columnconfigure(4,weight=1)
        self.instance_box.bind('<<ComboboxSelected>>',lambda e:self.instance_changed())
        self.world_box.bind('<<ComboboxSelected>>',lambda e:self.refresh_scenes())
        self.tabs=ttk.Notebook(outer);self.tabs.pack(fill='both',expand=True)
        self.build_page=ttk.Frame(self.tabs,padding=12)
        self.character_page=ttk.Frame(self.tabs,padding=12)
        guide=ttk.Frame(self.tabs,padding=12)
        self.tabs.add(self.build_page,text='  Make dialogue  ')
        self.tabs.add(self.character_page,text='  Characters & fitting  ')
        self.tabs.add(guide,text='  Easy guide  ')
        self.build_tab(self.build_page);self.character_tab(self.character_page);self.help_tab(guide)
        footer=ttk.Frame(outer);footer.pack(fill='x',pady=(8,4))
        ttk.Label(footer,textvariable=self.status,style='Muted.TLabel',wraplength=890).pack(side='left',fill='x',expand=True)
        self.cancel_button=ttk.Button(footer,text='Cancel current job',command=self.cancel.set,state='disabled')
        self.progress=ttk.Progressbar(outer,mode='indeterminate');self.progress.pack(fill='x')
        self.load_state()
        if not self.instance.get() and self.instances:self.instance.set(next(iter(self.instances)))
        self.refresh_profiles();self.refresh_worlds();self.refresh_character_actions()
        for variable in (self.audio,self.profile,self.instance,self.world,self.scene,self.actor,self.start,
                         self.use_scene,self.install,self.gestures,self.blinks):
            variable.trace_add('write',self.request_review)
        for variable in (self.character_name,self.head_bone,self.left_arm,self.right_arm):
            variable.trace_add('write',lambda *args:self.refresh_character_actions())
        self.refresh_review()
        self._poll_after=self.root.after(100,self.poll)
        self.root.protocol('WM_DELETE_WINDOW',self.close)

    def style(self):
        style=ttk.Style(self.root);style.theme_use('clam')
        self.root.configure(bg=BG)
        style.configure('.',font=('Segoe UI',10),background=BG,foreground='#edf2f7')
        style.configure('TFrame',background=BG)
        style.configure('TLabel',background=BG,foreground='#edf2f7')
        style.configure('Muted.TLabel',foreground='#abbcca')
        style.configure('Ready.TLabel',foreground='#70d6cf')
        style.configure('TButton',padding=(10,7),background='#294559')
        style.map('TButton',background=[('active','#365b73')],foreground=[('disabled','#73818d')])
        style.configure('Link.TButton',padding=(4,5),background=BG,foreground='#70d6cf')
        style.configure('Accent.TButton',background='#167d86',font=('Segoe UI',11,'bold'),padding=(12,10))
        style.configure('TEntry',fieldbackground='#203340',foreground='#edf2f7',insertcolor='white',padding=5)
        style.configure('TCombobox',fieldbackground='#203340',foreground='#edf2f7',padding=5)
        style.map('TCombobox',fieldbackground=[('readonly','#203340'),('disabled','#182a36')],
                  foreground=[('readonly','#edf2f7'),('disabled','#8294a2')])
        for name in ('TCheckbutton','TRadiobutton'):
            style.configure(name,background=BG,foreground='#edf2f7',padding=(0,3))
            style.map(name,background=[('active',BG)],foreground=[('disabled','#8294a2')])
        style.configure('TNotebook',background=BG)
        style.configure('TNotebook.Tab',padding=(12,8),background='#243846')
        style.map('TNotebook.Tab',background=[('selected','#167d86')])
        style.configure('TLabelframe',background=BG,bordercolor='#3c5362')
        style.configure('TLabelframe.Label',background=BG,foreground='#70d6cf',font=('Segoe UI',11,'bold'))

    def field(self,parent,label,variable,row,values=None,button=None):
        ttk.Label(parent,text=label).grid(row=row,column=0,sticky='w',padx=(0,10),pady=5)
        widget=(ttk.Entry(parent,textvariable=variable) if values is None else
                ttk.Combobox(parent,textvariable=variable,values=values,state='readonly'))
        widget.grid(row=row,column=1,sticky='ew',pady=5)
        if button:ttk.Button(parent,text=button[0],command=button[1]).grid(row=row,column=2,padx=(8,0))
        parent.columnconfigure(1,weight=1)
        return widget

    def hint(self,parent,text=None,variable=None,wrap=560):
        label=ttk.Label(parent,text=text,textvariable=variable,style='Muted.TLabel',wraplength=wrap,justify='left')
        label.pack(anchor='w',fill='x',pady=(4,2))
        return label

    def card(self,parent,title):
        card=ttk.LabelFrame(parent,text=title,padding=12)
        card.pack(fill='x',pady=(0,10))
        return card

    def scroll_page(self,host):
        frame=ttk.Frame(host);frame.pack(fill='both',expand=True)
        canvas=tk.Canvas(frame,bg=BG,highlightthickness=0)
        bar=ttk.Scrollbar(frame,orient='vertical',command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side='right',fill='y');canvas.pack(side='left',fill='both',expand=True)
        body=ttk.Frame(canvas,padding=(0,0,10,0))
        window=canvas.create_window((0,0),window=body,anchor='nw')
        body.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',lambda e:canvas.itemconfigure(window,width=e.width))
        def wheel(event):
            widget=event.widget
            # Keep text boxes and dropdowns' native scrolling.
            if isinstance(widget,(tk.Text,ttk.Combobox)):return
            while widget is not None:
                if widget==canvas:
                    if body.winfo_reqheight()>canvas.winfo_height():
                        canvas.yview_scroll(int(-event.delta/120),'units')
                        return 'break'
                    return
                widget=getattr(widget,'master',None)
        self.root.bind('<MouseWheel>',wheel,add='+')
        self.scroll_canvases.append(canvas)
        return body

    def build_tab(self,host):
        left=ttk.Frame(host);left.pack(side='left',fill='both',expand=True)
        right=ttk.Frame(host,width=290);right.pack(side='right',fill='y',padx=(14,0))
        # Fixed sidebar keeps animation/output choices and the action on screen.
        right.pack_propagate(False)
        page=self.scroll_page(left)
        character=self.card(page,'1  Choose your character')
        self._character_card=character
        self.profile_box=ttk.Combobox(character,textvariable=self.profile,state='readonly')
        self.profile_box.pack(fill='x')
        self.profile_box.bind('<<ComboboxSelected>>',lambda e:self.profile_selected())
        self.hint(character,variable=self.profile_info)
        actions=ttk.Frame(character);actions.pack(fill='x',pady=(6,0))
        ttk.Button(actions,text='+ Add a character',command=self.show_characters).pack(side='left')
        self.elbow_button=ttk.Button(actions,text='Add elbow joints',command=self.add_elbows)
        self.elbow_button.pack(side='left',padx=(8,0))
        self.hint(character,variable=self.elbow_hint)
        recording=self.card(page,'2  Choose a recording')
        self.field(recording,'Audio / video',self.audio,0,button=('Browse…',self.pick_audio))
        self.field(recording,'Speech language',self.language,1,values=['English','Other language'])
        ttk.Label(recording,text='One speaker per file. WAV, MP3, M4A, OGG, FLAC or video.',
                  style='Muted.TLabel',wraplength=550).grid(row=2,column=0,columnspan=3,sticky='w',pady=4)
        self.transcript_section=Disclosure(recording,'Add spoken words (optional)')
        self.transcript_section.grid(row=3,column=0,columnspan=3,sticky='ew')
        self.hint(self.transcript_section.body,'Paste what the recording says to help English accuracy. Text does not create audio.')
        self.transcript=tk.Text(self.transcript_section.body,height=3,wrap='word',bg='#203340',fg='#edf2f7',
                               insertbackground='white',relief='flat',font=('Segoe UI',10))
        self.transcript.pack(fill='x',pady=(4,0))
        destination=self.card(page,'3  Choose how to use it')
        ttk.Radiobutton(destination,text='Quick test film',variable=self.use_scene,value=False,
                        command=self.toggle_scene).pack(anchor='w')
        self.hint(destination,'Use the character’s saved camera and position for a quick preview.')
        ttk.Radiobutton(destination,text='Add dialogue to a saved scene',variable=self.use_scene,value=True,
                        command=self.toggle_scene).pack(anchor='w',pady=(6,0))
        self.scene_fields=ttk.Frame(destination)
        self.scene_box=self.field(self.scene_fields,'Saved scene',self.scene,0,values=[],button=('Refresh',self.refresh_scenes))
        self.scene_box.bind('<<ComboboxSelected>>',lambda e:self.refresh_actors())
        self.actor_box=self.field(self.scene_fields,'Who speaks?',self.actor,1,values=[])
        ttk.Label(self.scene_fields,text='Keeps this scene’s actors, camera and earlier lines. Choose the profile matching this speaker.',
                  style='Muted.TLabel',wraplength=550).grid(row=2,column=0,columnspan=3,sticky='w',pady=4)
        timing=ttk.Frame(destination);timing.pack(fill='x',pady=(8,0))
        ttk.Label(timing,text='Start at (seconds)').pack(side='left',padx=(0,10))
        self.start_entry=ttk.Entry(timing,textvariable=self.start,width=10);self.start_entry.pack(side='left')
        ttk.Label(timing,text='0 = beginning',style='Muted.TLabel').pack(side='left',padx=10)

        options_host=ttk.Frame(right);options_host.pack(fill='both',expand=True)
        options_page=self.scroll_page(options_host)
        performance=self.card(options_page,'Animation')
        ttk.Label(performance,text='Talking gestures').pack(anchor='w')
        self.gesture_box=ttk.Combobox(performance,textvariable=self.gestures,
                                     values=['Off','Subtle','Expressive'],state='readonly')
        self.gesture_box.pack(fill='x',pady=(4,2))
        self.gesture_box.bind('<<ComboboxSelected>>',self.remember_gestures)
        self.hint(performance,'Expressive = larger; Subtle = smaller.\nOff keeps your own arm animation.',wrap=250)
        self.blink_check=ttk.Checkbutton(performance,text='Natural eye blinks',variable=self.blinks,command=self.remember_blinks)
        self.blink_check.pack(anchor='w',pady=(8,0))
        self.blink_hint=tk.StringVar()
        self.hint(performance,variable=self.blink_hint,wrap=250)
        review=self.card(right,'Ready to create')
        review.pack(side='bottom',fill='x',pady=(0,0),before=options_host)
        ttk.Label(review,textvariable=self.review,wraplength=250,justify='left').pack(fill='x')
        ttk.Checkbutton(review,text='Install in selected world',variable=self.install).pack(anchor='w',pady=(10,0))
        self.hint(review,'Uncheck to save a package only.',wrap=250)
        self.generate=ttk.Button(review,text='Create test film',style='Accent.TButton',command=self.generate_job)
        self.generate.pack(fill='x',pady=(12,4))
        self.result_card=self.card(page,'Your result')
        ttk.Label(self.result_card,textvariable=self.result,wraplength=600,style='Ready.TLabel',justify='left').pack(fill='x')
        result_actions=ttk.Frame(self.result_card);result_actions.pack(fill='x',pady=(8,0))
        self.open_button=ttk.Button(result_actions,text='Open output folder',command=self.open_output,state='disabled')
        self.open_button.pack(side='left')
        self.next_line_button=ttk.Button(result_actions,text='Add another line to this film',command=self.use_last_scene,state='disabled')
        self.next_line_button.pack(side='left',padx=(8,0))
        self.result_card.pack_forget()
        self.toggle_scene()

    def character_tab(self,host):
        page=self.scroll_page(host)
        ttk.Label(page,text='Set up a character once. Reuse it for every recording.',
                  font=('Segoe UI',15,'bold')).pack(anchor='w',pady=(0,5))
        self.hint(page,'First place your character in a BBS film and save it. This app saves its appearance and face setup as a reusable profile.',
                  wrap=950)
        source=self.card(page,'1  Choose the character from a saved BBS film')
        self.field(source,'Saved BBS film',self.source_film,0,button=('Browse…',self.pick_source_film))
        self.source_actor_box=self.field(source,'Character in film',self.source_actor,1,values=[])
        self.source_actor_box.bind('<<ComboboxSelected>>',lambda e:self.load_bones())
        self.field(source,'Save character as',self.character_name,2)
        ttk.Label(source,textvariable=self.source_info,style='Muted.TLabel',wraplength=900).grid(
            row=3,column=0,columnspan=3,sticky='w',pady=(4,0))
        fitting=self.card(page,'2  Fit the face, then save the character')
        self.new_fit_radio=ttk.Radiobutton(fitting,text='This character needs a mouth fitted',variable=self.fit_mode,
                                         value='new',command=self.refresh_character_actions)
        self.new_fit_radio.pack(anchor='w')
        self.fitted_radio=ttk.Radiobutton(fitting,text='The mouth is already fitted in the selected film',variable=self.fit_mode,
                                        value='fitted',command=self.refresh_character_actions)
        self.fitted_radio.pack(anchor='w')
        self.hint(fitting,variable=self.fit_instructions,wrap=900)
        self.prepare_button=ttk.Button(fitting,text='Create fitting film',style='Accent.TButton',command=self.prepare_character)
        self.prepare_button.pack(anchor='w',pady=(10,4))
        self.import_button=ttk.Button(fitting,text='Save fitted character',style='Accent.TButton',command=self.import_character)
        self.import_button.pack(anchor='w',pady=(10,4))
        self.rig_section=Disclosure(page,'Advanced rig controls — head and shoulder bones')
        self.rig_section.pack(fill='x',pady=(0,10))
        ttk.Label(self.rig_section.body,text='Standard player bones are selected automatically. Change these only for a different rig.',
                  style='Muted.TLabel',wraplength=900).grid(row=0,column=0,columnspan=3,sticky='w',pady=4)
        self.bone_box=self.field(self.rig_section.body,'Head attachment',self.head_bone,1,values=[])
        self.left_box=self.field(self.rig_section.body,'Left shoulder',self.left_arm,2,values=[])
        self.right_box=self.field(self.rig_section.body,'Right shoulder',self.right_arm,3,values=[])
        ttk.Label(self.rig_section.body,text='Choose both shoulders or leave both blank for automatic detection. Custom joint axes need a suitable adapter.',
                  style='Muted.TLabel',wraplength=900).grid(row=4,column=0,columnspan=3,sticky='w',pady=4)
        next_steps=self.card(page,'3  Use your saved character')
        ttk.Label(next_steps,textvariable=self.character_ready,wraplength=900,style='Ready.TLabel').pack(anchor='w')
        ttk.Button(next_steps,text='Go to Make dialogue →',command=lambda:self.tabs.select(self.build_page)).pack(anchor='w',pady=(8,0))
        self.hint(next_steps,'Elbows: add them from Make dialogue after saving a compatible player profile. Blinks need eyelids fitted to this character; mouth fitting alone does not add them.',
                  wrap=900)

    def help_tab(self,host):
        page=self.scroll_page(host)
        ttk.Label(page,text='Start with one short recording',font=('Segoe UI',16,'bold')).pack(anchor='w')
        for title,text in (
            ('A character you already saved',
             'Make dialogue → choose character → choose recording → choose Quick test film or a saved scene → Create film. '
             'The Animation and Output controls stay on the right. Your installation and world stay visible above all tabs.'),
            ('A new custom character',
             'Put the model and skin in a BBS film and save. Characters & fitting → choose that film and actor → name the character. '
             'If it needs a mouth, create a fitting film. In Minecraft open the fit_ film, place and resize the mouth, check the shape cycle and save. '
             'The app selects that fitting file for you. Return and click Save fitted character. If BBS saved it under a different name, browse to that file first.'),
            ('Your own scene or a conversation',
             'Select Add dialogue to a saved scene, choose the scene and speaker, and set Start at in seconds. A new copy keeps its camera, actors and earlier lines. '
             'Use a separate recording for each speaker/line. After creating a film, Add another line to this film selects it as your next source and starts after the previous line. '
             'Same-speaker lines cannot overlap. To replace a line, begin with the scene version before that line was added.'),
            ('How audio drives animation',
             'Rhubarb marks speech sounds for mouth poses. Audio level and speech pauses guide optional arm gestures: preparation, accent, hold and recovery. '
             'Elbow profiles coordinate shoulder and forearm bends. The movements follow speech rhythm; pointing, picking up objects and other story actions need directed animation. '
             'The optional transcript helps recognition; it does not create a voice. Changing the recording requires generating the animation again.'),
            ('Rig support',
             'BBS 2.3.x films and local model/texture assets. The mouth fitting wizard needs a BBS cubic model with a head bone. Each new face/skin needs its own fit. '
             'Blinks need matching eyelids. Automatic elbows need straight standard player arms with the usual pivots. Custom rig axes need an adapter. '
             'Set gestures Off for authored arm animation or IK/physics controls. Check armour and held props when using elbow bends.'),
            ('Files, installation and review',
             'FFmpeg converts supported audio/video to WAV. Profiles snapshot their model/skin files. Each run saves a unique package under studio_data/jobs. '
             'Install in selected world copies assets into the chosen instance and the film into that world. Uncheck it for a package only. '
             'Review the generated film in Minecraft for face placement, elbow creases, lighting and collisions.'),
        ):
            card=self.card(page,title)
            ttk.Label(card,text=text,wraplength=920,justify='left').pack(fill='x')
        ttk.Button(page,text='Open the full step-by-step guide',command=self.open_guide).pack(anchor='w',pady=8)

    def open_guide(self):os.startfile(str(engine.ROOT/'LIP_SYNC_STUDIO_GUIDE.md'))
    def show_characters(self):self.tabs.select(self.character_page)

    def error(self,error):
        self.status.set(str(error));messagebox.showerror('Lip Sync Studio',str(error),parent=self.root)

    def pick_audio(self):
        path=filedialog.askopenfilename(title='Choose one speaker’s recording',
            filetypes=[('Audio / video','*.wav *.mp3 *.m4a *.ogg *.flac *.aac *.mp4 *.mov *.webm'),('All files','*.*')])
        if path:self.audio.set(path)

    def selected_instance(self):
        if self.instance.get() not in self.instances:raise engine.StudioError('Choose a Minecraft installation above the tabs.')
        return self.instances[self.instance.get()]

    def pick_instance(self):
        path=filedialog.askdirectory(title='Choose the Minecraft instance folder (contains mods and saves)')
        if not path:return
        try:
            engine.Assets(path)
            name=Path(path).name
            if name in self.instances and self.instances[name]!=Path(path):name=path
            self.instances[name]=Path(path);self.instance_box['values']=list(self.instances)
            self.instance.set(name);self.instance_changed()
        except Exception as e:self.error(e)

    def instance_changed(self):
        self.refresh_worlds()
        if self.source_film.get():self.load_bones()

    def refresh_worlds(self):
        try:
            worlds=sorted(p.name for p in (self.selected_instance()/'saves').iterdir()
                          if p.is_dir() and (p/'level.dat').is_file())
        except (OSError,engine.StudioError):worlds=[]
        self.world_box['values']=worlds
        if self.world.get() not in worlds:self.world.set(worlds[0] if worlds else '')
        self.refresh_scenes()

    def refresh_scenes(self):
        try:folder=self.selected_instance()/'saves'/self.world.get()/'bbs/films'
        except engine.StudioError:
            self.scene_paths={};self.scene_box['values']=[];self.scene.set('');self.refresh_actors();return
        self.scene_paths={p.stem:p for p in sorted(folder.glob('*.dat'))}
        self.scene_box['values']=list(self.scene_paths)
        if self.scene.get() not in self.scene_paths:self.scene.set(next(iter(self.scene_paths),''))
        self.refresh_actors()

    def refresh_actors(self):
        previous=self.actor.get()
        try:self.scene_actors=engine.bbs.plain(engine.read_film(self.scene_paths[self.scene.get()]))['replays']
        except Exception:self.scene_actors=[]
        labels=[f'{i+1}. {a.get("label") or "Character "+str(i+1)}' for i,a in enumerate(self.scene_actors)]
        self.actor_box['values']=labels
        self.actor.set(previous if previous in labels else (labels[0] if labels else ''))
        self.request_review()

    def toggle_scene(self):
        if self.use_scene.get():
            self.scene_fields.pack(fill='x',before=self.start_entry.master,pady=(6,0))
        else:self.scene_fields.pack_forget()
        self.request_review()

    def refresh_profiles(self):
        listed=[(path,meta) for path,meta in engine.profile_list() if meta.get('fitted')]
        counts={}
        for path,meta in listed:counts[meta['name']]=counts.get(meta['name'],0)+1
        self.profiles={(meta['name'] if counts[meta['name']]==1 else meta['name']+' ['+meta['id'][-4:]+']'):(path,meta)
                       for path,meta in listed}
        self.profile_box['values']=list(self.profiles)
        if self.profile.get() not in self.profiles:
            previous=getattr(self,'_preferred_profile_id',None) or self._active_profile_id
            label=next((label for label,(_,meta) in self.profiles.items() if meta['id']==previous),None)
            if not label:
                old=self.profile.get()
                label=next((label for label,(_,meta) in self.profiles.items()
                            if old==meta['name'] or old.endswith('['+meta['id'][-4:]+']')),None)
            self.profile.set(label or next(iter(self.profiles),''))
        self.profile_selected()

    def remember_gestures(self,event=None):self._gesture_preference=self.gestures.get()
    def remember_blinks(self):self._blink_preference=self.blinks.get()

    def profile_selected(self):
        selected=self.profiles.get(self.profile.get())
        self._profile_error='';self._profile_allowed=None
        if not selected:
            self.profile_info.set('No saved characters yet. Click + Add a character to get started.')
            self.elbow_hint.set('Save a fitted character before adding elbows.')
            self.blink_hint.set('Select a character to see its eye support.')
            self.elbow_button.configure(state='disabled');self.gesture_box.configure(state='disabled')
            self.blink_check.configure(state='disabled')
            self.request_review();return
        path,meta=selected
        try:
            eyes='eyes' in meta['bindings'];arms=bool(engine.gesture_mapping(path,meta))
            elbows=bool(engine.elbow_mapping(path,meta))
            saved=engine.bbs.plain(engine.read_film(path/'template.dat'))['replays'][meta['source_actor_index']]['form']
            self._profile_allowed=(
                [meta['source_model'],saved.get('model'),*meta.get('compatible_models',[])],
                [meta.get('source_skin'),saved.get('texture'),*meta.get('compatible_skins',[])])
        except Exception as error:
            self._profile_error=f'Character files need attention: {error}'
            self.profile_info.set(self._profile_error)
            self.elbow_button.configure(state='disabled');self.gesture_box.configure(state='disabled')
            self.blink_check.configure(state='disabled');self.request_review();return
        self._active_profile_id=meta['id']
        self.profile_info.set('Mouth ready  •  '+('Blinks ready' if eyes else 'No eyelid rig')+'  •  '+
                              ('Arms + elbow bends ready' if elbows else ('Talking arms ready' if arms else 'Arms need mapping')))
        self.elbow_button.configure(state='normal' if arms and not elbows and not self.busy else 'disabled')
        self.elbow_hint.set('Elbow joints are already included.' if elbows else
                            ('Optional: add elbow bends to compatible player arms.' if arms else
                             'Shoulder bones must be mapped before adding arm gestures.'))
        self.gesture_box.configure(state='readonly' if arms and not self.busy else 'disabled')
        self.gestures.set(self._gesture_preference if arms else 'Off')
        self.blink_check.configure(state='normal' if eyes and not self.busy else 'disabled')
        self.blinks.set(self._blink_preference if eyes else False)
        self.blink_hint.set('Uses this character’s fitted eyelids.' if eyes else 'Unavailable: this character has no fitted eyelids.')
        self.request_review()

    def request_review(self,*args):
        if self._closing or self._review_pending:return
        self._review_pending=True
        self._review_after=self.root.after_idle(self.refresh_review)

    def readiness_error(self):
        if self.profile.get() not in self.profiles:return 'Choose a saved character, or click + Add a character.'
        if self._profile_error:return self._profile_error
        if not self.audio.get() or not Path(self.audio.get()).is_file():return 'Choose an audio or video recording.'
        if self.instance.get() not in self.instances:return 'Choose a Minecraft installation above the tabs.'
        if not self.world.get() or self.world.get() not in self.world_box['values']:return 'Choose a world above the tabs.'
        try:
            start=float(self.start.get())
            if not math.isfinite(start) or start<0:raise ValueError()
        except ValueError:return 'Enter a start time of 0 or more seconds.'
        if self.use_scene.get():
            if self.scene.get() not in self.scene_paths:return 'Choose a saved scene, or switch to Quick test film.'
            try:
                actor=self.scene_actors[int(self.actor.get().split('.',1)[0])-1]
                form=actor['form']
                if not self._profile_allowed:return 'Select a valid saved character.'
                models,skins=self._profile_allowed
                if form.get('model') not in models or form.get('texture') not in skins:
                    return 'This speaker has a different model or skin. Choose their matching character profile.'
            except (ValueError,IndexError,KeyError):return 'Choose who speaks in the saved scene.'
        return ''

    def refresh_review(self):
        if self._closing:return
        if self._review_after is not None:
            self.root.after_cancel(self._review_after);self._review_after=None
        self._review_pending=False
        problem=self.readiness_error()
        if self.busy:
            self.review.set('Working…\n'+('You can cancel the current job below.'))
            self.generate.configure(state='disabled')
        else:
            self.generate.configure(state='disabled' if problem else 'normal',
                                    text='Add dialogue to scene' if self.use_scene.get() else 'Create test film')
            if problem:self.review.set(problem)
            else:
                short=lambda text:text if len(text)<31 else text[:28]+'…'
                target=('Scene: '+short(self.scene.get())+'\nSpeaker: '+short(self.actor.get())) if self.use_scene.get() else 'New quick test film'
                output='Install in '+self.world.get() if self.install.get() else 'Save package only — not installed'
                self.review.set(target+'\nStarts at '+self.start.get()+' seconds\n'+output)
        self.refresh_character_actions()

    def refresh_character_actions(self):
        if not hasattr(self,'prepare_button'):return
        new=self.fit_mode.get()=='new'
        self.prepare_button.pack_forget();self.import_button.pack_forget()
        if new:self.prepare_button.pack(anchor='w',pady=(10,4))
        else:self.import_button.pack(anchor='w',pady=(10,4))
        name_ok=bool(self.character_name.get().strip())
        pair_ok=bool(self.left_arm.get())==bool(self.right_arm.get())
        head_ok=self.head_bone.get() in self._bone_names
        common=self._source_valid and name_ok and pair_ok and not self.busy
        self.prepare_button.configure(state='normal' if common and head_ok and not self._source_has_mouth else 'disabled')
        self.import_button.configure(state='normal' if common and self._source_has_mouth else 'disabled')
        if not self._source_valid:
            instructions='Choose a saved BBS film above. The app will detect the head and shoulder controls.'
        elif not pair_ok:
            instructions='In Advanced rig controls, choose both shoulders or leave both blank.'
        elif new:
            instructions=('This actor already has a mouth. Choose “The mouth is already fitted” to save its profile.'
                          if self._source_has_mouth else
                          ('Create a separate fit_ film, open it in BBS, place the mouth on the face and save. Then return here to save the fitted character.'
                           if head_ok else 'No head bone was detected. Open Advanced rig controls and select the real head attachment.'))
        elif not self._source_has_mouth:
            instructions='No dialogue mouth was found on this actor. Choose “This character needs a mouth fitted” first.'
        elif self._pending_fit and Path(self.source_film.get())==self._pending_fit:
            instructions=('Fitting film selected: '+self._pending_fit.stem+
                          '\nOpen it in BBS → position the mouth → check opening and closure → save in BBS → click Save fitted character here.'
                          '\nIf you used Save As in BBS, browse to that saved file first.')
        else:
            instructions='Check the mouth’s placement and sound shapes in BBS, save the film, then click Save fitted character. The app reads the saved file again.'
        self.fit_instructions.set(instructions)
        if not self.character_ready.get():
            self.character_ready.set('After saving, the character appears in Make dialogue. Reuse it with new recordings and matching scene actors.')

    def lock_inputs(self):
        self._locked_states=[]
        def visit(widget):
            if widget!=self.cancel_button and isinstance(widget,(ttk.Button,ttk.Entry,ttk.Combobox,
                                                               ttk.Checkbutton,ttk.Radiobutton,tk.Text)):
                self._locked_states.append((widget,str(widget.cget('state'))))
                widget.configure(state='disabled')
            for child in widget.winfo_children():visit(child)
        visit(self.root)

    def unlock_inputs(self):
        for widget,state in self._locked_states:
            if widget.winfo_exists():widget.configure(state=state)
        self._locked_states=[]

    def start_work(self,operation):
        if self.busy:return
        self.busy=True;self.cancel.clear()
        self.lock_inputs();self.cancel_button.configure(state='normal');self.cancel_button.pack(side='right',padx=(8,0))
        self.progress.start(12);self.refresh_review()
        def worker():
            try:self.events.put(('done',operation(lambda text:self.events.put(('status',text)))))
            except Exception as e:self.events.put(('error',str(e)))
        threading.Thread(target=worker,daemon=True).start()

    def generate_job(self):
        problem=self.readiness_error()
        if problem:self.status.set(problem);self.refresh_review();return
        try:
            profile=self.profiles[self.profile.get()][0]
            scene=self.scene_paths[self.scene.get()] if self.use_scene.get() else None
            actor_index=int(self.actor.get().split('.',1)[0])-1 if scene else 0
            job=engine.Job(audio=Path(self.audio.get()),profile=profile,instance=self.selected_instance(),
                world=self.world.get(),scene=scene,actor_index=actor_index,start_seconds=float(self.start.get()),
                transcript=self.transcript.get('1.0','end').strip(),
                recognizer='pocketSphinx' if self.language.get()=='English' else 'phonetic',
                blinks=self.blinks.get(),gestures=self.gestures.get().lower())
            install=self.install.get();self.save_state()
        except Exception as error:self.error(error);return
        def operation(progress):
            package=engine.build_job(job,progress,self.cancel)
            if self.cancel.is_set():raise engine.Cancelled('Cancelled before installation. Your scenes were not changed.')
            film=engine.deploy(package,progress=progress) if install else None
            return ('job',package,film)
        self.start_work(operation)

    def pick_source_film(self):
        try:initial=self.selected_instance()/'saves'/self.world.get()/'bbs/films'
        except engine.StudioError:initial=engine.ROOT
        path=filedialog.askopenfilename(title='Choose a saved BBS character film',initialdir=initial,filetypes=[('BBS films','*.dat')])
        if path:self.load_character_source(Path(path))

    def load_character_source(self,path):
        try:
            self.source_names=engine.actors(path)
            self.source_film.set(str(path))
            labels=[f'{i+1}. {name}' for i,name in enumerate(self.source_names)]
            self.source_actor_box['values']=labels;self.source_actor.set(labels[0])
            self.load_bones()
        except Exception as error:
            self._source_valid=False;self.source_info.set(str(error));self.refresh_character_actions();self.error(error)

    def load_bones(self):
        self._source_valid=False;self._source_has_mouth=False;self._bone_names=[]
        try:
            index=int(self.source_actor.get().split('.',1)[0])-1
            actor=engine.bbs.plain(engine.read_film(self.source_film.get()))['replays'][index]
            form=actor['form']
            self._source_has_mouth='mouth' in engine.roles(form)
            try:names=engine.Assets(self.selected_instance()).bones(form['model'])
            except engine.StudioError:
                # Existing fitted actors can be snapshotted even when their
                # body format has no cubic bone list for the fitting wizard.
                if not self._source_has_mouth:raise
                names=[]
            self._bone_names=names
            self.bone_box['values']=names;self.left_box['values']=['']+names;self.right_box['values']=['']+names
            self.left_arm.set('left_arm' if 'left_arm' in names else '')
            self.right_arm.set('right_arm' if 'right_arm' in names else '')
            head=next((n for n in names if n.lower()=='head'),None)
            self.head_bone.set(head or '')
            self.fit_mode.set('fitted' if self._source_has_mouth else 'new')
            suggested=actor.get('label') or 'My character'
            if suggested.startswith('FIT FACE - '):suggested=suggested[len('FIT FACE - '):]
            if not self.character_name.get() or self.character_name.get()==self._auto_name:
                self.character_name.set(suggested)
            self._auto_name=suggested
            self._source_valid=True
            self.source_info.set('Model: '+form['model']+'  •  '+
                                 ('Head detected: '+head if head else
                                  ('Using the saved mouth attachment' if self._source_has_mouth else
                                   'Choose a head attachment in Advanced rig controls'))+
                                 '  •  '+('Dialogue mouth found' if self._source_has_mouth else 'Needs mouth fitting'))
            if not head and not self._source_has_mouth:self.rig_section.set_open(True)
        except Exception as error:
            self.bone_box['values']=[];self.left_box['values']=[];self.right_box['values']=[]
            self.head_bone.set('');self.left_arm.set('');self.right_arm.set('')
            self.source_info.set('Could not load this character: '+str(error))
        self.refresh_character_actions()

    def character_input(self):
        if not self._source_valid:raise engine.StudioError('Choose a saved film and a character first.')
        if not self.world.get():raise engine.StudioError('Choose a world above the tabs.')
        if not self.character_name.get().strip():raise engine.StudioError('Give this character a name.')
        return (self.selected_instance(),Path(self.source_film.get()),
                int(self.source_actor.get().split('.',1)[0])-1,self.character_name.get().strip())

    def prepare_character(self):
        try:
            args=self.character_input();bone=self.head_bone.get().strip();world=self.world.get()
            left,right=self.left_arm.get().strip(),self.right_arm.get().strip()
            if bool(left)!=bool(right):raise engine.StudioError('Choose both shoulder bones, or leave both blank.')
            arm_bones={'left':left,'right':right} if left else None
            if bone not in self._bone_names:raise engine.StudioError('Select this character’s head attachment in Advanced rig controls.')
            if self._source_has_mouth:raise engine.StudioError('This actor already has a mouth. Save it as a fitted character instead.')
        except Exception as error:self.error(error);return
        def operation(progress):
            progress('Creating a separate mouth fitting film…')
            profile=engine.capture_profile(*args,fitted=False,attach_head=bone,arm_bones=arm_bones)
            package=engine.make_fitting_package(profile,world)
            film=engine.deploy(package,progress=progress)
            return ('fit',package,film)
        self.start_work(operation)

    def import_character(self):
        try:
            args=self.character_input()
            left,right=self.left_arm.get().strip(),self.right_arm.get().strip()
            if bool(left)!=bool(right):raise engine.StudioError('Choose both shoulder bones, or leave both blank.')
            arm_bones={'left':left,'right':right} if left else None
        except Exception as error:self.error(error);return
        def operation(progress):
            progress('Reading the saved fit and saving the reusable character…')
            profile=engine.capture_profile(*args,arm_bones=arm_bones)
            return ('profile',profile,None)
        self.start_work(operation)

    def add_elbows(self):
        try:profile=self.profiles[self.profile.get()][0]
        except KeyError:self.status.set('Choose a fitted character first.');return
        def operation(progress):
            progress('Creating a separate elbow profile and preserving the skin…')
            converted=engine.create_elbow_profile(profile)
            return ('profile',converted,None)
        self.start_work(operation)

    def finish_result(self,category,package,film):
        self.refresh_profiles()
        if category=='profile':
            label=next((label for label,(path,meta) in self.profiles.items() if path==package),None)
            if label:self.profile.set(label);self.profile_selected()
            self.character_ready.set('Saved and selected: '+self.profile.get()+'. Go to Make dialogue and choose a recording.')
            self.status.set('Character saved and selected. Choose a recording in Make dialogue.')
            self.save_state()
            self.tabs.select(self.build_page)
        else:
            self.last_package=package;self.last_film=film;self.last_category=category
            manifest=json.loads((package/'manifest.json').read_text(encoding='utf-8'))
            self.result.set(('Open in BBS Films:\n'+manifest['name']) if film else
                            ('Package saved:\n'+manifest['name']+'\nNot installed in Minecraft.'))
            self.result_card.pack(fill='x',pady=(0,10),before=self._character_card)
            self.scroll_canvases[0].yview_moveto(0)
            self.open_button.configure(state='normal')
            self.next_line_button.configure(state='normal' if film and category=='job' else 'disabled')
            self.refresh_scenes()
            if category=='fit' and film:
                self._pending_fit=Path(film)
                self.load_character_source(Path(film))
                self.tabs.select(self.character_page)
                self.status.set('Fitting film ready. Open it in BBS, place the mouth and save. Then click Save fitted character here.')
            elif film:self.status.set('Film ready. Open the name shown in Your result in BBS and review it in Minecraft.')
            else:self.status.set('Package saved. Installation is off; use Open output folder to see its files.')
        self.refresh_review()

    def poll(self):
        try:
            while True:
                kind,value=self.events.get_nowait()
                if kind=='status':self.status.set(value)
                else:
                    self.busy=False;self.progress.stop();self.unlock_inputs()
                    self.cancel_button.configure(state='disabled');self.cancel_button.pack_forget()
                    if kind=='error':self.profile_selected();self.error(engine.StudioError(value))
                    else:self.finish_result(*value)
                    self.refresh_review()
        except queue.Empty:pass
        self._poll_after=self.root.after(100,self.poll)

    def use_last_scene(self):
        if not self.last_film or self.last_category!='job':return
        manifest=json.loads((self.last_package/'manifest.json').read_text(encoding='utf-8'))
        instance_path=Path(manifest['instance'])
        label=next((k for k,path in self.instances.items() if path==instance_path),None)
        if not label:
            self.status.set('Choose the output film’s Minecraft installation first.');return
        self.instance.set(label);self.world.set(manifest['world']);self.refresh_worlds()
        if self.last_film.stem not in self.scene_paths:
            self.status.set('The output film is no longer in this world. Refresh the scene list.');return
        self.use_scene.set(True);self.scene.set(self.last_film.stem);self.refresh_actors()
        index=manifest.get('actor_index',0)
        labels=list(self.actor_box['values'])
        if 0<=index<len(labels):self.actor.set(labels[index])
        self.start.set(str(round((manifest.get('start_tick',0)+manifest['ticks'])/20,2)))
        self.audio.set('');self.transcript.delete('1.0','end')
        self.toggle_scene();self.tabs.select(self.build_page)
        self.status.set('Previous film selected. Choose the next recording, speaker and matching character profile.')

    def open_output(self):
        if self.last_package:os.startfile(str(self.last_package))

    def save_state(self):
        selected=self.profiles.get(self.profile.get())
        engine.json_write(engine.DATA/'preferences.json',{'instance':self.instance.get(),'world':self.world.get(),
            'profile':self.profile.get(),'profile_id':selected[1]['id'] if selected else None,
            'gestures':self._gesture_preference,'blinks':self._blink_preference,
            'custom_instances':{k:str(v) for k,v in self.instances.items()}})

    def load_state(self):
        try:
            state=json.loads((engine.DATA/'preferences.json').read_text(encoding='utf-8'))
            for name,path in state.get('custom_instances',{}).items():
                if Path(path).is_dir():self.instances[name]=Path(path)
            self.instance_box['values']=list(self.instances)
            self.instance.set(state.get('instance',''));self.world.set(state.get('world',''))
            self.profile.set(state.get('profile',''));self._preferred_profile_id=state.get('profile_id')
            self._gesture_preference=state.get('gestures','Expressive')
            if self._gesture_preference not in ('Off','Subtle','Expressive'):self._gesture_preference='Expressive'
            self._blink_preference=bool(state.get('blinks',True))
        except (OSError,ValueError):pass

    def close(self):
        if self.busy:
            self.cancel.set();self.status.set('Stopping the job. Close the window after it stops.');return
        self.save_state();self.dispose()

    def dispose(self):
        self._closing=True
        for callback in (self._review_after,self._poll_after):
            if callback is not None:self.root.after_cancel(callback)
        self.root.destroy()


def ensure_default_profile():
    if engine.profile_list():return
    instance=Path(r'C:\Users\shafe\curseforge\minecraft\Instances\BBS 1.21.1')
    film=instance/'saves/test123/bbs/films/voice_depth_test.dat'
    if film.is_file():engine.capture_profile(instance,film,0,'Yellow character — fitted')


def main():
    root=tk.Tk();root.withdraw()
    try:ensure_default_profile()
    except Exception as error:messagebox.showwarning('Character setup',str(error),parent=root)
    App(root);root.deiconify();root.mainloop()


if __name__=='__main__':main()

