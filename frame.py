"""FTSK-4 v1 fixed-length packet, MSB first, CRC-16/CCITT-FALSE."""
from binascii import crc_hqx
import numpy as np
from ftsk_demo import FS, N, BOOK, transmit, energies, soft_decode

HEADER_TONES = np.array([5, 22, 11, 26, 3, 17, 8, 24, 13, 2, 20, 10, 27, 6, 19, 4])
HEADER = np.exp(2j * np.pi * (HEADER_TONES[:, None] - 15) * np.arange(N) / N).astype(np.complex64).ravel()
PAYLOAD_BYTES = 32
BODY_SAMPLES = 31 * 4 * N
FRAME_SAMPLES = len(HEADER) + BODY_SAMPLES


def pack_symbols(payload):
    if len(payload) != PAYLOAD_BYTES:
        raise ValueError('FTSK-4 v1 requires exactly 32 payload bytes')
    raw = payload + crc_hqx(payload, 0xffff).to_bytes(2, 'big')
    bits = np.unpackbits(np.frombuffer(raw, dtype=np.uint8))
    return np.pad(bits, (0, 7)).reshape(-1, 9) @ (1 << np.arange(8, -1, -1))


def encode_frame(payload: bytes):
    body = transmit(pack_symbols(payload)).astype(np.complex64).ravel()
    return np.concatenate([HEADER, body])


def decode_body(iq):
    """Decode an aligned, frequency-corrected body. Reject bad padding or CRC."""
    power = energies(np.asarray(iq).reshape(31, 4, N))
    # Preserve the original soft and slot-normalized decisions in order.
    payload = _checked_payload(soft_decode(power))
    if payload is not None:
        return payload
    totals = power.sum(axis=-1, keepdims=True)
    payload = _checked_payload(soft_decode(power / np.maximum(totals, 1e-20)))
    if payload is not None:
        return payload

    # A strong narrowband burst can still win after slot normalization. Use
    # the median slot energy to identify an isolated outlier independently
    # of bit decisions. Make only one erasure hypothesis per rejected body.
    slot = int(totals.argmax())
    if totals.flat[slot] <= 8 * np.median(totals):
        return None
    symbol, segment = divmod(slot, 4)
    power[symbol, segment] = 0
    return _checked_payload(soft_decode(power))


def _checked_payload(symbols):
    bits = ((symbols[:, None] >> np.arange(8, -1, -1)) & 1).ravel()
    if bits[-7:].any():
        return None
    raw = np.packbits(bits[:-7]).tobytes()
    if crc_hqx(raw[:-2], 0xffff) != int.from_bytes(raw[-2:], 'big'):
        return None
    return raw[:-2]
