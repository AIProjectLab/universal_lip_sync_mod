# Universal Lip Sync Mod for Minecraft (Blockbuster Studio)

[![Minecraft 1.21.1](https://img.shields.io/badge/Minecraft-1.21.1-brightgreen.svg)](https://minecraft.net/)
[![Fabric Loader 0.16+](https://img.shields.io/badge/Fabric-0.16.14%2B-blue.svg)](https://fabricmc.net/)
[![BBS Mod 2.3.3](https://img.shields.io/badge/BBS-2.3.3--1.21.1-orange.svg)](https://github.com/mchorse/bbs-mod)
[![Java 21](https://img.shields.io/badge/Java-21-red.svg)](https://adoptium.net/)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-yellow.svg)](https://python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-purple.svg)](LICENSE)

A dedicated, high-performance Fabric client mod and speech engine that automates **lip sync, natural eye blinking, and cadence arm/elbow gestures** for any character or entity inside **BBS (Blockbuster Studio)** on Minecraft 1.21.1.

---

## 🌟 Key Features

- **✨ 1-Click Auto-Fit:**
  - Instantly inspects character models, identifies head/neck joints, and binds dialogue mouth meshes and eyelids in a single click.
  - Zero manual vertex or bone adjustment required for standard and biped rigs.

- **🗣️ Full 6-Channel Viseme Generation:**
  - Synchronizes acoustic speech to 6 animated viseme channels: `Open`, `Closed`, `Inside`, `Up`, `Down`, and `Tongue`.
  - Driven by the Rhubarb Lip Sync engine with PocketSphinx acoustic analysis and phonetic text alignment.

- **👁️ Procedural Natural Eye Blinks:**
  - Automatically attaches and animates 4 eyelid channels: `LidLeft`, `LidRight`, `CreaseLeft`, and `CreaseRight`.
  - Generates realistic, un-choreographed blinking cadence during dialogue and natural speech pauses.

- **💪 Talking Gestures with Shoulder & Elbow Articulation:**
  - Generates cadence gestures based on speech volume and phonetic pacing.
  - Supports both standard 1-bone shoulder arms (`pose/left_arm`, `pose/right_arm`) and advanced 2-bone armatures with forearm elbow joints (`pose/low_left_arm`, `pose/low_right_arm`).

- **🦾 Universal Model Architecture Support:**
  - Works with standard Steve/Alex models, custom Blockbench models, BBS cubic rigs, and binary OBJ (`.bobj`) armatures.

- **⚡ Crash-Safe Asynchronous Rendering:**
  - Native OpenGL render-pass safe: background worker jobs and live film patches are dispatched safely via `MinecraftClient.send(...)`, eliminating driver crashes (`nvoglv64.dll` access violations).

- **🔄 Safe Overlap & Regeneration:**
  - Seamlessly re-generate or overwrite dialogue at the same timeline position without animation collisions or track conflict errors.

- **↩️ Full Native BBS Undo Support:**
  - All generated keyframes and model attachments integrate directly with native BBS film history (press `Ctrl+Z` to undo anytime).

- **🤖 Integrated AI Auto-Rig Assistance:**
  - Deep integration with **Antigravity** for automated model diagnosis, fallback rigging, and custom rig handoffs.

---

## 🏗️ Architecture

```
universal_lip_sync_mod/
├── client_mod/                     Fabric Client Mod (Java 21 / Fabric Loom 1.17.21)
│   ├── src/main/java/              Overlay UI, BridgeJob executor, and EditorPatch
│   │   └── dev/lipsync/studio/
│   │       ├── LipSyncAddon.java   BBS context-menu entrypoint
│   │       ├── DialogueOverlay.java Streamlined 1-Click dialogue UI
│   │       ├── SetupOverlay.java   Custom mouth offset & scale adjuster
│   │       ├── BridgeJob.java      Managed sub-process bridge worker
│   │       └── EditorPatch.java    Crash-safe live film deserializer/patcher
│   └── src/main/resources/         Fabric mod metadata & assets
│
├── speech_engine/                  Speech Analysis & Rigging Engine (Python 3.10+)
│   ├── bin/                        Bundled Rhubarb Lip Sync binary & PocketSphinx acoustic models
│   ├── presets/                    Entity presets (player, biped mobs, villagers, quadrupeds)
│   └── runtime/                    Core Python library & bridge
│       ├── lip_sync_studio/
│       │   ├── bbs_bridge.py       IPC bridge handling inspect, setup, generate, assist
│       │   ├── engine.py           Film patcher, viseme mapper, bobj/bbs armature parser
│       │   ├── gestures.py         Pitch & volume gesture curves
│       │   ├── rigging.py          Bone binding and mesh transform math
│       │   └── rig_assistance.py   Antigravity task handoff packager
│       ├── bbs_storage.py          Native BBS gzipped NBT codec
│       └── add_dialogue_blinks.py  Procedural eyelid blink generator
│
├── tests/                          Comprehensive unit test suite (44 test cases)
│   ├── test_elbow_rig.py           Elbow & armature detection regressions
│   ├── test_lip_sync_studio.py     Full engine & film synthesis tests
│   ├── test_rig_assistance.py      Antigravity handoff & schema validation
│   ├── test_studio_ui.py           Workflow & profile regressions
│   └── test_talking_gestures.py    Gesture cadence & bone mapping tests
│
├── Install BBS Addon.bat           Automated one-click setup & bridge config script
└── README.md                       Project documentation
```

---

## 🛠️ Technologies Used

- **Minecraft Platform:** Minecraft `1.21.1`
- **Mod Loader:** [Fabric Loader](https://fabricmc.net/) `0.16.14+`
- **Build Tool:** [Fabric Loom](https://github.com/FabricMC/fabric-loom) `1.17.21` & Gradle 9.x
- **Animation Framework:** [BBS (Blockbuster Studio)](https://github.com/mchorse/bbs-mod) `2.3.3-1.21.1`
- **Java Runtime:** Java 21 LTS (Temurin / JetBrains Runtime 21)
- **Acoustic Speech Analysis:** [Rhubarb Lip Sync](https://github.com/DanielSWolf/rhubarb-lip-sync) `1.14.0` with CMU PocketSphinx
- **Scripting & Codecs:** Python 3.10 / 3.11 with custom native GZIP-compressed NBT codecs
- **Audio Processing:** 16-bit PCM WAV, FFmpeg audio normalization pipeline

---

## 🤖 Engineering with Antigravity & Skills Utilized

This project was developed, refactored, and stabilized with **Antigravity** using a suite of specialized engineering skills:

- **`fabric-modding-pro`:**
  - Guided Fabric Loom 1.17 configuration, Java 21 compilation toolchains, and BBS 2.3.3 addon entrypoints.
  - Architected the clean UI overlay injection into BBS's film actor context menu without invasive bytecode modifications.

- **`minecraft-renderpearl-glsl` & Render-Pass Safety:**
  - Diagnosed and resolved native OpenGL driver crashes (`nvoglv64.dll` access violations at `nglDrawElements`).
  - Re-engineered `DialogueOverlay` and `SetupOverlay` to safely defer live actor/model state mutations to `MinecraftClient.getInstance().send(...)` instead of modifying geometry mid-render-pass.

- **`modern-java-perf`:**
  - Optimized file I/O and serialization buffers across `BridgeJob` and `EditorPatch` to stream gzipped NBT data without JVM allocation pressure or GC pauses.

- **Autonomous Reverse Engineering & Armature Parsing:**
  - Reverse engineered binary OBJ (`.bobj`) armatures and BBS model group trees to dynamically map shoulder bones (`arm_bone`, `left_arm`, `right_arm`) and elbow joints (`low_left_arm`, `low_right_arm`) across diverse custom model formats.

---

## 🚀 Installation & Setup

### Option 1: One-Click Installer (Recommended)
1. Close Minecraft if it is currently running.
2. Double-click **`Install BBS Addon.bat`** in this folder.
3. The installer automatically locates your BBS Minecraft instance, links the Python speech runtime, and copies `universal-lip-sync-0.1.0.jar` into your `mods/` directory.

### Option 2: Manual Gradle Build
```bash
cd client_mod
# Build Fabric mod jar using Java 21
./gradlew build
# Copy generated jar to your Minecraft mods folder
cp build/libs/universal-lip-sync-0.1.0.jar "<minecraft-instance>/mods/"
```

---

## 🎮 How to Use in BBS

1. **Open BBS Film Editor:** In Minecraft 1.21.1, open any film in your BBS project.
2. **Open Lip Sync Dialog:** Right-click any actor on the left panel or in the 3D scene, then select **`Lip sync…`**.
3. **Capabilities Overview:**
   The status bar will automatically display:
   `✅ Mouth ready for speech | Blinks ready | Elbows ready`
4. **1-Click Auto-Fit:**
   - If setting up a character for the first time, click **`✨ 1-Click Auto-Fit`**. The mouth and eyelids are positioned and attached instantly.
5. **Select Dialogue Audio:**
   - Click **`📂 Choose BBS Audio…`** to pick any sound in your BBS assets, or **`Browse…`** / **`🎵 Use Timeline Audio`** / **`🎙 Record Voice…`**.
6. **Generate & Apply:**
   - Toggle **`Natural eye blinks`** and **`Talking gestures`** as desired.
   - Click **`🚀 Generate & Apply (1-Click)`**.
7. **Preview:** Press the Play button in the BBS film timeline! Use standard `Ctrl+Z` to undo at any time.

---

## 🧪 Testing

The speech engine and bridge includes a test suite covering all core pipelines:

```bash
python -m unittest discover -s tests
```

**Results:**
```text
Ran 44 tests in 10.411s
OK
```

- `test_elbow_rig.py`: Tests 2-bone arm and elbow joint detection across custom rigs.
- `test_lip_sync_studio.py`: Validates mouth visemes, overlap resolution, and audio alignment.
- `test_rig_assistance.py`: Tests Antigravity task bundles, manifests, and return payloads.
- `test_studio_ui.py`: Verifies configuration discovery, instance lookup, and profile persistence.
- `test_talking_gestures.py`: Verifies cadence curves, volume tracking, and bone animation channels.

---

## 📄 License

This project is licensed under the MIT License.
Based on the foundational lip-sync research and Rhubarb speech engine.
