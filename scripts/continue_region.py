"""Freeze a verified completed phase, then extend the same creator project.

The playable archive is a physical copy. Installed/played Minecraft worlds
are never opened, and the next phase starts only after snapshot verification.
"""
import argparse
from contextlib import closing
import ctypes
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time
import traceback
import uuid
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import national_backend as n
from autogen import folder_bytes
from scripts.finish_phase import PhaseFinish,arm_phase,phase_counts

def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def validate_request(request):
    base=Path(request['base']).resolve();root=Path(request['project']).resolve();snapshot=Path(request['snapshot']).resolve()
    if root==base or not root.is_relative_to(base):raise ValueError('作成用プロジェクトが指定フォルダー外です。')
    if not snapshot.is_relative_to(base/'完成ワールド') or snapshot.suffix!='.mcworld':raise ValueError('プレイ用保存先が不正です。')
    if request['from_phase']==request['phase']:raise ValueError('元のフェーズと拡張先は別のIDにしてください。')
    config=n.load_project(root);plan=read(request['plan'])
    for key in ('map_id','projection','origin','scale','tile'):
        if config[key]!=request[key] or config[key]!=plan[key]:raise ValueError('ワールドのID・縮尺・座標系が一致しません: '+key)
    if digest(request['plan'])!=request['plan_sha256']:raise ValueError('拡張計画が変更されています。')
    if len({tuple(v) for v in plan['tiles']})!=plan['total']:raise ValueError('拡張区画の数が不正です。')
    if not 1<=request.get('workers',8)<=32:raise ValueError('CPU並列数が不正です。')
    for name in request['wait_states']:
        if Path(name).name!=name:raise ValueError('待機状態ファイル名が不正です。')
    if Path(request['filename']).name!=request['filename'] or not request['filename'].endswith('.mcworld'):raise ValueError('出力ファイル名が不正です。')
    return base,root,snapshot,config,plan

def ready(request,main):
    if main.get('phase')!=request['from_phase']:return False
    if main.get('status')!='complete' or main.get('postprocess_phase')!='native_ecology':return False
    for name in request['wait_states']:
        state=read(Path(request['base'])/'自動生成'/name)
        if state.get('status')!='complete':return False
    return True

def verify_completed_archive(path,request):
    with zipfile.ZipFile(path) as archive:
        meta=json.loads(archive.read('mapcraft_national.json'))
        if meta['project']['map_id']!=request['map_id'] or meta['progress']['completed']!=request['from_completed']:
            raise ValueError('完成アーカイブのワールドIDまたは区画数が一致しません。')
        phase=json.loads(archive.read('mapcraft_phase.json'))
        if phase['id']!=request['from_phase'] or not phase.get('complete'):raise ValueError('対象の23区版が完成していません。')
        ecology=json.loads(archive.read('mapcraft_ecology.json'))
        if ecology.get('options')!=request['mobs'] or ecology.get('runtime_pack') is not False:raise ValueError('動物・ボス・ビーコンの反映完了を確認できません。')
        villagers=json.loads(archive.read('mapcraft_villagers.json'))
        bathymetry=json.loads(archive.read('mapcraft_bathymetry.json'))
        if villagers['options']!=request['villagers'] or bathymetry['options']!=request['bathymetry']:
            raise ValueError('村人・海底の反映完了を確認できません。')
        if 'level.dat' not in archive.namelist() or 'db/CURRENT' not in archive.namelist():raise ValueError('プレイ用ワールドの構成が不足しています。')
    return meta

def freeze_archive(source,destination,request,notify=print):
    source=Path(source).resolve();destination=Path(destination).resolve()
    if source==destination:raise ValueError('プレイ用は別の場所へ保存してください。')
    verify_completed_archive(source,request)
    destination.parent.mkdir(parents=True,exist_ok=True)
    source_digest=digest(source)
    if destination.exists():
        if digest(destination)==source_digest:return {'path':str(destination),'sha256':source_digest,'bytes':destination.stat().st_size}
        # Keep an existing playable file even if it was independently replaced.
        stem=destination.stem
        for index in range(2,10000):
            candidate=destination.with_name(f'{stem}_{index}.mcworld')
            if not candidate.exists():destination=candidate;break
        else:raise ValueError('未使用のプレイ用ファイル名を確保できません。')
    before=source.stat();temporary=destination.with_name(destination.name+'.'+uuid.uuid4().hex+'.copying')
    copied=0;last=0;h=hashlib.sha256()
    try:
        with source.open('rb') as input_file,temporary.open('xb') as output:
            while chunk:=input_file.read(8*1024**2):
                output.write(chunk);h.update(chunk);copied+=len(chunk)
                if copied-last>=512*1024**2:notify(f'23区完成版を別保存: {copied/1024**3:.1f}/{before.st_size/1024**3:.1f} GiB');last=copied
            output.flush();os.fsync(output.fileno())
        after=source.stat()
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns) or h.hexdigest()!=source_digest or digest(temporary)!=source_digest:
            raise ValueError('コピーの整合性を確認できません。拡張を開始しません。')
        with zipfile.ZipFile(temporary) as archive:
            error=archive.testzip()
            if error:raise ValueError('完成版ZIPの検証に失敗しました: '+error)
        verify_completed_archive(temporary,request)
        if destination.exists():raise FileExistsError('プレイ用ファイルが作成されたため上書きを避けます。')
        os.rename(temporary,destination)
        return {'path':str(destination),'sha256':source_digest,'bytes':destination.stat().st_size}
    finally:temporary.unlink(missing_ok=True)

class Continuation:
    def __init__(self,request):
        self.request=request;self.base,self.root,self.snapshot,self.config,self.plan=validate_request(request)
        self.control=self.base/'自動生成';self.marker=self.control/request.get('state_file','東京都拡張_進捗.json')
        if self.marker.parent!=self.control:raise ValueError('状態保存先が不正です。')
        self.state=read(self.marker) if self.marker.exists() else {}
        if self.state and self.state.get('request_id')!=request['request_id']:raise ValueError('別の継続処理の状態を上書きしません。')
        self.state.update(pid=os.getpid(),request_id=request['request_id'],map_id=request['map_id'],phase=request['phase'])
    def save(self,**values):
        self.state.update(values,updated=datetime.now().astimezone().isoformat());n.atomic_json(self.marker,self.state)
    def capacity(self,additional=0):
        world_bytes=folder_bytes(self.root/'world');reserve=world_bytes+additional+4*1024**3
        if folder_bytes(self.base)+reserve>self.request['budget_bytes'] or shutil.disk_usage(self.base).free<reserve:
            raise RuntimeError('完成ファイル用の空きを確保できないため拡張を保留します。23区版と生成済みデータは保持しています。')
    def transition(self,main):
        request=self.request
        saved=self.state.get('snapshot')
        if saved:
            if not Path(saved['path']).exists() or digest(saved['path'])!=saved['sha256']:raise ValueError('保存済みの23区完成版を検証できません。拡張を保留します。')
        else:
            if not ready(request,main):raise ValueError('23区版の完成待ちです。')
            source=Path(main['output']).resolve()
            if not source.is_relative_to(self.base/'exports'):raise ValueError('元の完成ファイルが作成用出力先の外です。')
            self.capacity(source.stat().st_size)
            self.save(status='saving_snapshot',stage='全要素を反映した23区完成版を検証して別保存')
            saved=freeze_archive(source,self.snapshot,request,lambda message:self.save(stage=message))
            self.save(snapshot=saved,status='snapshot_saved',stage='23区完成版の別保存・整合性確認が完了')
        with closing(n.connect(self.root/'queue.sqlite')) as queue:
            completed={tuple(row) for row in queue.execute("SELECT tx,tz FROM tiles WHERE state='done'")}
        if not completed<={tuple(t) for t in self.plan['tiles']}:raise ValueError('計画外の生成済み区画を保持して停止します。')
        config=n.load_project(self.root)
        if main.get('phase') not in (request['from_phase'],request['phase']):raise ValueError('別の生成フェーズが開始されています。')
        pause=self.root/'pause.request'
        if pause.exists():
            allowed={'MapCraft phase export: '+request['from_phase']}
            if main.get('phase')==request['phase'] and phase_counts(self.root,request['phase'])['remaining']==0:
                allowed.add('MapCraft phase export: '+request['phase'])
            if pause.read_text(encoding='utf-8').strip() not in allowed:raise InterruptedError('既存の停止要求を保持しています。')
        arm_phase(self.root,self.plan,request['phase'])
        config['active_phase']={'id':request['phase'],'title':request['title'],'total':self.plan['total'],'complete':False,'stop_after_export':True}
        config['world_name']=request['title'];n.atomic_json(self.root/'project.json',config)
        n.atomic_json(self.root/'world/mapcraft_scope.json',{'scope':self.plan['scope'],'source':self.plan.get('source'),'snapshot':saved})
        if self.plan.get('source'):
            source=self.plan['source'];(self.root/'world/SCOPE-CREDITS.txt').write_text(f"出典：{source['name']}\n{source['url']}\n{source['license']}\nMapCraftの座標系へ投影し、沿岸を約500m含めた区画範囲に加工。元の23区版の周辺区画も保持。\n",encoding='utf-8')
        if pause.exists():pause.unlink()
        self.save(status='running',stage='保存済み23区を引き継ぎ、東京都の未生成区画を追加',snapshot=saved,progress=phase_counts(self.root,request['phase']))
        settings=read(request['settings']);settings.update(priority='tokyo_prefecture_land_and_coast',budget_bytes=request['budget_bytes'])
        job=PhaseFinish(settings,self.plan,request['phase'],request['title'],request['filename'])
        original=n.run_tiles
        def guarded(*args,**kwargs):self.capacity();return original(*args,**kwargs)
        n.run_tiles=guarded
        try:job.generate(request.get('workers',8))
        except Exception as error:
            job.save(status='paused' if isinstance(error,InterruptedError) else 'failed',error=str(error));raise
        finally:n.run_tiles=original
        self.save(status='complete',stage='東京都全域の出力完了',output=str(job.output),snapshot=saved,progress=phase_counts(self.root,request['phase']))
    def run(self,once=False):
        request=self.request
        with n.project_lock(self.control/'region-continuation-controller'):
            self.save(status='waiting',stage='23区版の海底・村人・動物・ボス・ビーコン・出力の完了待ち',planned_total=self.plan['total'],planned_additional=self.plan['additional'],snapshot_path=str(self.snapshot))
            while True:
                main=read(self.control/'進捗.json')
                resumable=self.state.get('snapshot') and main.get('phase')==request['phase'] and main.get('status') not in ('complete',)
                if ready(request,main) or resumable:
                    try:
                        with n.project_lock(self.control/'phase-controller'),n.project_lock(self.control),n.project_lock(self.root):
                            main=read(self.control/'進捗.json')
                            if ready(request,main) or (self.state.get('snapshot') and main.get('phase')==request['phase']):
                                ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
                                self.transition(main);return
                    except RuntimeError as error:
                        if '別の生成処理が使用中' not in str(error):raise
                if self.state.get('snapshot') and main.get('phase')==request['phase'] and main.get('status')=='complete':
                    self.save(status='complete',stage='東京都全域の完成出力を確認',output=main['output']);return
                if once:return
                time.sleep(15)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--request',required=True,type=Path);parser.add_argument('--once',action='store_true');args=parser.parse_args()
    job=Continuation(read(args.request))
    try:
        for attempt in range(1,6):
            try:job.run(once=args.once);break
            except InterruptedError:raise
            except Exception as error:
                job.save(status='retrying',error=str(error),attempt=attempt)
                if attempt==5 or args.once:raise
                traceback.print_exc();time.sleep(min(15*attempt,60))
    except Exception as error:
        job.save(status='paused' if isinstance(error,InterruptedError) else 'failed',error=str(error));traceback.print_exc();sys.exit(1)
    finally:ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
