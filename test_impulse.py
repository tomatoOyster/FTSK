"""Conditional decoder regression and reproducible, paired streaming diagnostic."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import types
import unittest
import zipfile

import numpy as np
import frame
from soak import CASES


def archived_module(name, source):
    module = types.ModuleType(name)
    sys.modules[name] = module  # required by dataclasses
    exec(compile(source, '<archive:' + name + '>', 'exec'), module.__dict__)
    return module


with zipfile.ZipFile(Path(__file__).with_name('ftsk4-v1.zip')) as archive:
    base_source = archive.read('frame.py')
    base = archived_module('_v1_frame', base_source)
    rx_source = archive.read('receiver.py')
    old_rx = archived_module('_v1_old_rx', rx_source)
    new_rx = archived_module('_v1_new_rx', rx_source)
old_rx.decode_body = base.decode_body
new_rx.decode_body = frame.decode_body


def window(rng, index, key):
    snr, ppm, echo, impulse, noise_only = CASES[key]
    payload = index.to_bytes(8, 'big') + rng.bytes(24)
    samples = base.encode_frame(payload)
    if ppm:
        t = np.arange(int(np.ceil(len(samples)*(1+ppm*1e-6))))/(1+ppm*1e-6)
        samples = np.interp(t, np.arange(len(samples)), samples, right=0)
    if echo:
        samples = np.convolve(samples, [1, 0, 0, 1j*echo])
    slot = None
    if impulse:
        slot = int(rng.integers(16, 140))
        samples[32*slot:32*slot+32] += 12*np.exp(2j*np.pi*rng.uniform(-.45,.45)*np.arange(32))
    lead = int(rng.integers(0,512))
    cfo = rng.uniform(-6000,6000)
    phase = rng.uniform(-np.pi,np.pi)
    s = np.pad(samples*np.exp(1j*(phase+2*np.pi*cfo*np.arange(len(samples))/frame.FS)), (lead,128))
    if noise_only:
        s[:] = 0
    s += np.sqrt(.5*10**(-snr/10))*(rng.normal(size=len(s))+1j*rng.normal(size=len(s)))
    chunks = []
    i = 0
    while i < len(s):
        n = int(rng.integers(128,1300))
        chunks.append(s[i:i+n])
        i += n
    return payload, chunks, s, lead, cfo, slot


class TraceReceiver(old_rx.Receiver):
    def _decode(self, start, coarse):
        self.candidates.append(start)
        return super()._decode(start, coarse)


def diagnose(stats, s, lead, cfo, slot, payload):
    # Oracle start/CFO is diagnostic only. All benchmark decisions use blind acquisition.
    iq = s[lead:lead+frame.FRAME_SAMPLES] * np.exp(-2j*np.pi*cfo*np.arange(frame.FRAME_SAMPLES)/frame.FS)
    h = iq[:len(frame.HEADER)]
    coarse = np.round(cfo/125)*125
    template = frame.HEADER*np.exp(2j*np.pi*(coarse-cfo)*np.arange(len(h))/frame.FS)
    gate = abs(np.vdot(template,h))**2/(len(h)*np.vdot(h,h).real)
    stats['min_gate_milli'] = min(stats.get('min_gate_milli', 1000), int(gate*1000))
    stats['oracle_gate_pass'] += int(gate >= .055)
    body = iq[len(h):]
    stats['oracle_old_ok'] += int(base.decode_body(body) == payload)
    stats['oracle_new_ok'] += int(frame.decode_body(body) == payload)
    power = frame.energies(body.reshape(31,4,32))
    total = power.sum(axis=-1)
    retry = total.max() > 8*np.median(total)
    stats['oracle_outlier'] += int(retry)
    expected = frame.pack_symbols(payload)
    for name, p in [('raw', power), ('norm', power/total[:,:,None])]:
        decided = frame.soft_decode(p)
        err = decided ^ expected
        stats[name+'_symbol_errors'] += int(np.count_nonzero(err))
        stats[name+'_bit_errors'] += sum(int(x).bit_count() for x in err)
        if slot is not None:
            stats[name+'_errors_in_hit_symbol'] += int(err[(slot-16)//4] != 0)
    if slot is not None:
        stats['strongest_slot_correct'] += int(total.argmax() == slot-16)


def benchmark(n, seed):
    rng = np.random.default_rng(seed)
    receivers = TraceReceiver(), new_rx.Receiver()
    stats = {key: Counter() for key in CASES}
    absolute = 0
    for i in range(n * len(CASES)):
        key = tuple(CASES)[i % len(CASES)]
        st = stats[key]
        payload, chunks, s, lead, cfo, slot = window(rng, i, key)
        receivers[0].candidates = []
        outcomes = []
        for name, rx in zip(('old','new'), receivers):
            packets = [p for chunk in chunks for p in rx.feed(chunk)]
            correct = any(p.payload == payload for p in packets) and key != 'noise'
            st[name+'_ok'] += int(correct)
            st[name+'_unexpected'] += len(packets)-int(correct)
            outcomes.append(correct)
        st['frames'] += 1
        if key != 'noise' and not outcomes[0]:
            st['recovered_conditional'] += int(outcomes[1])
            st['tried_exact_start'] += int(absolute+lead in receivers[0].candidates)
            diagnose(st, s, lead, cfo, slot, payload)
        if outcomes[0] and not outcomes[1]:
            st['regressions'] += 1
        absolute += len(s)
    return stats


class ImpulseRegression(unittest.TestCase):
    def test_conditioned_on_archived_rejection(self):
        rng = np.random.default_rng(713)
        rejected = 0
        for i in range(128):
            payload, _, s, lead, cfo, _ = window(rng, i, 'impulse')
            b = s[lead+512:lead+4480] * np.exp(-2j*np.pi*cfo*np.arange(3968)/frame.FS)
            previous = base.decode_body(b)
            result = frame.decode_body(b)
            if previous is not None:
                self.assertEqual(result, previous)
            else:
                rejected += 1
                self.assertEqual(result, payload)
        self.assertGreater(rejected, 0)

    def test_wrong_crc_with_real_outlier(self):
        rng = np.random.default_rng(779)
        for index in range(256):
            symbols = frame.pack_symbols(rng.bytes(32))
            # Flip a payload bit while leaving its original CRC in the waveform.
            symbols[index % 28] ^= 1 << (index % 9)
            body = frame.transmit(symbols).ravel()
            slot = int(rng.integers(124))
            body[slot*32:(slot+1)*32] += 12*np.exp(2j*np.pi*rng.uniform(-.45,.45)*np.arange(32))
            self.assertIsNone(frame.decode_body(body))

    def test_encoder_identical(self):
        for i in range(32):
            msg = bytes([i])*32
            np.testing.assert_array_equal(frame.encode_frame(msg),base.encode_frame(msg))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--per-case', type=int, default=512)
    parser.add_argument('--seed', type=int, default=90517)
    args = parser.parse_args()
    print('Archived frame SHA256', hashlib.sha256(base_source).hexdigest())
    print(json.dumps(benchmark(args.per_case,args.seed),indent=2))
