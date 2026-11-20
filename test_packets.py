import binascii
import unittest
import numpy as np
from frame import HEADER, FRAME_SAMPLES, encode_frame, pack_symbols, decode_body
from ftsk_demo import transmit
from receiver import Receiver


class PacketTests(unittest.TestCase):
    payload = bytes(range(32))

    def test_wire_vector(self):
        self.assertEqual(binascii.crc_hqx(self.payload, 65535), 0x23b3)
        self.assertEqual(pack_symbols(self.payload).tolist(), [0,4,16,48,128,321,259,264,18,40,88,192,417,387,392,17,36,76,160,337,194,454,12,282,54,112,232,481,484,236,384])
        self.assertEqual(FRAME_SAMPLES, 4480)
        for length in (0, 31, 33):
            with self.assertRaises(ValueError):
                encode_frame(bytes(length))

    def test_crc_and_pad(self):
        for index, mask in [(0, 64), (-1, 1)]:
            symbols = pack_symbols(self.payload)
            symbols[index] ^= mask
            self.assertIsNone(decode_body(transmit(symbols).ravel()))

    def test_impulsive_slot(self):
        body = encode_frame(self.payload)[len(HEADER):].copy()
        body[32:64] = 100*np.exp(2j*np.pi*9*np.arange(32)/32)
        self.assertEqual(decode_body(body), self.payload)

    def test_adjacent_packets_after_corruption(self):
        good = encode_frame(self.payload)
        symbols = pack_symbols(self.payload)
        symbols[4] ^= 77
        bad = np.concatenate([HEADER, transmit(symbols).ravel()])
        data = np.concatenate([bad, good, encode_frame(self.payload[::-1])])
        rx = Receiver()
        packets = []
        for i in range(0, len(data), 131):
            packets.extend(rx.feed(data[i:i+131]))
        self.assertEqual([p.payload for p in packets], [self.payload, self.payload[::-1]])
        self.assertEqual([p.start for p in packets], [FRAME_SAMPLES, 2*FRAME_SAMPLES])

    def test_unknown_offset_phase_and_chunking(self):
        rng = np.random.default_rng(77)
        for cfo in [-6000, -4000, -1324, 0, 377, 4000, 6000]:
            with self.subTest(cfo=cfo):
                rx = Receiver()
                offset = int(rng.integers(0, 2100))
                s = np.pad(encode_frame(self.payload), (offset, 0))
                iq = s * np.exp(1j*(1.1 + 2*np.pi*cfo*np.arange(len(s))/128000))
                iq += 0.35*(rng.normal(size=len(iq)) + 1j*rng.normal(size=len(iq)))
                packets = []
                i = 0
                while i < len(iq):
                    end = i + int(rng.integers(1, 1200))
                    packets.extend(rx.feed(iq[i:end]))
                    i = end
                self.assertEqual([p.payload for p in packets], [self.payload])
                self.assertEqual(packets[0].start, offset)
                self.assertLess(abs(packets[0].cfo_hz-cfo), 100)

    def test_noise_and_truncation(self):
        rng = np.random.default_rng(99)
        rx = Receiver()
        for _ in range(64):
            self.assertFalse(rx.feed(rng.normal(size=2048)+1j*rng.normal(size=2048)))
        self.assertFalse(rx.feed(encode_frame(self.payload)[:600]))


if __name__ == '__main__':
    unittest.main()
