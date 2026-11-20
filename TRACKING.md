# Optional linear drift and two-path receiver

`TrackingReceiver().feed(chunk)` uses the same 128 ksample/s complex stream and returns the same Packet type as the normal receiver. Every ordinary CRC-passing result is returned unchanged. After an acquired candidate fails, the optional mode makes one extra waveform reconstruction and one set of symbol decisions, checked by the original zero-padding and CRC rules. No extra air bits, transmitter changes or knowledge of the payload are used. [Implementation](<tracking_rx.py>) · [Paired benchmark and tests](<test_tracking_rx.py>)

## Paired measurements

Each row reuses the first 64 captures from the [independent channel study](<channels/study.py>), seed 194911, oversampling 8, nominal SNR −3 dB per complex sample. Both receivers process identical randomly phased IQ in 731-sample chunks. Correct means exactly one output equal to the expected bytes. All 512 cases together produced zero unexpected packets and no changed baseline accepts. Timings are total **receiver process CPU seconds per 64 captures**, excluding simulation. On this host one all-retry measurement added 0.81 CPU s/64 (about 13 ms per captured 48 ms window); runs with no retries can differ slightly due to timing variation. These are not real-time throughput guarantees.

| Case | Baseline | Optional | Baseline CPU s | Optional CPU s |
|---|---:|---:|---:|---:|
| Single path | 64 | 64 | 3.23 | 3.41 |
| +50 kHz/s drift | 61 | 64 | 3.34 | 3.44 |
| +100 kHz/s drift | 0 | 64 | 3.20 | 4.02 |
| Echo 2 µs, gain 0.4 | 55 | 63 | 3.39 | 3.38 |
| Echo 20 µs, gain 0.7 | 40 | 47 | 3.45 | 3.72 |
| Echo 80 µs, gain 0.9 | 24 | 51 | 3.27 | 3.89 |
| Echo 250 µs, gain 1 | 0 | 56 | 3.39 | 3.77 |
| Combined low drift, IQ/DC and short echo | 58 | 60 | 3.38 | 3.36 |

**Diagnosis.** Trial 0 at +100 kHz/s begins at sample 784 with initial carrier +1200 Hz. The static estimate +1398.44 Hz corresponds approximately to the middle of the header. Using that constant correction at the known start produces wrong raw symbol decisions for all indices 18–30 (zero-based). By packet end the actual offset has reached about +4700 Hz. The optional receiver returned the correct start and exact payload with an initial offset estimate +1209.46 Hz and slope +100000 Hz/s. A recovered packet reports the fitted initial CFO; an ordinary pass retains the original header estimate.

## Signal processing and limits

Each data slot has an unknown tone on a **known 4 kHz lattice**. The retry computes zero-padded FFT peaks and removes their integer bin ambiguity by mapping frequency modulo 4 kHz onto a circle. A grid search over slopes from −200 to +200 kHz/s in 4 kHz/s steps maximizes the resultant length across all 140 slots. The coarse header frequency anchors the modulo ambiguity at header midpoint. After removing the fitted quadratic phase, the known header refines the remaining constant frequency error.

A two-tap fit then compares signed delays of 1–32 samples against the known header. A second path must explain at least 2.5% of observed header energy beyond the fitted direct path to be included. Each 128-sample symbol is compared against all 512 forward-modeled candidate waveforms. To avoid contamination by unknown neighboring symbols, one boundary of width `abs(delay)` is excluded. Correlation magnitude removes an unknown common phase per symbol; subtracting template energy accounts for path-dependent gain. The final check tries one sequence, rather than using CRC to search over delays or slopes.

The original acquisition gate still has to find the packet. This mode does not model fast non-linear drift, changing echoes, arbitrary multi-tap fading or sampling-clock walk. Subsample echo delays use an integer approximation, and deep fades can destroy information. The 20 µs case still missed 17/64. A 250 µs echo may lock acquisition to either path; a returned start refers to that lock, not necessarily the first arrival. Wrong CRC-valid frames remain possible. Use the optional mode for offline or impaired links; profile for the available real-time CPU before enabling it on a live SDR.

```powershell
python -m unittest -v test_tracking_rx
python test_tracking_rx.py --trials 64
```
