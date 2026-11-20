"""Small shared safe-output helpers for binary CLI tools."""
from contextlib import contextmanager
import os
from pathlib import Path
import tempfile


def check_distinct(source, output):
    src, dst = Path(source), Path(output)
    if src.resolve() == dst.resolve() or (dst.exists() and src.exists() and src.samefile(dst)):
        raise ValueError('input and output must be distinct files')


@contextmanager
def atomic_writer(output, force=False):
    """Stage beside destination. A hard link prevents replacing a concurrent creator."""
    output = Path(output)
    if not force and output.exists():
        raise FileExistsError(f'output exists: {output}')
    temp = tempfile.NamedTemporaryFile(dir=output.parent, prefix='.ftsk-', delete=False)
    path = Path(temp.name)
    try:
        with temp as stream:
            yield stream
            stream.flush()
            os.fsync(stream.fileno())
        if force:
            os.replace(path, output)
        else:
            os.link(path, output)
    finally:
        path.unlink(missing_ok=True)
