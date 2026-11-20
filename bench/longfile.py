"""Continuous IQ file transport under scheduled recoverable erasures; no RF access."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from frame import FS, encode_frame
from receiver import Receiver
from transfer import DATA, HEADER, FileReceiver, iter_records


def measure(size, seed):
    rng=np.random.default_rng(seed)
    start=time.perf_counter()
    tid=bytes.fromhex('0123456789')
    checksum=lambda buf:hashlib.sha256(buf).hexdigest()
    with tempfile.TemporaryDirectory() as tmp:
        src=Path(tmp)/'src.bin'; dst=Path(tmp)/'received.bin'
        filedata=rng.bytes(size)
        src.write_bytes(filedata)
        rx=Receiver()
        pos=sent=suppressed=delivered=0
        # Fixed crystal offset, random initial phase per burst, AWGN and uncertain packet boundaries.
        with FileReceiver(dst,transfer_id=tid) as target:
            for record in iter_records(src,transfer_id=tid):
                _,tag,_,index=HEADER.unpack_from(record)
                # One deliberately missing data frame per group; choose a valid data index in the tail group.
                erased=(tag & 15)==DATA and index%8==0
                suppressed+=int(erased)
                wave=encode_frame(record)
                if erased:
                    wave[:]=0
                lead=int(rng.integers(0,150))
                wave=np.pad(wave,(lead,45))*np.exp(1j*(rng.uniform(-np.pi,np.pi)+2*np.pi*3721.5*np.arange(len(wave)+lead+45)/FS))
                wave+=.35*(rng.normal(size=len(wave))+1j*rng.normal(size=len(wave)))
                packets=[]
                for i in range(0,len(wave),509):
                    packets.extend(rx.feed(wave[i:i+509]))
                for packet in packets:
                    target.feed(packet.payload)
                sent+=1; pos+=len(wave); delivered+=len(packets)
            meta=target.finish()
        out=dst.read_bytes()
        if filedata!=out or meta['recovered']!=suppressed:
            raise AssertionError('end-to-end recovery failed')
        return dict(seed=seed,size=size,radio_used=False,records=sent,erased=suppressed,decoded_records=delivered,
                    file_bytes_verified=len(out),sha256=checksum(out),file_rx=meta,samples=pos,signal_seconds=pos/FS,
                    elapsed_s=time.perf_counter()-start, cfo_hz=3721.5,snr_db_per_sample=float(-10*np.log10(2*.35**2)))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bytes',type=int,default=100*1024)
    p.add_argument('--seed',type=int,default=703)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    result=measure(a.bytes,a.seed)
    a.out.write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
    print(json.dumps(result))
