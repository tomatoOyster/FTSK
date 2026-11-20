"""FTSK-4 file transfer over unchanged 32-byte PHY payloads. See TRANSFER.md."""
import argparse
from contextlib import ExitStack, nullcontext
import hashlib
import json
import os
from pathlib import Path
import secrets
import struct
import sys
import tempfile

import numpy as np
from profiles import encode, LENGTH
from radio import acquisition_backend
from io_helpers import atomic_writer, check_distinct

MAGIC = b'FT'
VERSION = 1
META, DATA, PARITY = range(3)
HEADER = struct.Struct('>2sB5sI')
BLOCK = 32 - HEADER.size
GROUP = 8
DEFAULT_MAX = 1024 * 1024
HARD_MAX = 16 * 1024 * 1024


def ceiling(a, b):
    return (a + b - 1) // b


def check_limit(max_bytes):
    if not 0 <= max_bytes <= HARD_MAX:
        raise ValueError(f'max-bytes must be in 0..{HARD_MAX}')


def record(kind, transfer_id, index, content):
    return HEADER.pack(MAGIC, VERSION << 4 | kind, transfer_id, index) + content


def xor(left, right):
    return bytes(a ^ b for a, b in zip(left, right))


def iter_records(path, *, transfer_id=None, max_bytes=DEFAULT_MAX):
    """Yield 32-byte PHY payloads. Source is read twice; detect concurrent edits."""
    check_limit(max_bytes)
    tid = secrets.token_bytes(5) if transfer_id is None else transfer_id
    if not isinstance(tid, bytes) or len(tid) != 5:
        raise ValueError('transfer ID must be 5 bytes')
    with open(path, 'rb') as src:
        size = os.fstat(src.fileno()).st_size
        if size > max_bytes:
            raise ValueError('input exceeds max-bytes')
        digest = hashlib.sha256()
        remaining = size
        while remaining:
            piece = src.read(min(65536, remaining))
            if not piece:
                raise ValueError('source truncated')
            digest.update(piece)
            remaining -= len(piece)
        manifest = struct.pack('>Q', size) + digest.digest()
        meta = [record(META, tid, i, manifest[i*BLOCK:(i+1)*BLOCK]) for i in (0, 1)]
        yield from meta
        src.seek(0)
        sent_hash = hashlib.sha256()
        blocks = ceiling(size, BLOCK)
        parity = bytes(BLOCK)
        for i in range(blocks):
            part = src.read(min(BLOCK, size-i*BLOCK))
            if len(part) != min(BLOCK, size-i*BLOCK):
                raise ValueError('source truncated')
            sent_hash.update(part)
            padded = part.ljust(BLOCK, b'\x00')
            yield record(DATA, tid, i, padded)
            parity = xor(parity, padded)
            if (i+1) % GROUP == 0 or i+1 == blocks:
                yield record(PARITY, tid, i//GROUP, parity)
                parity = bytes(BLOCK)
        if src.read(1) or sent_hash.digest() != digest.digest():
            raise ValueError('source changed during transmission')
        yield from meta


class FileReceiver:
    """One transfer. Use as a context manager. Call finish() after feeding records."""

    def __init__(self, output, *, transfer_id=None, max_bytes=DEFAULT_MAX, force=False):
        check_limit(max_bytes)
        if transfer_id is not None and (not isinstance(transfer_id, bytes) or len(transfer_id) != 5):
            raise ValueError('transfer ID must be 5 bytes')
        self.output = Path(output)
        self.transfer_id = transfer_id
        self.max_bytes = max_bytes
        self.force = force
        self.meta = {}
        self.size = None
        self.digest = None
        self.highest = [-1, -1]
        self.duplicates = 0
        self.recovered = 0
        self.result = None
        blocks = ceiling(max_bytes, BLOCK)
        self.seen = [bytearray(blocks), bytearray(ceiling(blocks, GROUP))]
        self.group_records = bytearray(len(self.seen[1]))
        self.rank = 0
        self.stack = ExitStack()
        try:
            self.files = [self.stack.enter_context(tempfile.TemporaryFile(dir=self.output.parent)) for _ in range(2)]
        except BaseException:
            self.stack.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.stack.close()

    def feed(self, payload):
        if self.result is not None:
            raise ValueError('transfer already published')
        if len(payload) != 32:
            return
        magic, tag, tid, index = HEADER.unpack_from(payload)
        kind = tag & 15
        if magic != MAGIC or tag >> 4 != VERSION or kind not in (META, DATA, PARITY):
            return
        if self.transfer_id is not None and self.transfer_id != tid:
            return
        content = payload[HEADER.size:]
        if kind == META and index > 1:
            raise ValueError('invalid manifest index')
        self.transfer_id = tid
        if kind == META:
            if index in self.meta:
                if self.meta[index] != content:
                    raise ValueError('conflicting manifest records')
                self.duplicates += 1
            self.meta[index] = content
            if index == 0:
                self.size = int.from_bytes(content[:8], 'big')
                if self.size > self.max_bytes:
                    raise ValueError('declared file exceeds max-bytes')
                if any(last >= limit for last, limit in zip(self.highest, self._counts())):
                    raise ValueError('stored index exceeds declared size')
            if len(self.meta) == 2:
                self.digest = self.meta[0][8:] + self.meta[1]
            return
        slot = kind - DATA
        limit = len(self.seen[slot]) if self.size is None else self._counts()[slot]
        if index >= limit:
            raise ValueError('record index exceeds file limit')
        stream = self.files[slot]
        stream.seek(index * BLOCK)
        if self.seen[slot][index]:
            if stream.read(BLOCK) != content:
                raise ValueError('conflicting duplicate record')
            self.duplicates += 1
            return
        stream.write(content)
        self.seen[slot][index] = 1
        self.highest[slot] = max(self.highest[slot], index)
        group = index // GROUP if kind == DATA else index
        if self.group_records[group] < GROUP:
            self.rank += 1
        self.group_records[group] += 1

    @property
    def ready(self):
        """All groups have sufficient independent records for a verification attempt."""
        if self.digest is None:
            return False
        blocks, groups = self._counts()
        last = blocks % GROUP
        excess = max(0, min(GROUP, self.group_records[groups-1]) - last) if last else 0
        return self.rank - excess == blocks

    def _counts(self):
        blocks = ceiling(self.size, BLOCK)
        return blocks, ceiling(blocks, GROUP)

    def finish(self):
        if self.result is not None:
            return self.result
        if self.digest is None:
            raise ValueError('incomplete transfer manifest')
        blocks, groups = self._counts()
        digest = hashlib.sha256()
        recovered = 0
        with atomic_writer(self.output, self.force) as out:
            for group in range(groups):
                start = group*GROUP
                count = min(GROUP, blocks-start)
                missing = [i for i in range(count) if not self.seen[0][start+i]]
                if len(missing) > 1 or (missing and not self.seen[1][group]):
                    raise ValueError(f'group {group} has unrecoverable missing data')
                self.files[0].seek(start*BLOCK)
                parts = [self.files[0].read(BLOCK) for _ in range(count)]
                if missing:
                    self.files[1].seek(group*BLOCK)
                    repair = self.files[1].read(BLOCK)
                    for i, part in enumerate(parts):
                        if i != missing[0]:
                            repair = xor(repair, part)
                    parts[missing[0]] = repair
                    recovered += 1
                chunk = b''.join(parts)[:self.size-start*BLOCK]
                digest.update(chunk)
                out.write(chunk)
            if out.tell() != self.size or digest.digest() != self.digest:
                raise ValueError('file SHA-256 or length mismatch')
        self.recovered = recovered
        self.result = dict(bytes=self.size, transfer_id=self.transfer_id.hex(), sha256=digest.hexdigest(),
                           recovered=recovered, duplicates=self.duplicates)
        return self.result


def parse_id(value):
    try:
        tid = bytes.fromhex(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError('expected 10 hex digits') from error
    if len(tid) != 5:
        raise argparse.ArgumentTypeError('expected 10 hex digits')
    return tid


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='cmd', required=True)
    for name in ('encode', 'decode'):
        sub = commands.add_parser(name)
        sub.add_argument('input')
        sub.add_argument('output')
        sub.add_argument('--profile', choices=('4', '8') if name == 'encode' else ('4', '8', 'auto'), default='4')
        sub.add_argument('--id', type=parse_id)
        sub.add_argument('--max-bytes', type=int, default=DEFAULT_MAX)
        sub.add_argument('--force', action='store_true')
    args = parser.parse_args(argv)
    try:
        if not ((args.cmd == 'encode' and args.output == '-') or (args.cmd == 'decode' and args.input == '-')):
            check_distinct(args.input, args.output)
        if args.cmd == 'encode':
            tid = args.id or secrets.token_bytes(5)
            writer = nullcontext(sys.stdout.buffer) if args.output == '-' else atomic_writer(args.output, args.force)
            frames = 0
            with writer as stream:
                for payload in iter_records(args.input, transfer_id=tid, max_bytes=args.max_bytes):
                    stream.write(encode(payload, int(args.profile)).astype('<c8', copy=False).tobytes())
                    frames += 1
            print(json.dumps(dict(transfer_id=tid.hex(), frames=frames, iq_bytes=frames*LENGTH[int(args.profile)]*8)), file=sys.stderr)
        else:
            source = nullcontext(sys.stdin.buffer) if args.input == '-' else open(args.input, 'rb')
            with source as stream, FileReceiver(args.output, transfer_id=args.id, max_bytes=args.max_bytes, force=args.force) as target:
                receiver, _ = acquisition_backend(profile=args.profile)
                tail = b''
                while chunk := stream.read(32768):
                    buf = tail + chunk
                    end = len(buf)//8*8
                    for packet in receiver.feed(np.frombuffer(buf[:end], dtype='<c8')):
                        target.feed(packet.payload)
                    tail = buf[end:]
                if tail:
                    raise ValueError('incomplete CF32 sample at EOF')
                print(json.dumps(target.finish()))
    except (OSError, ValueError) as error:
        parser.exit(1, f'FTSK transfer: {error}\n')


if __name__ == '__main__':
    main()
