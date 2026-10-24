package dev.lipsync.studio;

import java.util.List;
import java.util.logging.Logger;
import mchorse.bbs_mod.events.BBSAddonMod;
import mchorse.bbs_mod.events.Subscribe;
import mchorse.bbs_mod.events.register.RegisterDashboardPanelsEvent;
import mchorse.bbs_mod.film.replays.Replay;
import mchorse.bbs_mod.l10n.keys.IKey;
import mchorse.bbs_mod.ui.film.UIFilmPanel;
import mchorse.bbs_mod.ui.framework.elements.overlay.UIOverlay;
import mchorse.bbs_mod.ui.framework.elements.overlay.UIMessageOverlayPanel;
import mchorse.bbs_mod.ui.utils.icons.Icons;

/** Client-only companion. It appends to BBS menus; it does not replace the BBS jar. */
public final class LipSyncAddon implements BBSAddonMod {
    static final Logger LOG=Logger.getLogger("LipSyncStudio");

    @Subscribe
    public void dashboard(RegisterDashboardPanelsEvent event) {
        UIFilmPanel panel=event.dashboard.getPanel(UIFilmPanel.class);
        if (panel==null) return;
        panel.replayEditor.replaysList.replays.context(menu -> {
            List<Replay> actors=panel.replayEditor.replaysList.replays.getSelectedReplays();
            if (actors.size()==1) menu.action(Icons.MICROPHONE,IKey.constant("Lip sync…"),() -> {
                try {
                    if (panel.isRunning()) panel.togglePlayback();
                    UIOverlay.addOverlay(panel.getContext(),new DialogueOverlay(panel,actors.get(0)));
                } catch (Exception error) { showError(panel,error); }
            });
        });
        LOG.info("Lip Sync Studio: actor context menu registered for BBS 2.3.3.");
    }

    static void showError(UIFilmPanel panel,Throwable error) {
        LOG.warning(error.toString());
        UIOverlay.addOverlay(panel.getContext(),new UIMessageOverlayPanel(
            IKey.constant("Lip Sync Studio"),IKey.constant(error.getMessage()==null?error.toString():error.getMessage())));
    }
}
