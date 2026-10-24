"""Compile directly against the user's exact intermediary BBS/Minecraft jars, offline."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

ROOT=Path(__file__).resolve().parent
def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--instance',type=Path,default=Path(r'C:\Users\shafe\curseforge\minecraft\Instances\BBS 1.21.1'))
    parser.add_argument('--jdk',type=Path,default=Path(r'C:\Users\shafe\.gradle\jdks\eclipse_adoptium-21-amd64-windows.2'))
    args=parser.parse_args()
    jar=args.instance/'mods/bbs-2.3.3-1.21.1.jar'
    if not jar.is_file():raise SystemExit('This build targets bbs-2.3.3-1.21.1.jar.')
    library=args.instance.parents[1]/'Install/libraries'
    # CurseForge places Install beside Instances, not inside a specific instance.
    if not library.is_dir():library=Path.home()/'curseforge/minecraft/Install/libraries'
    classpath=[jar,*args.instance.joinpath('.fabric/remappedJars').rglob('client-intermediary.jar'),
               *args.instance.joinpath('.fabric/processedMods').glob('*.jar'),
               *(p for p in library.rglob('*.jar') if zipfile.is_zipfile(p))]
    output=ROOT/'build/classes';output.mkdir(parents=True,exist_ok=True)
    sources=sorted((ROOT/'src/main/java').rglob('*.java'))
    arguments=['--release','21','-proc:none','-encoding','UTF-8','-classpath',';'.join(str(p) for p in classpath),'-d',str(output),*map(str,sources)]
    argfile=ROOT/'build/javac.args'
    argfile.write_text('\n'.join('"'+a.replace('\\','/').replace('"','\\"')+'"' for a in arguments),encoding='utf-8')
    subprocess.run([str(args.jdk/'bin/javac.exe'),'@'+str(argfile)],check=True)
    archive=ROOT/'build/lip-sync-studio-0.1.0.jar'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for folder in (output,ROOT/'src/main/resources'):
            for file in sorted(folder.rglob('*')):
                if file.is_file():z.write(file,file.relative_to(folder).as_posix())
    manifest={'jar':str(archive),'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
              'bbs_sha256':hashlib.sha256(jar.read_bytes()).hexdigest(),'sources':len(sources),'java':21}
    (ROOT/'build/build.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(manifest,indent=2))
if __name__=='__main__':main()
