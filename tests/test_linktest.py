import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

import numpy as np
from frame import encode_frame
from linktest import Counts, confidence, main, probe


class LinkTest(unittest.TestCase):
    def test_duplicates_foreign_and_missed(self):
        tid = bytes(8)
        counts = Counts(tid, 5)
        validation = probe(tid, 4)
        for payload in [probe(tid, 3), probe(tid, 0), probe(tid, 0), probe(bytes([1])*8, 0), validation[:-1]+bytes([validation[-1]^1]), probe(tid, 6)]:
            counts.feed(SimpleNamespace(payload=payload, cfo_hz=4000))
        s = counts.summary()
        self.assertEqual((s['missed'], s['duplicate'], s['foreign'], s['invalid']), (3, 1, 1, 2))
        self.assertEqual(s['first_missing_indices'], [1, 2, 4])
        self.assertLess(s['per_95pct_wilson'][0], s['per'])
        self.assertGreater(s['per_95pct_wilson'][1], s['per'])

    def test_file_normal_entries(self):
        with tempfile.TemporaryDirectory() as d:
            iq = Path(d)/'probes.cf32'
            args = ['--iq', str(iq), '--id', '0123456789abcdef', '--count', '3']
            with redirect_stdout(StringIO()):
                self.assertEqual(main(['generate', *args]), 0)
            with redirect_stdout(StringIO()) as result:
                self.assertEqual(main(['analyze', *args]), 0)
            self.assertEqual(json.loads(result.getvalue())['received_unique'], 3)
            one = len(encode_frame(bytes(32)))
            samples = np.fromfile(iq, '<c8')
            samples[one:2*one] = 0
            samples.tofile(iq)
            with redirect_stdout(StringIO()) as result:
                self.assertEqual(main(['analyze', *args]), 0)
            self.assertEqual(json.loads(result.getvalue())['first_missing_indices'], [1])
            self.assertGreater(confidence(0, 100)[1], .03)
