---
name: parallel-numeric-work-can-hard-reboot-the-box
description: Windows hard-rebooted (Kernel-Power 41, ~25 display-driver timeouts in three minutes) while a JIT-parallel numerical job on all cores ran beside four Claude agents and two Codex calls. The mechanism is not proven; CPU starvation of the display driver is the hypothesis that fits the log. Cap numeric threads and run at most two heavy external jobs at once on a shared desktop.
metadata:
  type: feedback
---

The source program's always-on desktop rebooted without warning at the end of a night of parallel work. The system log
showed the GPU display driver "stopped responding and recovered" about 25 times in three minutes, the desktop
compositor crashed, then the kernel logged an unclean shutdown. What was running: a numba `prange` sweep using every
core, four Claude Code agents, two Codex CLI reviews, and thirteen data collectors. The mechanism cannot be proven
from the log: CPU starvation of the display driver is the hypothesis consistent with the driver timeouts, and memory
pressure before the reboot cannot be excluded (free RAM afterwards says nothing about before). The collectors
restarted from a scheduled task; the agents, a detached data pull,
and the hourly loop had to be re-established by hand, and the solver's partial outputs had to be re-verified.

**Why:** a desktop is not a server. The display driver, the compositor, and the scheduler share the cores with your
job, and a driver that misses its watchdog deadline takes the box down with it. If that is the mechanism, no memory
guard catches it; the harness's low-memory guard (another lesson) watches RAM, not CPU starvation. The advice below
is cheap either way.

**How to apply:**
- Pin thread counts on every numeric job: `NUMBA_NUM_THREADS=4 OMP_NUM_THREADS=4` (or cores minus two). Put it in the
  job's launch command, not in a shell you will forget to use.
- At most two heavy external processes at once (an agent fleet, a Codex panel, a solver): queue the rest.
- Anything that must survive a reboot runs detached, resumable, with a heartbeat and a scheduled-task restart
  (`harness-low-memory-guard-kills-tracked-jobs` has the pattern). Partial outputs get re-verified, never trusted.
- After an unexplained reboot, read the system log for driver timeouts before blaming memory or the code.
