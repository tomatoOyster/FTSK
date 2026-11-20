"""End-to-end tests for file transfer through the SoapySDR sample stream."""
import hashlib
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from itertools import cycle
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

import radio_file


class FileRadioTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.src = self.root / 'input.bin'
        self.dst = self.root / 'received.bin'
        self.content = bytes(range(251))
        self.src.write_bytes(self.content)
        self.args = ['--rate', '1024000', '--id', '0123456789', '--freq', '915000000']
        self.dev = Mock()
        self.dev.getSampleRate.return_value = 1_024_000
        self.dev.getStreamMTU.return_value = 2309
        self.dev.activateStream.return_value = self.dev.deactivateStream.return_value = 0
        module = SimpleNamespace(Device=lambda selector: self.dev, SOAPY_SDR_TX=0, SOAPY_SDR_RX=1,
                                 SOAPY_SDR_CF32='CF32', SOAPY_SDR_END_BURST=2, SOAPY_SDR_TIMEOUT=-1,
                                 errToStr=lambda code: f'device error {code}')
        self.enter_patch(patch.dict(sys.modules, SoapySDR=module))
        self.enter_patch(patch('radio_file.time.sleep'))

    def enter_patch(self, mock):
        mock.start()
        self.addCleanup(mock.stop)

    def run_cli(self, mode, path, *extra):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            status = radio_file.main([mode, str(path), *self.args, *extra])
        return status, out.getvalue(), err.getvalue()

    def transmit(self):
        blocks, end_markers = [], []
        partials = cycle((653, 221, 1023, 799))
        total = 0

        def write(stream, buffers, count, flags, timeoutUs):
            nonlocal total
            kept = min(count, next(partials))
            if flags:
                end_markers.append((total, count, flags))
            blocks.append(buffers[0][:kept].copy())
            total += kept
            return SimpleNamespace(ret=kept)

        self.dev.writeStream.side_effect = write
        status, out, err = self.run_cli('tx', self.src)
        self.assertEqual(status, 0, err)
        meta = json.loads(out)
        self.assertEqual(meta['samples_queued'], total)
        self.assertEqual(end_markers, [(total-1, 1, 2)])
        self.assertEqual(meta['records_queued'], 19)  # 13 data, two XOR, four manifest
        samples = np.concatenate(blocks)
        # Device rate is eight times the baseband rate. The TX burst ends with a 128-sample tail.
        record_samples, remainder = divmod(len(samples)-128*8, meta['records_queued'])
        self.assertEqual(remainder, 0)
        return samples, record_samples

    def receive(self, samples, drop_at=None):
        # Start the RX sampling clock between two TX baseband boundaries.
        samples = np.pad(samples, (19, 0))
        position, expired = 0, False
        sizes = cycle((513, 1091, 243, 2217))

        def read(stream, buffers, count, timeoutUs):
            nonlocal position, expired
            if drop_at is not None and position >= drop_at:
                return SimpleNamespace(ret=-4)
            n = min(count, next(sizes), len(samples)-position)
            if not n:
                expired = True
                return SimpleNamespace(ret=-1)
            buffers[0][:n] = samples[position:position+n]
            position += n
            return SimpleNamespace(ret=n)

        self.dev.readStream.side_effect = read
        with patch('radio_file.time.monotonic', side_effect=lambda: 2 if expired else 0):
            return self.run_cli('rx', self.dst, '--force', '--seconds', '1')

    def test_burst_with_erased_frames(self):
        samples, stride = self.transmit()
        # Two leading manifests; erase one data record from each of two groups.
        for record_index in (2, 11):
            samples[record_index*stride:(record_index+1)*stride] = 0
        self.dst.write_bytes(b'previous content')
        status, out, err = self.receive(samples)
        self.assertEqual(status, 0, err)
        meta = json.loads(out)
        self.assertEqual(meta['transfer_id'], '0123456789')
        self.assertEqual(meta['sha256'], hashlib.sha256(self.content).hexdigest())
        self.assertEqual(meta['recovered'], 2)
        self.assertEqual(self.dst.read_bytes(), self.content)
        self.assertEqual(self.dev.closeStream.call_count, 2)

    def test_two_erased_in_same_group_preserves_destination(self):
        samples, stride = self.transmit()
        samples[2*stride:4*stride] = 0
        self.dst.write_bytes(b'keep this')
        status, out, err = self.receive(samples)
        self.assertEqual(status, 1)
        self.assertIn('unrecoverable', err)
        self.assertEqual(self.dst.read_bytes(), b'keep this')
        self.assertEqual(set(self.root.iterdir()), {self.src, self.dst})
        self.assertEqual(self.dev.closeStream.call_count, 2)

    def test_driver_disconnect_preserves_destination(self):
        samples, stride = self.transmit()
        self.dst.write_bytes(b'keep this')
        status, out, err = self.receive(samples, drop_at=stride*4)
        self.assertEqual(status, 1)
        self.assertIn('readStream', err)
        self.assertEqual(self.dst.read_bytes(), b'keep this')
        self.assertEqual(set(self.root.iterdir()), {self.src, self.dst})
        self.assertEqual(self.dev.deactivateStream.call_count, 2)
        self.assertEqual(self.dev.closeStream.call_count, 2)


if __name__ == '__main__':
    unittest.main()
