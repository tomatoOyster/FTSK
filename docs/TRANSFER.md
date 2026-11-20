# FTSK-4 文件传输协议 FT/1

FT/1 将任意二进制文件切成可独立编号的片段，一个片段占一个 32 字节的 v1 物理帧载荷。输出文件逐字节还原，文件名由接收方指定。空文件也可传输。

## 在本地传一个文件

在项目目录执行以下命令，第三条命令输出两个相同的 SHA-256。

```powershell
python transfer.py encode examples/example.bin capture.cf32 --id 0123456789
python transfer.py decode capture.cf32 received.bin --id 0123456789
Get-FileHash examples/example.bin, received.bin -Algorithm SHA256
python -m unittest -v test_transfer
```

编码器每次写一帧小端 CF32（采样率 128 kHz）。解码器每次读取 4096 个复采样，调用流式 v1 接收机，在 IQ 输入结束时验证整文件。编码端的输出路径 `-` 表示标准输出；解码端的输入路径 `-` 表示标准输入。使用支持原样传递二进制字节的管道，例如 Windows `cmd.exe`，如下可直接运行。

```cmd
python transfer.py encode examples/example.bin - | python transfer.py decode - received-pipe.bin
```

编码器的摘要写入标准错误，不会混入二进制管道。默认创建新文件，目标已存在时返回错误。`--force` 允许**成功验证后**原子替换已有接收文件。写普通 IQ 文件也采用临时文件原子发布。未指定 `--id` 时，编码器随机生成 5 字节 ID；接收机选中首个有效 FT/1 记录的 ID，忽略其他传输。混有多项任务的捕获请指定所需 ID。重发相同文件时可以复用同一个 ID，把两次捕获按采样顺序接入同一接收机，或将两份 CF32 二进制拼接后解码。内容变化时换新 ID。

## 可选慢速与混合配置

所有 FT/1 记录均为 32 字节，因此也可选用 8 时段物理帧，不需更改文件格式、校验或修复逻辑：

```powershell
python transfer.py encode examples/example.bin slow.cf32 --profile 8
python transfer.py decode slow.cf32 restored.bin --profile auto
python radio_file.py rx restored-sdr.bin --freq $freqHz --rate 1024000 --profile auto --seconds 30
python radio_file.py tx examples/example.bin --freq $freqHz --rate 1024000 --profile 8
```

默认收发配置仍为 `4`。收端选择 `8` 只搜索慢速帧；选择 `auto` 可在同一传输内混用两种速率的记录。对于相同帧数，慢速理想空口时间和 CF32 字节数为 `66 ms × 帧数` 和 `67584 × 帧数`（不含 SDR 滤波和保护样本）。接收端应预留处理时间并设置足够的 `--seconds`。SDR 选项兼容关系及预算见[配置规范](<PROFILES.md>)。

## 记录与恢复算法

所有整数均为无符号大端。每条记录共 32 字节。

| 偏移 | 字节数 | 含义 |
|---|---:|---|
| 0 | 2 | ASCII `FT`（`46 54`） |
| 2 | 1 | 高 4 位版本 1；低 4 位类型，0 清单、1 数据、2 校验 |
| 3 | 5 | 传输 ID |
| 8 | 4 | 类型内从 0 开始的索引 |
| 12 | 20 | 记录内容 |

清单为 `8 字节原始文件长度 || 32 字节 SHA-256`，依次存入索引 0 和 1。发送端在整次传输的开头和结尾各发送一次完整清单。接收方取得两个索引各任一份即可。清单缺失时可追加同一传输的副本。

数据记录每条放 20 字节，最后一条在右侧补零。每 8 个数据记录组为一组，最后一组可以较短。组内各条逐字节 XOR，得到一个 20 字节校验记录，其索引为组号。接收机对重复的相同记录进行去重，即使清单晚到也可接收数据；同一 ID、类型与索引的冲突内容会使本次解码失败。组内丢一个数据记录时可用校验恢复。组内数据都在时可以丢掉该组的校验记录。若同组丢两个数据记录，须并入同 ID 同内容的重发。程序最终按声明长度裁剪补零，验证长度与完整 SHA-256 后原子发布。摘要用于检错，来源认证需要上层协议。

## 时间、容量和失败处理

设 `D = ceil(文件字节数 / 20)`，总帧数为 `4 + D + ceil(D / 8)`。每帧 35 ms、35840 字节 CF32。举例，1024 字节文件需要 63 帧，理想连续发射时间 2.205 秒，保存为 CF32 为 2257920 字节。较大文件宜流式收发，以节约 IQ 存储空间。

默认文件上限为 1 MiB。`--max-bytes N` 可在收发两端设置 0 至 16 MiB 的整数限制；超限的长度和索引在写相应分片前被拒绝。单个接收实例只保存一个 ID，数据和校验使用随机访问临时文件，去重与完成度用有界字节数组记录。临时磁盘峰值约为文件上限的 2.125 倍，另计文件系统开销和已有的输出。编码器从普通可定位文件先读一次计算摘要，第二次流式发射并核对源文件是否变化。

解码时只要清单、数据、校验不足，或 SHA-256 不匹配，命令返回非零退出码。临时文件与目标同目录，正常退出或异常清理时释放。默认无覆盖发布使用同文件系统硬链接，适用于 NTFS 等支持硬链接的文件系统；`--force` 走同目录原子替换。中途断电可能需人工清理残留的 `.ftsk-*` 临时文件。

## 接入其它信道

`iter_records(path, *, transfer_id=None, max_bytes=...)` 按传输顺序产生 32 字节 `bytes`，可直接交给现有 v1 发射机。将接收机通过 CRC 的载荷传给 `FileReceiver.feed(payload)`。长时间监听时，`ready` 表示已取得足够的独立记录供 `finish()` 验证，验证成功返回 `bytes`、`transfer_id`、`sha256`、`recovered` 和 `duplicates`。请用上下文管理器保证清理临时文件。

```python
from transfer import FileReceiver

with FileReceiver('received.bin', transfer_id=bytes.fromhex('0123456789')) as rx:
    for packet in received_packets:
        rx.feed(packet.payload)
        if rx.ready:
            result = rx.finish()
            break
```
