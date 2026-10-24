"""Install only this companion jar and worker configuration; never alter BBS or films."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile

ROOT=Path(__file__).resolve().parent
PROJECT=ROOT.parent
RUNTIME=PROJECT/'speech_engine/runtime'

def install(instance,dry_run=False):
    instance=Path(instance).resolve()
    original=instance/'mods/bbs-2.3.3-1.21.1.jar'
    candidates=[ROOT/'build/libs/universal-lip-sync-0.1.0.jar',ROOT/'build/lip-sync-studio-0.1.0.jar']
    archive=next((p for p in candidates if p.is_file()),candidates[0])
    if not original.is_file() or not archive.is_file():raise ValueError('Build the add-on for BBS 2.3.3 first (run gradlew build).')
    with zipfile.ZipFile(original) as z:
        if json.loads(z.read('fabric.mod.json'))['version']!='2.3.3-1.21.1':raise ValueError('Unsupported BBS build.')
    digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    worker=RUNTIME/'lip_sync_studio/bbs_bridge.py'
    if not worker.is_file():raise ValueError('The local worker is missing at '+str(worker))
    destination=instance/'mods'/archive.name
    config=instance/'config/lip_sync_studio/bridge.json'
    settings={'schema':1,'project_directory':str(RUNTIME),'python_executable':sys.executable,
              'bbs_sha256':digest(original),'addon_sha256':digest(archive)}
    plan={'addon':str(destination),'config':str(config),'settings':settings,'restart_required':True}
    if dry_run:return plan
    # Preserve earlier companion/config versions outside the mods scan path.
    backup=instance/'config/lip_sync_studio/backups'/datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    for path in (destination,config):
        if path.exists():
            backup.mkdir(parents=True,exist_ok=True);shutil.copy2(path,backup/path.name)
    destination.parent.mkdir(parents=True,exist_ok=True);config.parent.mkdir(parents=True,exist_ok=True)
    temporary=destination.with_suffix('.jar.tmp');shutil.copy2(archive,temporary);temporary.replace(destination)
    temporary=config.with_suffix('.json.tmp');temporary.write_text(json.dumps(settings,indent=2)+'\n',encoding='utf-8');temporary.replace(config)
    if digest(destination)!=digest(archive):raise ValueError('Installed jar verification failed.')
    return plan

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--instance',type=Path,default=Path(r'C:\Users\shafe\curseforge\minecraft\Instances\BBS 1.21.1'))
    parser.add_argument('--dry-run',action='store_true');args=parser.parse_args()
    print(json.dumps(install(args.instance,args.dry_run),indent=2))
if __name__=='__main__':main()
