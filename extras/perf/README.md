# A Pi 3 A+ that fits on a laptop

The frame's render stopped fitting once the station moved onto the same Pi. It
now completes only because a systemd drop-in stops `birdnet_analysis` for the
duration — about 53s of deafness on a no-change tick and 400–440s on a real
render. That is a workaround. This is somewhere to find the actual fix without
spending seven minutes an attempt on a live station.

## The footprint being reproduced

Measured on the real frame at `10.0.0.147`:

| | |
|---|---|
| RAM | 415MB (`MemTotal` 425,172 kB — 512MB less a 64MB GPU split) |
| CPU | 4× Cortex-A53 @1.4GHz, BogoMIPS 38.40 |
| swap | 207MB zram at priority 100, over a 2GB file on the SD card |
| OS | Debian 13 trixie, cgroup v2 |

`docker-compose.yml` sets `mem_limit: 415m`, `memswap_limit: 2515m` and
`cpus: 1.0` to match.

## What this does *not* reproduce

Read this before quoting a number off it.

- **Architecture.** The Pi is aarch64; this runs on whatever the host is.
- **Core strength.** Four very weak A53s is roughly one modern core's
  throughput, which is where `cpus: 1.0` comes from. It is the crudest part of
  the model and the first thing to distrust.
- **Swap latency.** The Pi swaps to zram and then to an SD card. This swaps to
  whatever the Docker VM uses, which is far faster.

So **absolute timings here are not comparable to the Pi.** What *is* comparable
is the memory figures and the ranking of phases — which is what the lab is for.
Every candidate fix gets confirmed on the real Pi before it counts.

Also: `free` inside the container shows the host's memory, because
`/proc/meminfo` is not namespaced. Read the cgroup files, or the monitor.

## Use

```sh
docker compose build
docker compose up -d
docker compose exec -u bird pi perf-install        # once, slow
docker compose exec -u bird pi perf-render base    # an instrumented render
python report.py out/base.jsonl
```

`perf-render` writes `out/<name>.jsonl` and `out/<name>.png`, which land in the
host checkout through the bind mount. `out/` is gitignored.

## Design notes

**One constrained service, not two.** The station and the frame are separate
systemd services, so modelling them as separate compose services is the obvious
move and it is wrong: docker limits are per-container, and what makes this a
problem is that both halves draw on *one* 415MB pool. Two containers with 200MB
each would be a different machine with a different failure.

**systemd is PID 1.** `scripts/install_birdnet.sh` calls `systemctl` about twenty
times unguarded, and the existing smoke tests stub it for the cases that only
need to observe the call — but a stub cannot *start* the analyser, and the
analyser is the other half of the experiment.

**A private cgroup namespace, not the host's.** With `cgroup: host` the
container reads the VM's memory instead of its own, which would quietly make
`frame/metrics.py` sample the wrong thing and report a render that never came
near its ceiling.

**`newinstaller.sh` is bypassed.** It clones from GitHub rather than the
checkout under test, refuses to run as root, and ends in `sudo reboot`.
`install-inside.sh` drives `scripts/install_birdnet.sh` directly against a clone
of `/source`, which is how the existing `tests/smoke_*.sh` already expect a
container to be arranged.

**A clone, not a bind mount, at `$HOME/BirdNET-Pi`.** The installer hardcodes
that path and runs `git log` there, so it must be a real repository — and it
writes a venv, a `requirements_custom.txt` and an install log, which have no
business landing in the host checkout. `/source` is mounted read-only so they
cannot.

**Two stubs only**, both following the smoke tests' existing pattern of shadowing
a command in `/usr/local/bin` and logging its argv to `/var/log/perf-stubs.log`:
`reboot` (both installers end in one) and `raspi-config` (`frame/install.sh:99`
enables SPI and I2C for a panel that is not here — `display.py --preview` never
touches one).

## The pieces

| file | what it is |
|---|---|
| `Dockerfile` | Debian trixie, systemd as PID 1, the stubs, an unprivileged `bird` user with passwordless sudo |
| `docker-compose.yml` | the caps, the mounts, and the monitor sidecar |
| `install-inside.sh` | → `perf-install`: clone `/source`, run both real installers |
| `render.sh` | → `perf-render`: one instrumented render to a preview file |
| `monitor.py` | samples the container's memory and CPU from *outside* the ceiling, via the Docker socket, so watching does not spend the budget being watched |
| `report.py` | turns a metrics log into a phase table, a memory verdict and an asset census |

`frame/metrics.py` is the other half: it records the phases and samples the
cgroup from inside during a render, including the PSI pressure files that say
whether the box was computing or only moving pages about.
