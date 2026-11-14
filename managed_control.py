"""Durable controls for detached creators. Never open a Minecraft LevelDB."""
from contextlib import closing,contextmanager
from datetime import datetime
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time
import uuid


def read(path,default=None):
    try:return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError,ValueError):return default


def write(path,value):
    from jobcache import atomic_json
    Path(path).parent.mkdir(parents=True,exist_ok=True);atomic_json(path,value)


def now():return datetime.now().astimezone().isoformat()


def manifest_path(base,job='current'):
    if not re.fullmatch(r'[a-z0-9-]+',job):raise ValueError('処理IDが不正です。')
    folder=Path(base)/'自動生成'
    return folder/'managed-generation.json' if job=='current' else folder/'managed-jobs'/(job+'.json')


def pause_path(base,job='current'):
    return manifest_path(base,job).with_suffix('.pause.json')


@contextmanager
def control_lock(base,job):
    import msvcrt
    path=manifest_path(base,job).with_suffix('.control-lock');path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as stream:
        if not stream.tell():stream.write(b'0');stream.flush()
        stream.seek(0)
        try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:raise RuntimeError('別の一時停止・再開操作を処理中です。')
        try:yield
        finally:stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)


def process_identity(pid):
    """Compare start time and executable, never just a recycled process ID."""
    if not pid or os.name!='nt':return None
    from ctypes import wintypes as w
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];kernel.OpenProcess.restype=w.HANDLE
    kernel.CloseHandle.argtypes=[w.HANDLE]
    kernel.GetProcessTimes.argtypes=[w.HANDLE,*([ctypes.POINTER(w.FILETIME)]*4)]
    kernel.QueryFullProcessImageNameW.argtypes=[w.HANDLE,w.DWORD,w.LPWSTR,ctypes.POINTER(w.DWORD)]
    kernel.GetExitCodeProcess.argtypes=[w.HANDLE,ctypes.POINTER(w.DWORD)]
    handle=kernel.OpenProcess(0x1000,False,int(pid))
    if not handle:return None
    try:
        code=w.DWORD()
        if not kernel.GetExitCodeProcess(handle,ctypes.byref(code)) or code.value!=259:return None
        created,exited,kernel_time,user_time=(w.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle,*[ctypes.byref(v) for v in (created,exited,kernel_time,user_time)]):return None
        name=ctypes.create_unicode_buffer(32768);length=w.DWORD(len(name))
        if not kernel.QueryFullProcessImageNameW(handle,0,name,ctypes.byref(length)):return None
        ticks=(created.dwHighDateTime<<32)|created.dwLowDateTime
        return {'creation_ticks':ticks,'image':name.value}
    finally:kernel.CloseHandle(handle)


def alive(task):
    actual=process_identity(task.get('pid'))
    if not actual or Path(actual['image']).resolve()!=Path(task['runtime']).resolve():return False
    expected=task.get('creation_ticks')
    if expected is not None:return actual['creation_ticks']==expected
    # Import a previously running creator recorded by PowerShell.
    created=task.get('created')
    if not created:return False
    expected=(datetime.fromisoformat(created).timestamp()+11644473600)*10000000
    return abs(actual['creation_ticks']-expected)<20000


def validate(base,record):
    base=Path(base).resolve()
    if not record or record.get('schema')!=1 or Path(record['base']).resolve()!=base:raise ValueError('保存済みの生成設定を読み取れません。')
    project=Path(record['project']).resolve();config=read(project/'project.json',{})
    if record.get('map_id') and config.get('map_id')!=record['map_id']:raise ValueError('生成ワールドのIDが変わっているため再開を保留しました。')
    for task in record['tasks']:
        if Path(task['runtime']).resolve()!=base/'engine/national-runtime/python.exe':raise ValueError('生成用Pythonが一致しません。')
        program=Path(task['program']).resolve()
        if not program.is_relative_to(base) or program.suffix!='.py' or not program.is_file():raise ValueError('再開用プログラムが見つかりません。')
        if not isinstance(task.get('args'),list) or not all(isinstance(v,str) for v in task['args']):raise ValueError('再開設定が不正です。')
        state=Path(task['state_file'])
        if state.is_absolute() or '..' in state.parts:raise ValueError('状態ファイルの指定が不正です。')
    return project


def checkpoint(project):
    base=Path(os.environ.get('MAPCRAFT_CONTROL_BASE',str(Path(project).resolve().parent.parent)))
    marker=read(pause_path(base,os.environ.get('MAPCRAFT_JOB_ID','current')),{})
    if marker and Path(marker['project']).resolve()==Path(project).resolve():
        raise InterruptedError('一時停止しました。MapCraftの「生成状況・一時停止・再開」から再開できます。')


def pause(base,job='current'):
    with control_lock(base,job):
        path=manifest_path(base,job);record=read(path);project=validate(base,record)
        if record.get('status') in ('paused','pausing') and pause_path(base,job).exists():return snapshot(base,job)
        intent='MapCraft managed pause: '+str(uuid.uuid4());root_marker=project/'pause.request'
        prior=root_marker.read_text(encoding='utf-8').strip() if root_marker.exists() else None
        record.update(status='pausing',requested_at=now(),pause_marker=intent,previous_pause_marker=prior)
        write(pause_path(base,job),{'project':str(project),'intent':intent,'requested_at':now()})
        if prior is None or prior.startswith(('MapCraft phase export: ','MapCraft managed pause: ')):
            root_marker.write_text(intent,encoding='utf-8')
        record['root_marker_owned']=root_marker.read_text(encoding='utf-8').strip()==intent
        write(path,record)
    return snapshot(base,job)


def locks_free(base,record):
    from national_state import project_busy
    return not any(project_busy(folder) for folder in (record['project'],Path(base)/'自動生成',Path(base)/'自動生成/phase-controller'))


def resume(base,job='current'):
    base=Path(base).resolve()
    with control_lock(base,job):
        path=manifest_path(base,job);record=read(path);project=validate(base,record)
        if any(alive(t) for t in record['tasks']):raise RuntimeError('生成処理はすでに起動しています。二重起動しません。')
        if not locks_free(base,record):raise RuntimeError('別の生成処理が書き込み中です。再開を保留しました。')
        states=[read(base/'自動生成'/t['state_file'],{}) for t in record['tasks']]
        pending=[task for task,state in zip(record['tasks'],states) if state.get('status')!='complete']
        if not pending:raise ValueError('登録された生成・出力処理はすべて完了しています。')
        root_marker=project/'pause.request'
        if root_marker.exists():
            actual=root_marker.read_text(encoding='utf-8').strip()
            if actual!=record.get('pause_marker'):raise RuntimeError('別の停止要求があるため保持しました。状態を確認してください。')
            previous=record.get('previous_pause_marker')
            if previous is None:root_marker.unlink()
            else:root_marker.write_text(previous,encoding='utf-8')
        pause_path(base,job).unlink(missing_ok=True)
        record.update(status='starting',resumed_at=now(),map_id=read(project/'project.json',{}).get('map_id'))
        write(path,record)
        launched=[]
        try:
            for task in pending:
                stamp=datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]
                output=base/'自動生成'/f'{task["id"]}-{stamp}-console.log';error=output.with_name(output.name.replace('console','errors'))
                environment={**os.environ,'PYTHONUNBUFFERED':'1','MAPCRAFT_CONTROL_BASE':str(base),'MAPCRAFT_JOB_ID':job}
                with output.open('wb') as stdout,error.open('wb') as stderr:
                    process=subprocess.Popen([task['runtime'],task['program'],*task['args']],cwd=base,stdin=subprocess.DEVNULL,
                        stdout=stdout,stderr=stderr,env=environment,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),close_fds=True)
                identity=process_identity(process.pid)
                if identity is None:raise RuntimeError('再開した生成処理が起動直後に終了しました。ログを確認してください。')
                task.update(pid=process.pid,creation_ticks=identity['creation_ticks'],stdout=str(output),stderr=str(error),started=now())
                launched.append(task['id']);write(path,record)
                if task.get('process_file'):write(task['process_file'],{**task,'resumed':True})
            record.update(status='running');write(path,record)
        except Exception as exc:
            record.update(status='launch_failed',error=str(exc),launched=launched);write(path,record)
            # Successfully launched writers keep running; a retry never duplicates them.
            raise
    return snapshot(base,job)


def list_jobs(base):
    paths=[manifest_path(base)]
    folder=Path(base)/'自動生成/managed-jobs'
    if folder.exists():paths.extend(folder.glob('*.json'))
    return [{'id':'current' if p.name=='managed-generation.json' else p.stem,'title':r.get('title',Path(r['project']).name)}
            for p in paths if not p.name.endswith('.pause.json') and (r:=read(p)) and r.get('schema')==1]


def snapshot(base,job='current'):
    base=Path(base);record=read(manifest_path(base,job))
    if not record:return {'status':'idle','title':'保存済みの生成処理はありません。','tasks':[],'can_resume':False,'safe_to_shutdown':True}
    project=Path(record['project']);tasks=[]
    for task in record['tasks']:
        state=read(base/'自動生成'/task['state_file'],{})
        tasks.append({'id':task['id'],'title':task['title'],'alive':alive(task),'status':state.get('status','unknown'),
                      'stage':state.get('last_event',{}).get('text') or state.get('stage',''),'updated':state.get('updated'),
                      'error':state.get('error'),'pid':task.get('pid')})
    active=any(t['alive'] for t in tasks);complete=all(t['status']=='complete' for t in tasks)
    held=pause_path(base,job).exists() or record.get('status') in ('pausing','paused')
    status='pausing' if active and held else 'running' if active else 'complete' if complete else 'paused' if held else 'interrupted'
    free=not active and locks_free(base,record)
    result={'status':status,'title':record.get('title',read(project/'project.json',{}).get('world_name',project.name)),
            'project':str(project),'tasks':tasks,'safe_to_shutdown':free,'can_resume':free and not complete,
            'updated':max((str(t['updated']) for t in tasks if t['updated']),default=record.get('paused_at',''))}
    for file,query,label in [('queue.sqlite',"SELECT COUNT(*),SUM(state='done') FROM tiles",'terrain'),
                              ('atlas.sqlite','SELECT COUNT(*) FROM rendered WHERE style=2','navigation')]:
        try:
            with closing(sqlite3.connect((project/file).resolve().as_uri()+'?mode=ro',uri=True,timeout=1)) as db:row=db.execute(query).fetchone()
            result[label]={'total':row[0],'completed':row[1] or 0} if label=='terrain' else {'completed':row[0]}
        except (OSError,sqlite3.Error):pass
    config=read(project/'project.json',{});phase=config.get('active_phase',{})
    if phase:result.setdefault('terrain',{})['total']=phase['total']
    if 'navigation' in result:result['navigation']['total']=result.get('terrain',{}).get('completed',0)
    main=read(base/'自動生成/進捗.json',{}) if job=='current' else read(base/'自動生成'/record['tasks'][0]['state_file'],{})
    result['current_step']=main.get('last_event',{}).get('text') or main.get('stage','')
    result['output']=main.get('output') or main.get('destination')
    return result


def register(base,project,runtime,action,request):
    """Keep a replayable request when a GUI child pauses or the PC restarts."""
    base=Path(base).resolve();project=Path(project).resolve();job='gui-'+hashlib.sha256(str(project).encode()).hexdigest()[:16]
    with control_lock(base,job):
        path=manifest_path(base,job);prior=read(path,{})
        if any(alive(task) for task in prior.get('tasks',[])):raise RuntimeError('この保存先の処理はすでに起動しています。')
        if read(project/'project.json',{}).get('active_phase'):raise ValueError('自動拡張で管理中です。「生成状況・一時停止・再開」を使用してください。')
        root_marker=project/'pause.request'
        if prior.get('pause_marker') and root_marker.exists():
            if root_marker.read_text(encoding='utf-8').strip()!=prior['pause_marker']:raise RuntimeError('別の停止要求を保持しました。')
            previous=prior.get('previous_pause_marker')
            if previous is None:root_marker.unlink()
            else:root_marker.write_text(previous,encoding='utf-8')
            pause_path(base,job).unlink(missing_ok=True)
        request_path=path.with_suffix('.request.json');write(request_path,request)
        state_file='managed-jobs/'+job+'.state.json'
        task={'id':job,'title':action+' / '+project.name,'runtime':str(runtime),'program':str(base/'managed_runner.py'),
              'args':['--base',str(base),'--job',job],'state_file':state_file,'pid':None}
        record={'schema':1,'base':str(base),'project':str(project),'title':request.get('selection',{}).get('label',project.name),
                'map_id':read(project/'project.json',{}).get('map_id'),'status':'registered','action':action,'request':str(request_path),'tasks':[task]}
        write(path,record)
        write(base/'自動生成'/state_file,{'status':'ready','stage':'開始待ち','updated':now()})
    return job,task
