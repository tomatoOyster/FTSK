# Recovering an FTSK-4 packet hit by one strong slot

The blind receiver recovered 512/512 packets with a +12-amplitude tone in one uniformly chosen body slot. The archived receiver recovered 450/512 from exactly the same IQ. All 62 misses had a valid header candidate at the correct sample. Erasing the one unusually energetic slot recovered all 62 from blind acquisition. This supports keeping the change in the body decoder. [frame.py](../frame.py)

## Paired benchmark

The diagnostic uses the archived receiver code twice, with independent continuous receiver state, and substitutes only the body decoder. It recreates all five soak cases in their original order with the same noise, carrier and chunk-size RNG draws. Results below use seed 90517 and 512 windows per case. Both streams emitted zero unexpected packets, including incorrect CRC-valid payloads and duplicates. [test_impulse.py](../tests/test_impulse.py) · [soak.py](../soak.py)

| Case | Archived correct | Updated correct | Recovered among archive misses |
|---|---:|---:|---:|
| Nominal | 512 | 512 | — |
| Impulse | 450 | 512 | 62/62 |
| Edge (−6 dB sample SNR) | 500 | 500 | 0/12 |
| Clock/echo (−3 dB, +25 ppm, echo 0.25) | 511 | 511 | 0/1 |
| Noise only | 0 outputs | 0 outputs | — |

The ongoing 50-minute run had loaded the original decoder. Its reported checkpoint of 971/1122 impulse and 1123/1123 nominal belongs to the baseline. The SHA-256 of the archived body-decoder source matches its startup record, `05ef49255f1828f9077d38378995e921243afaa13758ae1058e95d769fa79325`. [ftsk4-v1.zip](../fixtures/ftsk4-v1.zip) · [soak-50min.jsonl](../evidence/soak-50min.jsonl)

## Where those packets failed

* **Acquisition.** For every baseline miss in the three impaired cases, the blind receiver tried the exact transmitted start. The smallest normalized header scores at the nearest carrier grid point were 0.380 for impulse, 0.156 for edge and 0.238 for clock/echo (rounded down). The gate was 0.055.
* **Bit decisions.** With the true start and carrier offset supplied, the ordinary decoder made 62 symbol errors (279 coded-bit errors) across the 62 missed impulse packets. Every error was in the symbol containing the injected tone. Normalizing each slot by its energy reduced these to 61 symbol errors (278 bits). The normalized decision recovered one more packet under oracle carrier correction. The three undamaged slots contain enough information, but the strongest frequency in the interfered slot can still dominate after normalization.
* **Erasure selection.** On all 62 impulse misses the highest in-band slot energy occurred at the injected slot. All cleared the eight-times-median threshold. All recovered with the same rule using blind synchronization. The edge and clock/echo misses also failed with exact start and carrier and none cleared the outlier threshold. With the normalized decision, the edge misses had 12 symbol errors (48 coded bits) and the clock/echo miss had one symbol error (five bits). These results give no reason to lower the acquisition gate for these cases.

## Retry policy and CRC

Return the original raw decision immediately on valid padding and CRC. On failure, try the original energy-normalized decision. If both reject, compare the largest of the 124 slot energies with eight times their median. When it exceeds that threshold, erase that slot and decode once using ordinary energies from the other slots. The rule adds at most one candidate and uses only received IQ. All three paths check the seven zero padding bits and the original CRC-16. A CRC collision remains possible, and the correlated candidates do not justify assuming independent collision probabilities.

An independent known-start test using seed 713 found 19 archived rejects in 128 impulse packets. The new decoder recovered all 19 and returned identical payloads on the 109 archived accepts. In a separate corruption test, the decoder rejected all 256 deliberately CRC-invalid packets carrying a payload bit flip and a strong interferer. This and the paired payload comparisons bound the observed outcomes for these samples. The experiment covers a single slot-aligned tone in simulated IQ. [test_impulse.py](../tests/test_impulse.py)

```powershell
python -m unittest -v test_impulse test_packets
python test_impulse.py --per-case 512 --seed 90517
```
