"""Generate the Chinese release evidence summary from completed machine-readable results."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from evidence import check


def read(name):
    return json.loads((ROOT/'bench'/name).read_text('utf8'))


def build():
    old=check(ROOT/'evidence'/'soak-50min.jsonl',3000)
    new=check(ROOT/'evidence'/'soak-release.jsonl',2400)
    labels={'nominal':'0 dB 单径','edge':'−6 dB 单径','clock_echo':'−3 dB、25 ppm + 回波','impulse':'0 dB、强单时段脉冲'}
    output=['# FTSK 验收记录（v0.0.2）','',
        '**范围。** 这是可以编码、监听、重组文件并连接 SDR 驱动的参考实现。以下实验使用合成 IQ 或内存设备。没有实体电台、校准功率计或频谱仪参与，不能从本报告读出 dBm 灵敏度、发射合规性、传输距离或相对于 LoRa 的性能优势。','',
        '## 持续运行，先找失败，再独立复验','',
        f'原版长测 UTC {old["utc_start"]} 至 {old["utc_end"]}，单调时钟 **{old["monotonic_s"]:.1f} 秒**，进程 CPU {old["cpu_s"]:.1f} 秒，实际处理 {old["samples"]:,} 个 IQ 采样。新版在不同进程追加 {new["monotonic_s"]:.1f} 秒、{new["samples"]:,} 个采样。两次运行有时间重叠，不能把两项时长相加当作墙钟工作时长。','',
        '两次均固定种子 90517，在单个接收机实例上轮流流入五种信道。每分钟保存累计采样、窗口计数、CPU 时间和逐类结果，开始时记录接收机源文件指纹。旧版观察到脉冲失效后，改进版加入一次有条件的异常时段擦除重试。接收成功以逐字节载荷匹配为准。40 分钟复验的进程在加入非有限 IQ 拒收检查之前启动，验证的是新版解调与原同步前端，而非最终发布包的每一个字节；最终包另外经整套测试及每档 1,200 包复验。两次运行窗口总数不同；严格同样本配对结果见下一节。','',
        '| 接收条件 | 原版正确 / 发送 | 改进版正确 / 发送 | 改进版整包错误率 |','|---|---:|---:|---:|']
    for key,label in labels.items():
        a,b=old['stats'][key],new['stats'][key]
        output.append(f'| {label} | {a["received"]:,} / {a["sent"]:,} | {b["received"]:,} / {b["sent"]:,} | {100*b["per"]:.3f}% |')
    output += ['',f'两次纯噪声窗口分别输入 {old["stats"]["noise"]["input_seconds"]:.1f} s、{new["stats"]["noise"]["input_seconds"]:.1f} s。全部窗口中的多余输出分别为 {sum(v["unexpected"] for v in old["stats"].values())} 和 {sum(v["unexpected"] for v in new["stats"].values())}。每个有效包都使用未知初相、±6 kHz 内随机频偏和随机位置。这里的 50 分钟指计算机实耗墙钟时间，输入 IQ 所代表的信号时长另行统计。','',
        '原始记录：[原版 50 分钟](../evidence/soak-50min.jsonl) · [新版 40 分钟](../evidence/soak-release.jsonl)。在项目根目录运行 `python evidence.py soak-50min.jsonl --minimum 3000`、`python evidence.py soak-release.jsonl --minimum 2400` 可检查起止时间、计数及累计进度。','',
        '## 保留的失败样本与修复','',
        '1. **强单音脉冲。** 同一批 512 个脉冲污染包，改进前收回 450，改进后收回 512。原来的 62 次失败都找到了正确包头，错误在数据判决。更多类型的脉冲、深衰落或多段同时受损不在该保证内。[复现和诊断](IMPULSE.md)。',
        '2. **采样率转换的带边损失。** 原版 8 倍插值/抽取，32 字节全零、−6 kHz CFO、−3 dB 时为 0/96，简化基带对照却是 96/96。24 次配对复验中，新接收机抽取前宽搜在 −6 kHz 收到 24/24，在 +18.3 kHz 收到 23/24；准确已知偏移的校准模式分别为 24/24。已知偏移的结果不等于盲收能力。[实验数据](../bench/correction.json)。',
        '3. **包内频漂与多径。** 可选跟踪解调在 +100 kHz/s 合成线性漂移中从 0/64 改善到 64/64；80 µs、幅度比 0.9 的静态两径，单独均衡重试从 24/64 到 52/64，仍有 12 包丢失。[跟踪](TRACKING.md) · [均衡](EQUALIZER.md) · [重放失败包](../channels/README.md)。','',
        '## 弱信号接收（FTSK-4）','',
        '每档 1,200 个随机包，固定随机种子 9020。两种搜索接收完全相同的 IQ。所有档位的 SNR 均为每复采样 SNR，并非 dBm 或 Eb/N0。整包漏收计为错误。','',
        '| SNR | 完整搜索错包数 | 快速预筛错包数 | 完整搜索 PER 的 Wilson 95% 区间 |','|---:|---:|---:|---:|']
    waterfall=read('waterfall-release.json')['results']
    for level,results in waterfall.items():
        a,b=results['exhaustive'],results['fast']; lo,hi=a['wilson95']
        output.append(f'| {float(level):g} dB | {a["errors"]} / 1200 | {b["errors"]} / 1200 | {lo*100:.2f}% – {hi*100:.2f}% |')
    output+=['','[数据与全部配对差异](../bench/waterfall-release.json)。','',
             '## 长时间无包监听（快速预筛）','','每种工况独立输入一小时、460,800,000 个采样；这是 IQ 所代表的时间。','',
             '| 无有效包输入 | 进入相干搜索的时刻数 | 候选包头 | CRC 通过的输出 |','|---|---:|---:|---:|']
    for key,label in [('awgn','复白噪声'),('cw','白噪声加连续单音'),('impulsive','白噪声加稀疏脉冲')]:
        v=read(f'no-packet-{key}.json')
        output.append(f'| {label} | {v["prescreen_hits"]:,} | {v["candidate_headers"]:,} | {v["crc_passing_packets"]:,} |')
    output+=['','即使一小时零输出，泊松假设下的单侧 95% 上界仍接近 3 次/小时。三种输入分布不同，不应把三行合并为对真实频段的虚警保证。[复现脚本](../bench/false_alarm.py)。','',
             '## 工程交付与边界','','- **互通。** 独立的 C++17 实现和 Python 使用同一条空口规范，跨语言比较了载荷和浮点波形。详见 [C++ 验证](../cpp/README.md)。',
             '- **文件。** 文件长度和 SHA-256 通过后才发布接收结果；八个数据块加一个 XOR 校验块，每组可补一个丢块。超过能力的丢失不会交付不完整文件。见 [传输规则](TRANSFER.md)。',
             '- **慢速模式。** FTSK-8 的每帧用时 66 ms，FTSK-4 用时 35 ms。相同功率时八段模式多耗约 2.75 dB 能量；不能据此主张等能量条件下更灵敏。[公平比较](PROFILES.md)。',
             '- **计算与频谱。** 在此主机，已校频单路快速链的连续数据处理速度约为 2.43 倍实时（不含驱动）。插值后数字波形 99% 等尾带宽约 122.56 kHz，有效时间内 PAPR 中位数 4.21 dB。多路盲收和实物 RF 链均需单独验证。[数字谱和边带](../bench/README.md) · [完整计时](../bench/live_budget.json)。',
             '- **文件操作。** 同一路径和硬链接别名保护、损坏的采样或侧车元数据、模拟 fsync 失败等均有回归测试。[审查记录](AUDIT.md)。',
             '- **下一道验收。** 两台独立时钟的 SDR 经衰减器连接，记录端口功率、温度、每档整包错误率及频谱仪实测带宽，再开展现场测试。[操作步骤](HARDWARE.md)。','',
             '可用 `python selftest.py` 检查当前安装。完整回归命令为 `python -m unittest discover -v`；长跑、互通与性能基准分别执行，不以回归条数替代测量时长。']
    longfile=read('longfile.json')
    output+=['','## 跨几千帧的文件恢复','',f'在同一连续接收机上发送 {longfile["size"]:,} 字节二进制文件，共 {longfile["records"]:,} 帧。每组故意擦去一个数据帧，总计 {longfile["erased"]:,} 帧；PHY 盲收 {longfile["decoded_records"]:,} 帧，传输层修复 {longfile["file_rx"]["recovered"]:,} 个块。最终文件逐字节一致，SHA-256 为 `{longfile["sha256"]}`。这是一段时长 {longfile["signal_seconds"]:.1f} 秒、频偏 +3721.5 Hz 的合成 IQ。[完整记录](../bench/longfile.json)。','', '[独立解压、测试和原生编译记录](../evidence/test-report.txt)。']
    return '\n'.join(output)+'\n'


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    args.out.write_text(build(),encoding='utf8')
