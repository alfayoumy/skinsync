"""Audited, restart-only restoration of catalog reset SQLite backups.

NEVER restore onto a live SQLite connection. The operator stages a validated
snapshot, restarts the single backend process, and restoration runs before
catalog caches/services are imported. Backup paths are server-local and
restricted to this instance's managed backup directory.
"""
import atexit
import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from pipeline.catalog_reset import (_backup, _control_schema, _active_tasks,
                                    _active_workers, _tables)

RESTORE_CONFIRMATION = 'RESTORE CATALOG BACKUP'
BACKUP_PATTERN = re.compile(r'^catalog-\d{8}T\d{6}Z-[a-f0-9]{8}\.sqlite3$')
_INSTANCE_LOCK = None


def _database_path(conn=None, db_path=None):
    from pipeline.db_manager import DB_PATH
    actual=db_path or (conn.execute('PRAGMA database_list').fetchone()[2] if conn else DB_PATH)
    path=Path(actual).resolve()
    if not path.is_file():
        raise ValueError('Backup restoration requires an on-disk SQLite database.')
    return path


def _backup_dir(path):
    return Path(os.environ.get('SKINSYNC_CATALOG_BACKUP_DIR')
                or str(path.parent/'backups'/'catalog')).resolve()


def _manifest(path):
    return _backup_dir(path)/('.restore-pending-'+hashlib.sha256(str(path).encode()).hexdigest()[:16]+'.json')


def _last_restore(path):
    return _backup_dir(path)/('.restore-last-'+hashlib.sha256(str(path).encode()).hexdigest()[:16]+'.json')


def _write_json(path, data):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    temp=path.with_name(path.name+'.partial')
    try:
        with open(temp,'x',encoding='utf-8') as output:
            os.chmod(temp,0o600)
            json.dump(data,output,sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp,path)
    finally:
        if temp.exists():
            temp.unlink()


def _hash_file(path):
    digest=hashlib.sha256()
    with open(path,'rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            digest.update(chunk)
    return digest.hexdigest()


def _chosen_backup(db_path, filename):
    if not BACKUP_PATTERN.fullmatch(filename or ''):
        raise ValueError('Choose a managed catalog backup by its listed filename.')
    root=_backup_dir(db_path)
    item=root/filename
    if item.is_symlink() or not item.is_file() or item.resolve().parent!=root:
        raise ValueError('Managed backup file is not available.')
    return item


def list_catalog_backups(conn=None, db_path=None):
    path=_database_path(conn,db_path)
    folder=_backup_dir(path)
    items=[]
    if folder.exists():
        for backup in folder.iterdir():
            if not BACKUP_PATTERN.fullmatch(backup.name) or backup.is_symlink() or not backup.is_file():
                continue
            stat=backup.stat()
            items.append({'filename':backup.name,'bytes':stat.st_size,
                'modified_at':datetime.fromtimestamp(stat.st_mtime,timezone.utc).isoformat()})
    items.sort(key=lambda v:v['filename'],reverse=True)
    pending=_manifest(path)
    recent=_last_restore(path)
    return {'backups':items[:100],
        'pending':json.loads(pending.read_text()) if pending.exists() else None,
        'last_restore':json.loads(recent.read_text()) if recent.exists() else None,
        'restore_requires_restart':True}


def preview_catalog_restore(conn,filename,db_path=None):
    path=_database_path(conn,db_path)
    backup=_chosen_backup(path,filename)
    uri='file:'+str(backup)+'?mode=ro'
    with sqlite3.connect(uri,uri=True) as source:
        if source.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
            raise ValueError('Backup failed SQLite integrity_check.')
        if source.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('Backup has broken foreign-key references.')
        required={'products','canonical_products','stores','sources','pipeline_tasks'}
        existing={r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not required <= existing:
            raise ValueError('This file is not a complete compatible SkinSync catalog backup.')
        tables=('products','canonical_products','source_product_observations',
                'product_offers','identity_gemini_queue','inci_audit_evidence')
        counts={t:source.execute('SELECT COUNT(*) FROM "'+t+'"').fetchone()[0]
                for t in tables if t in existing}
    now_tables=_tables(conn)
    running=_active_tasks(conn,now_tables)
    workers=_active_workers(conn,now_tables)
    lock=False
    if 'catalog_reset_control' in now_tables:
        state=conn.execute('SELECT active FROM catalog_reset_control WHERE singleton=1').fetchone()
        lock=bool(state and state[0])
    if _manifest(path).exists():
        lock=True
    return {'filename':filename,'sha256':_hash_file(backup),
        'size_bytes':backup.stat().st_size,'tables':counts,
        'active_jobs':running,'active_workers':workers,'maintenance_locked':lock,
        'can_schedule':not running and not workers and not lock,
        'confirmation_phrase':RESTORE_CONFIRMATION,
        'warning':'Restores the ENTIRE backup database, including old identities, source observations and saved decisions. Current live changes are replaced. The backend must restart to apply. A rescue backup of the current database is created before any restore.'}


def queue_catalog_restore(conn,filename,sha256,confirmation,actor,db_path=None):
    if confirmation!=RESTORE_CONFIRMATION:
        raise ValueError('Type RESTORE CATALOG BACKUP exactly.')
    if not actor or not actor.strip():
        raise ValueError('Authenticated operations reviewer required.')
    path=_database_path(conn,db_path)
    plan=preview_catalog_restore(conn,filename,str(path))
    if not plan['can_schedule']:
        raise ValueError('Active jobs, workers, or an existing maintenance lock prevent restoration.')
    if sha256!=plan['sha256']:
        raise ValueError('Backup contents changed since preview; refresh.')
    request={'filename':filename,'sha256':sha256,'database_path':str(path),
             'requested_by':actor,'requested_at':datetime.now(timezone.utc).isoformat()}
    pending=_manifest(path)
    try:
        conn.execute('BEGIN IMMEDIATE')
        _control_schema(conn)
        if _active_tasks(conn,_tables(conn)) or _active_workers(conn,_tables(conn)):
            raise ValueError('A catalog job started since preview.')
        existing=conn.execute('SELECT active FROM catalog_reset_control WHERE singleton=1').fetchone()
        if existing and existing[0]:
            raise ValueError('Catalog maintenance already locked.')
        if pending.exists():
            raise ValueError('Another restore is already scheduled.')
        conn.execute("""INSERT INTO catalog_reset_control(singleton,active,started_at)
            VALUES(1,1,CURRENT_TIMESTAMP)
            ON CONFLICT(singleton) DO UPDATE SET active=1,started_at=CURRENT_TIMESTAMP""")
        _write_json(pending,request)
        conn.commit()
    except Exception:
        conn.rollback()
        if pending.exists() and pending.read_text()==json.dumps(request,sort_keys=True):
            pending.unlink()
        raise
    return {'status':'restart_required','filename':filename,
            'message':'Stop and restart the SkinSync backend once. The selected backup is restored before API services load. Do not begin discovery until restart completes.'}


def cancel_catalog_restore(conn,db_path=None):
    path=_database_path(conn,db_path)
    pending=_manifest(path)
    if not pending.exists():
        raise ValueError('No catalog restore is scheduled.')
    conn.execute('BEGIN IMMEDIATE')
    try:
        pending.unlink()
        conn.execute('UPDATE catalog_reset_control SET active=0 WHERE singleton=1')
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {'status':'cancelled'}


def apply_pending_catalog_restore(db_path):
    """Offline single-process startup phase. Must precede importing catalog_service."""
    path=Path(db_path).resolve()
    pending=_manifest(path)
    if not pending.exists():
        return None
    request=json.loads(pending.read_text(encoding='utf-8'))
    if request.get('database_path')!=str(path):
        raise RuntimeError('Restore manifest database path mismatch; refusing to start.')
    backup=_chosen_backup(path,request['filename'])
    if _hash_file(backup)!=request['sha256']:
        raise RuntimeError('Restore backup hash changed; refusing to start.')
    with sqlite3.connect('file:'+str(backup)+'?mode=ro',uri=True) as source:
        if source.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or source.execute('PRAGMA foreign_key_check').fetchone():
            raise RuntimeError('Backup integrity verification failed; refusing to start.')
        rescue=None
        with sqlite3.connect(path) as live:
            rescue=_backup(live,path)
            try:
                source.backup(live,pages=100)
                if live.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                    raise RuntimeError('Restored database failed integrity check.')
                if live.execute('PRAGMA foreign_key_check').fetchone():
                    raise RuntimeError('Restored database failed foreign-key check.')
                _control_schema(live)
                # Backups created before any reset may have the control table
                # without its singleton row. Create it as safely inactive.
                live.execute("""INSERT INTO catalog_reset_control(singleton,active,started_at)
                    VALUES(1,0,NULL) ON CONFLICT(singleton)
                    DO UPDATE SET active=0,started_at=NULL""")
                live.execute("""CREATE TABLE IF NOT EXISTS catalog_restore_history(
                    id INTEGER PRIMARY KEY,restored_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    actor TEXT NOT NULL,source_backup TEXT NOT NULL,rescue_backup TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL)""")
                live.execute("""INSERT INTO catalog_restore_history(actor,source_backup,rescue_backup,source_sha256)
                    VALUES(?,?,?,?)""",(request['requested_by'],backup.name,
                                         str(rescue),request['sha256']))
                live.commit()
            except Exception:
                # Rescue the database that existed immediately before restore.
                live.rollback()
                with sqlite3.connect('file:'+str(rescue)+'?mode=ro',uri=True) as safety:
                    safety.backup(live,pages=100)
                live.commit()
                raise
    _write_json(_last_restore(path),{'status':'restored','filename':backup.name,
        'restored_at':datetime.now(timezone.utc).isoformat(),
        'rescue_backup':str(rescue),'requested_by':request['requested_by']})
    pending.unlink()
    return backup.name


def startup_catalog_restore_guard():
    """Hold an instance-level file lock to prevent two API workers using a DB."""
    global _INSTANCE_LOCK
    from pipeline.db_manager import DB_PATH
    db=Path(DB_PATH).resolve()
    if _INSTANCE_LOCK is not None:
        return
    try:
        import fcntl
    except ImportError:
        if _manifest(db).exists():
            raise RuntimeError('Pending restore requires a POSIX filesystem lock; use the offline restore method.')
        return
    lock_path=db.with_name('.'+db.name+'.api-process.lock')
    db.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(lock_path,os.O_CREAT|os.O_RDWR,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise RuntimeError('Another SkinSync backend has this database open. Stop all existing workers before starting restoration.')
    _INSTANCE_LOCK=fd
    atexit.register(lambda:os.close(fd))
    apply_pending_catalog_restore(db)


def restore_is_pending():
    """Cheap filesystem-only guard for mutating HTTP operations."""
    from pipeline.db_manager import DB_PATH
    return _manifest(Path(DB_PATH).resolve()).exists()
