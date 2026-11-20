"""Non-finite sample rejection preserves receiver state and reports absolute indices."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from fast_acquire import FastReceiver
from frame import encode_frame
from profiles import DualReceiver
from receiver import Receiver
from wide_rx import WideReceiver


class FiniteIQTests(unittest.TestCase):
    def test_base_and_profile_no_partial_commit(self):
        frame = encode_frame(bytes(32))
        for factory in [Receiver, FastReceiver, DualReceiver]:
            for value in (np.nan, np.inf, complex(0, -np.inf)):
                with self.subTest(factory=factory, value=value):
                    rx = factory()
                    self.assertEqual(rx.feed(frame[:1100]), [])
                    corrupted = frame[1100:].copy()
                    corrupted[1777] = value
                    with self.assertRaisesRegex(ValueError, 'non-finite IQ at sample 2877'):
                        rx.feed(corrupted)
                    self.assertEqual([(p.start,p.payload) for p in rx.feed(frame[1100:])], [(0, bytes(32))])

    def test_device_input_before_split(self):
        rx = WideReceiver(1, 0)
        signal = encode_frame(bytes(32))
        rx.feed(signal[:200])
        bad = signal[200:].copy()
        bad[-1] = np.inf
        with self.assertRaisesRegex(ValueError,'non-finite IQ at sample 4479'):
            rx.feed(bad)
        self.assertEqual([(p.start,p.payload) for p in rx.feed(signal[200:])], [(0, bytes(32))])

    def test_packet_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'invalid.cf32'
            samples = np.zeros(4098, '<c8')
            samples[-1] = np.nan
            samples.tofile(path)
            proc = subprocess.run([sys.executable,'-B',str(Path(__file__).resolve().parents[1]/'ftsk.py'),
                                   'packet','decode',str(path)],capture_output=True,text=True,timeout=30)
            self.assertNotEqual(proc.returncode,0)
            self.assertIn('non-finite IQ at sample 4097',proc.stderr)
            self.assertFalse(proc.stdout)


if __name__ == '__main__':
    unittest.main()
