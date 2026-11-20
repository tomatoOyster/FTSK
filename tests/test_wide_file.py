from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path
import tempfile
from unittest import TestCase

import numpy as np
from capture import main
from frame import encode_frame
from radio import FS, interpolate
from transfer import iter_records


class WideFileTest(TestCase):
    def test_recover_file_at_large_unknown_offset(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp)
            source=base/'source'; result=base/'received'; path=base/'air.cf32'
            data=bytes(range(97))
            source.write_bytes(data)
            records=list(iter_records(source,transfer_id=b'abcde'))
            pieces=[]
            for i,record in enumerate(records):
                tx=interpolate(np.pad(encode_frame(record),(64,64)),8)
                if i==3: tx[:]=0  # one erased data frame, parity must restore it
                pieces.append(tx)
            signal=np.concatenate(pieces)
            signal*=np.exp(2j*np.pi*18300*np.arange(len(signal))/(FS*8))
            signal.astype('<c8').tofile(path)
            with redirect_stdout(StringIO()) as out:
                code=main(['replay',str(path),'--rate','1024000','--span','20000','--fast','--output-file',str(result),'--id',b'abcde'.hex()])
            self.assertEqual(code,0)
            self.assertIn('"recovered": 1',out.getvalue())
            self.assertEqual(result.read_bytes(),data)
