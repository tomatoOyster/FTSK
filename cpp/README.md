# FTSK-4 v1 C++17 reference CLI

The encoder and decoder use only the C++17 standard library. One input frame contains 4,480 samples at 128 ksample/s. Each sample occupies eight bytes, a little-endian IEEE binary32 I value followed by Q. The aligned decoder expects the header at sample zero and carrier frequency already corrected. It checks the known header, calculates 31 separate complex tone projections per data slot, scores all 512 trajectories and checks padding and CRC-16/CCITT-FALSE. A failed first decode triggers one pass using power normalized per slot. Unknown constant carrier phase is supported.

## Blind stream mode

`ftsk blind STREAM.cf32 [read_chunk_samples]` searches an arbitrary-length raw file from the first sample to EOF. The optional read size is 1–65536 samples (default 4096). Each accepted frame prints one JSON line with `start` (zero-based input samples), `cfo_hz` (positive for a higher received carrier), and `payload_hex`. A partial frame at EOF emits nothing; a partial CF32 sample is an error. Frames require no trailing samples. This implementation retains less than one incomplete frame plus one input block, independent of file size. Compile with the adjacent `blind.hpp` in place; no extra linker arguments or dependencies.

The acquisition path uses a differential lag-one header gate, a 1024-point radix-2 FFT over the dehopped header to select a carrier offset in ±6 kHz, local coherent timing refinement and a two-half phase-slope frequency refinement. It then uses the same slot projection, padding and CRC checks as aligned mode. Chunk sizes of at least 256 samples are recommended for performance. Python's exhaustive receiver does not have the differential prescreen and can recover packets the native screen rejects near threshold. This implementation does not compensate sustained carrier drift, sample clock skew or overlapping packets.

```powershell
.\cpp\ftsk.exe blind example.cf32
python -B cpp/test_blind.py cpp/ftsk.exe
```

On Windows with MSYS2 GCC 15.2.0 (`-O2`), native and Python receivers each recovered 36/36 packets at −3 dB, 36/36 at −6 dB and 7/36 at −8 dB (per-sample SNR, paired seeded IQ, includes ±6000 Hz and half-grid offsets). These small trials do not establish identical sensitivity. A four-second pure-noise file decoded with zero payloads in 0.127 wall-clock seconds (31.4× real time, default read size, includes process startup and file I/O). Repeated adjacent payloads after a CRC-invalid frame decoded once each at exact sample timestamps across read sizes 23, 257, 4096 and 65536. No physical device was used.

## Build and run

Run from the project root. Windows with MinGW GCC in PowerShell:

```powershell
g++ -std=c++17 -O2 -Wall -Wextra -Wpedantic cpp/ftsk.cpp -o cpp/ftsk.exe
.\cpp\ftsk.exe encode example.bin cpp/encoded.cf32
.\cpp\ftsk.exe decode cpp/encoded.cf32 cpp/decoded.bin
fc.exe /b example.bin cpp/decoded.bin
python -B cpp/test_interop.py cpp/ftsk.exe
```

For a Visual Studio Developer Command Prompt use:

```bat
cl /nologo /EHsc /std:c++17 /O2 /W4 cpp\ftsk.cpp /Focpp\ftsk.obj /Fecpp\ftsk.exe
```

Linux or macOS shell with a C++17 compiler:

```sh
c++ -std=c++17 -O2 -Wall -Wextra -Wpedantic cpp/ftsk.cpp -o cpp/ftsk
./cpp/ftsk encode example.bin cpp/encoded.cf32
./cpp/ftsk decode cpp/encoded.cf32 cpp/decoded.bin
cmp example.bin cpp/decoded.bin
python3 -B cpp/test_interop.py cpp/ftsk
```

`encode` reads exactly 32 payload bytes and writes 35,840 IQ bytes. `decode` reads exactly one frame and writes 32 bytes after validation. A successful operation returns 0. Input or I/O failure returns 1 with a diagnostic on stderr. Invalid command syntax returns 2. Use a new output path. Both encode and decode refuse any existing destination, including hard-link aliases; no `--force` mode is offered. They stage the bytes in a freshly reserved sibling directory, check write/flush/close, then publish the complete result with a no-replace hard link. Filesystems without hard-link support cannot publish (the command fails rather than falling back to destructive replacement). Staging cleanup is best effort after a crash or external interference. C++17 streams do not provide a portable power-loss durability guarantee.

## Verification record

Built and executed on Windows with MSYS2 UCRT64 GCC 15.2.0 under the flags above. Python 3.12 and NumPy are needed only for the interoperability suite. GCC emitted no warnings. Linux, macOS and MSVC commands are provided for reproduction. Those environments have not been exercised here.

All four interop test groups passed. Output safety tests verified refusal of existing destinations, same-file aliases, failure to stage under missing or non-directory parents, and eight competing writers with exactly one successful publisher and no residual staging directories. Disk-full conditions were not physically tested. The first uses the fixed published 31-symbol sequence for payload bytes `00` through `1f` in both directions. The second sends 34 payloads including all-zero, all-one and deterministic random data. Every native frame agreed with the Python waveform within absolute complex tolerance `2e-6` and decoded in the Python streaming receiver. The native decoder recovered the Python frames with added complex Gaussian noise and a phase offset. The third verifies diagnostics and preserves existing output for eight malformed inputs covering payload and IQ lengths, corrupt CRC, nonzero padding, missing header and non-finite samples.
