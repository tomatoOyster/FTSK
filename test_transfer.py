import hashlib
from pathlib import Path
import random
import tempfile
import unittest

from transfer import (BLOCK, DATA, DEFAULT_MAX, FileReceiver, HARD_MAX, HEADER,
                      META, PARITY, iter_records, main, record)


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.src = self.dir/'source'
        self.dst = self.dir/'result'
        self.tid = b'FT123'

    def packets(self, data):
        self.src.write_bytes(data)
        return list(iter_records(self.src, transfer_id=self.tid))

    def test_boundaries_reorder_duplicates_loss(self):
        for size in (0,1,19,20,21,159,160,161,341,512):
            with self.subTest(size=size):
                data = random.Random(size).randbytes(size)
                packets = self.packets(data)
                # Lose first data in every group and both leading manifest records.
                kept = [r for r in packets[2:] if not(r[2]&15 == DATA and HEADER.unpack_from(r)[3]%8 == 0)]
                kept.extend(kept[::3])
                random.Random(14).shuffle(kept)
                with FileReceiver(self.dst, force=True, max_bytes=size) as rx:
                    for r in kept:
                        rx.feed(r)
                    self.assertTrue(rx.ready)
                    result = rx.finish()
                    self.assertEqual(result['recovered'], (size+159)//160)
                    self.assertEqual(result['sha256'], hashlib.sha256(data).hexdigest())
                self.assertEqual(self.dst.read_bytes(), data)

    def test_file_integrity_and_atomic_failure(self):
        self.dst.write_bytes(b'previous')
        for action in ('more loss', 'bad bytes', 'no manifest', 'conflict'):
            with self.subTest(action=action):
                records = self.packets(b'hello'*87)
                if action == 'more loss':
                    del records[2:4]
                elif action == 'no manifest':
                    records = [p for p in records if p[2]&15 != META]
                else:
                    p = records[2]
                    bad = p[:-1]+bytes([p[-1]^4])
                    if action=='conflict':
                        records.append(bad)
                    else:
                        records[2] = bad
                with self.assertRaises(ValueError), FileReceiver(self.dst, force=True) as rx:
                    for r in records:
                        rx.feed(r)
                    rx.finish()
                self.assertEqual(self.dst.read_bytes(), b'previous')
                self.assertEqual(set(self.dir.iterdir()), {self.src,self.dst})

    def test_parity_loss_and_partial_last_group_rank(self):
        r = self.packets(bytes(range(170)))
        with FileReceiver(self.dst) as rx:
            for p in r:
                kind, index = p[2]&15, HEADER.unpack_from(p)[3]
                if kind==DATA and index==3 or kind==PARITY and index==0:
                    continue
                rx.feed(p)
            self.assertFalse(rx.ready)
            rx.feed(r[5])
            self.assertTrue(rx.ready)
            self.assertEqual(rx.finish()['recovered'], 0)

    def test_bounds_and_ids(self):
        with self.assertRaises(ValueError):
            FileReceiver(self.dst, max_bytes=HARD_MAX+1)
        for kind, index in ((DATA, 2), (PARITY, 1), (META, 2)):
            with self.assertRaises(ValueError), FileReceiver(self.dst, max_bytes=21) as rx:
                rx.feed(record(kind, self.tid, index, bytes(BLOCK)))
        packets = self.packets(b'data')
        with FileReceiver(self.dst, transfer_id=self.tid) as rx:
            rx.feed(record(META, b'other', 99, bytes(BLOCK)))
            rx.feed(b'0'*32)
            wrong_version = packets[0][:2]+b'\x20'+packets[0][3:]
            rx.feed(wrong_version)
            self.assertEqual(rx.meta,{})
            for p in packets:
                rx.feed(p)
            self.assertEqual(rx.finish()['bytes'],4)
        with self.assertRaises(ValueError), FileReceiver(self.dst, max_bytes=2) as rx:
            rx.feed(packets[0])
        with self.assertRaises(ValueError):
            list(iter_records(self.src,max_bytes=3))

    def test_cli_iq_and_no_clobber(self):
        iq = self.dir/'test.cf32'
        self.src.write_bytes(bytes(range(89)))
        main(['encode',str(self.src),str(iq),'--id',self.tid.hex()])
        main(['decode',str(iq),str(self.dst)])
        self.assertEqual(self.src.read_bytes(),self.dst.read_bytes())
        with self.assertRaises(SystemExit):
            main(['decode',str(iq),str(self.dst)])
        with iq.open('ab') as stream:
            stream.write(b'x')
        with self.assertRaises(SystemExit):
            main(['decode',str(iq),str(self.dst),'--force'])
        self.assertEqual(self.src.read_bytes(),self.dst.read_bytes())

    def test_input_changed(self):
        self.src.write_bytes(bytes(80))
        with self.assertRaises(ValueError):
            frames = iter_records(self.src)
            next(frames)
            self.src.write_bytes(bytes([1])*80)
            list(frames)


if __name__ == '__main__':
    unittest.main()
