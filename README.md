# FTSK · 频迹键控收发机

**版本 `v0.0.1-demo`（首个公开演示版）** · Python ≥ 3.10 · 依赖 NumPy

频迹键控（Frequency Trajectory Shift Keying）是一种频率轨迹调制：一个符号由多段等长单音组成，信息同时编码在起始频点和各段之间的跳频规律里。本仓库是该体制的可运行物理层参考实现，保留原 FTSK-4 空口，并包含 FTSK-8 慢速档、变长文件传输、独立 C++ 互通实现和 SDR 台架工具。

**这是演示版，不是定型产品。** 软件仿真及内存设备回环已验证；尚无实体电台间的接收灵敏度、空口距离或法规符合性实测。当前能力与缺口见 [验收报告](ACCEPTANCE.md)，版本变更见 [CHANGELOG](CHANGELOG.md)。

## 先看一条消息怎么到达

在有 NumPy 的环境中运行 `python demo_cn.py`。内置示例把 75 字节的中文消息编码成 9 帧，主动抹掉 1 个数据帧，再加入频偏和噪声；接收端从其余 8 帧重建完整消息并打印 SHA-256。也可以把自己的文字作为命令行参数传入。运行 `python selftest.py` 可用独立参考 IQ 和文件收发链检查安装。

## 从一个文件开始

推荐使用 Python 3.12（实测版本 3.12.14）。安装依赖后，在项目根目录运行以下命令。示例输出文件需为新路径。

```powershell
python -m pip install -r requirements.txt
python ftsk.py file encode README.md message.cf32
python ftsk.py file decode message.cf32 recovered.md
Get-FileHash README.md, recovered.md
```

两个 SHA-256 应相同。要快速看单包、模拟丢包或只看四段/八段的区别，可以运行 `python ftsk.py --help`。

| 我想做什么 | 入口与说明 |
|---|---|
| 发送二进制文件，处理掉包、重复包、乱序 | `ftsk.py file`；[分组格式与恢复条件](TRANSFER.md) |
| 较高速或较强时段冗余 | `ftsk.py profile`；[FTSK-4 / FTSK-8 比较](PROFILES.md) |
| 用两台 SDR 收发、录制 IQ、校频、测 PER | `ftsk.py radio` / `radio-file` / `capture` / `link`；[台架步骤](HARDWARE.md) |
| 与另一种语言独立互通 | [C++17 编译与双向测试](cpp/README.md) |
| 研究弱信号、多径或包内频漂 | [信道模型与失败样本](channels/README.md)、[频漂跟踪](TRACKING.md)、[均衡重试](EQUALIZER.md) |
| 复核结论 | [验收汇总](ACCEPTANCE.md)、[设备链校频、带宽与 PAPR](bench/README.md)、[脉冲故障诊断](IMPULSE.md)、[文件及输入校验审计](AUDIT.md) |

物理层每帧传输 32 字节；文件传输层用多帧运送文件。接收机从连续复数采样中自行寻找包头、估计频偏、校正并检查 CRC。默认输出不会覆盖已有文件；仅在支持的命令中显式使用 `--force` 才允许替换。

## 本地运行

建议 Python 3.10 或更新版本，安装 NumPy `python -m pip install numpy`。在本目录执行：

```powershell
python modem.py demo --frames 100 --snr -3 --cfo 4000
python modem.py encode example.bin my-frame.cf32
python modem.py decode my-frame.cf32
python -m unittest discover -v
python ftsk_demo.py
```

`example.bin` 是 `00 01 ... 1f` 共 32 字节。包编码器读取原始二进制文件。`example.cf32` 是该包的参考采样。IQ 文件按小端 float32 顺序交错存储 I 和 Q，无文件头，采样率 128000 Hz。每行解码输出包含绝对起始采样位置、估计载波频偏（Hz）和通过校验的十六进制载荷。

`demo` 默认种子 2026，逐包生成随机载荷、初相和起点。`--snr` 是相对于单位功率信号的每复采样信噪比，区别于符号 Demo 的 Eb/N0。`--ppm` 引入线性插值模拟的时钟偏差，`--echo` 增加一个延迟 3 个采样、相移 90° 的回波。`packet_error_rate` 将漏收包计入分母，`unexpected_packets` 单列多余输出。计数为零时仍受测试样本量限制。

## 空口规范 v1

| 项目 | 取值 |
|---|---|
| 采样率和时段 | 128 kHz，每段 32 个复采样，250 µs |
| 频点 | 0 至 30，对应 `(k−15) × 4000 Hz` |
| 每段波形 | `exp(j 2π(k−15)n/32)`，`n=0…31`，每段结束时相位累计整数圈 |
| 前导频点 | `5 22 11 26 3 17 8 24 13 2 20 10` |
| 帧起始标记频点 | `27 6 19 4`，与前导合并成 512 采样的同步模板 |
| 数据顺序 | 32 字节载荷、2 字节 CRC（高字节在前）、7 个零位。字节内 MSB 在前，每 9 位组成一个整数 |
| CRC | CRC-16/CCITT-FALSE，poly `0x1021`，init `0xffff`，refin/refout false，xorout `0`，仅覆盖载荷 |
| 符号映射 | `a=m//31`，`b=m%31`，四段依次取 `(b+a*x)%31`，`x=[0,1,3,7]` |
| 时间与净速率 | 4 ms 包头 + 31 ms 数据，共 4480 个采样，35 ms。连续无间隙时净载荷约 7.31 kbit/s |

参考载荷 `00…1f` 的 CRC 为 `23b3`，31 个符号如下：

```
0 4 16 48 128 321 259 264 18 40 88 192 417 387 392 17 36 76 160 337 194 454 12 282 54 112 232 481 484 236 384
```

接收机在校正频偏后，对每个数据时段做 32 点 FFT，对 512 条轨迹计算能量和。首次 CRC 或填充校验失败时，再用各时段总能量归一化重试一次，以降低单个强干扰时段的影响。新版 FTSK-4 在两次失败后仅当最强时段能量超过中位数八倍才尝试擦除该时段，最多追加一次候选判决。[原始故障与修复结果](IMPULSE.md) 使用同一批 IQ 配对复验。CRC 用于检错，不能证明消息来源。该版本不包含网络层、重传和加密。

## 初版短测记录（历史对照）

以下是旧版 100 包短测。新版的长测、误包率和失败边界见 [验收汇总](ACCEPTANCE.md)。

Python 3 环境中 12 项分组和设备接口测试全部通过，包括 1.024 MS/s 内存设备回环、分块读写、CRC 拒包、紧邻包和末尾无保护间隔的完整帧。接收机的频偏搜索网格为 −6000 到 +6000 Hz，步进 125 Hz，随后用包头细化频偏。固定随机种子 2026，载波偏移 +4000 Hz、每档 100 个包的 AWGN 仿真结果如下。

| 每采样 SNR | 正确恢复 | 多余输出 |
|---|---:|---:|
| −6 dB | 94/100 | 0 |
| −3 dB | 100/100 | 0 |
| 0 dB | 100/100 | 0 |

另一次组合测试使用 `--frames 100 --snr -3 --cfo -4750 --ppm 25 --echo 0.25`，恢复 100/100 包。载荷固定为 32 字节，短包内使用一个包头频偏估计；对持续变化的载频、强多径和长期虚警率需要分别测量。

## 软件无线电

另需 SoapySDR Python 绑定、与设备匹配的驱动和支持所需方向的 SDR。查看 `python radio.py tx --help` 和 `python radio.py rx --help`。两端要设置相同中心频率。初次台架测试可采用同轴线加适当衰减，使接收机输入功率保持在其额定范围内。频率和增益按设备、测试环境与当地要求选择。

```powershell
python radio.py rx --freq $freqHz --rate 1024000 --seconds 30
python radio.py tx --freq $freqHz --rate 1024000 --payload-hex 000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f
```

在各自的 PowerShell 中先把 `$freqHz` 设为选定的中心频率（单位 Hz）。先启动接收端，再发包。硬件采样率取 128000 Hz 的受支持整数倍。硬件验收时应记录设备、增益、样率、中心频率、输入功率、收发包数及实测占用带宽。本项目提供的信道结果是软件仿真，灵敏度、频谱和空口距离需要在具体硬件上测量。
