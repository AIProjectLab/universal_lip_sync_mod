package dev.lipsync.studio;

import java.io.IOException;
import java.nio.file.Path;
import mchorse.bbs_mod.BBSModClient;
import mchorse.bbs_mod.data.types.*;
import mchorse.bbs_mod.settings.values.base.BaseValue;
import mchorse.bbs_mod.ui.film.UIFilmPanel;
import mchorse.bbs_mod.ui.film.utils.UIFilmUndoHandler;

/** Apply a generated patch to the captured live actor, preserving all other actors. */
final class EditorPatch {
    static void apply(UIFilmPanel panel,BridgeJob job,Path result,boolean generated) throws Exception {
        if(!job.unchanged(panel))throw new IOException("The film changed while processing. No result was applied. Open Lip sync again to use the current edit.");
        MapType updated=BridgeJob.read(result);
        MapType target=updated.getList("replays").getMap(job.actorIndex);
        MapType original=job.before.getList("replays").getMap(job.actorIndex);
        // Validate response shape before making the first edit.
        if(!target.has("form"))throw new IOException("The worker returned no character form.");
        if(generated&&(!target.has("animation")||!updated.has("camera")))throw new IOException("The animation patch is incomplete.");
        UIFilmUndoHandler undo=panel.getUndoHandler();
        if(undo==null)throw new IOException("The editor undo handler is not ready. Reopen this film.");
        undo.submitUndo();undo.getUndoManager().markLastUndoNoMerging();
        try {
            edit(undo,job.actor.form,target.get("form"));
            if(generated) {
                edit(undo,job.actor.animation,target.get("animation"));
                if(target.has("sections"))edit(undo,job.actor.sections,target.get("sections"));
                edit(undo,job.film.camera,updated.get("camera"));
                if(updated.has("description"))edit(undo,job.film.description,updated.get("description"));
            }
            undo.submitUndo();undo.getUndoManager().markLastUndoNoMerging();
        } catch(Exception error) {
            job.actor.form.fromData(original.get("form"));
            job.actor.animation.fromData(original.get("animation"));
            if(original.has("sections"))job.actor.sections.fromData(original.get("sections"));
            job.film.camera.fromData(job.before.get("camera"));
            if(job.before.has("description"))job.film.description.fromData(job.before.get("description"));
            undo.submitUndo();throw error;
        }
        loadModels(target.getMap("form"));
        panel.getEditorState().markTracksChanged();
        panel.replayEditor.setReplay(job.actor);
        panel.fillData();
        panel.getController().createEntities();
        panel.replayEditor.replaysList.replays.refreshReplayList();
        panel.markDirty();
    }

    private static void edit(UIFilmUndoHandler undo,BaseValue value,BaseType data) {
        if(data==null||BridgeJob.same(value.toData(),data))return;
        undo.handlePreValues(value,1);
        value.fromData(data);
    }

    private static void loadModels(MapType form) {
        if(form.has("model")) {
            try { BBSModClient.getModels().loadModel(form.getString("model")); }
            catch(Exception error){LipSyncAddon.LOG.warning("Model refresh: "+error);}
        }
        for(BaseType part:form.getList("parts")) {
            if(part instanceof MapType map && map.has("form"))loadModels(map.getMap("form"));
        }
    }
}
