# FTSK-4 / FTSK-8 packet profiles

The optional dual-profile modem encodes either rate and identifies it from the received sync waveform. Profile 4 calls the existing v1 encoder and body decoder directly. The original command-line tools continue to use profile 4. [Dual-profile module](<profiles.py>) · [v1 format](<README.md>)

```powershell
python profiles.py encode example.bin slow.cf32 --profile 8
python profiles.py decode slow.cf32
python profiles.py measure --frames 100 --seed 2026 --snr -7
python -m unittest -v test_profiles test_packets
```

## File and SDR entry points

The transfer, radio-file and single-packet radio CLIs all default to profile 4. Select `--profile 8` to transmit or receive only slow frames; `--profile auto` on RX searches for both headers. See [file examples](<TRANSFER.md>). The profile encoder refuses an existing output unless `--force` is specified; source/output aliases remain forbidden. Example SDR single-packet commands (set `$freqHz` first):

```powershell
python radio.py rx --freq $freqHz --rate 1024000 --profile auto --offset-hz 12000 --seconds 20
python radio.py tx --freq $freqHz --rate 1024000 --profile 8 --payload-hex ('ab'*32)
```

Use the offset only if calibration shows a corresponding carrier error. For **every** RX profile, calibrated `--offset-hz` mixes the device-rate input before decimation and is included in the reported CFO. V1 also offers `--fast` (prescreen acquisition) or `--tracked` (drift/echo retry). Those two algorithms are mutually exclusive and neither supports `8` or `auto`; either may be combined with upstream calibration. The residual offset after mixing must still lie within the receiver search range. Known-rate RX runs one header bank; auto RX runs two banks.

**Runtime budget:** streaming requires processing one second of device samples in under one second. In one local reference run (1 s of noise at 128 ksample/s, 4096-sample blocks, median of 3 runs, acquisition only), RX took 1.35 s for `4`, 1.23 s for `8` and 2.41 s for `auto`. Other workloads were active. These measurements illustrate why real-time operation is not assured: radio I/O, resampling and decoding cost additional time. For `auto` prefer recording and decoding offline where the CPU cannot keep up. A longer RX deadline cannot cure sustained device overflow.

## Wire format

Each slot lasts 32 complex samples at 128 ksample/s. Transmit `exp(j 2π(k−15)n/32)` for tone index `k` and `n=0…31`. The common payload packer takes 32 bytes, appends the big-endian CRC-16/CCITT-FALSE and seven zero padding bits, and emits 31 nine-bit integers MSB first. Both profiles use `(m%31 + (m//31)*x) mod 31` for each slot. Any two distinct coordinates identify one line over GF(31). Thus the slow profile has minimum distance seven and uniquely corrects up to three hard-slot substitutions per symbol, or up to six known erasures, in the aligned hard-decision model.

| Field | FTSK-4 v1 | FTSK-8 |
|---|---|---|
| First 12 sync slots, tone indices | `5 22 11 26 3 17 8 24 13 2 20 10` | `21 8 26 11 1 24 6 18 28 12 4 23` |
| Four delimiter slots | `27 6 19 4` | `9 20 2 16` |
| Body coordinates in transmission order | `0 1 3 7` | `0 1 3 7 12 18 23 29` |
| Tones for symbol 163 | `8 13 23 12` | `8 13 23 12 6 5 30 29` |
| Total frame samples / duration | 4480 / 35 ms | 8448 / 66 ms |
| Net payload rate, zero interframe gap | 7.314 kbit/s | 3.879 kbit/s |

The two 16-slot headers are the profile identifiers. The receiver searches both templates on each block using one input FFT, one window-energy computation and one absolute sample clock. Each template has its own inverse-FFT correlation bank over ±6000 Hz, so the second profile has a real computational cost. Two independent receivers would duplicate input transforms and buffering and require reconciliation of competing packet boundaries. The shared receiver refines carrier offset using the selected header, then combines the slot energies noncoherently over the indicated body length.

An accepted CRC and valid padding commit precisely the half-open interval `[start, start+profile_length)`. The next sample can start another header. A failed candidate consumes no packet interval. An incomplete long candidate does not delay a complete short candidate. Samples required by a pending hypothesis or the next overlap-save block are retained across calls. The decoder accepts a frame that ends on the final input sample. Simultaneous overlapping on-air packets are outside this serial stream model.

## Blind packet-error measurement

The command above draws 100 payloads, start delays, phases, and carrier offsets uniformly between −5500 and +5500 Hz (uniform offsets only). Each comparison uses the same payload, offset, initial phase, timing and shared complex AWGN draws. The baseline per-sample SNR is −7 dB. A missing correct CRC-valid payload/profile counts as one packet error. Each stream ends on its frame's final sample.

| Profile and comparison | Relative sample power | Relative total signal energy per payload, including header | Packet errors | Unexpected outputs |
|---|---:|---:|---:|---:|
| FTSK-4 reference | 1.000 | 1.000 | 27/100 | 0 |
| FTSK-8 at equal sample power | 1.000 | 1.886 | 0/100 | 0 |
| FTSK-8 at equal energy per payload | 0.5303 | 1.000 | 48/100 | 0 |

At equal power the longer packet spends 2.75 dB more energy. Matching the complete-packet energy reduces the amplitude of *all* slow-profile samples by `sqrt(4480/8448)`, including its header. The third result includes acquisition losses as well as body-decoding losses. These counts describe this fixed-seed AWGN experiment, including sampling uncertainty. Hardware sensitivity and sustained real-time throughput require separate measurements.

The regression suite exercises noisy adjacent 8→4→4→8 frames across arbitrary chunk boundaries, corruption, false header retention, end-of-stream decoding, pure noise, and three altered slots in every slow symbol. [Regression tests](<test_profiles.py>)
