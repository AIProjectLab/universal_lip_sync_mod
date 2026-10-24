package dev.lipsync.studio;

import com.google.gson.*;
import java.io.*;
import java.nio.file.*;
import java.util.*;
import java.util.function.Consumer;
import net.minecraft.client.MinecraftClient;
import mchorse.bbs_mod.BBSMod;
import mchorse.bbs_mod.camera.clips.misc.AudioClip;
import mchorse.bbs_mod.film.replays.Replay;
import mchorse.bbs_mod.l10n.keys.IKey;
import mchorse.bbs_mod.resources.Link;
import mchorse.bbs_mod.ui.film.UIFilmPanel;
import mchorse.bbs_mod.ui.film.audio.UIAudioRecorder;
import mchorse.bbs_mod.ui.framework.UIContext;
import mchorse.bbs_mod.ui.framework.elements.UIElement;
import mchorse.bbs_mod.ui.framework.elements.buttons.*;
import mchorse.bbs_mod.ui.framework.elements.input.UITrackpad;
import mchorse.bbs_mod.ui.framework.elements.input.text.UITextbox;
import mchorse.bbs_mod.ui.framework.elements.context.UISearchListContextMenu;
import mchorse.bbs_mod.ui.framework.elements.overlay.*;
import mchorse.bbs_mod.ui.framework.elements.utils.*;
import mchorse.bbs_mod.ui.utils.UIUtils;
import mchorse.bbs_mod.utils.clips.Clip;

/** Streamlined in-editor UI with 1-click workflows and fine-tuning controls. */
final class DialogueOverlay extends UIOverlayPanel {
    final UIFilmPanel panel;
    final Replay actor;
    private final List<UIElement> inputs=new ArrayList<>();
    private final UIText status=new UIText("Checking this character…");
    private final UIText capability=new UIText("");
    private final UITextbox audio=new UITextbox(null);
    private final UITextbox transcript=new UITextbox(null);
    private final UITrackpad start=new UITrackpad(null);
    private final UIButton autoFit,setup,codex,generateApply,generate,apply,advanced,cancel;
    private UIButton checkHelp;
    private final UIToggle blinks=new UIToggle(IKey.constant("Natural eye blinks"),true,null);
    private final UIToggle gestures=new UIToggle(IKey.constant("Talking gestures"),true,null);
    private final UIButton language,style;
    private boolean english=true,expressive=true,more=false,autoApplyOnFinish=false;
    private AudioClip reusedAudio;
    private BridgeJob job,readyJob;
    private JsonObject readyResponse;
    private JsonObject character=new JsonObject();
    @FunctionalInterface
    interface Callback {void accept(JsonObject response) throws Exception;}
    @FunctionalInterface
    interface JobCallback {void accept(BridgeJob job,JsonObject response) throws Exception;}
    private JobCallback completion;
    private String bundleDirectory;
    private boolean inspected;
    private volatile String chosenFile;
    private volatile String pickerError;
    private boolean pickerOpen;
    private UIElement details=new UIElement();

    DialogueOverlay(UIFilmPanel panel,Replay actor) {
        super(IKey.constant("Lip sync — "+actor.getName()));this.panel=panel;this.actor=actor;
        label("1  Audio Source",8,6,560,16);
        place(audio,8,26,414,22);audio.placeholder(IKey.constant("Choose a recording or use an audio clip"));
        button("Browse…",428,26,140,22,this::browseAudio);
        button("🎵 Use Timeline Audio",8,54,180,22,this::useSelectedAudio);
        button("📂 Choose BBS Audio…",194,54,180,22,this::chooseAsset);
        button("🎙 Record Voice…",380,54,188,22,this::record);

        label("2  Character Setup",8,86,560,16);
        place(capability,8,104,560,26);
        autoFit=button("✨ 1-Click Auto-Fit",8,132,175,24,this::quickAutoFit);
        setup=button("⚙ Custom / Adjust…",189,132,180,24,this::openSetup);
        codex=button("🤖 AI Auto-Rig…",375,132,193,24,this::openAiAssist);

        label("3  Animation & Timing",8,166,560,16);
        label("Start (sec)",8,188,75,22);place(start,85,188,85,22);
        start.limit(0,86400).values(.05,.01,1);start.setValue(panel.getCursor()/20.0);
        place(gestures,178,190,192,18);place(blinks,378,190,190,18);

        advanced=button("More options ▾",8,220,170,22,() -> {more=!more;details.setVisible(more);resize();});
        checkHelp=button("Check AI Rig Result",184,220,384,22,this::resumeHelp);
        checkHelp.setEnabled(false);

        place(details,8,248,560,90);details.setVisible(false);
        language=new UIButton(IKey.constant("Language: English"),b -> {
            english=!english;languageLabel();
        });
        language.relative(details).xy(0,0).wh(215,22);details.add(language);inputs.add(language);
        style=new UIButton(IKey.constant("Gestures: Expressive"),b -> {
            expressive=!expressive;b.label=IKey.constant(expressive?"Gestures: Expressive":"Gestures: Subtle");
        });style.relative(details).xy(223,0).wh(337,22);details.add(style);inputs.add(style);
        UILabel script=new UILabel(IKey.constant("Spoken words (optional; helps English phoneme precision)"));
        script.relative(details).xy(0,30).wh(560,16);details.add(script);
        transcript.relative(details).xy(0,50).wh(560,22);transcript.placeholder(IKey.constant("Paste spoken text here"));details.add(transcript);inputs.add(transcript);

        generateApply=button("🚀 Generate & Apply (1-Click)",8,350,300,30,this::generateAndApply);
        generate=button("Generate Only",314,350,130,30,this::generate);
        apply=button("Apply",450,350,118,30,this::applyReady);apply.setEnabled(false);

        cancel=button("Cancel",8,388,90,22,() -> {if(job!=null){job.cancel();status.text("Cancelling…");}});cancel.setEnabled(false);
        button("Open Job Folder",104,388,150,22,() -> {if(readyJob!=null)UIUtils.openFolder(readyJob.directory.toFile());else if(job!=null)UIUtils.openFolder(job.directory.toFile());});
        place(status,8,416,560,46);

        // Auto-use timeline clip if exactly one audio clip is selected
        List<Clip> selectedClips=panel.cameraEditor.clips.getClipsFromSelection();
        if(selectedClips.size()==1&&selectedClips.get(0) instanceof AudioClip) {
            useSelectedAudio();
        }
    }

    @Override public void applyDefaultSize(){this.wh(596,494);}
    private void languageLabel(){language.label=IKey.constant(english?"Language: English":"Language: Other");}
    private void place(UIElement widget,int x,int y,int w,int h){widget.relative(content).xy(x,y).wh(w,h);content.add(widget);inputs.add(widget);}
    private void label(String text,int x,int y,int w,int h){UILabel l=new UILabel(IKey.constant(text));l.relative(content).xy(x,y).wh(w,h);content.add(l);}
    private UIButton button(String text,int x,int y,int w,int h,Runnable action){
        UIButton b=new UIButton(IKey.constant(text),v -> {try{action.run();}catch(Exception e){status.text(e.getMessage()==null?e.toString():e.getMessage());LipSyncAddon.LOG.warning(e.toString());}});
        place(b,x,y,w,h);return b;
    }

    private void quickAutoFit() {
        JsonObject defaults=character.has("setup_defaults")?character.getAsJsonObject("setup_defaults"):new JsonObject();
        String headBone=getString(defaults,"head_bone","head");
        JsonObject settings=new JsonObject();
        settings.addProperty("head_bone",headBone);
        settings.addProperty("visual_confirmed",true);
        settings.addProperty("elbows",flag(defaults,"elbows"));
        settings.addProperty("tx",defaults.has("tx")?defaults.get("tx").getAsDouble():0.0);
        settings.addProperty("ty",defaults.has("ty")?defaults.get("ty").getAsDouble():0.05);
        settings.addProperty("tz",defaults.has("tz")?defaults.get("tz").getAsDouble():0.004);
        settings.addProperty("sx",defaults.has("sx")?defaults.get("sx").getAsDouble():1.0);
        settings.addProperty("sy",defaults.has("sy")?defaults.get("sy").getAsDouble():1.0);
        settings.addProperty("sz",defaults.has("sz")?defaults.get("sz").getAsDouble():1.0);

        status.text("Applying 1-Click Auto-Fit…");
        setup(settings,true,response -> {
            status.text("✨ 1-Click Auto-Fit applied! Choose audio and click 'Generate & Apply'.");
        });
    }

    private void openAiAssist() {
        run("assist_prepare",null,(activeJob,response) -> {
            bundleDirectory=getString(response,"bundle_directory","");
            status.text("🤖 AI Rig task created. Ask Antigravity to fit this model, then click 'Check AI Rig Result'.");
            checkHelp.setEnabled(true);
        });
    }

    private void useSelectedAudio() {
        List<Clip> selected=panel.cameraEditor.clips.getClipsFromSelection();
        if(selected.size()!=1||!(selected.get(0) instanceof AudioClip clip)) {
            status.text("Select one audio clip on the timeline first, or choose a recording file.");return;
        }
        Link link=clip.audio.get();File file=BBSMod.getProvider().getFile(link);
        if(file==null||!file.isFile()){status.text("The selected clip is not a local audio file.");return;}
        reusedAudio=clip;audio.setText(file.getAbsolutePath());start.setValue(clip.tick.get()/20.0);
        status.text("🎵 Using selected timeline audio. Start time automatically matched.");
    }

    private void chooseAsset() {
        UIOverlay.addOverlay(getContext(),new UISoundOverlayPanel(link -> {
            File file=BBSMod.getProvider().getFile(link);
            if(file==null||!file.isFile()){status.text("Choose a local BBS sound asset.");return;}
            reusedAudio=null;audio.setText(file.getAbsolutePath());
        },getContext()));
    }

    private void browseAudio() {
        if(pickerOpen)return;
        pickerOpen=true;
        Thread thread=new Thread(() -> {
            try {
                java.awt.FileDialog dialog=new java.awt.FileDialog((java.awt.Frame)null,"Choose dialogue recording",java.awt.FileDialog.LOAD);
                dialog.setFile("*.wav;*.mp3;*.m4a;*.ogg;*.flac;*.mp4");dialog.setVisible(true);
                if(dialog.getFile()!=null)chosenFile=Path.of(dialog.getDirectory(),dialog.getFile()).toString();
                dialog.dispose();
            }catch(Exception error){pickerError="File picker unavailable. Paste path or use Choose BBS audio.";}
            finally{pickerOpen=false;}
        },"Lip Sync file picker");thread.setDaemon(true);thread.start();
    }

    private void record() {
        int origin=(int)Math.round(start.getValue()*20);
        UIAudioRecorder.start(getContext(),"lip_sync_studio/voice_"+System.currentTimeMillis(),(name,wave) -> {
            File file=new File(BBSMod.getAudioFolder(),name+".wav");
            audio.setText(file.getAbsolutePath());reusedAudio=null;start.setValue(origin/20.0);
            status.text("Voice saved! Click 'Generate & Apply'.");
        });
    }

    private void generateAndApply() {
        autoApplyOnFinish=true;
        generate();
    }

    private void generate() {
        if(!character.has("fitted")||!character.get("fitted").getAsBoolean()) {
            status.text("Click '✨ 1-Click Auto-Fit' or '⚙ Custom' first to set up mouth.");return;
        }
        if(audio.getText().isBlank()||!Files.isRegularFile(Path.of(audio.getText()))) {
            status.text("Choose an audio file or click 'Record Voice' first.");return;
        }
        JsonObject options=new JsonObject();
        options.addProperty("audio",Path.of(audio.getText()).toAbsolutePath().toString());
        options.addProperty("start_tick",Math.round(start.getValue()*20));
        options.addProperty("language",english?"English":"Other language");options.addProperty("transcript",transcript.getText());
        options.addProperty("gestures",gestures.getValue()?(expressive?"expressive":"subtle"):"off");options.addProperty("blinks",blinks.getValue());
        if(reusedAudio!=null) {
            if(!reusedAudio.audio.get().toString().isBlank()&&Math.round(start.getValue()*20)!=reusedAudio.tick.get()) {
                status.text("Reused clip keeps its timeline start time.");return;
            }
            options.addProperty("audio_reuse",true);options.addProperty("audio_offset_ticks",reusedAudio.offset.get());
            options.addProperty("audio_duration_ticks",reusedAudio.duration.get());options.addProperty("audio_link",reusedAudio.audio.get().toString());
        }
        run("generate",options,(activeJob,response) -> {
            readyJob=activeJob;readyResponse=response;apply.setEnabled(true);
            if(autoApplyOnFinish) {
                autoApplyOnFinish=false;
                applyReady();
            } else {
                status.text("Lip sync generated! Click 'Apply' to review, or BBS Undo to revert.");
            }
        });
    }

    private void applyReady() {
        if(readyJob==null||readyResponse==null)return;
        try {
            EditorPatch.apply(panel,readyJob,Path.of(readyResponse.get("result_film").getAsString()),true);
            readyResponse=null;readyJob=null;status.text("Applied! Play film to review; BBS Undo (Ctrl+Z) reverts anytime.");
            close();
        }catch(Exception e){status.text(e.getMessage());apply.setEnabled(false);}
    }

    private void openSetup(){if(job==null)UIOverlay.addOverlayLeft(getContext(),new SetupOverlay(this),330);}
    boolean working(){return job!=null;}
    void setup(JsonObject settings,boolean confirmed,Callback complete) {
        JsonObject options=new JsonObject();settings.addProperty("visual_confirmed",confirmed);options.add("setup",settings);
        run("setup",options,(activeJob,response) -> {
            try {
                EditorPatch.apply(panel,activeJob,Path.of(response.get("result_film").getAsString()),false);
                if(complete!=null)complete.accept(response);
                character=response;updateCapabilities();
            } catch(Exception e) {
                status.text(e.getMessage()==null?e.toString():e.getMessage());
                LipSyncAddon.LOG.warning("Setup apply error: "+e);
            }
        });
    }

    void run(String action,JsonObject options,JobCallback complete) {
        if(job!=null)return;
        try {
            JsonObject request=options==null?new JsonObject():options.deepCopy();request.addProperty("action",action);
            job=new BridgeJob(panel,actor,request);completion=complete;
            for(UIElement input:inputs)input.setEnabled(false);cancel.setEnabled(true);close.setEnabled(true);
            status.text("Processing "+action+"…");
        }catch(Exception e){status.text(e.getMessage());job=null;}
    }

    private void resumeHelp() {
        if(bundleDirectory==null)return;
        JsonObject options=new JsonObject();options.addProperty("bundle_directory",bundleDirectory);
        run("assist_resume",options,(activeJob,response) -> {
            try {
                EditorPatch.apply(panel,activeJob,Path.of(response.get("result_film").getAsString()),false);
                character=response;updateCapabilities();
                status.text("AI rig applied! Mouth is now ready for lip sync.");
            } catch(Exception e) {
                status.text(e.getMessage()==null?e.toString():e.getMessage());
                LipSyncAddon.LOG.warning("Resume help error: "+e);
            }
        });
    }

    private void updateCapabilities() {
        JsonObject c=character.has("capabilities")?character.getAsJsonObject("capabilities"):new JsonObject();
        boolean fitted=character.has("fitted")&&character.get("fitted").getAsBoolean();
        capability.text((fitted?"✅ Mouth ready for speech":"⚠️ One-time mouth setup needed")+"  |  "+
            (flag(c,"eyes")?"Blinks ready":"No eyelids")+"  |  "+(flag(c,"elbows")?"Elbows ready":flag(c,"arms")?"Arms ready":"Mouth only"));
        blinks.setEnabled(flag(c,"eyes"));if(!flag(c,"eyes"))blinks.setValue(false);else if(!blinks.getValue())blinks.setValue(true);
        gestures.setEnabled(flag(c,"arms"));if(!flag(c,"arms"))gestures.setValue(false);else if(!gestures.getValue())gestures.setValue(true);
        apply.setEnabled(readyResponse!=null);checkHelp.setEnabled(bundleDirectory!=null);
        generate.setEnabled(fitted);
        generateApply.setEnabled(fitted);
        autoFit.setEnabled(true);
        autoFit.label=IKey.constant(fitted?"✨ 1-Click Re-Fit":"✨ 1-Click Auto-Fit");
        setup.setEnabled(true);
    }

    JsonObject character(){return character;}
    static boolean flag(JsonObject obj,String key){return obj.has(key)&&!obj.get(key).isJsonNull()&&obj.get(key).getAsBoolean();}
    static String getString(JsonObject obj,String key,String fallback){return obj.has(key)&&!obj.get(key).isJsonNull()?obj.get(key).getAsString():fallback;}

    @Override public void render(UIContext context) {
        if(!this.hasParent()) return;
        if(!inspected){
            inspected=true;
            run("inspect",null,(activeJob,response)->{
                character=response;updateCapabilities();
                status.text("Ready. Use 1-Click Auto-Fit if mouth is not yet set up.");
            });
        }
        if(chosenFile!=null){audio.setText(chosenFile);chosenFile=null;reusedAudio=null;}
        if(pickerError!=null){status.text(pickerError);pickerError=null;}
        if(job!=null) {
            if(job.process.isAlive())status.text(job.status());
            else {
                BridgeJob finished=job;
                JobCallback callback=completion;
                job=null;
                completion=null;
                for(UIElement input:inputs)input.setEnabled(true);
                cancel.setEnabled(false);
                MinecraftClient.getInstance().send(() -> {
                    try {
                        JsonObject resp=finished.response();
                        if(callback!=null)callback.accept(finished,resp);
                    } catch(Exception error) {
                        status.text(error.getMessage()==null?error.toString():error.getMessage());
                        LipSyncAddon.LOG.warning("LipSync job error: "+error);
                    } finally {
                        updateCapabilities();
                    }
                });
                return;
            }
        }
        if(!this.hasParent()) return;
        super.render(context);
    }

    @Override public void close(){if(job!=null)job.cancel();super.close();}
}
