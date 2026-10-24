package dev.lipsync.studio;

import com.google.gson.*;
import java.util.*;
import mchorse.bbs_mod.l10n.keys.IKey;
import mchorse.bbs_mod.ui.framework.UIContext;
import mchorse.bbs_mod.ui.framework.elements.UIElement;
import mchorse.bbs_mod.ui.framework.elements.buttons.*;
import mchorse.bbs_mod.ui.framework.elements.input.UITrackpad;
import mchorse.bbs_mod.ui.framework.elements.context.UISearchListContextMenu;
import mchorse.bbs_mod.ui.framework.elements.overlay.UIOverlayPanel;
import mchorse.bbs_mod.ui.framework.elements.utils.*;

/** Character mouth fitting wizard with 1-click presets and fine-tuning sliders. */
final class SetupOverlay extends UIOverlayPanel {
    private final DialogueOverlay owner;
    private final UIText help=new UIText("Choose a preset or adjust sliders. Click 'Save Fit' when ready. BBS Undo can revert anytime.");
    private final UIButton head,preview,save;
    private final LinkedHashMap<String,UITrackpad> values=new LinkedHashMap<>();
    private final UIToggle elbows=new UIToggle(IKey.constant("Add compatible elbow joints"),false,null);
    private String headName;

    SetupOverlay(DialogueOverlay owner) {
        super(IKey.constant("Character Mouth Fitting"));this.owner=owner;
        JsonObject data=owner.character();JsonObject defaults=data.has("setup_defaults")?data.getAsJsonObject("setup_defaults"):new JsonObject();
        headName=DialogueOverlay.getString(defaults,"head_bone","head");
        head=new UIButton(IKey.constant("Head Attachment: "+headName),b -> {
            List<String> bones=new ArrayList<>();
            if(data.has("bones"))for(JsonElement bone:data.getAsJsonArray("bones"))bones.add(bone.getAsString());
            if(bones.isEmpty()){help.text("No head controls found. Use AI Auto-Rig for this model.");return;}
            getContext().replaceContextMenu(UISearchListContextMenu.strings(IKey.constant("Head attachment"),false,bones,value->{headName=value;b.label=IKey.constant("Head Attachment: "+value);}).set(headName));
        });place(head,8,6,294,22);

        text("Quick Presets",8,32,294,14);
        UIButton btnSteve=new UIButton(IKey.constant("Steve / Std"),b -> applyPreset(0.0, 0.05, 0.004, 1.0, 1.0, 1.0));
        place(btnSteve,8,48,94,20);
        UIButton btnAlex=new UIButton(IKey.constant("Alex / Slim"),b -> applyPreset(0.0, 0.05, 0.004, 0.95, 0.95, 1.0));
        place(btnAlex,106,48,94,20);
        UIButton btnVillager=new UIButton(IKey.constant("Villager"),b -> applyPreset(0.0, -0.15, 0.12, 0.85, 0.75, 0.85));
        place(btnVillager,204,48,98,20);

        text("Mouth Position",8,72,294,14);
        field("tx","Left / right",0,88,defaults,-16,16,.005);
        field("ty","Up / down",.05,114,defaults,-16,16,.005);
        field("tz","Forward / back",.004,140,defaults,-16,16,.001);

        text("Mouth Size",8,166,294,14);
        field("sx","Width",1,182,defaults,.05,8,.05);
        field("sy","Height",1,208,defaults,.05,8,.05);
        field("sz","Depth",1,234,defaults,.05,8,.05);

        place(elbows,8,262,294,18);
        elbows.setValue(DialogueOverlay.flag(defaults,"elbows"));
        JsonObject capability=data.has("capabilities")?data.getAsJsonObject("capabilities"):new JsonObject();
        elbows.setEnabled(DialogueOverlay.flag(capability,"arms")&&!DialogueOverlay.flag(capability,"elbows"));

        place(help,8,284,294,44);
        preview=new UIButton(IKey.constant("👁 Preview in BBS (Optional)"),b -> send(false));place(preview,8,332,294,24);
        save=new UIButton(IKey.constant("💾 Save Fit & Continue"),b -> send(true));place(save,8,360,294,26);
    }

    private void applyPreset(double tx, double ty, double tz, double sx, double sy, double sz) {
        if(values.containsKey("tx")) values.get("tx").setValue(tx);
        if(values.containsKey("ty")) values.get("ty").setValue(ty);
        if(values.containsKey("tz")) values.get("tz").setValue(tz);
        if(values.containsKey("sx")) values.get("sx").setValue(sx);
        if(values.containsKey("sy")) values.get("sy").setValue(sy);
        if(values.containsKey("sz")) values.get("sz").setValue(sz);
        help.text("Preset loaded! Click 'Save Fit & Continue' to apply.");
    }

    private void text(String message,int x,int y,int w,int h){place(new UILabel(IKey.constant(message)),x,y,w,h);}
    private void place(UIElement e,int x,int y,int w,int h){e.relative(content).xy(x,y).wh(w,h);content.add(e);}
    private void field(String id,String label,double fallback,int y,JsonObject defaults,double min,double max,double step){
        text(label,8,y,140,22);UITrackpad value=new UITrackpad(null);value.limit(min,max).values(step,step/5,step*10);
        value.setValue(defaults.has(id)?defaults.get(id).getAsDouble():fallback);place(value,154,y,148,22);values.put(id,value);
    }

    private void send(boolean confirm){
        JsonObject settings=new JsonObject();settings.addProperty("head_bone",headName);settings.addProperty("elbows",elbows.getValue());
        for(var entry:values.entrySet())settings.addProperty(entry.getKey(),entry.getValue().getValue());
        owner.setup(settings,confirm,response -> {
            if(confirm){super.close();}
            else{super.close();owner.close();}
        });
        help.text(confirm?"Saving character rig…":"Previewing mouth in BBS. Reopen Lip sync to adjust or confirm.");
    }

    @Override public void render(UIContext context){
        if(!this.hasParent()) return;
        boolean idle=!owner.working();preview.setEnabled(idle);save.setEnabled(idle);
        super.render(context);
    }
}
