"""Check an extracted release in isolation, including the independent C++17 receiver."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile
from release_check import verify


def validate(archive, compiler):
    print(verify(archive), flush=True)
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp)/'work'
        with zipfile.ZipFile(archive) as package:
            package.extractall(root)
        def execute(*args):
            print('>',*args,flush=True)
            subprocess.run(args,cwd=root,check=True)
        execute(sys.executable,'-m','unittest','discover','-v')
        execute(sys.executable,'selftest.py')
        native=root/'cpp'/('ftsk.exe' if sys.platform=='win32' else 'ftsk')
        execute(compiler,'-std=c++17','-O2','-Wall','-Wextra','-Wpedantic','cpp/ftsk.cpp','-o',str(native))
        execute(sys.executable,'cpp/test_interop.py',str(native))
        execute(sys.executable,'cpp/test_blind.py',str(native))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('archive',type=Path)
    p.add_argument('--compiler',default='g++')
    a=p.parse_args()
    if not shutil.which(a.compiler):
        p.error('C++17 compiler not found')
    validate(a.archive,a.compiler)
