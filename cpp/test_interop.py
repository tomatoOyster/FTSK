"""Exercise the native CLI using independent vectors and the Python modem."""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.dont_write_bytecode = True
from frame import encode_frame
from receiver import Receiver

# Published vector, transcribed independently of either implementation.
VECTOR = [0,4,16,48,128,321,259,264,18,40,88,192,417,387,392,17,36,76,160,337,194,454,12,282,54,112,232,481,484,236,384]
PREAMBLE = [5,22,11,26,3,17,8,24,13,2,20,10,27,6,19,4]


def independent_wave(symbols):
    indices = PREAMBLE + [(m % 31 + (m // 31)*x) % 31 for m in symbols for x in (0,1,3,7)]
    return np.exp(2j*np.pi*(np.array(indices)[:,None]-15)*np.arange(32)/32).astype('<c8').ravel()


class Interop(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(dir=HERE)
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)

    def invoke(self, op, data, reason=None):
        source, target = self.base/'input', self.base/'output'
        source.write_bytes(data)
        sentinel = b'existing output must survive rejected input'
        if reason:
            target.write_bytes(sentinel)
        elif target.exists():
            self.assertEqual(target.resolve().parent, self.base.resolve())
            target.unlink()
        run = subprocess.run([EXE, op, source, target], capture_output=True, timeout=10)
        detail = run.stderr.decode(errors='replace')
        if reason:
            self.assertEqual(run.returncode, 1, detail)
            self.assertIn(reason, detail)
            self.assertEqual(target.read_bytes(), sentinel)
        else:
            self.assertEqual(run.returncode, 0, detail)
            return target.read_bytes()

    def test_golden_vector(self):
        reference = independent_wave(VECTOR)
        native = self.invoke('encode', bytes(range(32)))
        np.testing.assert_allclose(np.frombuffer(native,'<c8'), reference, atol=2e-6, rtol=0)
        self.assertEqual(self.invoke('decode',reference.tobytes()), bytes(range(32)))

    def test_both_directions(self):
        rng = np.random.default_rng(823)
        for payload in [bytes(32), b'\xff'*32, *[rng.bytes(32) for _ in range(32)]]:
            with self.subTest(payload=payload.hex()):
                native = np.frombuffer(self.invoke('encode', payload), '<c8')
                py = encode_frame(payload)
                np.testing.assert_allclose(native, py, atol=2e-6, rtol=0)
                self.assertEqual([p.payload for p in Receiver().feed(native)], [payload])
                iq = py*np.exp(0.74j) + 0.25*(rng.normal(size=len(py))+1j*rng.normal(size=len(py)))
                self.assertEqual(self.invoke('decode', iq.astype('<c8').tobytes()), payload)

    def test_preserve_existing_and_invalid_paths(self):
        import os
        src = self.base/'source'
        src.write_bytes(bytes(range(32)))
        alias = self.base/'alias'; os.link(src,alias)
        existing = self.base/'existing'; existing.write_bytes(b'saved')
        for dest in (alias, src, existing, self.base/'missing'/'child', existing/'child'):
            result = subprocess.run([EXE,'encode',src,dest],capture_output=True)
            self.assertEqual(result.returncode,1)
            self.assertTrue(result.stderr)
            self.assertEqual(src.read_bytes(),bytes(range(32)))
            self.assertEqual(existing.read_bytes(),b'saved')
            self.assertFalse(list(self.base.glob('.ftsk-stage-*')))
        dest = self.base/'race'
        jobs = [subprocess.Popen([EXE,'encode',src,dest],stderr=subprocess.DEVNULL) for _ in range(8)]
        self.assertEqual(sorted(p.wait(timeout=10) for p in jobs),[0]+[1]*7)
        np.testing.assert_allclose(np.fromfile(dest,'<c8'), independent_wave(VECTOR),atol=2e-6,rtol=0)
        self.assertFalse(list(self.base.glob('.ftsk-stage-*')))

    def test_bad_inputs(self):
        good = independent_wave(VECTOR)
        for n in (31,33):
            self.invoke('encode',bytes(n),'exactly 32')
        raw = good.tobytes()
        for data in (raw[:-1],raw+b'\0'):
            self.invoke('decode',data,'exactly 35840')
        for i, mask, reason in ((0,1,'CRC mismatch'),(-1,1,'padding')):
            symbols = VECTOR.copy()
            symbols[i] ^= mask
            self.invoke('decode',independent_wave(symbols).tobytes(),reason)
        bad = good.copy()
        bad[:512] = 0
        self.invoke('decode',bad.tobytes(),'header correlation')
        bad[100] = np.nan
        self.invoke('decode',bad.tobytes(),'non-finite')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('executable',type=Path)
    args = parser.parse_args()
    EXE = args.executable.resolve(strict=True)
    unittest.main(argv=[sys.argv[0]], verbosity=2)
