import unittest
import numpy as np

import frame
from receiver import Receiver
from profiles import ABSCISSAS, BOOK8, HEADERS, LENGTH, TONE, DualReceiver, encode


class DualProfileTests(unittest.TestCase):
    def test_wire_compatibility(self):
        payload = bytes(range(32))
        self.assertEqual(encode(payload, 4).tobytes(), frame.encode_frame(payload).tobytes())
        self.assertEqual(Receiver().feed(encode(payload, 4))[0].payload, payload)
        np.testing.assert_array_equal(BOOK8[:, :4], frame.BOOK)
        np.testing.assert_array_equal(BOOK8[163], [8,13,23,12,6,5,30,29])
        self.assertEqual(len(set(ABSCISSAS)), 8)
        self.assertEqual(LENGTH, {4:4480, 8:8448})
        with self.assertRaises(ValueError):
            encode(payload, 6)
        for i in range(511):
            self.assertGreaterEqual(np.count_nonzero(BOOK8[i+1:] != BOOK8[i], axis=1).min(), 7)

    def test_mixed_adjacent_noise_cfo(self):
        rng = np.random.default_rng(2026)
        start = 259
        data = [np.zeros(start, dtype=complex)]
        expected = []
        for profile, cfo in ((8,6000), (4,-6000), (4,4062.5), (8,-3021.5)):
            payload = rng.bytes(32)
            wave = encode(payload, profile)
            data.append(wave * np.exp(1j*(rng.uniform(-np.pi,np.pi) + 2*np.pi*cfo*np.arange(len(wave))/128000)))
            expected.append((start, profile, payload))
            start += len(wave)
        iq = np.concatenate(data)
        iq += .7*(rng.normal(size=len(iq)) + 1j*rng.normal(size=len(iq)))
        for chunk_size in (173, len(iq)):
            rx = DualReceiver()
            packets = [p for i in range(0,len(iq),chunk_size) for p in rx.feed(iq[i:i+chunk_size])]
            self.assertEqual([(p.start, p.profile, p.payload) for p in packets], expected)

    def test_bad_slow_then_valid_fast_at_eof(self):
        payload = bytes(range(32))
        wave = encode(payload, 8)
        symbols = frame.pack_symbols(payload)
        wave[512:768] = TONE[BOOK8[symbols[0] ^ 128]].ravel()
        rx = DualReceiver()
        self.assertEqual(rx.feed(wave), [])
        # Also inject a truncated slow header+body directly before a fast frame.
        # A pending slow candidate must never block a complete fast frame at EOF.
        self.assertEqual(rx.feed(wave[:700]), [])
        packets = rx.feed(encode(payload, 4))
        self.assertEqual([(p.start,p.profile,p.payload) for p in packets], [(8448+700,4,payload)])

    def test_noise_and_eight_slot_errors(self):
        rng = np.random.default_rng(77)
        rx = DualReceiver()
        self.assertEqual(rx.feed(rng.normal(size=32000)+1j*rng.normal(size=32000)), [])
        payload = bytes(range(32))
        wave = encode(payload, 8)
        body = wave[len(HEADERS[8]):].reshape(31,8,32)
        # Replace three of eight slots in every symbol by another symbol's tones.
        body[:, [0,2,5]] = TONE[BOOK8[frame.pack_symbols(payload) ^ 128][:,[0,2,5]]]
        self.assertEqual(rx.feed(wave)[0].payload, payload)


if __name__ == '__main__':
    unittest.main()
