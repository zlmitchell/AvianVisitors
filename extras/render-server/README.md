# Render the frame's plate somewhere that can afford a browser

## Why

A Pi 3 A+ running a BirdNET station cannot also run headless chromium. Measured
on one, uncontended, with every optimisation this repository has:

| | on the Pi | off-box |
|---|---|---|
| playwright driver start | 75.8s | — |
| chromium launch | 106.5s | — |
| whole render | **636s, failed** | **27s** |
| `Page.goto` | timed out at 180s | fine |

The page itself is not the problem: the same Pi serves it over curl in under a
second. Getting a browser to exist is the problem, and no amount of trimming the
collage fixes that.

So this takes the browser off the Pi and leaves everything else where it was.

## What stays on the Pi

The renderer produces the **screenshot**, not the panel image. Matting,
dithering and the panel push stay on the Pi, where they measured 0.55s of a 9s
render and need no browser. The Pi also keeps its own change gate, so it still
decides when the birds have changed enough to be worth twelve seconds of e-ink.

Measured after the switch, with the analyser running throughout:

    render + fetch + dither + push    80s   (was 636s and failed)
    unchanged birds, gate skips       33s

and `birdnet_analysis` no longer has to be stopped for any of it.

## Running it

```sh
scp <pi>:~/.birdframe/config.toml ./config/config.toml
STATION_URL=http://birdnet.local docker compose up -d
```

Then on the Pi, in `~/.birdframe/config.toml`:

```toml
shoot     = false
image_url = "http://<this-host>:8080/frame.png"
```

The frame's config is mounted because `label_scale` is applied at capture time -
a renderer that did not read it would size the lettering for a different mat.
`STATION_URL` overrides `base_url`, which on the Pi's own config says
`localhost` and means something else from inside a container elsewhere.

## What happens when this host is off

`display.py`'s fetch fails, it logs and keeps the last panel image. The frame
goes stale rather than blank, which is the right failure for a picture on a
wall. It catches up on the next tick after this comes back.

## Cadence

The server re-renders when the species signature changes, not on a timer: the
plate is a pure function of the birds, so an unchanged signature means an
identical PNG the Pi's own gate would ignore anyway. `INTERVAL_SECONDS` (default
300) is how often it *looks*. Keep it at or below the Pi's 15-minute timer, or
the frame can only ever push a picture that is a cycle stale.

## Not constrained, on purpose

`extras/perf` exists to reproduce the Pi's 415MB ceiling. This exists to escape
it. Putting a memory cap on this container would recreate the problem it was
built to solve.
