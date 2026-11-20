"""Build and check a self-contained source distribution. No hardware access."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parent
SUBDIRS = ('cpp','bench','channels')
ALLOWED_EXT = {'.py','.cpp','.md','.json','.jsonl','.bin','.cf32','.txt'}


def contents():
    # Scope packaging to reviewed source, evidence and examples; exclude host binaries and user IQ captures.
    paths = [p for p in ROOT.iterdir() if p.is_file() and (p.suffix in {'.py','.md'} or p.name in
             {'example.bin','example.cf32','ftsk4-v1.zip','soak-50min.jsonl','soak-release.jsonl','requirements.txt','test-report.txt'})]
    paths += [p for sub in SUBDIRS for p in (ROOT/sub).glob('*') if p.is_file() and p.suffix in {'.py','.cpp','.hpp','.md','.json'}]
    return sorted(paths, key=lambda p:p.relative_to(ROOT).as_posix())


def create(path):
    with zipfile.ZipFile(path,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=7) as z:
        manifest = {}
        for p in contents():
            rel=p.relative_to(ROOT).as_posix()
            buf=p.read_bytes()
            manifest[rel]=hashlib.sha256(buf).hexdigest()
            z.writestr(rel,buf)
        z.writestr('MANIFEST.json', json.dumps({'version':'FTSK software release 1.1','sha256':manifest},indent=2))
    return verify(path)


def verify(path):
    with zipfile.ZipFile(path) as z:
        entries=z.namelist()
        if len(set(entries))!=len(entries):
            raise ValueError('duplicate archive entries')
        manifest=json.loads(z.read('MANIFEST.json'))['sha256']
        if set(manifest) != set(entries)-{'MANIFEST.json'}:
            raise ValueError('archive inventory mismatch')
        for rel,digest in manifest.items():
            if Path(rel).is_absolute() or '..' in Path(rel).parts or hashlib.sha256(z.read(rel)).hexdigest()!=digest:
                raise ValueError(f'invalid entry: {rel}')
    return dict(path=str(path), entries=len(manifest), bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['build','verify'])
    p.add_argument('archive',type=Path)
    a=p.parse_args()
    try:
        print(json.dumps((create if a.action=='build' else verify)(a.archive), indent=2))
    except (ValueError, OSError, KeyError, zipfile.BadZipFile) as exc:
        sys.exit(f'release verification: {exc}')
