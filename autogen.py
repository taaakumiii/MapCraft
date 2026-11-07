"""Durable unattended, budget-limited creator job. Uses the bundled Python 3.12."""
from contextlib import contextmanager
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
BASE=Path(__file__).resolve().parent
sys.path.insert(0,str(BASE))
import national_backend as n
from national_state import index_info,index_busy,external_index_progress

class SpaceLimit(RuntimeError):pass

def folder_bytes(root):
    return sum(p.stat().st_size for p in Path(root).rglob('*') if p.is_file())

def exported(path,config,completed=None):
    try:
        with zipfile.ZipFile(path) as archive:data=json.loads(archive.read('mapcraft_national.json'))
        return data['project']['map_id']==config['map_id'] and data['project']['scale']==1 and (completed is None or data['progress']['completed']==completed)
    except (OSError,ValueError,KeyError,zipfile.BadZipFile):return False

def make_priority(root,config):
    """Kanto first, then southwest, with distance order inside each group."""
    import numpy as np
    import shapely
    from shapely.geometry import Polygon
    from shapely.ops import unary_union,transform
    root=Path(root);marker=root/'priority-plan.json'
    if marker.exists():return
    groups=[[],[]]
    with n.connect(config['index']) as source:
        for code,blob in source.execute('SELECT code,data FROM prefectures'):
            area=n.unpacked(blob);pref=int(code.split('-')[-1])
            if pref<8:continue
            geometry=unary_union([Polygon(ring) for ring in area['outer']])
            if area['inner']:geometry=geometry.difference(unary_union([Polygon(ring) for ring in area['inner']]))
            groups[0 if 8<=pref<=14 else 1].append(geometry.buffer(0))
    projected=[transform(lambda lon,lat,z=None:n.forward(lat,lon,config['scale']),unary_union(group)) for group in groups]
    tile=config['tile'];sx,sz=n.spawn_blocks(config);sx//=tile;sz//=tile;counts=[0,0,0]
    with n.connect(root/'queue.sqlite') as queue:
        columns={row[1] for row in queue.execute('PRAGMA table_info(tiles)')}
        for column in ('generation_rank','generation_distance'):
            if column not in columns:queue.execute('ALTER TABLE tiles ADD COLUMN '+column+' INTEGER')
        rows=queue.execute('SELECT tx,tz FROM tiles').fetchall()
        for offset in range(0,len(rows),10000):
            batch=np.asarray(rows[offset:offset+10000],dtype=np.int64);x=batch[:,0];z=batch[:,1]
            boxes=shapely.box(x*tile,z*tile,(x+1)*tile,(z+1)*tile)
            rank=np.full(len(batch),2,dtype=int)
            if not projected[1].is_empty:rank[shapely.intersects(boxes,projected[1])]=1
            if not projected[0].is_empty:rank[shapely.intersects(boxes,projected[0])]=0
            distance=(x-sx)**2+(z-sz)**2
            queue.executemany('UPDATE tiles SET generation_rank=?,generation_distance=? WHERE tx=? AND tz=?',
                [(int(r),int(d),int(a),int(b)) for r,d,(a,b) in zip(rank,distance,batch)])
            for r in rank:counts[int(r)]+=1
            queue.commit();n.emit('log',text=f'生成順を準備: {min(offset+10000,len(rows)):,}/{len(rows):,} 区画')
        queue.execute('CREATE INDEX IF NOT EXISTS generation_priority ON tiles(state,generation_rank,generation_distance,tz,tx)');queue.commit()
    n.atomic_json(marker,{'order':['関東1都6県','中部・関西・中国・四国・九州・沖縄','東北・北海道'],'tiles':counts})

class Pipeline:
    def __init__(self,settings):
        self.settings=settings;self.base=Path(settings['base']).resolve();self.root=Path(settings['project']).resolve()
        self.control=self.base/'自動生成';self.control.mkdir(exist_ok=True)
        self.state={'status':'starting','stage':'準備','pid':os.getpid(),'settings':settings,'started':datetime.now().astimezone().isoformat()}
        self.original_emit=n.emit;self.last_event=None;self.owns_lock=False
    def save(self,**values):
        self.state.update(values);self.state['updated']=datetime.now().astimezone().isoformat()
        n.atomic_json(self.control/'進捗.json',self.state)
        self.write_status()
    def write_status(self):
        p=self.state.get('progress',{});text=f"MapCraft 関東中心・等倍 自動生成\n状態: {self.state['status']}\n工程: {self.state['stage']}\n更新: {self.state.get('updated','')}\n"
        text+=f'縮尺: 1m = 1ブロック\n宝箱: 25% / ベッド: 25% / 地中充填・地質: オン\n保存全体の上限: {self.settings["budget_bytes"]/1e12:g} TB（出力用の容量も確保）\n'
        if p:text+=f"生成: {p.get('completed',0):,} / {p.get('total',0):,} 区画\n"
        if self.last_event:text+='現在の処理: '+str(self.last_event)+'\n'
        if self.state.get('error'):text+='直近のエラー: '+self.state['error']+'\n'
        if self.state.get('output'):text+='完成ファイル: '+self.state['output']+'\n'
        temporary=self.control/'進捗.txt.writing';temporary.write_text(text,encoding='utf-8-sig');os.replace(temporary,self.control/'進捗.txt')
    def emit(self,kind,**values):
        event={'event':kind,**values}
        with (self.control/'実行.log').open('a',encoding='utf-8') as stream:stream.write(json.dumps({'time':time.time(),**event},ensure_ascii=False)+'\n')
        if kind in ('index','indexed'):self.state['index_progress']=event
        if kind in ('progress','status'):self.state['progress']=values
        self.last_event=values.get('text',f'{kind}: '+str(values))
        self.save(last_event=event)
    def attempt(self,stage,operation,attempts=5):
        self.save(status='running',stage=stage)
        for attempt in range(1,attempts+1):
            try:
                result=operation();self.state.pop('error',None);return result
            except (SpaceLimit,InterruptedError):raise
            except Exception as error:
                self.save(status='retrying',error=str(error),attempt=attempt)
                self.emit('log',text=f'{stage}: {attempt}/{attempts} 回目の失敗。保存済みの工程から再試行します。')
                if attempt==attempts:raise
                time.sleep(min(15*2**(attempt-1),180))
                self.save(status='running')
    def wait_index(self):
        index=Path(self.settings['index'])
        self.save(stage='全国OSMの索引作成',status='running')
        while index_busy(index):
            progress=external_index_progress(self.base,index)
            if progress and progress!=self.state.get('index_progress'):
                self.emit(progress['event'],**{k:v for k,v in progress.items() if k!='event'})
            time.sleep(3)
        try:index_info(index);return
        except ValueError:pass
        def build():
            with n.project_lock(index.parent):n.index_pbf(self.settings['source'],index,self.root/'pause.request')
        self.attempt('全国OSMの索引作成',build,3)
    def resources(self,progress,batch=8):
        usage=folder_bytes(self.base);free=shutil.disk_usage(self.base).free
        reserve=max(int(progress['bytes']*1.2),4*1024**3)
        allowance=max(self.state.get('last_tile_bytes',0)*3,64*1024**2)*batch
        margin=20*1024**3
        self.save(resource={'bytes_used':usage,'disk_free':free,'export_reserve':reserve,'limit_bytes':self.settings['budget_bytes']})
        return usage+reserve+allowance+margin<self.settings['budget_bytes'] and free>reserve+allowance+margin
    def cap_extent(self,reason):
        cfg=n.load_project(self.root)
        with n.connect(self.root/'queue.sqlite') as queue:
            omitted=queue.execute("SELECT generation_rank,COUNT(*) FROM tiles WHERE state!='done' GROUP BY generation_rank").fetchall()
            queue.execute("UPDATE tiles SET state='omitted_budget' WHERE state!='done'");queue.commit()
            done=queue.execute("SELECT COUNT(*) FROM tiles WHERE state='done'").fetchone()[0]
        cfg.update(original_total=cfg.get('original_total',cfg['total']),total=done,extent_limited=True,
                   extent_limit_reason=reason,omitted_tiles_by_rank=omitted)
        n.atomic_json(self.root/'project.json',cfg)
        self.emit('log',text='容量上限に合わせて生成済みの範囲を確定し、完成ファイルを出力します。')
    def run(self):
        # Prevent unattended system sleep while permitting the display to turn off.
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
        n.emit=self.emit
        try:
            with n.project_lock(self.control):
                self.owns_lock=True
                self.wait_index()
                with n.project_lock(self.root):
                    if not (self.root/'project.json').exists():
                        cfg=self.attempt('全国座標の生成計画',lambda:n.create_project(self.root,self.settings['index'],1.,self.settings['poly'],
                            treasure={'enabled':True,'probability':.25},beds={'enabled':True,'probability':.25},geology={'enabled':True},tile=self.settings.get('tile',1024)))
                        cfg.update(world_name='MapCraft 関東中心（1m）',storage_budget_bytes=self.settings['budget_bytes'],priority='kanto_then_southwest')
                        n.atomic_json(self.root/'project.json',cfg)
                    cfg=n.load_project(self.root)
                    if cfg['scale']!=1:raise ValueError('この自動生成は1m=1ブロックのプロジェクト専用です。')
                    self.attempt('関東を優先する生成順の準備',lambda:make_priority(self.root,cfg))
                    progress=n.status(self.root)
                    if not progress['completed']:
                        self.attempt('東京駅付近の最初の区画',lambda:n.run_tiles(self.root,1))
                        progress=n.status(self.root)
                    # Produce an early playable snapshot while the full build continues.
                    exports=self.base/'exports';exports.mkdir(exist_ok=True)
                    preview=exports/'MapCraft_東京駅開始_1m.mcworld'
                    if not exported(preview,cfg):self.attempt('東京駅付近の試遊ワールド出力',lambda:n.export_world(self.root,preview))
                    while progress['completed']<progress['total']:
                        if (self.root/'pause.request').exists():raise InterruptedError('ユーザーの一時停止要求を受け付けました。')
                        if not self.resources(progress):
                            self.cap_extent('2.5TBの保存上限または完成ファイル用の空き容量確保');progress=n.status(self.root);break
                        before=progress
                        self.attempt('関東中心・周辺へ生成を継続',lambda:n.run_tiles(self.root,8))
                        progress=n.status(self.root)
                        if progress['completed']==before['completed']:raise RuntimeError('未完了の区画がある状態で生成が進みませんでした。進捗は保持しています。')
                        self.state['last_tile_bytes']=max(0,progress['bytes']-before['bytes'])//(progress['completed']-before['completed'])
                        self.save(progress=progress)
                    destination=exports/'MapCraft_関東中心_1m.mcworld'
                    # The upper bound includes an uncompressed-size export reservation.
                    usage=folder_bytes(self.base);need=progress['bytes']+4*1024**3
                    if not exported(destination,cfg,progress['completed']):
                        if usage+need>self.settings['budget_bytes'] or shutil.disk_usage(self.base).free<need:
                            raise SpaceLimit('完成ファイルを安全に出力する容量が不足しています。生成済みデータを保持して停止しました。')
                        self.attempt('完成ワールドと実地図・宝箱・ベッドの出力',lambda:n.export_world(self.root,destination))
                    self.save(status='complete',stage='完成',progress=n.status(self.root),output=str(destination),
                        extent_limited=n.load_project(self.root).get('extent_limited',False))
        except InterruptedError as error:self.save(status='paused',error=str(error))
        except SpaceLimit as error:self.save(status='insufficient_space',error=str(error))
        except Exception as error:
            if not self.owns_lock:
                print('自動生成は既に実行中です。既存の処理を継続します。',flush=True);return
            self.save(status='failed',error=str(error))
            with (self.control/'error.log').open('a',encoding='utf-8') as stream:traceback.print_exc(file=stream)
        finally:
            n.emit=self.original_emit
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description='Run a resumable MapCraft creator job from an explicit configuration.')
    parser.add_argument('--config',type=Path,default=BASE/'config/autogen.json')
    args=parser.parse_args()
    if not args.config.is_file():parser.error('Copy config/autogen.example.json to config/autogen.json and configure your paths first.')
    settings=json.loads(args.config.read_text(encoding='utf-8-sig'))
    for key in ('base','project','index','source','poly'):
        path=Path(settings[key]);settings[key]=str((BASE/path).resolve() if not path.is_absolute() else path.resolve())
    Pipeline(settings).run()
