"""Optional blind FTSK-4 drift and two-path receiver. Same feed API as Receiver."""
import numpy as np
from frame import HEADER, FRAME_SAMPLES, FS, BOOK, _checked_payload
from receiver import Packet, Receiver

_T = np.arange(FRAME_SAMPLES) / FS
_SLOT_T = _T.reshape(-1, 32).mean(axis=1)
_SLOPES = np.arange(-200000, 200001, 4000)
_GRID = np.exp(-2j*np.pi*_SLOPES[:, None]*(_SLOT_T-.002)/4000)
# Fit only known header samples for all signed offsets.
_H = HEADER[32:-32].astype(complex)
_LAGS = np.r_[np.arange(-32, 0), np.arange(1, 33)]
_SHIFTS = np.stack([HEADER[32-d:480-d] for d in _LAGS]).astype(complex)
_ORTHO = _SHIFTS - np.mean(_SHIFTS*_H.conj(), axis=1)[:,None]*_H
_ORTHO_ENERGY = np.sum(abs(_ORTHO)**2, axis=1)
_WAVES = np.exp(2j*np.pi*(BOOK[:,:,None]-15)*np.arange(32)/32).reshape(512,128)


def estimate_carrier(raw, coarse):
    # Data tone indices are unknown, but each lies on the same 4 kHz lattice.
    peaks = abs(np.fft.fft(raw.reshape(-1,32), n=256, axis=1)).argmax(axis=1)
    residual = np.exp(2j*np.pi*(peaks*FS/256-coarse)/4000)
    sums = np.einsum('ij,j->i', _GRID, residual)
    idx = np.argmax(abs(sums))
    slope = float(_SLOPES[idx])
    intercept = coarse + np.angle(sums[idx])*4000/(2*np.pi) - .002*slope
    # Calibrate the intercept coherently from the known header after dechirping.
    h = raw[:512]*HEADER.conj()*np.exp(-2j*np.pi*(intercept*_T[:512]+.5*slope*_T[:512]**2))
    spectrum = abs(np.fft.fft(h,n=4096))
    bins = np.arange(-12,13)
    intercept += bins[np.argmax(spectrum[bins])]*FS/4096
    return intercept, slope


def fit_paths(corrected):
    y = corrected[32:480]
    direct = np.vdot(_H,y)/len(_H)
    corr = np.einsum('ij,j->i',_ORTHO.conj(),y)
    scores = abs(corr)**2/_ORTHO_ENERGY
    i = np.argmax(scores)
    if scores[i] < .025*np.vdot(y,y).real:
        return 0,direct,0j
    echo = corr[i]/_ORTHO_ENERGY[i]
    direct -= echo*np.vdot(_H,_SHIFTS[i])/len(_H)
    return int(_LAGS[i]), direct, echo


def matched_symbols(body, delay, direct, echo):
    # Drop the boundary at which the other path contains an adjacent symbol.
    lo = max(0,delay)
    hi = 128+min(0,delay)
    candidates = direct*_WAVES[:,lo:hi] + echo*_WAVES[:,lo-delay:hi-delay]
    slots = body.reshape(-1,128)[:,lo:hi]
    corr = np.einsum('si,ki->sk',slots,candidates.conj(),optimize=False)
    # Common phase may rotate between symbols as residual CFO accumulates.
    score = 2*abs(corr) - (abs(candidates)**2).sum(axis=1)
    return score.argmax(axis=1)


class TrackingReceiver(Receiver):
    def __init__(self):
        super().__init__()
        self.retries = 0
        self.recovered = 0

    def _decode(self, start, coarse):
        packet = super()._decode(start, coarse)
        if packet is not None:
            return packet
        self.retries += 1
        raw = self._iq[start-self._base:start-self._base+FRAME_SAMPLES]
        cfo, slope = estimate_carrier(raw, coarse)
        corrected = raw*np.exp(-2j*np.pi*(cfo*_T+.5*slope*_T**2))
        delay, direct, echo = fit_paths(corrected)
        symbols = matched_symbols(corrected[512:],delay,direct,echo)
        payload = _checked_payload(symbols)
        if payload is not None:
            self.recovered += 1
            return Packet(start,float(cfo),payload)
        return None
