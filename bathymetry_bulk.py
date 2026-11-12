"""Stage marine changes in SQLite, then write a verified new Bedrock DB in order.

Requires the creator's project/control locks and no other native DB owner.
The old database remains in backups. No installed player world is accessed.
"""
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import uuid

import leveldb
import national_backend as n
from bathymetry import CREDITS,options
from bathymetry_backend import apply_directory
from parallel_furniture import database_fingerprint,rebuilt_database,recover_swap,swap_database


class CaptureDB:
    def __init__(self,source,changes):self.source=source;self.changes=changes
    def iterate(self):return self.source.iterate()
    def putBatch(self,values):
        self.changes.executemany('INSERT OR REPLACE INTO changes VALUES(?,?)',values.items());self.changes.commit()


def copy_journal(source,destination):
    with closing(sqlite3.connect(destination)) as output:
        if Path(source).exists():
            with closing(sqlite3.connect(Path(source).resolve().as_uri()+'?mode=ro',uri=True)) as original:original.backup(output)
        else:output.execute('CREATE TABLE chunks(x INTEGER,z INTEGER,signature TEXT,PRIMARY KEY(x,z))');output.commit()


def publish(root,transaction):
    root=Path(root).resolve();work=Path(transaction['work']).resolve()
    if not work.is_relative_to(root/'.bulk-bathymetry'):raise ValueError('海底計算の保存先がプロジェクト外です。')
    temporary=root/('bathymetry-state.'+uuid.uuid4().hex+'.writing')
    try:
        if (root/'bathymetry-state.sqlite').exists():
            with closing(sqlite3.connect(root/'bathymetry-state.sqlite')) as old:old.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        copy_journal(work/'chunks.sqlite',temporary);os.replace(temporary,root/'bathymetry-state.sqlite')
    finally:temporary.unlink(missing_ok=True)
    n.atomic_json(root/'world/mapcraft_bathymetry.json',transaction['report'])
    (root/'world/BATHYMETRY-CREDITS.txt').write_text(CREDITS,encoding='utf-8')
    n.atomic_json(root/'bulk-bathymetry-swap.json',{**transaction,'status':'complete'})
    return transaction['report']


def recover(root):
    root=Path(root).resolve();marker=root/'bulk-bathymetry-swap.json'
    recover_swap(root)
    if not marker.exists():return None
    transaction=json.loads(marker.read_text(encoding='utf-8'))
    if transaction['status']=='complete':return None
    fresh=Path(transaction['fresh']).resolve()
    if not fresh.is_relative_to(root/'.bulk-bathymetry'):raise ValueError('海底DBの作業先が不正です。')
    swap_marker=root/'parallel-db-swap.json'
    if not fresh.exists() and swap_marker.exists() and (root/'world/db').exists():
        swap=json.loads(swap_marker.read_text(encoding='utf-8'))
        if Path(swap['fresh']).resolve()==fresh and Path(swap['backup']).exists():
            if database_fingerprint(root/'world/db')!=transaction['fresh_identity']:raise ValueError('一括保存後にDBが変更されています。古い海底記録の反映を保留します。')
            return publish(root,transaction)
    if fresh.exists() and (root/'world/db').exists():
        if database_fingerprint(root/'world/db')==transaction['source_identity'] and database_fingerprint(fresh)==transaction['fresh_identity']:
            swap_database(root,fresh,transaction['verification']);return publish(root,transaction)
    # A prepared rebuild with a different source is kept but not reused.
    return None


def apply_bulk(root,reference,cache,settings,notify=print,*,freshwater=None,survey=None,coverage=None,expected_chunks=None):
    settings=options(settings)
    if not settings['enabled']:return None
    root=Path(root).resolve();recovered=recover(root)
    if recovered is not None and recovered.get('coverage')==coverage:return recovered
    world=root/'world';marker=world/'mapcraft_bathymetry.json'
    if marker.exists():
        previous=json.loads(marker.read_text(encoding='utf-8'))
        from dataclasses import asdict
        import hashlib
        signature=hashlib.sha256(json.dumps([1,asdict(reference),settings],sort_keys=True).encode()).hexdigest()
        if previous.get('signature')==signature and previous.get('coverage')==coverage:return previous
    size=sum(p.stat().st_size for p in (world/'db').iterdir() if p.is_file())
    if shutil.disk_usage(root).free<size*2+4*1024**3:raise RuntimeError('海底の一括保存用の空き容量が不足しています。元DBは変更しません。')
    config_path=root/'project.json'
    if config_path.exists():
        from autogen import folder_bytes
        budget=n.load_project(root).get('storage_budget_bytes',2500000000000)
        if folder_bytes(root.parent.parent)+size*2+4*1024**3>budget:raise RuntimeError('海底一括保存の容量上限を超えるため保留しました。')
    work=root/'.bulk-bathymetry'/uuid.uuid4().hex;work.mkdir(parents=True)
    copy_journal(root/'bathymetry-state.sqlite',work/'chunks.sqlite')
    copy_journal(root/'bathymetry-state.sqlite',work/'original-chunks.sqlite')
    db=None
    with closing(sqlite3.connect(work/'changes.sqlite')) as changes:
        changes.execute('CREATE TABLE changes(k BLOB PRIMARY KEY,v BLOB NOT NULL) WITHOUT ROWID');changes.commit()
        try:
            db=leveldb.LevelDB(str(world/'db'),create_if_missing=False)
            notify('海底の変更を別DBで計算します。元ワールドへの小分け書き込みは行いません。')
            report=apply_directory(world,reference,cache,settings,notify,survey=survey,freshwater=freshwater,
                state_path=work/'chunks.sqlite',coverage=coverage,database=CaptureDB(db,changes),publish=False)
            report={**report,'storage_mode':'verified_sorted_rebuild'}
            notify('海底の計算完了。全レコードをキー順に新しいワールドDBへ保存します。')
            fresh=work/'rebuilt-db'
            with closing(changes.execute('SELECT k,v FROM changes ORDER BY k')) as patches:
                verification=rebuilt_database(db,{},fresh,notify,16*1024**2,patches_sorted=patches,expected_chunks=expected_chunks)
            db.close();db=None
            transaction={'status':'prepared','work':str(work),'fresh':str(fresh),'verification':verification,'report':report,
                         'source_identity':database_fingerprint(world/'db'),'fresh_identity':database_fingerprint(fresh)}
            n.atomic_json(root/'bulk-bathymetry-swap.json',transaction)
            swap_database(root,fresh,verification)
            notify('新DBの全レコード検証・切り替えが完了しました。元DBはバックアップに保持しています。')
            return publish(root,transaction)
        finally:
            if db is not None:db.close()
