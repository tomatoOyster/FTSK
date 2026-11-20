from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

import numpy as np
from radio import FS, interpolate, read_packets
from receiver import Receiver
from frame import encode_frame


class CalibrationTest(unittest.TestCase):
    def test_clock_offset_before_downsampling(self):
        # All-zero payload uses the outermost low-frequency tone over much of the body.
        payload = bytes(32)
        frame = interpolate(np.pad(encode_frame(payload), (96, 96)), 8)
        for cfo in (-18300, -6000, 18300):
            with self.subTest(cfo=cfo):
                pos = 0
                signal = frame*np.exp(2j*np.pi*cfo*np.arange(len(frame))/(FS*8))
                def read(stream, buffers, size, timeoutUs):
                    nonlocal pos
                    count = min(size, 797, len(frame)-pos)
                    buffers[0][:count] = signal[pos:pos+count]
                    pos += count
                    return NS(ret=count)
                device = Mock()
                device.readStream.side_effect = read
                with patch('radio.time.monotonic', side_effect=lambda: 0 if pos<len(frame) else 2):
                    packets = list(read_packets(NS(SOAPY_SDR_TIMEOUT=-1), device, None, 2048, 8, 1, Receiver(), offset_hz=cfo))
                self.assertEqual([p.payload for p in packets], [payload])
                self.assertLess(abs(packets[0].cfo_hz-cfo), 10)
