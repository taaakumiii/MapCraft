"""Read-only checks for national workflow prerequisites. Never create an index by probing it."""
import json
from pathlib import Path
import sqlite3

INDEX_TABLES={'meta','features','bounds','towns','prefectures'}

def read_database(path):
    return sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=2)

def index_info(path):
    path=Path(path)
    if not path.is_file():
        raise ValueError('全国データの索引が未作成です。日本の .osm.pbf ファイルを選び、「全国データの索引を作成」を先に実行してください。')
    db=None
    try:
        db=read_database(path)
        tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not INDEX_TABLES<=tables:
            raise ValueError('選択したファイルは完成したMapCraftの索引ではありません。「全国データの索引を作成」を実行してください。空の索引は作成時に退避します。')
        result=dict(db.execute('SELECT key,value FROM meta'))
        if result.get('complete')!='1':
            raise ValueError('全国データの索引が未完成です。先に索引作成を完了してください。')
        return result
    except sqlite3.Error as error:
        raise ValueError('全国データの索引を読み取れません。索引作成中なら完了を待ち、破損している場合は別の索引保存先を選んで作成してください。') from error
    finally:
        if db is not None:db.close()

def empty_index(path):
    """Only a truly empty SQLite file may be preserved and replaced automatically."""
    path=Path(path);db=None
    if not path.is_file() or path.stat().st_size>4096:return False
    try:
        db=read_database(path)
        return not db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    except sqlite3.Error:return False
    finally:
        if db is not None:db.close()

def project_info(root):
    root=Path(root);path=root/'project.json'
    if not path.is_file():
        raise ValueError('全国プロジェクトが未作成です。索引作成の完了後、「新規プロジェクトを作成」を先に実行してください。作成済みの場合は、その保存先を選択してください。')
    try:
        config=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(config,dict) or config.get('format')!=1 or config.get('origin')!=[35.,135.]:
            raise ValueError('全国プロジェクトの形式が一致しません。正しい保存先を選択してください。')
        if not (root/'queue.sqlite').is_file():raise ValueError('全国プロジェクトの区画記録がありません。元のプロジェクトフォルダー全体を使用してください。')
        return config
    except (OSError,json.JSONDecodeError) as error:
        raise ValueError('全国プロジェクトの設定を読み取れません。正しい保存先を選択してください。') from error

def downloaded_source(base):
    """Select only a verified, finalized downloader output, never a .part file."""
    base=Path(base);status=base/'national_data/download-status.json'
    try:
        data=json.loads(status.read_text(encoding='utf-8'))
        path=Path(data['file']).resolve();folder=(base/'national_data').resolve()
        if data.get('state')=='complete' and path.parent==folder and path.name.endswith('.osm.pbf') and path.is_file() and path.stat().st_size==data.get('bytes'):
            return path
    except (OSError,ValueError,KeyError,TypeError):pass
    return None

def index_busy(path):
    """Probe the existing index-builder lock without creating files."""
    import msvcrt
    lock=Path(path).resolve().parent/'builder.lock'
    if not lock.is_file():return False
    try:
        with lock.open('r+b') as stream:
            try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:return True
            msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
    except OSError:return True
    return False

def external_index_progress(base,index):
    """Read the bounded tail of this creator's detached index job, if it matches."""
    base=Path(base)
    try:
        request=json.loads((base/'national_data/index-request.json').read_text(encoding='utf-8'))
        if Path(request['index']).resolve()!=Path(index).resolve():return None
        with (base/'national_data/index.log').open('rb') as stream:
            stream.seek(0,2);size=stream.tell();stream.seek(max(0,size-8192));lines=stream.read().splitlines()
        for line in reversed(lines):
            try:value=json.loads(line)
            except ValueError:continue
            if value.get('event') in ('index','indexed','error'):return value
    except (OSError,ValueError,KeyError,TypeError):pass
    return None
