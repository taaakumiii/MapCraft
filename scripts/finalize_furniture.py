"""Queue native furniture after an older unattended build finishes naturally.

Run with the bundled runtime, from a separate copy of the new creator source.
It never opens the build database while the old producer owns its locks.
"""
import argparse
import ctypes
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import sys
import time
import traceback
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import national_backend as n
from autogen import folder_bytes


def process(settings,notify=print):
    base=Path(settings['base']).resolve();root=Path(settings['project']).resolve()
    if not root.is_relative_to(base):raise ValueError('Project must be inside the configured creator base.')
    control=base/'自動生成';state_path=control/'家具事前配置.json'
    state={'status':'waiting','pid':os.getpid(),'project':str(root)}
    def save(**changes):
        state.update(changes,updated=datetime.now().astimezone().isoformat());n.atomic_json(state_path,state)
    save(stage='既存の生成が自然に完了するまで待機')
    while True:
        main=json.loads((control/'進捗.json').read_text(encoding='utf-8'))
        if main.get('status')=='complete':
            try:
                with n.project_lock(control),n.project_lock(root):
                    # Re-read after both locks: a resumed producer may have won.
                    main=json.loads((control/'進捗.json').read_text(encoding='utf-8'))
                    if main.get('status')!='complete':continue
                    cfg=n.load_project(root)
                    output=Path(main['output']).resolve()
                    if not output.is_relative_to(base/'exports') or output.suffix!='.mcworld':
                        raise ValueError('Refusing to replace an export outside the creator exports folder.')
                    if output.exists():
                        with zipfile.ZipFile(output) as archive:
                            meta=json.loads(archive.read('mapcraft_national.json'))
                        if meta['project']['map_id']!=cfg['map_id']:
                            raise ValueError('Export belongs to a different project; keeping it untouched.')
                        if meta['project'].get('furniture_baked'):
                            save(status='complete',stage='事前配置済みの完成ファイルを確認',output=str(output));return
                    ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
                    save(status='running',stage='保存済み区画へ宝箱・ベッドを事前配置')
                    from furniture_backend import ensure_project_furniture
                    summary=ensure_project_furniture(root,notify)
                    save(summary=summary,stage='完成ファイルを再出力')
                    # The creator database is authoritative and remains intact.
                    # Avoid holding TWO giant exports beside the same database.
                    if output.exists():output.unlink()
                    needed=n.status(root)['bytes']+4*1024**3
                    if folder_bytes(base)+needed>settings['budget_bytes'] or shutil.disk_usage(base).free<needed:
                        raise RuntimeError('容量不足です。家具入りの元データを保持しています。出力を再試行してください。')
                    n.export_world(root,output)
                    main.update(status='complete',stage='完成（宝箱・ベッド事前配置済み）',output=str(output),furniture=summary,
                                updated=datetime.now().astimezone().isoformat())
                    n.atomic_json(control/'進捗.json',main)
                    with (control/'進捗.txt').open('a',encoding='utf-8-sig') as stream:
                        stream.write('\n宝箱・ベッドの事前配置と完成ファイルの再出力が完了しました。\n')
                    save(status='complete',stage='完了',output=str(output));return
            except RuntimeError as error:
                if '別の生成処理が使用中' not in str(error):raise
        time.sleep(30)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True,type=Path);args=parser.parse_args()
    settings=json.loads(args.config.read_text(encoding='utf-8-sig'))
    try:process(settings)
    except Exception as error:
        state=Path(settings['base'])/'自動生成/家具事前配置.json'
        n.atomic_json(state,{'status':'failed','pid':os.getpid(),'error':str(error),'updated':datetime.now().astimezone().isoformat()})
        traceback.print_exc();sys.exit(1)
    finally:ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
