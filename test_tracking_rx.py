"""Paired channel replay, running only the receivers inside the CPU timers."""
import argparse
from collections import Counter
from time import process_time
import unittest
import numpy as np
from channels.study import noise_and_reference, capture, CASES, SEED
from receiver import Receiver
from tracking_rx import TrackingReceiver, estimate_carrier


def run(rx, iq):
    return [p for i in range(0,len(iq),731) for p in rx.feed(iq[i:i+731])]


def paired(trials, seed, cases):
    stats = {case: Counter() for case in cases}
    for i in range(trials):
        ref = noise_and_reference(i,seed,8)
        for case in cases:
            iq = capture(ref,CASES[case],-3,8)
            st = stats[case]
            last = None
            for label, rx in [('base',Receiver()), ('tracking',TrackingReceiver())]:
                ts = process_time()
                pkts = run(rx,iq)
                st[label+'_cpu_s'] += process_time()-ts
                ok = len(pkts)==1 and pkts[0].payload==ref[0]
                st[label+'_ok'] += ok
                st[label+'_extra'] += len(pkts)-int(ok)
                if last is not None:
                    st['changed_accepts'] += int(bool(last) and last != pkts)
                    st['retries'] += rx.retries
                last = pkts
    return stats


class TrackingTests(unittest.TestCase):
    def test_high_drift(self):
        ref = noise_and_reference(0, SEED,8)
        iq = capture(ref,CASES['slope100000'],-3,8)
        self.assertFalse(run(Receiver(),iq))
        packets = run(TrackingReceiver(),iq)
        self.assertEqual([p.payload for p in packets],[ref[0]])
        _, slope = estimate_carrier(iq[ref[1]:ref[1]+4480],1375)
        self.assertLess(abs(slope-100000),15000)

    def test_baseline_passthrough(self):
        ref = noise_and_reference(2,SEED,8)
        iq = capture(ref,CASES['base'],-3,8)
        self.assertEqual(run(Receiver(),iq),run(TrackingReceiver(),iq))

    def test_long_echo_recovery(self):
        ref = noise_and_reference(0,SEED,8)
        iq = capture(ref,CASES['echo250'],-3,8)
        self.assertFalse(run(Receiver(),iq))
        self.assertEqual([p.payload for p in run(TrackingReceiver(),iq)],[ref[0]])

    def test_noise_only(self):
        rng = np.random.default_rng(120)
        rx = TrackingReceiver()
        self.assertFalse(run(rx,rng.normal(size=128000)+1j*rng.normal(size=128000)))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--trials',type=int,default=64)
    p.add_argument('--seed',type=int,default=SEED)
    p.add_argument('--cases',nargs='+',choices=CASES,default=['base','slope50000','slope100000','echo2','echo20','echo80','echo250','combined'])
    args = p.parse_args()
    for case,counts in paired(args.trials,args.seed,args.cases).items():
        print(case,dict(counts))
