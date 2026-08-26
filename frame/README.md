# AvianVisitors e-ink frame

*A day's birds, framed on the wall by your window.*

A [Pimoroni Inky Impression 13.3"](https://amzn.to/4xlAWr3) (Spectra 6) mirroring the live collage. A Pi screenshots the site, mats it onto the panel, and pushes it, refreshing only when the birds change. Every bird carries its name written along its own outline; whichever birds are singing right now wear a stroke round their silhouette; and the ones that have gone quiet fade out over their second day. Build one of your own at [theodore.net/projects/AvianVisitors#frame-ous](https://theodore.net/projects/AvianVisitors/#frame-ous).

![](https://theodore.net/assets/images/AvianVisitors/final.jpg)

---

### BOM

| Qty | Description | Price | Link |
|-----|-------------|-------|------|
| 1 | Raspberry Pi 3 A+ or Zero 2 W | ~$25-35 | [Amazon](https://amzn.to/49Xp58I) |
| 1 | 13.3" E Ink Display     | $299.99 | [Amazon](https://amzn.to/4xlAWr3) |
| 1 | A4 Wood Photo Frame    | $21.99 | [Amazon](https://amzn.to/3RWFbJE) |
| 1 | Long, Flat Micro USB Cable    | $7.99 | [Amazon](https://a.co/d/0a59rKSk) |
| 1 | Flat USB Brick    | $7.59 | [Amazon](https://amzn.to/3S4CtSs) |
| | **Total** | **~$365** | | |

The 3 A+ and Zero 2 W are both tested and set up identically; any Pi with the 40-pin header that runs 64-bit Raspberry Pi OS works. The printed backing pressure-fits either board.

CAD + 3d print files can be found in [`hardware/`](hardware/).

### Kits

I offer the frame and the bird mic as separate electronics kits. I put up a store for some of my open-source projects and will soon be able to offer kits cheaper than buying all the components individually, once I start buying in bulk.

- [Frame kit](https://theodore.net/store/avian-visitors/)
- [Bird mic kit](https://theodore.net/store/avian-mic/)

---

## 1. Flash the SD card

Flash an sd card with Raspberry Pi OS Lite (64-bit) via [Raspberry Pi Imager](https://www.raspberrypi.com/software/). In the customisation dialog set:

- Username
- WiFi SSID + password
- Hostname: `birdpic`
- Enable SSH with password auth

Then install in Pi and power up.

## 2. Run the installer

```bash
ssh <your-username>@birdpic.local
sudo apt update && sudo apt install -y git
git clone https://github.com/Twarner491/AvianVisitors
cd AvianVisitors/frame
```

Pick how the frame gets its birds:

```bash
# Pair with your bird mic on the same network (birdnet.local). The default.
./install.sh

# No microphone: draw the collage from BirdWeather for any ZIP code.
./install.sh --bird-weather --zip 94107

# Bird mic hosted at a public URL: point the frame straight at it.
./install.sh --image-url https://bird.onethreenine.net/frame.png?k=YOUR_FRAME_KEY
```

Each one enables SPI + I2C, installs the deps and a systemd timer, and writes `~/.birdframe/config.toml`. Full options live in [`config.example.toml`](config.example.toml).

On a **first** install SPI is not up yet, so it reboots and the timer draws the first frame about two minutes later. On a **re-run or upgrade** SPI is already up, so it restarts the units and forces one render immediately — without that the panel would sit on the old picture, because an upgrade changes how the frame renders but not what the birds are doing, and the change-gate would correctly decide there was nothing to redraw.

### Layout: the mat, or the bare panel

A default install draws into the **A5 window of the A4 frame from the BOM** — what an unmodified kit has in front of the panel. That window covers 47% of the glass, and it is the default because anything larger prints under the matboard.

**Take the matboard out** and the collage runs to the glass: 94% of the panel instead of 47%. Five keys go together, and they are already in `~/.birdframe/config.toml` commented out:

| Key | A5 mat (default) | Bare panel |
|-----|------------------|------------|
| `opening` | `0.7071` | `0.97` |
| `opening_aspect` | `0.7071` | `0.75` (the panel's own 1200x1600) |
| `collage_frac` | `0.92` | `0.98` |
| `shoot_collage_vh` | `52` | `74` |
| `hours` | `24` | `48` |

**The text does not change size — the birds do.** `title_frac` and `gap_frac` are fractions of the *panel*, not of the opening, so one value holds the title at 74px and the space under it at 113px whatever is cut in front of the glass. The bird names hold still too: they are sized from their tile inside the browser and the whole collage is then scaled onto the panel, so a larger opening would have quietly enlarged every name along with the birds. The frame cancels that scale-up out — `label_scale`, derived from the opening, or set it to a positive number to override. All of the extra room therefore lands on the drawings: **3.1x the collage area**.

`opening` is the opening's height as a fraction of the panel and `opening_aspect` is its width over its height, so the pair describes the bare panel and any mat you cut for one. `collage_frac` is the share of the opening's width the collage fills. `shoot_collage_vh` decides how many source pixels the birds are drawn with before being resampled onto the panel — 52 suits the small opening, 74 the large one, and much past 76 the title runs out of viewport.

**Move all five or none.** And `hours = 48` is only affordable *because* the opening got bigger: see [the size note under Birds going quiet](#birds-going-quiet).

One trade worth knowing: holding the names still means the browser rasterises them at about half the pixel size it used to, because there is much less downscale afterwards to sharpen them. On the bare panel they are a shade less crisp than the same physical size in the A5 mat. Raising `dsf` would fix it, at a memory cost a Zero 2 W does not have.

Check it before you cut anything: `display.py --preview out.png --mat-box` writes an approximate six-ink dither with the opening outlined in red, on any machine, no panel needed.

Any config value can be overridden for one run with `-o key=value`, repeatable, without touching `config.toml` — which is how to try the bare panel on the real thing before committing to it:

```bash
display.py --config ~/.birdframe/config.toml --force -o opening=0.97 -o opening_aspect=0.75 -o collage_frac=0.98 -o shoot_collage_vh=74 -o hours=48
```

An unknown key is an error rather than a silent no-op, so a typo cannot look like "that setting does nothing".

### Bird names

Every bird wears its common name, written along its own outline — a back, a belly line, the leading edge of a wing — rather than captioned beneath it. Where a bird offers no run worth writing along, the name goes on a line drawn tangent to the silhouette, so no bird is left bare. The name's box goes back into the packer, so the rest of the flock nests around the lettering instead of through it.

Names are **on** by default. Existing frames keep whatever their `~/.birdframe/config.toml` already says, so a frame installed before this change needs one command:

```bash
# in a shell on the frame Pi - these are commands, not config lines
birdframe-names on
birdframe-names off
```

Either one saves the preference and requests an immediate refresh.

Those are **commands you run in a shell on the Pi**, not lines for `config.toml`. The setting they write is `bird_names = true`; putting `birdframe-names = on` in the config file makes it invalid TOML and the frame refuses to start (it will say so, and name the line).

For an `--image-url` frame there is no local render to change, so the frame instead asks its source for what it wants: `labels=1`/`labels=0`, `fresh=<minutes>` and `fade=<start>-<end>` go on the source URL. The source has to honor them, or its image will not change.

### Which bird is singing now

A bird heard in the last `fresh_minutes` (30 by default) is outlined on the panel, traced round its own silhouette in the Spectra red. The outline comes off once that bird has been quiet for the window. Set `fresh_minutes = 0` to turn the mark off.

It reads the `last_seen` and `anchor` the recent API already returns, so there is nothing to install and nothing new to configure — but that also means it needs a mic. BirdWeather reports no per-species last-heard time, so nothing is ever outlined in `--bird-weather` mode.

**Why an outline and not a clock.** This panel has no partial refresh. Pimoroni's `inky_el133uf1` driver sends both half-panel buffers and a full display refresh on every `show()`; a `PTLW` (partial window) command exists in the controller's command set but the library never issues it, and Spectra 6's four-pigment film needs the full waveform regardless. So there is no way to repaint a corner of the panel cheaply, and a printed "3 minutes ago" would mean a twelve-second full redraw every minute, all day.

An outline changes only when a bird crosses in or out of the window, which is the one thing about the passage of time that is worth a redraw. The set of outlined birds is folded into the frame's change signature alongside the species list and the count brackets, so the timer still refreshes only on a real change.

### Birds going quiet

A bird that has not been heard for `fade_hours` (24) starts losing its colour, draining in five steps until the window drops it at `hours`. So the plate becomes a day's listening at full strength with yesterday's birds fading out behind it.

**This is off on a default install**, because `hours` is also 24 — there is no tail past the fade point, so nothing dims. Widening the window is the single thing that switches it on; `fade_hours` is already sitting there waiting for a tail to run down.

Spectra 6 has six inks and nothing between them, so there is no grey to fade through: the dither renders a faded bird as sparse black stipple on cream. It reads as fading from across a room and as stipple up close. On the website the same ramp is a smooth grey, and hovering a quiet bird brings it back to full strength.

Five steps rather than a gradient, and for the same reason the singing mark is an outline rather than a clock — no partial refresh, so anything continuous would redraw the whole panel to move one bird a shade paler. Each step is folded into the change signature, so the panel redraws when a bird visibly dims and not otherwise. The 15-minute timer caps the day at 96 redraws whatever happens.

`fade_hours = 0` turns fading off. Setting `hours = 24` also turns it off, because there is then no tail to fade over.

**Why the window is tied to the opening.** Tile areas are shares of one budget, so twice the birds is roughly half the area each — and `apt.js`'s tuning table steps the budget down again past 12 and 24 species. Measured on a plausible day's counts, median tile on the panel:

| | median tile | vs a default install |
|---|---|---|
| 10 species / 24h / A5 mat | 96 px | **baseline (default)** |
| 20 species / 48h / A5 mat | 54 px | 0.56x — birds half size, most on the minimum-tile floor |
| 10 species / 24h / bare panel | 211 px | 2.20x |
| 20 species / 48h / bare panel | 117 px | 1.22x |

Which is why `hours = 48` sits in the bare-panel block and not on its own: widening the window inside the A5 mat makes every bird smaller than it is today. On the bare panel the second day still costs most of the 2.20x, but it costs it from a much larger starting point.

If you want the fade *and* the size, the remaining lever is fewer birds on the plate — cap the species, or shorten `fade_hours` so the tail is shorter.

### Why the birds stop jumping

Two things used to re-shuffle the whole plate on every refresh, which mattered much more once the outline and the fade started earning refreshes far more often than a new species did:

- **Tile sizes came from raw call counts.** Areas are shares of one budget, so a tile's size depends on every *other* bird's count too — one new detection anywhere resized everything and repacked the plate. Counts are now snapped to the same eight brackets the change signature already uses, so a count change too small to earn a refresh is also too small to move anything.
- **Flight-or-perched was `Math.random()`,** cached in a variable that died with the page. The frame launches a fresh browser per render, so roughly one bird in seven changed silhouette each time — and a changed silhouette repacks the plate. The pose now comes from a hash of the species slug, so a bird is always drawn the same way, on any machine, with nothing stored.

The layout is now a pure function of (species set, count bracket, slug). Identical inputs give a byte-identical plate, so a refresh driven by a bird starting to sing or stepping down the fade redraws the same picture with only that mark changed — nothing moves. Birds still rearrange when a species arrives or leaves, or when a count crosses a bracket, which is when the plate genuinely is a different plate.

A note on `tests/test_frame_layout.py`: the brackets and the fade-step count live in both `apt.js` and `display.py` and have to agree, or the panel refreshes for changes the renderer does not make. There are tests that read the constants straight out of `apt.js` and compare, so the two halves cannot drift apart silently.

### BirdWeather mode, and birds the bundle cannot draw

BirdWeather mode renders on the Pi from this repo's illustrations on GitHub, so there is no image set to copy over. ZIP codes with no station nearby fall back to the closest ones. If you are far from any BirdWeather station, add `--ebird-key <key>` (a free key from [ebird.org/api/keygen](https://ebird.org/api/keygen)) and the frame fills from eBird sightings instead.

The bundled illustrations center on the western U.S. If birds near your ZIP aren't in the set you cloned, the installer flags them and the frame skips them until they exist. To generate them, run [`generate_illustrations.py`](generate_illustrations.py) on a laptop or workstation (it uses the same rembg cutout as the rest of the pipeline, which the Pi can't fit in memory), passing your ZIP and a paid Google Gemini key, then commit the new cutouts or copy them to the Pi:

```bash
python3 generate_illustrations.py --zip 10001 --gemini-key YOUR_GEMINI_KEY
```

It generates only the species you're missing; `--country` and `--sample` carry through for non-US postcodes or a wider region.
