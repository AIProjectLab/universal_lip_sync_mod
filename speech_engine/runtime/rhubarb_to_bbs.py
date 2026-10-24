#!/usr/bin/env python3
"""
Rhubarb to BBS (BlockBuster Studio 1.6+ / 2.3+ & Blockbuster) Lip-Sync Converter
================================================================================
Converts speech audio files (.wav/.ogg) into:
  1. BBS 1.6/2.3+ Texture Track JSON Keyframe Preset ("0/texture")
     - Type: "link", Interp: "constant" (stepped sprite swaps)
  2. BBS 1.6/2.3+ Pose Track JSON Keyframe Preset ("0/pose")
     - Type: "pose", Interp: "cubic_out" (smooth 3D bone poses)
  3. Legacy Sequencer Morph NBT (for Blockbuster 1.12-1.16)

Timing & Drift-Free Engine:
  - 20 TPS absolute master-clock snapping: tick = round(cue_start * 20.0).
  - Mathematically zero cumulative drift across audio of any length.
  - Trailing silence handling: speech cue end is reported separately from the
    total audio duration, and the neutral mouth shape ('X' / 'idle') is held
    firmly through the entire audio duration (e.g. 14.37s -> 287.4 ticks / 287 ticks).
  - Automatically deploys presets, textures, and audio directly into detected
    BBS Minecraft instances on the user's system.
"""

import sys
import os
import json
import shutil
import subprocess
import argparse
from copy import deepcopy
from pathlib import Path

_local_bin = Path(__file__).parent.parent / "bin" / "rhubarb.exe"
DEFAULT_RHUBARB_PATH = _local_bin if _local_bin.is_file() else Path(__file__).parent / "Rhubarb-Lip-Sync-1.14.0-Windows" / "rhubarb.exe"
POSES_JSON_PATH = Path(__file__).parent / "bbs_poses.json"

# Common BBS instance paths on user's system
KNOWN_BBS_INSTANCES = [
    Path(r"C:\Users\shafe\curseforge\minecraft\Instances\BBS 1.21.1"),
    Path(r"C:\Users\shafe\curseforge\minecraft\Instances\BBS Working Studio"),
    Path(r"C:\Users\shafe\curseforge\minecraft\Instances\BBS"),
]

# Rhubarb Viseme -> BBS Texture Image
RHUBARB_TO_TEXTURE = {
    "A": "mouth_a.png",  # Closed (P, B, M)
    "B": "mouth_b.png",  # Teeth (Consonants, EE)
    "C": "mouth_c.png",  # Open (EH, AE)
    "D": "mouth_d.png",  # Wide Open (AA)
    "E": "mouth_e.png",  # Rounded (AO, ER)
    "F": "mouth_f.png",  # Puckered (UW, OW, W)
    "G": "mouth_g.png",  # Upper teeth on lip (F, V)
    "H": "mouth_h.png",  # Tongue raised (L)
    "X": "mouth_x.png",  # Idle / Rest
}

# Project-specific 3D art direction: E uses the requested EE/I smile.
# H stays tongue-raised (L/TH); F owns O/U/W rounding, since one cue cannot
# select both a rounded aperture and a distinct tongue-contact pose.
# The 2D texture mapping above retains its original Rhubarb sprite meanings.
RHUBARB_TO_POSE_KEY = {
    "A": "a",      # Closed (P, B, M)
    "B": "b",      # Clenched teeth (K, S, T)
    "C": "c",      # Open (EH, AE)
    "D": "d",      # Wide open (AA, AH)
    "E": "ee",     # Horizontal smile (EE, I)
    "F": "o",      # Puckered / round (UW, OW, W)
    "G": "f",      # Upper teeth on lip (F, V)
    "H": "l",      # Tongue raised (L, TH)
    "X": "idle",   # Neutral rest
}

MOUTH_BONES = ("Open", "Closed", "Inside", "Up", "Down", "Tongue")
IDENTITY_BONE_TRANSFORM = {
    "r2": [0.0, 0.0, 0.0],
    "r": [0.0, 0.0, 0.0],
    "s": [1.0, 1.0, 1.0],
    "t": [0.0, 0.0, 0.0],
    "fix": 0.0,
    "color": -1,
    "lighting": 0.0,
}

def run_rhubarb(audio_path, dialog_text=None, recognizer="pocketSphinx", rhubarb_bin=DEFAULT_RHUBARB_PATH):
    """Runs Rhubarb CLI and returns parsed JSON mouth cues."""
    audio_path = Path(audio_path).resolve()
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")
    if not Path(rhubarb_bin).exists():
        raise FileNotFoundError(f"Rhubarb executable not found at: {rhubarb_bin}")

    cmd = [
        str(rhubarb_bin),
        "-f", "json",
        "-r", recognizer,
        "--extendedShapes", "GHX"
    ]

    temp_dialog_file = None
    if dialog_text:
        temp_dialog_file = audio_path.parent / f"_temp_dialog_{audio_path.stem}.txt"
        temp_dialog_file.write_text(dialog_text, encoding="utf-8")
        cmd.extend(["-d", str(temp_dialog_file)])

    cmd.append(str(audio_path))

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return json.loads(result.stdout)
    finally:
        if temp_dialog_file and temp_dialog_file.exists():
            temp_dialog_file.unlink()

def convert_cues_to_drift_free_ticks(cues, audio_duration, tps=20.0):
    """
    Converts Rhubarb cues into drift-free Minecraft timeline ticks.
    - Each cue start is snapped to the absolute master clock: tick = round(cue.start * tps).
    - Trailing silence is held with neutral mouth ('X') until the audio end.
    - Returns (timed_cues, timing_stats)
    """
    total_audio_ticks = float(round(audio_duration * tps))

    # Identify end of active speech sounds (non-neutral cues)
    last_speech_cue = None
    for cue in reversed(cues):
        if cue["value"] != "X":
            last_speech_cue = cue
            break

    if last_speech_cue:
        speech_end_seconds = last_speech_cue["end"]
        speech_end_tick = float(round(speech_end_seconds * tps))
    else:
        speech_end_seconds = 0.0
        speech_end_tick = 0.0

    trailing_silence_seconds = max(0.0, audio_duration - speech_end_seconds)
    trailing_silence_ticks = max(0.0, total_audio_ticks - speech_end_tick)

    timed_cues = []
    last_tick = -1.0

    # Ensure timeline begins at tick 0.0 (idle rest if first cue starts later)
    if not cues or cues[0]["start"] > 0.02:
        timed_cues.append({
            "tick": 0.0,
            "shape": "X",
            "raw_start": 0.0,
            "raw_end": cues[0]["start"] if cues else audio_duration
        })
        last_tick = 0.0

    for cue in cues:
        raw_start = cue["start"]
        shape = cue["value"]

        # Absolute master-clock alignment
        tick = float(round(raw_start * tps))

        # Handle sub-tick overlaps (strictly increasing ticks)
        if tick <= last_tick:
            tick = last_tick + 1.0

        timed_cues.append({
            "tick": tick,
            "shape": shape,
            "raw_start": raw_start,
            "raw_end": cue["end"]
        })
        last_tick = tick

    # Handle trailing silence & hold neutral rest mouth to total_audio_ticks
    if last_tick < total_audio_ticks:
        if timed_cues and timed_cues[-1]["shape"] == "X":
            # Last cue is already neutral 'X' (e.g. at tick 282).
            # Add an explicit keyframe at total_audio_ticks (tick 287) to hold neutral mouth
            # and establish the full clip length in BBS timeline.
            timed_cues.append({
                "tick": total_audio_ticks,
                "shape": "X",
                "raw_start": audio_duration,
                "raw_end": audio_duration
            })
        else:
            # Last cue was active speech. Close mouth to neutral immediately at speech_end_tick,
            # then hold until total_audio_ticks.
            if speech_end_tick > last_tick:
                timed_cues.append({
                    "tick": speech_end_tick,
                    "shape": "X",
                    "raw_start": speech_end_seconds,
                    "raw_end": speech_end_seconds
                })
            if total_audio_ticks > speech_end_tick:
                timed_cues.append({
                    "tick": total_audio_ticks,
                    "shape": "X",
                    "raw_start": audio_duration,
                    "raw_end": audio_duration
                })

    timing_stats = {
        "audio_duration_seconds": audio_duration,
        "total_audio_ticks": total_audio_ticks,
        "exact_ticks_calc": audio_duration * tps,
        "speech_end_seconds": speech_end_seconds,
        "speech_end_tick": speech_end_tick,
        "trailing_silence_seconds": trailing_silence_seconds,
        "trailing_silence_ticks": trailing_silence_ticks,
        "total_keyframes": len(timed_cues)
    }

    return timed_cues, timing_stats

def generate_bbs_texture_json(timed_cues, texture_dir="assets:textures/mouths/steve/"):
    """
    Generates BBS 1.6/2.3+ '0/texture' Link Track Keyframe Preset JSON.
    - Type: "link"
    - Interp: "constant" (stepped sprite swaps, zero easing)
    - Shape: "DIAMOND", Color: 5038997
    """
    if not texture_dir.endswith("/"):
        texture_dir += "/"

    keyframes = []
    for cue in timed_cues:
        img_name = RHUBARB_TO_TEXTURE.get(cue["shape"], "mouth_x.png")
        keyframes.append({
            "duration": 0.0,
            "interp": "constant",
            "color": 5038997,
            "shape": "DIAMOND",
            "tick": cue["tick"],
            "value": f"{texture_dir}{img_name}"
        })

    return {
        "0/texture": {
            "type": "link",
            "keyframes": keyframes
        }
    }

def generate_bbs_pose_json(timed_cues, poses_db):
    """
    Generates BBS 1.6/2.3+ '0/pose' Pose Track Keyframe Preset JSON.
    - Type: "pose"
    - Interp: "cubic_out" (smooth 3D bone rig interpolation)
    - Shape: "CIRCLE", Color: 16763461
    - Value: {"static": false, "pose": {...}}
    Open drives the aperture; Inside, Up, Down and Tongue inherit its scale
    in player/mouth. Child scales in the database are local adjustments,
    so do not multiply them by Open's scale again here.
    """
    keyframes = []
    for cue in timed_cues:
        pose_key = RHUBARB_TO_POSE_KEY.get(cue["shape"], "idle")
        pose_data = poses_db[pose_key]["pose"]
        missing_bones = set(MOUTH_BONES) - pose_data.keys()
        if missing_bones:
            raise ValueError(f"Mouth pose {pose_key!r} is missing bones: {sorted(missing_bones)}")

        # Emit every transform component, including identity resets. BBS can
        # then ease every bone channel between complete, independent poses.
        complete_pose = {}
        for bone, transform in pose_data.items():
            complete_pose[bone] = deepcopy(IDENTITY_BONE_TRANSFORM)
            complete_pose[bone].update(deepcopy(transform))

        keyframes.append({
            "duration": 0.0,
            "interp": "cubic_out",
            "color": 16763461,
            "shape": "CIRCLE",
            "tick": cue["tick"],
            "value": {
                "static": False,
                "pose": complete_pose
            }
        })

    return {
        "0/pose": {
            "type": "pose",
            "keyframes": keyframes
        }
    }

def generate_legacy_sequencer_nbt(timed_cues, total_audio_ticks, texture_dir="b.a:textures/mouths/steve/"):
    """
    Generates legacy Blockbuster 1.12-1.16 Sequencer Morph NBT.
    Uses interval ticks: duration = next_tick - current_tick.
    """
    if not texture_dir.endswith("/"):
        texture_dir += "/"

    entries = []
    for i, cue in enumerate(timed_cues):
        if i < len(timed_cues) - 1:
            duration_ticks = timed_cues[i + 1]["tick"] - cue["tick"]
        else:
            duration_ticks = max(1.0, total_audio_ticks - cue["tick"])

        if duration_ticks < 0.5:
            duration_ticks = 0.5

        img_name = RHUBARB_TO_TEXTURE.get(cue["shape"], "mouth_x.png")
        full_texture = f"{texture_dir}{img_name}"
        morph_nbt = f'Morph:{{Texture:"{full_texture}",Name:"blockbuster.image"}}'
        slot_nbt = f'{{Random:0.0f,SetDuration:1b,{morph_nbt},Duration:{duration_ticks:.1f}f,EndPoint:0b}}'
        entries.append(slot_nbt)

    list_str = ",".join(entries)
    return f'{{List:[{list_str}],KeepProgress:1b,Name:"sequencer",Offset:[0.0f,0.0f,0.0f]}}'

def create_pixel_art_mouth_sprites(output_dir, character="steve"):
    """
    Generates clean 16x16 transparent pixel-art mouth sprites for the 2D texture track.
    Matches Minecraft skin face scale and palette.
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return []

    char_dir = output_dir / "mouths" / character
    char_dir.mkdir(parents=True, exist_ok=True)

    # Steve mouth palette
    SKIN = (0, 0, 0, 0)         # Transparent background (or skin)
    BEARD = (89, 54, 22, 255)    # #593616
    MOUTH_DARK = (56, 20, 14, 255) # #38140E
    TEETH = (235, 235, 230, 255) # Off-white
    TONGUE = (199, 77, 67, 255)  # Pinkish-red

    shapes = {
        "mouth_x.png": "X", # Neutral closed line
        "mouth_a.png": "A", # Closed lips (m, b, p)
        "mouth_b.png": "B", # Clenched teeth
        "mouth_c.png": "C", # Medium open
        "mouth_d.png": "D", # Wide open (ah)
        "mouth_e.png": "E", # Rounded open (oh)
        "mouth_f.png": "F", # Small puckered (oo)
        "mouth_g.png": "G", # Upper teeth on lip (f, v)
        "mouth_h.png": "H", # Tongue up (l)
    }

    created = []
    for filename, shape in shapes.items():
        img = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Mouth area is centered in lower half (e.g. x: 5..10, y: 8..11)
        if shape in ("X", "A"):
            # Subtle closed line
            draw.rectangle([5, 9, 10, 9], fill=MOUTH_DARK)
            draw.rectangle([6, 10, 9, 10], fill=BEARD)
        elif shape == "B":
            # Teeth visible
            draw.rectangle([5, 8, 10, 10], fill=MOUTH_DARK)
            draw.rectangle([6, 9, 9, 9], fill=TEETH)
        elif shape == "C":
            # Medium open mouth
            draw.rectangle([5, 8, 10, 11], fill=MOUTH_DARK)
            draw.rectangle([6, 8, 9, 8], fill=TEETH)
            draw.rectangle([6, 10, 9, 10], fill=TONGUE)
        elif shape == "D":
            # Wide open
            draw.rectangle([4, 7, 11, 12], fill=MOUTH_DARK)
            draw.rectangle([5, 7, 10, 8], fill=TEETH)
            draw.rectangle([5, 11, 10, 12], fill=TONGUE)
        elif shape == "E":
            # Rounded
            draw.rectangle([5, 7, 10, 11], fill=MOUTH_DARK)
            draw.rectangle([6, 10, 9, 10], fill=TONGUE)
        elif shape == "F":
            # Small puckered 'O'
            draw.rectangle([6, 8, 9, 11], fill=MOUTH_DARK)
            draw.rectangle([7, 9, 8, 10], fill=TONGUE)
        elif shape == "G":
            # Upper teeth on lower lip
            draw.rectangle([5, 8, 10, 10], fill=MOUTH_DARK)
            draw.rectangle([5, 8, 10, 9], fill=TEETH)
        elif shape == "H":
            # Tongue up (L)
            draw.rectangle([5, 8, 10, 11], fill=MOUTH_DARK)
            draw.rectangle([6, 8, 9, 9], fill=TONGUE)

        dest = char_dir / filename
        img.save(dest, "PNG")
        created.append(dest)

    return created

def deploy_to_bbs_instances(texture_preset, pose_preset, audio_path, sprites):
    """Deploys presets, audio, and mouth sprites into detected BBS instances."""
    deployed_to = []

    for inst in KNOWN_BBS_INSTANCES:
        if not inst.exists():
            continue

        # 1. Presets folder
        presets_dir = inst / "config" / "bbs" / "settings" / "presets" / "keyframes"
        presets_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(texture_preset, presets_dir / texture_preset.name)
        if pose_preset and pose_preset.exists():
            shutil.copy2(pose_preset, presets_dir / pose_preset.name)

        # 2. Audio assets folder
        audio_dir = inst / "config" / "bbs" / "assets" / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(audio_path, audio_dir / audio_path.name)

        # 3. Mouth texture sprites folder
        if sprites:
            mouths_dir = inst / "config" / "bbs" / "assets" / "textures" / "mouths" / "steve"
            mouths_dir.mkdir(parents=True, exist_ok=True)
            for s in sprites:
                shutil.copy2(s, mouths_dir / s.name)

        deployed_to.append(inst.name)

    return deployed_to

def main():
    parser = argparse.ArgumentParser(description="Convert speech audio to BBS 1.6+ / 2.3+ JSON Keyframe Presets and Sequencer NBT.")
    parser.add_argument("audio", help="Input speech audio file (.wav or .ogg)")
    parser.add_argument("-d", "--dialog", help="Spoken dialogue text or script file (recommended)", default=None)
    parser.add_argument("-c", "--character", help="Character name (default: 'steve')", default="steve")
    parser.add_argument("-t", "--texture-prefix", help="BBS texture path prefix", default=None)
    parser.add_argument("-o", "--output-dir", help="Output directory (default: current directory)", default=".")
    parser.add_argument("-r", "--recognizer", help="Rhubarb recognizer (pocketSphinx or phonetic)", default="pocketSphinx")
    parser.add_argument("--no-deploy", help="Skip auto-copying to detected BBS instances", action="store_true")
    args = parser.parse_args()

    audio_path = Path(args.audio).resolve()
    dialog_text = None
    if args.dialog:
        if Path(args.dialog).exists():
            dialog_text = Path(args.dialog).read_text(encoding="utf-8")
        else:
            dialog_text = args.dialog

    texture_prefix = args.texture_prefix or f"assets:textures/mouths/{args.character}/"
    legacy_texture_prefix = args.texture_prefix or f"b.a:textures/mouths/{args.character}/"
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{audio_path.stem}_{args.character}"

    print(f"[*] Processing audio: {audio_path.name}")
    if dialog_text:
        print(f"[*] Spoken dialogue: \"{dialog_text[:60]}...\"")

    # 1. Run Rhubarb
    result = run_rhubarb(audio_path, dialog_text=dialog_text, recognizer=args.recognizer)
    raw_cues = result["mouthCues"]
    duration = result["metadata"]["duration"]

    # 2. Convert to drift-free timeline ticks with trailing silence holding
    timed_cues, stats = convert_cues_to_drift_free_ticks(raw_cues, audio_duration=duration, tps=20.0)

    # 3. Transparent Timing Report
    print("\n" + "-"*65)
    print("TIMING REPORT (20 TPS ABSOLUTE MASTER-CLOCK)")
    print("-"*65)
    print(f"  Audio duration:      {duration:.2f}s -> {stats['exact_ticks_calc']:.1f} ticks (snapped to {stats['total_audio_ticks']:.0f} ticks)")
    print(f"  Speech cues end:     {stats['speech_end_seconds']:.2f}s ({stats['speech_end_tick']:.0f} timeline ticks)")
    print(f"  Trailing silence:    {stats['trailing_silence_seconds']:.2f}s ({stats['trailing_silence_ticks']:.0f} ticks) [HELD NEUTRAL 'X' TO END]")
    print(f"  Timeline keyframes:  {stats['total_keyframes']} keyframes spanning 0 to {stats['total_audio_ticks']:.0f} ticks")
    print(f"  Cumulative drift:    0.00 ms (mathematically drift-free)")
    print("-"*65)

    # 4. Generate BBS 1.6 / 2.3+ Texture Track JSON Preset (Stepped)
    texture_json = generate_bbs_texture_json(timed_cues, texture_prefix)
    texture_file = output_dir / f"{stem}_texture_preset.json"
    texture_file.write_text(json.dumps(texture_json, indent=4), encoding="utf-8")
    print(f"[+] BBS Texture Preset (Stepped 'constant'): {texture_file.name}")

    # 5. Generate BBS 1.6 / 2.3+ Pose Track JSON Preset (Smooth)
    pose_file = None
    if POSES_JSON_PATH.exists():
        poses_db = json.loads(POSES_JSON_PATH.read_text(encoding="utf-8"))
        pose_json = generate_bbs_pose_json(timed_cues, poses_db)
        pose_file = output_dir / f"{stem}_pose_preset.json"
        pose_file.write_text(json.dumps(pose_json, indent=4), encoding="utf-8")
        print(f"[+] BBS Pose Preset (Smooth 'cubic_out'):   {pose_file.name}")

    # 6. Generate Legacy Sequencer NBT
    nbt_string = generate_legacy_sequencer_nbt(timed_cues, stats["total_audio_ticks"], legacy_texture_prefix)
    nbt_file = output_dir / f"{stem}_sequencer.nbt.txt"
    nbt_file.write_text(nbt_string, encoding="utf-8")
    print(f"[+] Legacy Sequencer NBT (Blockbuster):     {nbt_file.name}")

    # 7. Generate 2D Pixel-art Mouth Sprites
    sprites = create_pixel_art_mouth_sprites(output_dir, args.character)
    if sprites:
        print(f"[+] Created {len(sprites)} 2D mouth sprite textures in ./mouths/{args.character}/")

    # 8. Auto-Deploy to BBS Instances
    if not args.no_deploy:
        deployed = deploy_to_bbs_instances(texture_file, pose_file, audio_path, sprites)
        if deployed:
            print(f"[+] Auto-deployed presets & audio to BBS instances: {', '.join(deployed)}")

    print("\n" + "="*70)
    print("HOW TO LOAD AND PLAY IN BBS (BLOCKBUSTER STUDIO):")
    print("="*70)
    print("1. In Minecraft, open your BBS Film / Action Editor (Key: 'N' or Film Slate).")
    print("2. Add an Audio Clip on an audio track and select your audio:")
    print(f"   -> File: {audio_path.name} (Duration: {stats['total_audio_ticks']:.0f} ticks)")
    print("3. On your character actor:")
    print("   OPTION A (3D Bone Mouth Rig - Recommended):")
    print("     - Attach the 'player/mouth' model to the head.")
    print("     - Select the mouth's 'pose' track.")
    print("     - Open Presets (P) -> Click: " + (pose_file.name if pose_file else "None"))
    print("   OPTION B (2D Sprite Texture Swapping):")
    print("     - Add a 'Link' (Texture) track.")
    print(f"     - Open Presets (P) -> Click: {texture_file.name}")
    print("4. Press SPACE or Play to watch the actor lip-sync in exact audio sync!")
    print("="*70 + "\n")

if __name__ == "__main__":
    main()
