# FTSK · 频迹键控收发机

**v0.0.2-demo** · Python ≥ 3.10 · 仅依赖 NumPy

[![version](https://img.shields.io/badge/version-v0.0.2--demo-blue)](CHANGELOG.md)
[![python](https://img.shields.io/badge/python-3.10%2B-3776AB)](requirements.txt)
[![tests](https://img.shields.io/badge/tests-59%20passed-brightgreen)](tests/)
[![radio](https://img.shields.io/badge/physical%20RF-not%20yet%20measured-lightgrey)](docs/HARDWARE.md)

频迹键控（Frequency Trajectory Shift Keying，FTSK）是一种频率轨迹调制：**一个符号由多段等长单音组成，信息同时编码在起始频点和各段之间的跳频规律里**。本仓库是它的可运行物理层参考实现——从比特、波形、同步接收，一直到文件传输和软件无线电接口。

> **这是演示版，不是定型产品。** 全部结果来自合成 IQ 与内存设备回环；尚未用两台实体电台测量接收灵敏度、占用带宽、通信距离或法规符合性。能力与缺口见 [验收记录](docs/ACCEPTANCE.md)。

---

## 目录

- [核心思路](#核心思路)
- [30 秒上手](#30-秒上手)
- [实测数据](#实测数据)
- [目录结构](#目录结构)
- [命令行工具](#命令行工具)
- [空中接口](#空中接口)
- [已验证与未验证](#已验证与未验证)
- [文档索引](#文档索引)
- [复现与测试](#复现与测试)

---

## 核心思路

把 9 个比特解释为整数 $m\in[0,511]$，令 $a=\lfloor m/31\rfloor$、$b=m\bmod 31$，四段依次发送频点

$$
k_i=(b+a\,x_i)\bmod 31,\qquad x=(0,1,3,7)
$$

31 是素数，任意两条不同的模 31 直线最多相交一次，所以**任意两个符号至少在三段上不同**。接收端只要知道哪两段受损，剩下两段就能唯一恢复整个符号——这就是 FTSK 用“轨迹”而不是“单个频点”承载信息的原因。

| 输入 `010100011`（163） | `a=5, b=8` |
|---|---|
| 发射频点 | **8 → 13 → 23 → 12** |
| 对应基带频率 | −28 → −8 → +32 → −12 kHz |
| 接收判决 | 沿 512 条候选轨迹累加四段能量，取最大者 |

---

## 30 秒上手

```powershell
python -m pip install -r requirements.txt

# 1. 看一条中文消息如何被丢帧后仍然还原
python demo_cn.py

# 2. 传一个真实文件（编码 → 盲收 → 校验）
python ftsk.py file encode README.md message.cf32
python ftsk.py file decode message.cf32 recovered.md
```

第二条命令中，接收端只看连续复数采样，不知道包从哪里开始，自行搜索 ±6 kHz 载波频偏、解出 32 字节载荷、重组文件，并用 SHA-256 校验后才发布 `recovered.md`。

想直接看全部入口：

```powershell
python ftsk.py --help
```

---

## 实测数据

以下数字全部由仓库内脚本生成，原始数据在 [bench/](bench/)、[channels/](channels/)、[evidence/](evidence/)。

### 整包错误率（FTSK-4，每档 1,200 个随机包）

| 每复采样 SNR | 完整搜索 | 快速预筛 | PER 的 95% 区间 |
|---:|---:|---:|---:|
| −4 dB | 0 / 1200 | 0 / 1200 | 0 – 0.32% |
| −5 dB | 3 / 1200 | 3 / 1200 | 0.09% – 0.73% |
| −6 dB | 46 / 1200 | 46 / 1200 | 2.89% – 5.08% |
| −7 dB | 329 / 1200 | 333 / 1200 | 25.0% – 30.0% |
| −8 dB | 943 / 1200 | 958 / 1200 | 76.2% – 80.8% |

> SNR 指**每复采样信噪比**，不是 dBm，也不是 Eb/N0。不能直接换算成通信距离。

### 长时间连续运行

| 项目 | 结果 |
|---|---|
| 连续监听 | 50 分 01 秒实际耗时，70228 个窗口，3.415 亿个 IQ 采样 |
| 0 dB 单径 | 14,046 / 14,046 |
| −6 dB 单径 | 13,497 / 14,046 |
| 强单音脉冲 | 配对 512 包：450 → 512；20 分钟段 10,352 / 10,352 |
| 纯噪声误报 | 0 个多余输出 |

### 其他指标

| 指标 | 数值 |
|---|---|
| FTSK-4 净载荷速率 | 7.31 kbit/s（35 ms/帧，无帧间隙） |
| FTSK-8 净载荷速率 | 3.88 kbit/s（66 ms/帧，最小分段距离 7） |
| 99% 等尾占用带宽（插值后数字 IQ） | 122.56 kHz |
| 包内 PAPR 中位数 | 4.21 dB |
| 单路快速接收吞吐（本机） | 约 2.43 倍实时 |
| 文件恢复 | 100 KiB / 5764 帧，主动擦除 640 帧后仍逐字节还原 |

---

## 目录结构

```
FTSK/
├── README.md               本文件
├── CHANGELOG.md            版本变更
├── requirements.txt        运行依赖（仅 NumPy）
├── ftsk.py                 统一命令行入口
│
├── frame.py                帧结构、比特打包、CRC、载荷判决
├── receiver.py             连续流盲同步接收机
├── ftsk_demo.py            单符号调制/解调基础实现
├── profiles.py             FTSK-4 / FTSK-8 双速率
├── transfer.py             变长文件传输（FT/1）
├── radio.py                SoapySDR 单包收发
├── radio_file.py           SoapySDR 文件收发
├── capture.py              录音、离线宽频偏搜索
├── linktest.py             编号探针与丢包统计
├── fast_acquire.py         可选：差分包头预筛
├── wide_rx.py              可选：多路宽频偏搜索
├── tracking_rx.py          可选：包内频漂跟踪
├── equalizer.py            可选：静态多径均衡
├── modem.py / demo_cn.py / selftest.py / soak.py / evidence.py
├── io_helpers.py           原子写入与别名保护
├── release_check.py        发布包构建与校验
│
├── docs/                   全部说明文档
├── tests/                  59 项回归测试
├── examples/               参考载荷与参考 IQ
├── fixtures/               历史版本归档（用于回归对照）
├── evidence/               长跑记录、验收日志
├── bench/                  频谱、吞吐、误包率、虚警实验
├── channels/               多径、频漂、时钟误差配对实验
└── cpp/                    独立 C++17 实现（编码、对齐解码、连续流盲收）
```

顶层保留扁平模块，是为了让 `python ftsk.py ...` 无需安装即可运行；测试、文档、数据与样例各自归入子目录。

---

## 命令行工具

统一入口 `ftsk.py`，子命令对应上表模块：

| 子命令 | 用途 | 典型命令 |
|---|---|---|
| `packet` | 单包编码/盲收/仿真 | `python ftsk.py packet decode capture.cf32` |
| `profile` | 四段与八段模式 | `python ftsk.py profile decode slow.cf32` |
| `file` | 任意长度文件收发 | `python ftsk.py file encode a.bin a.cf32` |
| `radio` | 单包 SDR 收发 | `python ftsk.py radio rx --freq 433920000` |
| `radio-file` | 文件 SDR 收发 | `python ftsk.py radio-file tx a.bin --freq 433920000` |
| `capture` | 录音与离线宽搜 | `python ftsk.py capture replay rec.cf32 --span 20000` |
| `link` | 台架丢包统计 | `python ftsk.py link analyze --iq run.cf32 --id 0123456789abcdef --count 100` |

每个模块也能单独运行，例如 `python modem.py demo --frames 100 --snr -3 --cfo 4000`。

硬件路径需要 SoapySDR 绑定与对应驱动，操作步骤见 [台架验收](docs/HARDWARE.md)。

---

## 空中接口

| 参数 | FTSK-4 v1 | FTSK-8 |
|---|---|---|
| 采样率 / 时段长度 | 128 kS/s / 32 采样（250 µs） | 同左 |
| 每符号段数 × 比特 | 4 × 9 bit | 8 × 9 bit |
| 轨迹坐标 | `0 1 3 7` | `0 1 3 7 12 18 23 29` |
| 每帧 | 32 字节 + CRC-16 + 7 填充位 | 同左 |
| 帧长 | 4480 采样 / 35 ms | 8448 采样 / 66 ms |
| 最小分段距离 | 3（可纠 1 段） | 7（可纠 3 段） |
| 净速率 | 7.314 kbit/s | 3.879 kbit/s |

完整比特序、CRC 参数与参考向量见 [空中接口规范](docs/AIR-INTERFACE.md)；文件传输的分组与修复规则见 [TRANSFER](docs/TRANSFER.md)。

---

## 已验证与未验证

**已验证**

- 512 个符号的无噪声 IQ 往返、61,440 种单段错判、3,072 种双段擦除恢复
- 未知起点、随机初相、±6 kHz 频偏、分块长度任意时的连续流盲收
- 50 分钟长跑与每档 1,200 包的误包率统计
- 59 项 Python 回归 + 4 组 C++ 互通测试，可在干净解压目录一键复验
- 设备滤波带边损失的真实反例（0/96）与修复（24/24）
- 文件级 SHA-256 校验、原子发布、硬链接别名与 fsync 失败的输入保护

**未验证**

- 实体 SDR 之间的灵敏度（dBm）、有效距离、天线与功放影响
- 频谱仪上的实际占用带宽、杂散与法规符合性
- 与 LoRa 在相同带宽/速率/能量下的对比
- 多节点组网、重传、加密与身份认证（本版均未实现）

---

## 文档索引

| 文档 | 内容 |
|---|---|
| [ACCEPTANCE](docs/ACCEPTANCE.md) | 验收记录：长跑、修复对照、性能边界 |
| [AIR-INTERFACE](docs/AIR-INTERFACE.md) | 比特到波形的完整规范 |
| [PROFILES](docs/PROFILES.md) | 四段与八段的空中格式、公平比较 |
| [TRANSFER](docs/TRANSFER.md) | FT/1 文件传输格式与恢复条件 |
| [HARDWARE](docs/HARDWARE.md) | 双电台台架步骤、校频、PER 统计 |
| [IMPULSE](docs/IMPULSE.md) | 强脉冲干扰的故障复现与修复 |
| [TRACKING](docs/TRACKING.md) | 包内频漂跟踪 |
| [EQUALIZER](docs/EQUALIZER.md) | 静态多径均衡重试 |
| [AUDIT](docs/AUDIT.md) | 输入校验与文件安全审查 |
| [bench/README](bench/README.md) | 频谱、吞吐、校频实验 |
| [channels/README](channels/README.md) | 多径、频漂、时钟误差配对实验 |
| [cpp/README](cpp/README.md) | 独立 C++17 实现与互通验证 |

---

## 复现与测试

```powershell
python -m unittest discover -v      # 59 项回归
python selftest.py                  # 参考向量 + 文件收发自检
python bench/waterfall.py --out bench/waterfall-new.json   # 误包率曲线（约 5 分钟）
g++ -std=c++17 -O2 -Wall -Wextra -Wpedantic cpp/ftsk.cpp -o cpp/ftsk.exe   # 见 cpp/README.md
```

回归测试使用固定随机种子，输出可直接比对；长跑与频谱实验分别记录在 `evidence/` 与 `bench/`，每条结果都带有生成时的源码 SHA-256。

---

## 版本与许可

当前版本 **v0.0.2-demo**，变更见 [CHANGELOG](CHANGELOG.md)。仓库尚未声明开源许可证，默认保留所有权利；如需复用请先联系作者。物理层设计可自由用于研究与评估，但**投入使用前必须自行完成射频合规与安全评估**。
