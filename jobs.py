"""Stage checkpoints and safe retry for ordinary MapCraft generation jobs."""
from contextlib import contextmanager
from dataclasses import asdict
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import zipfile
from core import ENGINE_SHA256,find_worlds,validate_bbox
from jobcache import atomic_json,source_hash

STAGES={'validate':'保存情報の確認','engine':'地形・建物の生成','bathymetry':'実際の海底地形の反映','geology':'公的地盤データの反映','furniture':'宝箱・ベッドをワールドへ事前配置','map':'実地図の作成','boundaries':'市町村境界の準備',
        'treasure':'チェストの準備','beds':'ベッドの準備','addon':'アドオンの作成',
        'embed':'ワールドへの組み込み','metadata':'生成情報の保存','complete':'完了'}
class JobBusy(RuntimeError):pass

@contextmanager
def job_lock(folder):
    import msvcrt
    folder=Path(folder)
    with (folder/'generation.lock').open('a+b') as lock:
        if lock.tell()==0:lock.write(b'0');lock.flush()
        lock.seek(0)
        try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:raise JobBusy('この生成は別の処理で使用中です。完了または停止してから再試行してください。')
        try:yield
        finally:lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)

def load_job(folder):
    folder=Path(folder)
    data=json.loads((folder/'generation.json').read_text(encoding='utf-8-sig'))
    validate_bbox(data['bbox'])
    if data.get('edition') not in ('java','bedrock'):raise ValueError('MapCraftの生成情報ではありません。')
    if not isinstance(data.get('command'),list) or not data['command']:raise ValueError('生成コマンドが保存されていません。')
    if not math.isfinite(float(data['scale'])) or not .3<=float(data['scale'])<=4:raise ValueError('保存された縮尺が不正です。')
    return data

def ensure_legacy_idle(data):
    # Pre-0.11 apps did not take a job lock. Avoid concurrently writing a
    # legacy job still marked running while that exact old app is open.
    if data.get('job_schema')==1 or data.get('status')!='running':return
    version=str(data.get('app','')).removeprefix('MapCraft ').strip()
    if not re.fullmatch(r'\d+\.\d+(?:\.\d+)?',version):return
    name='MapCraft-v'+version+'.exe'
    result=subprocess.run(['tasklist','/FI','IMAGENAME eq '+name,'/FO','CSV','/NH'],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=10)
    rows=csv.reader(io.StringIO(result.stdout.decode('utf-8',errors='replace')))
    if any(row and row[0].lower()==name.lower() for row in rows):
        raise JobBusy('旧版のMapCraftが起動中で、この生成は処理中として保存されています。元のアプリで処理を停止して閉じてから再試行してください。')

def normalized_command(data,folder,engine):
    """Use the saved options, but never launch an executable from a manifest."""
    values={'--bbox','--output-dir','--scale','--mode','--height-multiplier','--overture','--canopy-height',
            '--spawn-lat','--spawn-lng','--gamemode','--map-item','--ground-level','--save-json-file','--file'}
    flags={'--bedrock','--no-3d','--vanilla-height','--fillground'}
    args=[str(Path(engine).resolve())];saved=data['command'][1:];i=0
    while i<len(saved):
        token=saved[i];key,equals,value=token.partition('=')
        if key in flags:
            if equals:raise ValueError('保存コマンドのフラグ形式が不正です。')
            args.append(key);i+=1;continue
        if key not in values:raise ValueError('再試行に対応していない生成オプションです: '+key)
        if not equals:
            if i+1>=len(saved):raise ValueError('保存コマンドに値がありません: '+key)
            value=saved[i+1];i+=1
        if key=='--output-dir':value=str(Path(folder).resolve())
        if key=='--save-json-file':value=str(Path(folder).resolve()/'osm.json')
        args.extend([key,value]);i+=1
    if '--output-dir' not in args:raise ValueError('生成先が保存されていません。')
    if '--bbox' not in args:raise ValueError('範囲が保存されていません。')
    bbox=validate_bbox([float(v) for v in args[args.index('--bbox')+1].split(',')])
    if any(abs(a-b)>2e-7 for a,b in zip(bbox,data['bbox'])):raise ValueError('保存された範囲とコマンドが一致しません。')
    if ('--bedrock' in args)!=(data['edition']=='bedrock'):raise ValueError('保存された出力形式が一致しません。')
    return args

def cached_osm_command(args,folder):
    source=Path(folder)/'osm.json'
    try:
        with source.open(encoding='utf-8') as stream:
            data=json.load(stream);valid=isinstance(data.get('elements'),list) and not data.get('remark')
    except (OSError,ValueError,AttributeError):valid=False
    if not valid:return args,False
    result=[];i=0
    while i<len(args):
        if args[i] in ('--file','--save-json-file'):i+=2;continue
        result.append(args[i]);i+=1
    return result+['--file',str(source.resolve())],True

def engine_key(args):
    values=[];i=1
    while i<len(args):
        if args[i] in ('--output-dir','--save-json-file','--file'):i+=2;continue
        values.append(args[i]);i+=1
    return hashlib.sha256(json.dumps(values,separators=(',',':')).encode()).hexdigest()

def valid_world(path,data):
    try:
        if data['edition']=='java':return (path/'level.dat').is_file() and (path/'region').is_dir()
        with zipfile.ZipFile(path) as archive:
            names=archive.namelist()
            if 'level.dat' not in names or 'db/CURRENT' not in names:return False
            meta=json.loads(archive.read('metadata.json'))
            bbox=[meta[k] for k in ('minGeoLat','minGeoLon','maxGeoLat','maxGeoLon')]
            return meta.get('format')=='bedrock-mcworld' and meta.get('projection')=='local' and abs(float(meta['scale'])-data['scale'])<1e-9 and all(abs(a-b)<2e-7 for a,b in zip(bbox,data['bbox']))
    except (OSError,ValueError,KeyError,zipfile.BadZipFile):return False

def list_jobs(roots):
    paths=set()
    for root in roots:
        if root and Path(root).is_dir():paths.update(Path(root).resolve().glob('*/generation.json'))
    result=[]
    for path in paths:
        try:
            data=load_job(path.parent)
            result.append((path.parent,data,path.stat().st_mtime))
        except (OSError,ValueError,KeyError,TypeError):continue
    return sorted(result,key=lambda row:row[2],reverse=True)

class GenerationJob:
    def __init__(self,folder,engine,*,cancelled=None,notify=None,process_changed=None):
        self.folder=Path(folder).resolve();self.engine=Path(engine).resolve()
        self.cancelled=cancelled or threading.Event();self.notify=notify or (lambda kind,value:None)
        self.process_changed=process_changed or (lambda process:None)
        self.active_process=None

    def save(self):atomic_json(self.folder/'generation.json',self.data)
    def log(self,text):
        with (self.folder/'generation.log').open('a',encoding='utf-8') as stream:stream.write(text);stream.flush()
        self.notify('log',text)
    def check(self):
        if self.cancelled.is_set():raise InterruptedError('中止しました。保存済みの工程から再試行できます。')
    def stage(self,name):
        self.check();self.data['stage']=name;self.save();self.notify('stage',STAGES[name]);self.log('\n[MapCraft] '+STAGES[name]+'\n')

    def engine_worlds(self):
        data=self.data;checkpoint=data.get('engine_checkpoint',{})
        if checkpoint.get('complete'):
            if checkpoint.get('settings') and checkpoint['settings']!=self.engine_signature:
                raise ValueError('生成後に元の設定が変更されています。設定を変える場合は、新しい生成を開始してください。')
            paths=[self.folder/name for name in checkpoint['files']]
            for path in paths:
                if path.parent!=self.folder or not valid_world(path,data):raise ValueError('保存済みの完成ワールドを確認できません。元のファイルを同じ保存先へ戻してください。')
            return paths
        # Adopt completed outputs from older versions, including jobs whose
        # app stopped after Arnis finished but before writing its return code.
        log=self.folder/'generation.log'
        content=log.read_text(encoding='utf-8',errors='replace') if log.exists() else ''
        finished=data.get('returncode')==0 or 'Done! Bedrock world saved to:' in content or (data['edition']=='java' and 'Done!' in content)
        candidates=find_worlds(self.folder,data['edition']) if finished else []
        paths=[p for p in candidates if valid_world(p,data)]
        if candidates and not paths:raise ValueError('保存済みワールドと生成情報が一致しません。上書きせず停止しました。')
        return paths

    def run(self):
        with job_lock(self.folder):
            self.data=load_job(self.folder);ensure_legacy_idle(self.data)
            started=time.monotonic();previous=self.data.get('elapsed_seconds',0)
            self.data.update(job_schema=1,status='running',owner_pid=os.getpid(),attempt=self.data.get('attempt',0)+1)
            self.data.pop('error',None);self.save()
            self.log('\n── 再開可能な生成 '+str(self.data['attempt'])+'回目 '+time.strftime('%Y-%m-%d %H:%M:%S')+' ──\n')
            worlds=[]
            try:
                self.stage('validate')
                args=normalized_command(self.data,self.folder,self.engine)
                self.engine_signature=engine_key(args)
                if source_hash(self.engine)!=ENGINE_SHA256:raise ValueError('生成エンジンのハッシュが一致しません。')
                self.stage('engine');worlds=self.engine_worlds()
                if worlds:self.log('完成済みの地形・建物を再利用します。生成エンジンは実行しません。\n')
                else:
                    args,reused=cached_osm_command(args,self.folder)
                    if reused:self.log('保存済みのOSMデータを使用します。地図の再取得を省略します。\n')
                    self.data['last_engine_command']=args;self.save();self.check()
                    process=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,cwd=str(self.engine.parent.parent),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    self.active_process=process
                    self.process_changed(process)
                    if self.cancelled.is_set():process.terminate()
                    for raw in process.stdout:
                        line=re.sub(r'\x1b\[[0-9;?]*[a-zA-Z]','',raw.decode('utf-8',errors='replace'));self.log(line)
                    code=process.wait();self.active_process=None;self.data['returncode']=code;self.process_changed(None);self.save();self.check()
                    if code:raise RuntimeError(f'生成エンジンがエラーで終了しました（終了コード {code}）。')
                    worlds=[p for p in find_worlds(self.folder,self.data['edition']) if valid_world(p,self.data)]
                    if not worlds:raise RuntimeError('完成ワールドを確認できませんでした。ログを確認してください。')
                # Commit before any network request / expensive gameplay work.
                self.data['engine_checkpoint']={'complete':True,'files':[p.name for p in worlds],'settings':self.engine_signature};self.data['returncode']=0;self.save()
                if self.data['edition']=='bedrock':self.prepare_bedrock(worlds)
                self.check();self.data['status']='complete';self.data['stage']='complete'
                self.data['worlds']=[str(p) for p in worlds]
            except Exception as error:
                self.data['status']='cancelled' if isinstance(error,InterruptedError) or self.cancelled.is_set() else 'failed'
                self.data['error']={'stage':self.data.get('stage','engine'),'message':str(error),'time':time.time()}
                self.log('\n[MapCraft] '+str(error)+'\n保存済みデータは保持しました。「履歴・再試行」から続けられます。\n')
            finally:
                if self.active_process is not None and self.active_process.poll() is None:
                    self.active_process.terminate()
                    try:self.active_process.wait(timeout=10)
                    except subprocess.TimeoutExpired:self.active_process.kill();self.active_process.wait()
                self.process_changed(None)
                seconds=round(time.monotonic()-started);self.data['elapsed_seconds']=previous+seconds
                self.data.setdefault('attempts',[]).append({'attempt':self.data['attempt'],'seconds':seconds,'status':self.data['status'],'stage':self.data.get('stage'),'error':self.data.get('error')})
                self.save()
            return self.data['status'],worlds if self.data['status']=='complete' else []

    def prepare_bedrock(self,worlds):
        from tracking import MapReference
        from realmap import prepare_map
        from municipalities import prepare_boundaries
        from treasure import prepare_treasure
        from beds import prepare_beds
        from nativepack import write_addon,embed_native_pack,read_addon_bundle,VERSION
        reference=MapReference.from_generation(self.data,self.folder.name)
        if self.data.get('bathymetry',{}).get('enabled'):
            self.stage('bathymetry')
            import sys
            base=Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent
            runtime=base/'engine/national-runtime/python.exe';request=self.folder/'bathymetry-request.json'
            for world in worlds:
                atomic_json(request,{'world':str(world),'reference':asdict(reference),'cache':str(base/'cache/bathymetry'),'settings':self.data['bathymetry'],'osm':str(self.folder/'osm.json')})
                process=subprocess.Popen([str(runtime),str(base/'bathymetry_backend.py'),'--request',str(request)],env={**os.environ,'PYTHONIOENCODING':'utf-8'},stdout=subprocess.PIPE,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),cwd=str(base))
                self.active_process=process;self.process_changed(process)
                for raw in process.stdout:self.check();self.log(raw.decode('utf-8',errors='replace'))
                code=process.wait();self.active_process=None;self.process_changed(None);self.check()
                if code:raise RuntimeError('海底地形の反映に失敗しました。元の地形を保持しています。再試行できます。')
        if self.data.get('geology',{}).get('enabled') and self.data.get('fillground'):
            self.stage('geology')
            import sys
            base=Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent
            runtime=base/'engine/national-runtime/python.exe'
            if not runtime.exists():raise RuntimeError('地盤反映用のランタイムがありません。MapCraftフォルダー全体を使用してください。')
            request=self.folder/'geology-request.json'
            for world in worlds:
                atomic_json(request,{'world':str(world),'reference':asdict(reference),'cache':str(base/'cache/geology'),'backend':self.data['geology'].get('backend','auto')})
                process=subprocess.Popen([str(runtime),str(base/'geology_backend.py'),'--request',str(request)],env={**os.environ,'PYTHONIOENCODING':'utf-8'},stdout=subprocess.PIPE,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),cwd=str(base))
                self.active_process=process;self.process_changed(process)
                for raw in process.stdout:
                    self.check();self.log(raw.decode('utf-8',errors='replace'))
                code=process.wait();self.active_process=None;self.process_changed(None);self.check()
                if code:raise RuntimeError('地盤の反映に失敗しました。地形・建物は保存済みです。「履歴・再試行」で続けられます。')
        self.stage('furniture')
        import sys
        from dataclasses import replace
        base=Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent
        request=self.folder/'furniture-request.json';runtime=base/'engine/national-runtime/python.exe'
        boundaries=None
        if self.data.get('villagers',{}).get('enabled'):
            self.stage('boundaries');boundary_root=self.folder/'resident-boundaries'
            prepare_boundaries(reference,boundary_root);boundaries=boundary_root/'municipalities.json'
            self.stage('furniture')
        for world in worlds:
            atomic_json(request,{'world':str(world),'reference':asdict(reference),'osm':str(self.folder/'osm.json'),
                                 'vanilla':not self.data.get('include_navigation',True),'villagers':self.data.get('villagers'),'mobs':self.data.get('mobs'),
                                 'boundaries':str(boundaries) if boundaries is not None else None})
            process=subprocess.Popen([str(runtime),str(base/'furniture_backend.py'),'--request',str(request)],env={**os.environ,'PYTHONIOENCODING':'utf-8'},stdout=subprocess.PIPE,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),cwd=str(base))
            self.active_process=process;self.process_changed(process)
            for raw in process.stdout:self.check();self.log(raw.decode('utf-8',errors='replace'))
            code=process.wait();self.active_process=None;self.process_changed(None);self.check()
            if code:raise RuntimeError('家具の事前配置に失敗しました。保存済みの生成から再試行できます。')
        self.data['furniture_baked']=True;self.save();reference=replace(reference,furniture_baked=True)
        if not self.data.get('include_navigation',True):
            self.stage('metadata');metadata=self.folder/'mapcraft_tracking.json';atomic_json(metadata,asdict(reference))
            self.data['navigation']={'metadata':str(metadata),'mode':'vanilla_map'};self.save();return
        self.stage('map');root,atlas=prepare_map(reference,source=self.folder/'osm.json')
        for stage,prepare in [('boundaries',prepare_boundaries),('treasure',prepare_treasure),('beds',prepare_beds)]:
            self.stage(stage);prepare(reference,root)
        self.stage('addon');addon=self.folder/f'MapCraftRealMap_{reference.map_id[:8]}.mcaddon'
        key={'reference':asdict(reference),'version':VERSION,'source':atlas.get('sha256')}
        key=json.loads(json.dumps(key));checkpoint=self.data.get('addon_checkpoint',{})
        if not (checkpoint.get('key')==key and addon.exists() and checkpoint.get('sha256')==source_hash(addon)):
            write_addon(reference,addon)
            self.data['addon_checkpoint']={'key':key,'sha256':source_hash(addon)};self.save()
        else:self.log('作成済みのアドオンを再利用します。\n')
        bundle=read_addon_bundle(addon);pack_hash=self.data['addon_checkpoint']['sha256']
        self.stage('embed')
        embedded=self.data.setdefault('embedded',{})
        for world in worlds:
            self.check();record=embedded.get(world.name,{})
            if record.get('pack_sha256')==pack_hash and record.get('world_sha256')==source_hash(world):
                self.log('組み込み済みのワールドを再利用します。\n');continue
            embed_native_pack(world,reference,files_bundle=bundle)
            embedded[world.name]={'pack_sha256':pack_hash,'world_sha256':source_hash(world)};self.save()
        self.stage('metadata');metadata=self.folder/'mapcraft_tracking.json'
        atomic_json(metadata,asdict(reference))
        self.data['navigation']={'metadata':str(metadata),'pack':str(addon),'mode':'bedrock_native_hud'};self.save()
