package dev.lipsync.studio;

import com.google.gson.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.zip.*;
import mchorse.bbs_mod.BBSMod;
import mchorse.bbs_mod.data.storage.DataStorage;
import mchorse.bbs_mod.data.types.*;
import mchorse.bbs_mod.film.Film;
import mchorse.bbs_mod.film.replays.Replay;
import mchorse.bbs_mod.ui.film.UIFilmPanel;

/** One managed worker per request, no HTTP service or shell command interpolation. */
final class BridgeJob {
    static final Gson JSON=new GsonBuilder().setPrettyPrinting().create();
    final Path directory;
    final Film film;
    final Replay actor;
    final int actorIndex;
    final MapType before;
    final Process process;
    final String action;
    private long lastStatusRead;
    private String message="Starting local lip-sync worker…";

    BridgeJob(UIFilmPanel panel,Replay actor,JsonObject request) throws Exception {
        this.film=(Film)panel.getData();this.actor=actor;
        actorIndex=film.replays.getList().indexOf(actor);
        if(actorIndex<0) throw new IOException("The selected actor is no longer in this film.");
        action=request.get("action").getAsString();
        Path instance=BBSMod.getGameFolder().toPath().toAbsolutePath();
        Path config=instance.resolve("config/lip_sync_studio/bridge.json");
        if(!Files.isRegularFile(config)) throw new IOException("Lip Sync Studio worker is not configured. Run Install BBS Addon.bat once, then restart Minecraft.");
        JsonObject settings=JsonParser.parseString(Files.readString(config)).getAsJsonObject();
        Path project=Path.of(settings.get("project_directory").getAsString()).toAbsolutePath();
        Path python=Path.of(settings.get("python_executable").getAsString()).toAbsolutePath();
        if(!Files.isRegularFile(python)||!Files.isRegularFile(project.resolve("lip_sync_studio/bbs_bridge.py")))
            throw new IOException("The configured worker moved or is missing. Run Install BBS Addon.bat again.");
        directory=instance.resolve("config/lip_sync_studio/jobs/"+UUID.randomUUID());Files.createDirectories(directory);
        before=(MapType)film.toData().copy();
        write(directory.resolve("snapshot.dat"),before);
        request.addProperty("schema",1);request.addProperty("instance",instance.toString());
        request.addProperty("snapshot",directory.resolve("snapshot.dat").toString());request.addProperty("actor_index",actorIndex);
        Files.writeString(directory.resolve("request.json"),JSON.toJson(request),StandardCharsets.UTF_8);
        ProcessBuilder builder=new ProcessBuilder(python.toString(),"-m","lip_sync_studio.bbs_bridge",directory.resolve("request.json").toString());
        builder.directory(project.toFile());builder.environment().put("PYTHONUTF8","1");
        builder.redirectErrorStream(true);builder.redirectOutput(directory.resolve("worker.log").toFile());
        process=builder.start();
    }

    String status() {
        if(System.currentTimeMillis()-lastStatusRead<500)return message;
        lastStatusRead=System.currentTimeMillis();
        try { message=JsonParser.parseString(Files.readString(directory.resolve("status.json"))).getAsJsonObject().get("message").getAsString(); }
        catch(Exception ignored) {}
        return message;
    }

    JsonObject response() throws Exception {
        Path response=directory.resolve("response.json");
        if(!Files.isRegularFile(response))throw new IOException("Worker stopped without a result. See "+directory.resolve("worker.log"));
        if(Files.size(response)>8_000_000)throw new IOException("Worker response is too large.");
        JsonObject data=JsonParser.parseString(Files.readString(response)).getAsJsonObject();
        if(!data.has("ok")||!data.get("ok").getAsBoolean())throw new IOException(
            data.has("message")?data.get("message").getAsString():data.has("error")?data.get("error").getAsString():"Lip sync failed. See the job log.");
        return data;
    }

    void cancel() {
        try { Files.writeString(directory.resolve("cancel.flag"),"cancel"); }
        catch(IOException ignored) {}
        // Give the worker time to terminate its own FFmpeg/Rhubarb children cleanly.
        Thread cleanup=new Thread(() -> {try {if(!process.waitFor(8,java.util.concurrent.TimeUnit.SECONDS))process.destroy();}catch(InterruptedException ignored){}});
        cleanup.setDaemon(true);cleanup.start();
    }

    boolean unchanged(UIFilmPanel panel) {
        if(panel.getData()!=film || film.replays.getList().indexOf(actor)!=actorIndex)return false;
        MapType current=(MapType)film.toData();
        // The film's active-time counter legitimately changes while the dialog is open.
        for(String key:before.keys()) {
            if(key.equals("time_spent_active"))continue;
            if(!same(before.get(key),current.get(key)))return false;
        }
        return true;
    }

    static boolean same(BaseType left,BaseType right) {
        if(left==null||right==null)return left==right;
        return left.equals(right);
    }

    static void write(Path path,BaseType data) throws IOException {
        try(OutputStream out=new GZIPOutputStream(Files.newOutputStream(path))){DataStorage.writeToStream(out,data);}
    }
    static MapType read(Path path) throws IOException {
        try(InputStream in=new GZIPInputStream(Files.newInputStream(path))){return (MapType)DataStorage.readFromStream(in);}
    }
}
