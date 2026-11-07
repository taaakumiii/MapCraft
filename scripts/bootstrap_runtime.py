"""Fetch the pinned official engine and prepare an isolated Windows runtime."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import urllib.request
import zipfile
ROOT=Path(__file__).resolve().parents[1]
DOWNLOADS={
 'engine':('https://github.com/louis-e/arnis/releases/download/v3.4.0/arnis-windows.exe','201f67720148e6262e8adbbf51a9bb29fc31364439f740447cf67b42a1d9b0f9'),
 'python':('https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip','4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3')}

def obtain(kind,destination):
    url,expected=DOWNLOADS[kind];destination=Path(destination)
    if destination.exists():
        with destination.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
        if actual==expected:return
        raise ValueError('An existing download has an unexpected hash: '+str(destination))
    destination.parent.mkdir(parents=True,exist_ok=True);temporary=destination.with_suffix(destination.suffix+'.download')
    request=urllib.request.Request(url,headers={'User-Agent':'MapCraft build bootstrap'})
    with urllib.request.urlopen(request,timeout=60) as response,temporary.open('wb') as output:
        while block:=response.read(1024*1024):output.write(block)
    with temporary.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
    if actual!=expected:raise ValueError('Download checksum mismatch: '+url)
    temporary.replace(destination)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--engine-only',action='store_true');args=parser.parse_args()
    obtain('engine',ROOT/'engine/arnis-windows.exe')
    if args.engine_only:return
    archive=ROOT/'build/downloads/python312.zip';obtain('python',archive)
    runtime=ROOT/'engine/national-runtime';runtime.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(archive) as source:source.extractall(runtime)
    (runtime/'python312._pth').write_text('python312.zip\n.\nLib/site-packages\nimport site\n',encoding='ascii')
    target=runtime/'Lib/site-packages'
    subprocess.run([sys.executable,'-m','pip','install','--disable-pip-version-check','--only-binary=:all:',
        '--platform','win_amd64','--python-version','3.12','--implementation','cp','--abi','cp312',
        '--no-compile','--target',str(target),'-r',str(ROOT/'requirements-runtime.txt')],check=True)
    (runtime/'RUNTIME-PROVENANCE.json').write_text(json.dumps({'python':DOWNLOADS['python'][0],
        'python_sha256':DOWNLOADS['python'][1],'requirements':(ROOT/'requirements-runtime.txt').read_text(),
        'licenses':'Full wheel licenses are retained in Lib/site-packages/*dist-info.'},indent=2),encoding='utf-8')
    print('Runtime prepared. Preserve all included dependency licenses when redistributing.')

if __name__=='__main__':main()
