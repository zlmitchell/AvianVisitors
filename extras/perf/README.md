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
docker compose exec -u bird pi perf-install        # once, ~25 min
docker compose restart pi                          # the reboot the installer wanted
docker compose exec -u bird pi perf-seed           # a reproducible day of birds
docker compose exec -u bird pi perf-render base -o base_url=http://localhost
python report.py out/base.jsonl --monitor out/monitor.jsonl
```

`perf-install` ends at a stubbed `reboot`, so the units it enabled are not
running yet — restart the container once and systemd starts the lot.

Stop the frame's own timer before measuring (`systemctl stop birdframe.timer`),
or it will be mid-render holding the lock when you try to take a reading.

To test a change: edit the host checkout, `perf-sync`, render again. Re-running
`perf-install` for a one-line edit would take half an hour.

`perf-render` writes `out/<name>.jsonl` and `out/<name>.png`, which land in the
host checkout through the bind mount. `out/` is gitignored.

## What it found

A ten-bird plate, measured under the cap with the station running. Each row adds
to the one above it.

| | render slice | decoded bitmap | page | picture |
|---|---|---|---|---|
| as it was | 239.3 MB | 17.4 MB | 8.78 MB | — |
| + cutouts at drawn size | 233.8 MB | 1.9 MB | 4.03 MB | same |
| + `frame=1` render mode | — | 1.9 MB | 3.32 MB | same |
| + chromium launch flags | **199.0 MB** | 1.9 MB | 3.32 MB | byte-identical |

The render's own cgroup slice is the number that decides whether this fits on
the Pi, which has 415MB and a station already holding ~142MB of it. 40MB came
off it for no change to the plate at all — `ImageChops.difference` returns a
bbox of `None` between the first and last renders.

Two findings the lab could not have produced on its own, and one it could not
produce at all:

- **The illustrations were the largest single line item**, not any of the code
  paths. Sources carried 12.8x the pixels that were painted, worst case 67x.
  That was fifth on the list of suspects before anything was measured.
- **`shoot()` was waiting for the wrong thing** — it took the birdless branch on
  every instrumented run, and captured a collage that happened to arrive during
  a 250ms sleep.
- **The 30s poll cannot be measured here.** A render finishes in about eight
  seconds, so the poll never fires and re-renders read 1.0 either way. It only
  bites on a box slow enough to have the problem, which is the Pi. This is the
  clearest case of the fidelity gap below: absence of evidence here is not
  evidence of absence there.

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
| `seed-db.sh` | → `perf-seed`: a fixed day of detections, so two runs are comparable |
| `sync.sh` | → `perf-sync`: push the host checkout's `frame/` and `avian/frontend/` into the installed clone |
| `monitor.py` | samples the container's memory and CPU from *outside* the ceiling, via the Docker socket, so watching does not spend the budget being watched |
| `report.py` | turns a metrics log into a phase table, a memory verdict and an asset census |

`frame/metrics.py` is the other half: it records the phases and samples the
cgroup from inside during a render, including the PSI pressure files that say
whether the box was computing or only moving pages about.
