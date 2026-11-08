"""Finish a fixed set of national grid tiles, export, then leave expansion held.

An older producer may keep running. Only pending queue rows are gated while
it runs; world access waits for its normal tile-boundary pause and both locks.
"""
import argparse
from contextlib import closing
import ctypes
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time
import traceback

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import national_backend as n
from autogen import folder_bytes


def phase_counts(root,phase):
    with closing(sqlite3.connect(Path(root)/'queue.sqlite',timeout=30)) as db:
        rows=db.execute('SELECT t.state,COUNT(*) FROM tiles t JOIN phase_tiles p USING(tx,tz) WHERE p.phase=? GROUP BY t.state',(phase,)).fetchall()
    counts=dict(rows);total=sum(counts.values());done=counts.get('done',0)
    return {'total':total,'completed':done,'remaining':total-done,'pending':counts.get('pending',0),'running':counts.get('running',0)}


def arm_phase(root,plan,phase):
    root=Path(root);config=n.load_project(root)
    for key in ('map_id','projection','origin','scale','tile'):
        actual=list(config[key]) if key=='origin' else config[key]
        expected=list(plan[key]) if key=='origin' else plan[key]
        if actual!=expected:
            raise ValueError('Phase grid does not match project: '+key)
    targets={tuple(t) for t in plan['tiles']}
    if len(targets)!=plan['total'] or not targets:raise ValueError('Invalid phase tile list')
    with closing(sqlite3.connect(root/'queue.sqlite',timeout=30)) as db:
        db.execute('BEGIN IMMEDIATE')
        columns={r[1] for r in db.execute('PRAGMA table_info(tiles)')}
        if 'generation_rank' not in columns:db.execute('ALTER TABLE tiles ADD COLUMN generation_rank INTEGER DEFAULT 0')
        if 'generation_distance' not in columns:
            db.execute('ALTER TABLE tiles ADD COLUMN generation_distance INTEGER')
            sx,sz=n.spawn_blocks(config);sx//=config['tile'];sz//=config['tile']
            db.execute('UPDATE tiles SET generation_distance=(tx-?)*(tx-?)+(tz-?)*(tz-?)',(sx,sx,sz,sz))
        db.execute('CREATE TABLE IF NOT EXISTS phase_tiles(phase TEXT,tx INTEGER,tz INTEGER,PRIMARY KEY(phase,tx,tz))')
        prior={tuple(r) for r in db.execute('SELECT tx,tz FROM phase_tiles WHERE phase=?',(phase,))}
        if prior and prior!=targets:raise ValueError('An existing phase cannot silently change its tile list')
        for tx,tz in targets:
            if not db.execute('SELECT 1 FROM tiles WHERE tx=? AND tz=?',(tx,tz)).fetchone():raise ValueError('Phase references a tile outside the national plan')
        db.executemany('INSERT OR IGNORE INTO phase_tiles VALUES(?,?,?)',[(phase,*t) for t in targets])
        # Keep all nationwide rows, their coordinates, and completed chunk data.
        db.execute("UPDATE tiles SET state='deferred_phase' WHERE state='pending' AND NOT EXISTS(SELECT 1 FROM phase_tiles p WHERE p.phase=? AND p.tx=tiles.tx AND p.tz=tiles.tz)",(phase,))
        db.execute("UPDATE tiles SET state='pending',generation_rank=-1 WHERE state IN ('pending','deferred_phase') AND EXISTS(SELECT 1 FROM phase_tiles p WHERE p.phase=? AND p.tx=tiles.tx AND p.tz=tiles.tz)",(phase,))
        db.commit()
    return phase_counts(root,phase)


class PhaseFinish:
    def __init__(self,settings,plan,phase,title,filename):
        self.settings=settings;self.plan=plan;self.phase=phase;self.title=title
        self.base=Path(settings['base']).resolve();self.root=Path(settings['project']).resolve()
        if not self.root.is_relative_to(self.base):raise ValueError('Creator project must be inside base')
        if Path(filename).name!=filename or not filename.endswith('.mcworld'):raise ValueError('Use an export filename, without directories')
        self.output=self.base/'exports'/filename;self.control=self.base/'自動生成'
        self.state={'status':'starting','pid':os.getpid(),'phase':phase,'title':title,'output':str(self.output),'stop_after_export':True}
        self.owns_world=False

    def save(self,**values):
        self.state.update(values,updated=datetime.now().astimezone().isoformat(),progress=phase_counts(self.root,self.phase))
        n.atomic_json(self.control/'地域生成_進捗.json',self.state)
        p=self.state['progress']
        text=f'{self.title}\n状態: {self.state["status"]}\n工程: {self.state.get("stage", "")}\n生成: {p["completed"]:,} / {p["total"]:,} 区画\n更新: {self.state["updated"]}\n'
        text+='縮尺: 1m = 1ブロック / 宝箱・ベッド: 各25% / 地中充填・地質: オン\n完成ファイルの出力後に停止。対象外の計画と生成済みデータは保持します。\n'
        if self.state.get('error'):text+='エラー: '+self.state['error']+'\n'
        if self.state['status']=='complete':text+='完成ファイル: '+str(self.output)+'\n'
        (self.control/'地域生成_進捗.txt').write_text(text,encoding='utf-8-sig')
        if self.owns_world:
            main={**self.state,'settings':self.settings,'national_progress':n.status(self.root)}
            n.atomic_json(self.control/'進捗.json',main)
            (self.control/'進捗.txt').write_text(text,encoding='utf-8-sig')

    def emit(self,kind,**values):
        with (self.control/'実行.log').open('a',encoding='utf-8') as stream:
            stream.write(json.dumps({'time':time.time(),'event':kind,**values},ensure_ascii=False)+'\n')
        self.save(last_event={'event':kind,**values})

    def finish(self):
        self.owns_world=True;ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
        config=n.load_project(self.root)
        config['active_phase']={'id':self.phase,'title':self.title,'total':self.plan['total'],'complete':True,'stop_after_export':True}
        n.atomic_json(self.root/'project.json',config)
        # level.dat stays the same world/seed/frame; only its display name changes.
        import amulet_nbt as nbt
        import struct
        level=self.root/'world/level.dat';raw=level.read_bytes()
        tag=nbt.load(raw[8:],compressed=False,little_endian=True);tag.compound['LevelName']=nbt.StringTag(self.title)
        body=tag.save_to(compressed=False,little_endian=True)
        temporary=level.with_suffix('.phase-writing');temporary.write_bytes(raw[:4]+struct.pack('<I',len(body))+body);os.replace(temporary,level)
        (self.root/'world/levelname.txt').write_text(self.title,encoding='utf-8')
        n.atomic_json(self.root/'world/mapcraft_phase.json',{**config['active_phase'],'wards':self.plan.get('wards',[]),'scope':self.plan.get('scope','grid subset'),'scale':config['scale']})
        n.emit=self.emit
        for attempt in range(1,6):
            try:
                self.save(status='running',stage='宝箱・ベッドを事前配置し、地域ワールドを出力',attempt=attempt,error=None)
                needed=n.status(self.root)['bytes']+4*1024**3
                if folder_bytes(self.base)+needed>self.settings['budget_bytes'] or shutil.disk_usage(self.base).free<needed:
                    raise RuntimeError('完成ファイル用の容量が不足しています。元データは保持しています。')
                self.output.parent.mkdir(exist_ok=True);n.export_world(self.root,self.output)
                self.save(status='complete',stage='地域ワールドの出力完了・ここで区切り',error=None)
                return
            except Exception as error:
                self.save(status='retrying',error=str(error),attempt=attempt)
                if attempt==5:raise
                time.sleep(min(15*attempt,60))

    def generate(self,workers):
        self.owns_world=True;ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
        n.ENGINE=self.base/'engine/arnis-windows.exe'
        os.environ['MAPCRAFT_GEOLOGY_WORKERS']=str(workers)
        os.environ.setdefault('MAPCRAFT_GEOLOGY_CACHE',str(self.base/'cache/geology'))
        n.emit=self.emit
        while phase_counts(self.root,self.phase)['remaining']:
            if (self.root/'pause.request').exists():raise InterruptedError('区画保存後の停止要求を受け付けました。')
            self.save(status='running',stage=f'対象地域を生成中（演算自動判定／CPU最大{workers}並列）')
            for attempt in range(1,6):
                try:
                    before=phase_counts(self.root,self.phase)['completed']
                    n.run_tiles(self.root,1)
                    if phase_counts(self.root,self.phase)['completed']<=before:raise RuntimeError('対象区画の生成が進みませんでした。保存済みデータを保持しています。')
                    self.save(status='running',error=None);break
                except InterruptedError:raise
                except Exception as error:
                    self.save(status='retrying',error=str(error),attempt=attempt)
                    if attempt==5:raise
                    time.sleep(min(15*attempt,60))
        (self.root/'pause.request').write_text('MapCraft phase export: '+self.phase,encoding='utf-8')
        self.finish()

    def run(self,takeover=False,workers=8):
        with n.project_lock(self.control/'phase-controller'):
            arm_phase(self.root,self.plan,self.phase)
            self.save(status='running',stage='対象地域を生成中。完成後に出力して区切り')
            if takeover:
                pause=self.root/'pause.request';intent='MapCraft parallel handoff: '+self.phase
                try:
                    with pause.open('x',encoding='utf-8') as stream:stream.write(intent)
                except FileExistsError:
                    if pause.read_text(encoding='utf-8')!=intent:raise InterruptedError('既存の一時停止要求を保持しています。')
                while True:
                    try:
                        with n.project_lock(self.control),n.project_lock(self.root):
                            if pause.read_text(encoding='utf-8')!=intent:raise InterruptedError('停止要求が変更されています。')
                            pause.unlink();self.generate(workers);return
                    except RuntimeError as error:
                        if '別の生成処理が使用中' not in str(error):raise
                    self.save(status='handoff',stage='現在の区画を保存後、地盤の並列処理へ引き継ぎ')
                    time.sleep(2)
            while True:
                progress=phase_counts(self.root,self.phase)
                # Request a pause BEFORE the last running tile finishes. The
                # producer saves that tile normally, without terminating it.
                if progress['pending']==0:
                    pause=self.root/'pause.request'
                    try:
                        with pause.open('x',encoding='utf-8') as stream:stream.write('MapCraft phase export: '+self.phase)
                    except FileExistsError:pass
                if progress['remaining']==0:
                    try:
                        with n.project_lock(self.control),n.project_lock(self.root):
                            if phase_counts(self.root,self.phase)['remaining']==0:self.finish();return
                    except RuntimeError as error:
                        if '別の生成処理が使用中' not in str(error):raise
                self.save(status='running',stage='対象地域を生成中' if progress['pending'] else '最後の区画を保存後、出力へ移行')
                time.sleep(2 if progress['remaining']<=2 else 15)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--plan',type=Path,required=True)
    p.add_argument('--phase',default='tokyo-23');p.add_argument('--title',default='MapCraft 東京23区（1m）');p.add_argument('--filename',default='MapCraft_東京23区_1m.mcworld')
    p.add_argument('--takeover',action='store_true');p.add_argument('--workers',type=int,default=8,choices=range(1,33))
    args=p.parse_args();settings=json.loads(args.config.read_text(encoding='utf-8-sig'));plan=json.loads(args.plan.read_text(encoding='utf-8-sig'))
    job=PhaseFinish(settings,plan,args.phase,args.title,args.filename)
    try:job.run(takeover=args.takeover,workers=args.workers)
    except Exception as error:
        job.save(status='paused' if isinstance(error,InterruptedError) else 'failed',error=str(error))
        if not isinstance(error,InterruptedError):traceback.print_exc();sys.exit(1)
    finally:ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
