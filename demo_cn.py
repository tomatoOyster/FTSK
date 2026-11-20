"""FTSK 中文端到端演示：一条命令发送一段 UTF-8 文字（仅数字 IQ）。"""
import argparse
import hashlib
from pathlib import Path
import tempfile

import numpy as np
from frame import FS, encode_frame
from receiver import Receiver
from transfer import DATA, HEADER, FileReceiver, iter_records


def run(text, cfo=4000, snr=-2, seed=364, erase=True):
    data=text.encode('utf8')
    if len(data)>4096:
        raise ValueError('演示文字最多 4096 字节；更长消息请使用文件模式')
    rng=np.random.default_rng(seed)
    rx=Receiver()
    with tempfile.TemporaryDirectory() as tmp:
        src=Path(tmp)/'source'; dst=Path(tmp)/'out'
        src.write_bytes(data)
        cursor=sent=dropped=delivered=0
        with FileReceiver(dst, transfer_id=b'DEMO1') as out:
            for record in iter_records(src,transfer_id=b'DEMO1'):
                _,tag,_,index=HEADER.unpack_from(record)
                damaged=erase and (tag & 15)==DATA and index%8==0
                tx=encode_frame(record)
                if damaged: tx[:]=0
                dropped+=int(damaged)
                leading=int(rng.integers(0,512))
                s=np.pad(tx,(leading,64))
                s=s*np.exp(1j*(2*np.pi*cfo*np.arange(len(s))/FS + rng.uniform(-np.pi,np.pi)))
                s+=np.sqrt(.5*10**(-snr/10))*(rng.normal(size=len(s))+1j*rng.normal(size=len(s)))
                for i in range(0,len(s),367):
                    for p in rx.feed(s[i:i+367]):
                        out.feed(p.payload)
                        delivered+=1
                cursor+=len(s); sent+=1
            meta=out.finish()
        result=dst.read_bytes()
        if result!=data:
            raise ValueError('消息不一致')
        print('发送文字：',text)
        print(f'UTF-8 长度：{len(data)} 字节；频偏：{cfo:g} Hz；每采样 SNR：{snr:g} dB')
        print(f'时轴上发送 {sent} 帧，人为擦除 {dropped} 帧；接收机盲收 {delivered} 帧，校验块修复 {meta["recovered"]} 个数据块。')
        print('接收文字：',result.decode('utf8'))
        print('SHA-256：',meta['sha256'])
        return meta


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('text',nargs='?',default='频迹键控：这条消息在丢掉一个数据帧后仍然完整到达。')
    p.add_argument('--cfo',type=float,default=4000)
    p.add_argument('--snr',type=float,default=-2)
    p.add_argument('--seed',type=int,default=364)
    p.add_argument('--no-erasure',action='store_true')
    a=p.parse_args()
    try:
        run(a.text,a.cfo,a.snr,a.seed,not a.no_erasure)
    except (ValueError,OSError) as exc:
        p.exit(1,f'解码未完成：{exc}\n')
