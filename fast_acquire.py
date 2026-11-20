"""Optional two-lag differential prescreen for the baseline streaming receiver.

At the correct start, r[n+d]*conj(r[n]) contains the known differential
header times a constant phasor for any constant CFO. Combine the two lag
powers noncoherently. Timings above the gate receive the full coherent
125 Hz CFO grid test. CRC, overlap handling and frequency refinement are
inherited from Receiver. A weak or rapidly varying channel can miss the
prescreen even when the exhaustive search would recover the packet.
"""
import numpy as np

from receiver import Receiver, _BLOCK, _H, _HEADER, _GRID, _RADIUS, _NFFT, FS

from radio import Decimator


class FFTDecimator(Decimator):
    """Same causal FIR and output phase as Decimator, via overlap-save FFT."""
    def __init__(self, factor):
        super().__init__(factor)
        self._spectra = {}

    def feed(self, iq):
        if self.factor == 1 or not len(iq):
            return iq
        joined = np.concatenate((self.tail, iq))
        fft_size = 1 << (len(joined)-1).bit_length()
        if fft_size not in self._spectra:
            self._spectra[fft_size] = np.fft.fft(self.taps.astype(np.float64), n=fft_size)
        result = np.fft.ifft(np.fft.fft(joined, n=fft_size) * self._spectra[fft_size])
        out = result[len(self.tail)+self.phase:len(joined):self.factor].astype(np.complex64)
        self.phase = (self.phase-len(iq)) % self.factor
        self.tail = joined[-len(self.tail):].copy()
        return out


_LAGS = (32, 79)
_KERNELS = tuple(np.fft.fft(((_HEADER[d:] * _HEADER[:-d].conj())[::-1]).conj(), n=_NFFT) for d in _LAGS)
_CFO_NFFT = 1024    # 128 kS/s / 1024 = 125 Hz
_BINS = (_GRID * _CFO_NFFT // FS).astype(int)


class FastReceiver(Receiver):
    """Drop-in optional single-channel receiver with observable prescreen counts."""
    def __init__(self):
        super().__init__()
        self.tested_starts = 0
        self.prescreen_starts = 0
        self.header_candidates = 0

    def _search(self, left, right):
        first = max(0, left - _RADIUS)
        count = right + _RADIUS - first
        offset = first - self._base
        data = self._iq[offset:offset + count + _H - 1]
        sq = np.abs(data)**2
        csum = np.r_[0., np.cumsum(sq)]
        energy = csum[_H:] - csum[:-_H]
        diff_power = np.zeros(count)
        for d, kernel in zip(_LAGS, _KERNELS):
            diff = data[d:] * data[:-d].conj()
            corr = np.fft.ifft(np.fft.fft(diff, n=_NFFT)*kernel)[_H-d-1:_H-d-1+count]
            diff_power += (np.abs(corr)*(_H/(_H-d)))**2
        # Normalize by total window energy, preserving invariance to RF gain.
        candidates = np.flatnonzero((energy > 0) & (diff_power > (2 * .105**2)*energy**2))
        self.tested_starts += right-left
        self.prescreen_starts += np.count_nonzero((candidates >= left-first) & (candidates < right-first))
        if not candidates.size:
            return
        windows = np.lib.stride_tricks.sliding_window_view(data, _H)
        scores = np.zeros(count)
        best = np.zeros(count, dtype=int)
        # Batch size caps temporary memory for highly correlated interference.
        for batch in range(0, len(candidates), 32):
            ix = candidates[batch:batch+32]
            dehopped = windows[ix] * _HEADER.conj()
            power = abs(np.fft.fft(dehopped, n=_CFO_NFFT, axis=-1)[:, _BINS])**2
            best[ix] = power.argmax(axis=1)
            scores[ix] = power[np.arange(len(ix)),best[ix]] / (_H*energy[ix])
        for i in np.flatnonzero(scores[left-first:right-first] >= .055) + left-first:
            lo = max(0, i-_RADIUS)
            if i == lo + np.argmax(scores[lo:i+_RADIUS+1]):
                self._pending.append((first + int(i), _GRID[best[i]]))
                self.header_candidates += 1
