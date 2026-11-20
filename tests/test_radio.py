"""Hardware-adapter unit tests. All device calls use Python mocks."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np

import radio


MODULE = NS(SOAPY_SDR_CF32='CF32', SOAPY_SDR_END_BURST=2, SOAPY_SDR_TIMEOUT=-1,
            errToStr=lambda code: f'fake error {code}')


class RadioTests(unittest.TestCase):
    def test_polyphase_and_streaming_decimator(self):
        rng = np.random.default_rng(17)
        iq = (rng.normal(size=200) + 1j*rng.normal(size=200)).astype(np.complex64)
        for factor in (1, 3, 8):
            up = radio.interpolate(iq, factor)
            if factor > 1:
                z = np.zeros(len(iq)*factor, np.complex64)
                z[::factor] = iq
                h = radio.fir(factor)
                np.testing.assert_allclose(up, np.convolve(z, h*factor), atol=2e-6)
                expected = np.convolve(up, h)[:len(up):factor]
            else:
                expected = iq
            dec = radio.Decimator(factor)
            result = np.concatenate([dec.feed(up[i:i+17]) for i in range(0, len(up), 17)])
            np.testing.assert_allclose(result, expected, atol=3e-6)

    def test_two_cli_commands_via_memory_device(self):
        from contextlib import redirect_stdout
        from io import StringIO
        import sys

        payload = bytes(range(32))
        dev = Mock()
        rate = 1_024_000
        dev.getSampleRate.return_value = rate
        dev.getStreamMTU.return_value = 1024
        dev.activateStream.return_value = dev.deactivateStream.return_value = 0
        module = NS(**vars(MODULE), Device=lambda selector: dev, SOAPY_SDR_TX=0, SOAPY_SDR_RX=1)
        queue = []
        def write(stream, buffers, count, **kwargs):
            n = min(513, count)
            queue.append(buffers[0][:n].copy())
            return NS(ret=n)
        def read(stream, buffers, count, **kwargs):
            piece = queue.pop(0)
            buffers[0][:len(piece)] = piece
            return NS(ret=len(piece))
        dev.writeStream.side_effect = write
        dev.readStream.side_effect = read
        args = ['--rate', str(rate), '--freq', '915000000']
        output = StringIO()
        with patch.dict(sys.modules, SoapySDR=module), redirect_stdout(output):
            with patch.object(radio.time, 'sleep'):
                self.assertEqual(radio.main(['tx', *args, '--payload-hex', payload.hex()]), 0)
            with patch.object(radio.time, 'monotonic', side_effect=lambda: 0 if queue else 2):
                self.assertEqual(radio.main(['rx', *args, '--seconds', '1']), 0)
        self.assertIn(f'payload={payload.hex()}', output.getvalue())
        self.assertEqual(dev.closeStream.call_count, 2)

    def test_short_writes_and_end_marker(self):
        samples = np.arange(31, dtype=np.complex64)
        captured = []
        flags_used = []
        def write(stream, buffers, count, flags, timeoutUs):
            n = min(count, 3)
            captured.extend(buffers[0][:n])
            flags_used.append(flags)
            return NS(ret=n)
        dev = Mock()
        dev.writeStream.side_effect = write
        radio.write_samples(MODULE, dev, None, 8, samples)
        np.testing.assert_array_equal(captured, samples)
        self.assertEqual(flags_used, [0]*(len(flags_used)-1) + [2])

    def test_reads_with_timeout_and_short_buffers(self):
        dev, receiver = Mock(), Mock()
        pieces = iter((None, [1, 2], [3]))
        def read(stream, buffers, count, timeoutUs):
            piece = next(pieces)
            if piece is None:
                return NS(ret=-1)
            buffers[0][:len(piece)] = piece
            return NS(ret=len(piece))
        dev.readStream.side_effect = read
        receiver.feed.side_effect = lambda block: list(block.real)
        with patch.object(radio.time, 'monotonic', side_effect=[0, 0, 0, 0, 2]):
            self.assertEqual(list(radio.read_packets(MODULE, dev, None, 10, 1, 1, receiver)), [1, 2, 3])

    def test_timeout_and_device_error(self):
        dev = Mock()
        dev.writeStream.return_value = NS(ret=0)
        with self.assertRaisesRegex(RuntimeError, 'stalled'):
            radio.write_samples(MODULE, dev, None, 8, np.ones(12, np.complex64))
        dev.readStream.return_value = NS(ret=-4)
        with self.assertRaisesRegex(RuntimeError, 'readStream'):
            list(radio.read_packets(MODULE, dev, None, 10, 1, 1, Mock()))

    def test_stream_cleanup_and_rate_verification(self):
        dev = Mock()
        opts = NS(rate=radio.FS, freq=1e8, gain=None, channel=0)
        dev.getSampleRate.return_value = radio.FS
        dev.getStreamMTU.return_value = 64
        dev.activateStream.return_value = dev.deactivateStream.return_value = 0
        with self.assertRaisesRegex(RuntimeError, 'fake failure'):
            with radio.open_stream(MODULE, dev, 0, opts):
                raise RuntimeError('fake failure')
        dev.deactivateStream.assert_called_once()
        dev.closeStream.assert_called_once()
        dev.getSampleRate.return_value = 1e6
        with self.assertRaisesRegex(RuntimeError, 'device selected'):
            with radio.open_stream(MODULE, dev, 0, opts):
                self.fail('should reject mismatched rate')
        with self.assertRaises(ValueError):
            radio.rate_factor(1_000_000)


if __name__ == '__main__':
    unittest.main()
