"""Profile selection at the transfer, radio and radio-file command boundaries."""
from collections import deque
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase, main
from unittest.mock import Mock, patch
import sys

import numpy as np
import profiles, radio, radio_file, transfer


class ProfileIO(TestCase):
    def test_file_cli(self):
        with TemporaryDirectory() as tmp, redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            root = Path(tmp)
            src, capture, out = (root / name for name in ('src','iq','out'))
            src.write_bytes(bytes(range(93)))
            transfer.main(['encode', str(src), str(capture), '--profile', '8'])
            transfer.main(['decode', str(capture), str(out), '--profile', '8'])
            self.assertEqual(src.read_bytes(), out.read_bytes())
            with capture.open('wb') as f:
                for i, record in enumerate(transfer.iter_records(src)):
                    profiles.encode(record, (4,8)[i%2]).astype('<c8').tofile(f)
            transfer.main(['decode', str(capture), str(out), '--profile','auto','--force'])
            self.assertEqual(src.read_bytes(), out.read_bytes())

    def test_atomic_cli_and_aliases(self):
        import os
        with TemporaryDirectory() as tmp, redirect_stderr(StringIO()):
            root = Path(tmp)
            src, alias, out = (root / n for n in ('source','alias','out'))
            src.write_bytes(bytes(32))
            os.link(src, alias)
            for cli in (profiles.main,transfer.main):
                with self.assertRaises(SystemExit):
                    cli(['encode',str(src),str(alias),'--force'])
                self.assertEqual(src.read_bytes(), bytes(32))
            out.write_bytes(b'keep')
            with self.assertRaises(SystemExit):
                profiles.main(['encode',str(src),str(out)])
            self.assertEqual(out.read_bytes(), b'keep')
            profiles.main(['encode',str(src),str(out),'--profile','8','--force'])
            self.assertEqual(out.stat().st_size, profiles.LENGTH[8]*8)

    def test_memory_radio(self):
        rate = 256000
        dev = Mock()
        dev.getSampleRate.return_value = rate
        dev.getStreamMTU.return_value = 1201
        dev.activateStream.return_value = dev.deactivateStream.return_value = 0
        module = SimpleNamespace(Device=lambda s:dev, SOAPY_SDR_TX=0, SOAPY_SDR_RX=1, SOAPY_SDR_CF32='CF32',
                                 SOAPY_SDR_END_BURST=1, SOAPY_SDR_TIMEOUT=-1, errToStr=str)
        queue = deque()
        clock = 0
        def write(stream, buffers, count, **kw):
            nonlocal clock
            n = min(count, 847)
            rotation = np.exp(2j*np.pi*12000*(clock+np.arange(n))/rate)
            queue.append(buffers[0][:n]*rotation)
            clock += n
            return SimpleNamespace(ret=n)
        def read(stream, buffers, count, **kw):
            piece = queue.popleft()
            buffers[0][:len(piece)] = piece
            return SimpleNamespace(ret=len(piece))
        dev.writeStream.side_effect = write
        dev.readStream.side_effect = read
        base = ['--freq','915000000','--rate',str(rate)]
        rx = ['--profile','auto','--offset-hz','12000','--seconds','1']
        out = StringIO()
        with patch.dict(sys.modules, SoapySDR=module), patch('radio.time.sleep'), redirect_stdout(out), TemporaryDirectory() as tmp:
            for k in ('8','4'):
                self.assertEqual(radio.main(['tx',*base,'--profile',k,'--payload-hex',k.zfill(2)*32]),0)
            with patch('radio.time.monotonic', side_effect=lambda: 0 if queue else 2):
                self.assertEqual(radio.main(['rx',*base,*rx]),0)
            for k in ('8','4'):
                self.assertRegex(out.getvalue(),rf'profile={k} start=\d+ cfo_hz=12000.00 payload={k.zfill(2)*32}')
            root = Path(tmp)
            src, dest = root/'source', root/'dest'
            src.write_bytes(b'Profile integration test through both SDR file commands')
            self.assertEqual(radio_file.main(['tx',str(src),*base,'--profile','8']),0)
            with patch('radio.time.monotonic', side_effect=lambda: 0 if queue else 2):
                self.assertEqual(radio_file.main(['rx',str(dest),*base,*rx]),0)
            self.assertEqual(src.read_bytes(),dest.read_bytes())

    def test_receiver_options(self):
        with self.assertRaisesRegex(ValueError, 'profile 4'):
            radio.acquisition_backend(True, 'auto')
        for options in [dict(fast=True),dict(profile='8')]:
            with self.assertRaisesRegex(ValueError, '--tracked'):
                radio.acquisition_backend(tracked=True, **options)
        self.assertEqual(type(radio.acquisition_backend(True, offset_hz=1000)[0]).__name__, 'FastReceiver')
        self.assertEqual(type(radio.acquisition_backend(tracked=True, offset_hz=12000)[0]).__name__, 'TrackingReceiver')
        rx,_ = radio.acquisition_backend(profile='8')
        wave = np.concatenate([profiles.encode(bytes(32),4), profiles.encode(bytes(32),8)])
        self.assertEqual([p.profile for p in rx.feed(wave)], [8])

if __name__ == '__main__':
    main()
