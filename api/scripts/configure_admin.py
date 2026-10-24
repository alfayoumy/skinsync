"""Create a scoped administrator token in a local .env and a private token file."""
import argparse
import json
import os
from pathlib import Path
import secrets
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from security.admin import PERMISSIONS


def configure(env_path,token_path,name,permissions):
    if not name.strip() or not set(permissions)<=PERMISSIONS:raise ValueError('Invalid administrator or permissions.')
    env_path=Path(env_path);token_path=Path(token_path)
    existing=env_path.read_text() if env_path.exists() else ''
    if any(line.strip().startswith('SKINSYNC_ADMIN_CREDENTIALS=') for line in existing.splitlines()):
        raise ValueError('Administrator credentials already configured; edit them explicitly to add or rotate users.')
    if token_path.exists():raise ValueError('Choose a new private token file.')
    token=secrets.token_urlsafe(48)
    token_path.parent.mkdir(parents=True,exist_ok=True)
    with token_path.open('x') as handle:handle.write(token+'\n')
    os.chmod(token_path,0o600)
    records=json.dumps({name:{'token':token,'permissions':permissions}},separators=(',',':'))
    with env_path.open('a') as handle:handle.write("\nSKINSYNC_ADMIN_CREDENTIALS='"+records+"'\n")
    os.chmod(env_path,0o600)
    return {'configured_administrator':name,'permissions':permissions,'private_token_file':str(token_path),'restart_required':True}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file',default=str(Path(__file__).resolve().parents[1]/'.env'))
    parser.add_argument('--token-file',required=True);parser.add_argument('--name',default='local-admin')
    parser.add_argument('--permissions',default=','.join(sorted(PERMISSIONS)))
    args=parser.parse_args()
    print(json.dumps(configure(args.env_file,args.token_file,args.name,args.permissions.split(',')),indent=2))
