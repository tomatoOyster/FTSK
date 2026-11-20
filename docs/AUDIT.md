# FTSK release-readiness check: malformed inputs and output preservation

Scope: Windows, Python 3.12, NumPy 2.3.5, current working tree at the time of review. Tests used temporary files and synthetic IQ; no radio, actual full disk or power-loss tests. Reproductions describe the observed pre-fix snapshot, not a promise that a later checkout still exhibits them.

## 1. Release blocker: two encoder commands overwrite the input via a hard link (verified)

Both `packet encode` and `profile encode` compare resolved path strings and then open the output for truncation. Two different names for the same existing file pass the check. Both commands exited 0; in both cases the original 32-byte source became 35,840 bytes. Reproduce safely in a scratch directory from the project root:

```python
import os, subprocess, sys, tempfile
from pathlib import Path
with tempfile.TemporaryDirectory() as tmp:
    for command in ('packet', 'profile'):
        a, b = (Path(tmp)/(command+s) for s in ('-in','-out'))
        a.write_bytes(bytes(32)); os.link(a,b)
        r = subprocess.run([sys.executable,'ftsk.py',command,'encode',str(a),str(b)])
        print(command,r.returncode,a.stat().st_size)
```

Recommendation: check `samefile` when destination exists, refuse existing destinations by default, and publish from a temporary sibling through the existing atomic writer. If explicit replacement is supported, use an explicit `--force`; never open the destination for writing while validating it. Regression criteria: preserve input/output for hard links, existing unrelated outputs, and simulated fsync failures. Same-filesystem hard links were tested; a symlink or mount alias was not.

## 2. Non-finite IQ can hide an intact subsequent frame (verified)

With 100 zero samples followed by a valid all-zero-payload profile-4 frame, the baseline receiver returned 1 packet. Changing **only sample zero of the prefix**, outside the packet, to NaN or +Inf resulted in 0 packets. Inf also produced NumPy runtime warnings. In an independent CLI test, a CF32 file with one NaN at the start of the header exited 0 with empty stdout and no diagnostic. A finite malformed header can legitimately decode to no frames; a non-finite CF32 value is distinguishable from RF noise and should be surfaced.

```python
import numpy as np
from frame import encode_frame
from receiver import Receiver
s = np.r_[np.zeros(100), encode_frame(bytes(32))]
for v in (0, float('nan'), float('inf')):
    s[0] = v; print(v, len(Receiver().feed(s)))  # 1, 0, 0
```

**Fixed and regression-tested:** `Receiver`, `FastReceiver`, `DualReceiver` and `WideReceiver` now reject a chunk containing a non-finite sample before accepting any part of it. Diagnostics use a zero-based absolute sample index (hardware-rate input index for wide reception). A caller can resubmit the corrected chunk without a gap or duplicate. Verified NaN, +Inf and imaginary −Inf, corrected retransmission under each baseband receiver, rejection spanning multiple wide-receiver internal blocks, and nonzero CLI exit with `non-finite IQ at sample 4097`. Reproduce with `python -B -m unittest -v test_audit_io test_packets` (9 tests passed).

## 3. Link-test generator leaves a plausible but incomplete final file (verified fault injection)

With `link generate --count 2`, a fault on the second iteration returned 1 but left a valid-length, 35,840-byte first frame at the requested output path. The file carries no intended frame count and prevents a normal retry at the same path (`'xb'`). Reproduction: use `unittest.mock.patch('linktest.encode_frame', side_effect=[encode_frame(bytes(32)), OSError(28, 'injected interruption')])` around `linktest.main(['generate','--count','2','--iq',tmp_output])`. This injected an exception during generation; real ENOSPC at the OS write boundary was not tested. Recommendation: stage and atomically publish only on successful completion.

## 4. Capture metadata type errors escape normal CLI diagnostics (verified)

For an existing CF32 input, set its `.json` sidecar to valid JSON `[]` and run `python ftsk.py capture replay FILE --rate 1024000`. Observed a Python traceback ending `AttributeError: 'list' object has no attribute 'get'`. Require a JSON object, typed positive integer rate and sample count and a valid digest when present. Return a single actionable diagnostic describing the sidecar. The capture operation also publishes the IQ file before its sidecar. A sidecar write failure can therefore leave a standalone IQ file; this latter observation is **inferred from ordering**, not fault injected. Preserve recoverable IQ, but report the path and recovery instructions distinctly.

## Follow-up verification of findings 1, 3 and 4

`python -m unittest -v test_safeoutput test_audit_io` passed all seven tests. Both encoders reject a source alias even with `--force`; staging rejects accidental replacement and preserves existing data on injected fsync failure. The numbered-probe generator no longer leaves a plausible truncated final file after a simulated second-frame error. Malformed non-object metadata, a boolean sample rate, and a negative sample count now produce diagnostics rather than tracebacks. IQ and its metadata sidecar are still two files; a failed metadata publish explicitly reports where the saved IQ is and what sample rate to use for recovery.

## Positive checks

- All-zero (32-byte) payload decoded correctly under exclusive profile 4 and profile 8 and under automatic reception of each. Each exclusive receiver ignored the other profile. These cover meaningful zero bytes in payload, not zero-size files.
- For `transfer.atomic_writer(existing, force=True)`, fault injection of `OSError(errno.ENOSPC, ...)` at `transfer.os.fsync` preserved the original bytes (`b'old'`) and left no `.ftsk-*` staging file. This verifies failure handling for that particular call; power-failure durability and system-full behavior during transfer were not measured.
- Physical RF, production OS permissions on removable storage, and cross-OS behavior remain outside this audit.
