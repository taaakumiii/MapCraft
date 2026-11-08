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
from furniture_backend import VERSION


def process(settings,notify=print,state_filename='家具事前配置.json'):
    base=Path(settings['base']).resolve();root=Path(settings['project']).resolve()
    if not root.is_relative_to(base):raise ValueError('Project must be inside the configured creator base.')
    if Path(state_filename).name!=state_filename:raise ValueError('Use a status filename without directories')
    control=base/'自動生成';state_path=control/state_filename
    state={'status':'waiting','pid':os.getpid(),'project':str(root)}
    active_main=None
    def save(**changes):
        state.update(changes,updated=datetime.now().astimezone().isoformat());n.atomic_json(state_path,state)
        if active_main is not None:
            active_main.update(status=state['status'],stage=state.get('stage','各フロアの宝箱を仕上げ'),pid=os.getpid(),updated=state['updated'],postprocess_phase='per_floor_chests')
            n.atomic_json(control/'進捗.json',active_main)
            p=active_main.get('progress',{})
            current=f'{active_main.get("title","MapCraft")}\n状態: {state["status"]}\n工程: {state.get("stage", "")}\n生成: {p.get("completed",0):,} / {p.get("total",0):,} 区画\n更新: {state["updated"]}\n'
            current+='宝箱は各フロアで独立に抽選。仕上げの後、完成ファイルを出力して停止します。\n'
            if state['status']=='complete':current+='完成ファイル: '+str(active_main['output'])+'\n'
            (control/'進捗.txt').write_text(current,encoding='utf-8-sig')
            phase=control/'地域生成_進捗.json'
            if phase.exists():
                data=json.loads(phase.read_text(encoding='utf-8'));data.update(status=state['status'],stage=state.get('stage'),updated=state['updated'],pid=os.getpid())
                n.atomic_json(phase,data);(control/'地域生成_進捗.txt').write_text(current,encoding='utf-8-sig')
    save(stage='既存の生成が自然に完了するまで待機')
    while True:
        main=json.loads((control/'進捗.json').read_text(encoding='utf-8'))
        if main.get('status')=='complete' or main.get('postprocess_phase')=='per_floor_chests':
            try:
                with n.project_lock(control),n.project_lock(root):
                    # Re-read after both locks: a resumed producer may have won.
                    main=json.loads((control/'進捗.json').read_text(encoding='utf-8'))
                    if main.get('status')!='complete' and main.get('postprocess_phase')!='per_floor_chests':continue
                    cfg=n.load_project(root)
                    output=Path(main['output']).resolve()
                    if not output.is_relative_to(base/'exports') or output.suffix!='.mcworld':
                        raise ValueError('Refusing to replace an export outside the creator exports folder.')
                    if output.exists():
                        with zipfile.ZipFile(output) as archive:
                            meta=json.loads(archive.read('mapcraft_national.json'))
                        if meta['project']['map_id']!=cfg['map_id']:
                            raise ValueError('Export belongs to a different project; keeping it untouched.')
                        if meta['project'].get('furniture_baked') and meta['project'].get('furniture_schema',0)>=VERSION:
                            save(status='complete',stage='事前配置済みの完成ファイルを確認',output=str(output));return
                    ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
                    active_main=main
                    save(status='running',stage='保存済み区画へフロアごとに宝箱を配置')
                    from furniture_backend import ensure_project_furniture
                    def progress(message):
                        notify(message);save(stage=message)
                    summary=ensure_project_furniture(root,progress)
                    save(summary=summary,stage='完成ファイルを再出力')
                    # The creator database is authoritative and remains intact.
                    # Avoid holding TWO giant exports beside the same database.
                    if output.exists():output.unlink()
                    needed=n.status(root)['bytes']+4*1024**3
                    if folder_bytes(base)+needed>settings['budget_bytes'] or shutil.disk_usage(base).free<needed:
                        raise RuntimeError('容量不足です。家具入りの元データを保持しています。出力を再試行してください。')
                    n.export_world(root,output)
                    main.update(status='complete',stage='完成（各フロアの宝箱・ベッド事前配置済み）',output=str(output),furniture=summary,
                                updated=datetime.now().astimezone().isoformat())
                    n.atomic_json(control/'進捗.json',main)
                    with (control/'進捗.txt').open('a',encoding='utf-8-sig') as stream:
                        stream.write('\n宝箱・ベッドの事前配置と完成ファイルの再出力が完了しました。\n')
                    save(status='complete',stage='完了',output=str(output));return
            except RuntimeError as error:
                if '別の生成処理が使用中' not in str(error):raise
        time.sleep(30)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True,type=Path);parser.add_argument('--state-file',default='家具事前配置.json');args=parser.parse_args()
    settings=json.loads(args.config.read_text(encoding='utf-8-sig'))
    try:
        for attempt in range(1,6):
            try:process(settings,state_filename=args.state_file);break
            except Exception:
                if attempt==5:raise
                traceback.print_exc();time.sleep(min(15*attempt,60))
    except Exception as error:
        state=Path(settings['base'])/'自動生成'/args.state_file
        n.atomic_json(state,{'status':'failed','pid':os.getpid(),'error':str(error),'updated':datetime.now().astimezone().isoformat()})
        traceback.print_exc();sys.exit(1)
    finally:ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
