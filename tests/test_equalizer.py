import unittest
from unittest.mock import patch

import numpy as np

from channels.study import CASES, SEED, capture, noise_and_reference
from equalizer import EqualizedReceiver, retry
from frame import HEADER, encode_frame, pack_symbols
from ftsk_demo import transmit
from receiver import Receiver


class EqualizerTests(unittest.TestCase):
    def test_preserves_clean_packet(self):
        payload = bytes(range(32))
        with patch('equalizer.retry', side_effect=AssertionError('unexpected retry')):
            packets = EqualizedReceiver().feed(encode_frame(payload))
        self.assertEqual([p.payload for p in packets], [payload])

    def test_known_echo_replay(self):
        for index, name, expected in [(2,'echo2',False),(3,'echo2',True),(5,'echo20',True),(4,'echo80',True)]:
            with self.subTest(index=index, name=name):
                ref = noise_and_reference(index, SEED, 8)
                iq = capture(ref, CASES[name], -3, 8)
                self.assertEqual(Receiver().feed(iq), [])
                receiver = EqualizedReceiver()
                packets = []
                for i in range(0, len(iq), 317):
                    packets.extend(receiver.feed(iq[i:i+317]))
                self.assertEqual([p.payload for p in packets], [ref[0]] if expected else [])

    def test_rejects_invalid_crc_and_noise(self):
        symbols = pack_symbols(bytes(range(32)))
        symbols[0] ^= 4
        bad = np.r_[HEADER, transmit(symbols).ravel()]
        self.assertIsNone(retry(bad, 0))
        self.assertEqual(EqualizedReceiver().feed(bad), [])
        rng = np.random.default_rng(256)
        noise = rng.normal(size=20000)+1j*rng.normal(size=20000)
        self.assertEqual(EqualizedReceiver().feed(noise), [])


if __name__ == '__main__':
    unittest.main()
