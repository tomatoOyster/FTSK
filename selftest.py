"""Validate the installed reference package offline, including actual CLI-to-CLI file transfer."""
from contextlib import redirect_stdout, redirect_stderr
import hashlib
from io import StringIO
import json
from pathlib import Path
import tempfile

import numpy as np
from frame import encode_frame
from receiver import Receiver
from transfer import main as transfer_cli


def selftest():
    root=Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory() as tmp:
        work=Path(tmp)
        payload=(root/'examples'/'example.bin').read_bytes()
        vector=np.fromfile(root/'examples'/'example.cf32', '<c8')
        gen=encode_frame(payload)
        np.testing.assert_allclose(gen,vector,atol=2e-6,rtol=0)
        pkts=Receiver().feed(vector)
        if len(pkts)!=1 or pkts[0].payload!=payload:
            raise ValueError('golden packet not recovered')
        # Mixed binary data, including NUL and non-UTF8 octets.
        original=bytes(range(256))+b'\x00\xff\x00'
        src=work/'source'; src.write_bytes(original)
        iq=work/'test.cf32'; result=work/'restored'
        with redirect_stdout(StringIO()) as out, redirect_stderr(StringIO()):
            transfer_cli(['encode',str(src),str(iq),'--id','0102030405'])
            transfer_cli(['decode',str(iq),str(result)])
        info=json.loads(out.getvalue())
        if result.read_bytes()!=original or info['sha256']!=hashlib.sha256(original).hexdigest():
            raise ValueError('transferred file digest mismatch')
        return dict(numpy=np.__version__, golden_vector_samples=len(vector),
                    verified_file_bytes=len(original), sha256=info['sha256'],physical_radio_used=False)


if __name__=='__main__':
    print(json.dumps(selftest(),indent=2))
