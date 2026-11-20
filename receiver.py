"""Streaming acquisition of FTSK-4 frames at 128 ksample/s."""
from collections import deque
from dataclasses import dataclass

import numpy as np

from frame import HEADER, FRAME_SAMPLES, decode_body

FS = 128_000
_HEADER = np.asarray(HEADER, dtype=np.complex128)
_H = len(_HEADER)
_BLOCK = 1024  # candidate start times per overlap-save search
_RADIUS = 32  # suppress adjacent timing sidelobes of the same header
_NFFT = 1 << (_BLOCK + 2 * _RADIUS + _H - 2).bit_length()
_GRID = np.arange(-6000, 6001, 125)
_T = 2j * np.pi * np.arange(_H) / FS
_ENERGY = np.vdot(_HEADER, _HEADER).real
# A bank of matched filters implements a joint time/frequency search. Half a
# frequency bin causes at most 0.92 dB coherent loss over a 4 ms header.
_templates = _HEADER * np.exp(_GRID[:, None] * _T)
_FILTERS = np.fft.fft(_templates[:, ::-1].conj(), n=_NFFT, axis=1)


def checked_iq(iq, start=0, *, dtype=np.complex128):
    """Validate one contiguous chunk before it can alter a signal-processing state."""
    with np.errstate(over='ignore', invalid='ignore'):
        chunk = np.asarray(iq, dtype=dtype)
    if chunk.ndim != 1:
        raise ValueError('iq must be one-dimensional')
    finite = np.isfinite(chunk)
    if not finite.all():
        raise ValueError(f'non-finite IQ at sample {start + int(np.argmin(finite))}')
    return chunk


@dataclass(frozen=True)
class Packet:
    start: int  # absolute, zero-based sample index
    cfo_hz: float  # positive when received frequency exceeds nominal
    payload: bytes


class Receiver:
    """Accept contiguous chunks, retaining overlap and incomplete frames."""

    def __init__(self):
        self._iq = np.empty(0, dtype=np.complex128)
        self._base = 0
        self._next = 0
        self._pending = deque()
        self._accepted_end = 0

    def _search(self, left, right):
        # Search [left, right) with a full header and timing-peak context. All
        # indices outside numpy slices refer to the original stream.
        first = max(0, left - _RADIUS)
        count = right + _RADIUS - first
        data = self._iq[first-self._base:first-self._base+count+_H-1]
        fft = np.fft.fft(data, n=_NFFT)
        corr = np.fft.ifft(_FILTERS * fft, axis=1)[:, _H-1:_H-1+count]
        power = np.abs(corr) ** 2
        bins = power.argmax(axis=0)
        cumulative = np.r_[0.0, np.cumsum(np.abs(data) ** 2)]
        energy = cumulative[_H:] - cumulative[:-_H]
        scores = np.divide(power[bins, np.arange(count)], energy * _ENERGY,
                           out=np.zeros(count), where=energy > 0)
        # In complex white noise a single normalized power has Beta(1,H-1)
        # distribution. A 0.055 gate leaves CRC as the final acceptance test.
        for i in np.flatnonzero(scores[left-first:right-first] >= 0.055) + left-first:
            lo = max(0, i - _RADIUS)
            if i == lo + scores[lo:i+_RADIUS+1].argmax():
                self._pending.append((first + int(i), _GRID[bins[i]]))

    def _decode(self, start, coarse):
        offset = start - self._base
        raw = self._iq[offset:offset+FRAME_SAMPLES]
        # After removal of the known header, the residual is a pure tone.
        # Zero padding gives 3.90625 Hz spacing for the local refinement.
        residual = raw[:_H] * _HEADER.conj() * np.exp(-coarse * _T)
        m = 32768
        spectrum = np.fft.fft(residual, n=m)
        offsets = np.arange(-32, 33)
        best = offsets[np.abs(spectrum[offsets]).argmax()]
        freq = float(coarse + best * FS / m)
        phasor = np.exp(-2j * np.pi * freq * np.arange(FRAME_SAMPLES) / FS)
        corrected = raw * phasor
        phase = np.angle(np.vdot(_HEADER, corrected[:_H]))
        payload = decode_body(corrected[_H:] * np.exp(-1j * phase))
        if payload is not None:
            return Packet(start, freq, payload)
        return None

    def feed(self, iq) -> list[Packet]:
        """Consume a 1-D IQ array and return newly completed CRC-valid frames."""
        chunk = checked_iq(iq, self._base + len(self._iq))
        self._iq = np.concatenate((self._iq, chunk))
        end = self._base + len(self._iq)
        packets = []
        # Acquisition runs ahead of the body by using blocks shorter than the
        # body. A frame is emitted even when the stream ends at its last sample.
        while self._next + _BLOCK + _RADIUS + _H - 1 <= end:
            self._search(self._next, self._next + _BLOCK)
            self._next += _BLOCK
        while self._pending and self._pending[0][0] + FRAME_SAMPLES <= end:
            start, coarse = self._pending.popleft()
            if start < self._accepted_end:
                continue
            packet = self._decode(start, coarse)
            if packet is not None:
                packets.append(packet)
                self._accepted_end = start + FRAME_SAMPLES
        retain = max(0, self._next - _RADIUS)
        if self._pending:
            retain = min(retain, self._pending[0][0])
        self._iq = self._iq[retain-self._base:]
        self._base = retain
        return packets
