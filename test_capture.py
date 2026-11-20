from contextlib import redirect_stdout, redirect_stderr
from io import BytesIO, StringIO
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

import numpy as np
from capture import main, write_capture
from frame import encode_frame
from radio import FS


class CaptureTest(unittest.TestCase):
    def test_hardware_timestamp_gap(self):
        dev = Mock()
        dev.readStream.side_effect = [NS(ret=3, flags=4, timeNs=0), NS(ret=3, flags=4, timeNs=90000)]
        mod = NS(SOAPY_SDR_HAS_TIME=4, SOAPY_SDR_TIMEOUT=-1)
        with self.assertRaisesRegex(RuntimeError, 'timestamp discontinuity'):
            write_capture(mod, dev, None, 3, NS(rate=FS, seconds=6/FS), BytesIO())

    def test_record_replay_sidecar(self):
        frame = encode_frame(bytes(range(32)))
        dev = Mock()
        dev.getSampleRate.return_value = FS
        dev.getStreamMTU.return_value = 319
        dev.activateStream.return_value = dev.deactivateStream.return_value = 0
        mod = NS(SOAPY_SDR_HAS_TIME=4, SOAPY_SDR_TIMEOUT=-1, SOAPY_SDR_CF32='CF32',
                 SOAPY_SDR_RX=1, Device=lambda _:dev)
        pos = 0
        def read(stream, arr, amount, timeoutUs):
            nonlocal pos
            n = min(amount, 211)
            arr[0][:n] = frame[pos:pos+n]
            stamp = round(pos*1e9/FS)
            pos += n
            return NS(ret=n, flags=4, timeNs=stamp)
        dev.readStream.side_effect = read
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'capture.cf32'
            with patch.dict(sys.modules, SoapySDR=mod), redirect_stdout(StringIO()):
                self.assertEqual(main(['record', str(path), '--rate', str(FS), '--freq', '100000000', '--seconds','0.035']), 0)
            self.assertEqual(path.stat().st_size, len(frame)*8)
            with redirect_stdout(StringIO()) as out:
                self.assertEqual(main(['replay', str(path), '--span','0']), 0)
            self.assertEqual(json.loads(out.getvalue())['payload_hex'], bytes(range(32)).hex())
            with path.open('r+b') as dest:
                dest.write(b'1234')
            with redirect_stderr(StringIO()) as err:
                self.assertEqual(main(['replay', str(path), '--span','0']), 1)
                self.assertIn('digest', err.getvalue())
