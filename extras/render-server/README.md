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

## Running it on a NAS (the reason there is an image)

A renderer on a desktop is a frame that goes stale whenever that desktop sleeps.
This went three days once - the container had been killed when Docker Desktop
shut down, the Pi's fetches failed, `display.py` correctly kept the last picture,
and the wall carried on looking like a working frame. Put it somewhere always
on.

Published to `ghcr.io/<owner>/avian-render-server` by
`.github/workflows/render-server-image.yml`, and self-contained: it carries
`frame/` and the label font, so it needs no checkout at runtime.

On Unraid, add a container with:

| | |
|---|---|
| Repository | `ghcr.io/zlmitchell/avian-render-server:latest` |
| Port | `8080` → whatever host port you like |
| Path | `/config` → a folder holding the frame's `config.toml` (read-only) |
| Path | `/out` → any writable folder |
| Variable | `STATION_URL` = `http://192.168.1.x` — the station's **IP** |
| Extra Parameters | `--user pwuser --security-opt seccomp=unconfined` |

**Use the IP, not `birdnet.local`,** unless you run with host networking. Give
the Pi a DHCP reservation so the address does not move.

`.local` is mDNS. The image carries `libnss-mdns` with `mdns4_minimal` ahead of
`dns` in `/etc/nsswitch.conf`, so the name can resolve - but the module does not
send the multicast itself. Inspecting it shows it references `/run/avahi-daemon`
and contains neither `224.0.0.251` nor port `5353`: it asks a running
avahi-daemon to do the query. That is the useful part, because it means the
HOST's avahi can do the multicast on the container's behalf, and a bridged
container never needs multicast of its own.

So, to use `birdnet.local` in bridge mode, add one more volume:

| Path | `/run/avahi-daemon` → `/var/run/avahi-daemon` (read-only) |

Unraid runs avahi-daemon, so the socket is there. `--network host` also works and
needs no socket, but then the port mapping no longer applies and the server sits
on 8080 of the host.

The IP remains the answer that needs nothing explained to it.

Worth knowing why this is easy to miss: Docker Desktop forwards a container's
DNS to the host resolver, and a Mac or Windows host already speaks mDNS - so
`.local` resolves there and the problem is invisible. On a Linux NAS the query
goes to a unicast nameserver that has never heard of `.local` and the render
fails with "Name or service not known".

Those last two are not optional. Chromium refuses to run as root without
`--no-sandbox`, and that flag would have to live in `shoot.py` and follow the
frame everywhere - so the container runs unprivileged instead. Its sandbox then
needs syscalls Docker's default seccomp profile blocks, and dies with SIGTRAP
without the second flag. Relaxing seccomp keeps the sandbox rather than
switching it off.

Then point the Pi at it:

```toml
image_url = "http://<nas>:8080/frame.png"
```

If the package is private, the NAS needs a pull credential - a GitHub PAT with
`read:packages` - or make the package public in the repository's package
settings.

## What happens when this host is off

`display.py`'s fetch fails, it logs and keeps the last panel image. The frame
goes stale rather than blank, which is the right failure for a picture on a
wall. It catches up on the next tick after this comes back.

## Forcing a render

`GET /frame.png?force=1` draws the plate before serving it, instead of handing
over whatever the poll loop last happened to make. It blocks for the length of a
render, which is the point.

This is what the frame should use:

```toml
image_url = "http://<nas>:8080/frame.png?force=1"
```

The two halves poll independently otherwise, and the Pi can push a plate drawn
before the bird that triggered the push - right the next cycle, wrong for
fifteen minutes. It costs nothing on an idle tick, because `display.py` only
fetches the image on a run where its own change gate has already decided to
push. The frame's `timeout` (180s) bounds the wait.

A forced render that fails - station unreachable, or a plate that does not pass
the ink check - leaves the previous picture in place and serves that. A frame
showing the last good plate is a better answer to a failed refresh than an error
the Pi would treat as a broken fetch and skip anyway.

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
