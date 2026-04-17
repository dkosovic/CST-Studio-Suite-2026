# CST Studio Suite 2026 - HPC Job Submission Frontend Modifications for 2FA

This project provides modified versions of the Python and Qt-based frontend components used by CST Studio Suite 2026
to submit simulation jobs to an HPC cluster over SSH, adding support for TOTP and Okta-based two‑factor authentication.

The repository includes the following modified files:
- [`hpc_job_submission_gui.py`](https://raw.githubusercontent.com/dkosovic/CST-Studio-Suite-2026/refs/heads/main/hpc_job_submission_gui.py)
- [`hpc_submit.py`](https://raw.githubusercontent.com/dkosovic/CST-Studio-Suite-2026/refs/heads/main/hpc_submit.py)

These files replace the originals located in:
- `C:\Program Files\CST Studio Suite 2026\Library\Python\lib\emag_job_submission_gui\`

Modifications include:
- Okta 2FA push notification support.
- TOTP 2FA support including for DUO.
- Improved submission error handling.
- Added walltime option (`Auto` or `HH:MM`).
- Added memory option (`Auto` or explicit values like `16GB`, `64GB`, etc.).
- Enhanced GPU selection logic with remote queue limit awareness.
- Integration with remote queue limits:
  - Automatically query and set the maximum GPU count allowed for the selected queue.
  - Query and prefill available walltime when supported by the scheduler.

 ## Configuration Notes
- Memory option accepts Auto or numeric values with optional units (M/MB, G/GB, T/TB). Values are converted to MB before submission.
- Walltime option accepts `Auto` or `HH:MM`.
- Remote queue limit queries refine available GPU and walltime options when the scheduler provides this information.
