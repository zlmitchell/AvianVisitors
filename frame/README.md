# AvianVisitors e-ink frame

*A day's birds at full strength, yesterday's fading out behind them, framed on the wall by your window.*

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

Each one enables SPI + I2C, installs the deps and a systemd timer, writes `~/.birdframe/config.toml`, and reboots once to bring SPI up. Full options live in [`config.example.toml`](config.example.toml).

### Layout: the panel, or a mat cut for it

The frame draws to the **bare panel** by default: pull the matboard out of the A4 frame and the collage runs to the glass. The A5 window used 47% of the panel; the bare panel uses 94%, and the collage inside it goes from 528px wide to 1141px — **2.16x linear** on the title, the names and the birds.

That is the opening's contribution on its own. What actually reaches the wall also depends on how many birds are on the plate, and the default 48h window puts roughly twice as many there as 24h did — see [the note under Birds going quiet](#birds-going-quiet), which is where the rest of that 2.16x goes.

Two numbers describe the opening, and three more divide it up:

| Key | Bare panel (default) | A5 mat |
|-----|---------------------|--------|
| `opening` | `0.97` | `0.7071` |
| `opening_aspect` | `0.75` (the panel's own 1200x1600) | `0.7071` |
| `title_frac` | `0.10` | `0.065` |
| `collage_frac` | `0.98` | `0.66` |
| `gap_frac` | `0.05` | `0.1` |

`opening` is the opening's height as a fraction of the panel and `opening_aspect` is its width over its height, so the pair describes a bare panel and any mat you cut for one. `title_frac` and `gap_frac` are fractions of the opening's height, `collage_frac` of its width.

**Keeping your mat?** Set all five to the right-hand column. Setting `opening` alone leaves the content the size it always was, just further apart — that is what these five are for.

Check it before hanging anything: `display.py --preview out.png --mat-box` writes an approximate six-ink dither with the opening outlined in red, on any machine, no panel needed.

Any config value can be overridden for one run with `-o key=value`, repeatable, without touching `config.toml` — which is how to try a setting on the actual panel before committing to it:

```bash
# the old A5 mat, just for this render
display.py --config ~/.birdframe/config.toml --force   -o opening=0.7071 -o opening_aspect=0.7071   -o title_frac=0.065 -o collage_frac=0.66 -o gap_frac=0.1
```

An unknown key is an error rather than a silent no-op, so a typo cannot look like "that setting does nothing".

If you enlarge the opening a lot, raise `shoot_collage_vh` with it (74 by default, was 52). The render is a fixed 1200x1600 whatever happens, so that number decides how many source pixels the birds and their names are drawn with before being resampled onto the panel. Much past 76 and the title runs out of viewport.

### Bird names

Every bird wears its common name, written along its own outline — a back, a belly line, the leading edge of a wing — rather than captioned beneath it. Where a bird offers no run worth writing along, the name goes on a line drawn tangent to the silhouette, so no bird is left bare. The name's box goes back into the packer, so the rest of the flock nests around the lettering instead of through it.

Names are **on** by default. Existing frames keep whatever their `~/.birdframe/config.toml` already says, so a frame installed before this change needs one command:

```bash
birdframe-names on
birdframe-names off
```

Either one saves the preference and requests an immediate refresh.

For an `--image-url` frame there is no local render to change, so the frame instead asks its source for what it wants: `labels=1`/`labels=0`, `fresh=<minutes>` and `fade=<start>-<end>` go on the source URL. The source has to honor them, or its image will not change.

### Which bird is singing now

A bird heard in the last `fresh_minutes` (30 by default) is outlined on the panel, traced round its own silhouette in the Spectra red. The outline comes off once that bird has been quiet for the window. Set `fresh_minutes = 0` to turn the mark off.

It reads the `last_seen` and `anchor` the recent API already returns, so there is nothing to install and nothing new to configure — but that also means it needs a mic. BirdWeather reports no per-species last-heard time, so nothing is ever outlined in `--bird-weather` mode.

**Why an outline and not a clock.** This panel has no partial refresh. Pimoroni's `inky_el133uf1` driver sends both half-panel buffers and a full display refresh on every `show()`; a `PTLW` (partial window) command exists in the controller's command set but the library never issues it, and Spectra 6's four-pigment film needs the full waveform regardless. So there is no way to repaint a corner of the panel cheaply, and a printed "3 minutes ago" would mean a twelve-second full redraw every minute, all day.

An outline changes only when a bird crosses in or out of the window, which is the one thing about the passage of time that is worth a redraw. The set of outlined birds is folded into the frame's change signature alongside the species list and the count brackets, so the timer still refreshes only on a real change.

### Birds going quiet

A bird that has not been heard for `fade_hours` (24 by default) starts losing its colour, draining in five steps until the window drops it at `hours` (48). So the plate is a day's listening at full strength with yesterday's birds fading out behind it.

Spectra 6 has six inks and nothing between them, so there is no grey to fade through: the dither renders a faded bird as sparse black stipple on cream. It reads as fading from across a room and as stipple up close. On the website the same ramp is a smooth grey, and hovering a quiet bird brings it back to full strength.

Five steps rather than a gradient, and for the same reason the singing mark is an outline rather than a clock — no partial refresh, so anything continuous would redraw the whole panel to move one bird a shade paler. Each step is folded into the change signature, so the panel redraws when a bird visibly dims and not otherwise. The 15-minute timer caps the day at 96 redraws whatever happens.

`fade_hours = 0` turns fading off. Setting `hours = 24` also turns it off, because there is then no tail to fade over.

**What the second day costs in size.** The plate's tile areas are shares of one budget, so twice the birds means roughly half the area each — and `apt.js`'s own tuning table steps the budget down again past 12 and 24 species. Measured on a plausible day's counts, median tile size on the panel:

| | median tile | vs the old frame |
|---|---|---|
| 10 species / 24h / A5 opening | 96 px | baseline |
| 10 species / 24h / bare panel | 207 px | 2.16x |
| 20 species / 48h / bare panel | 127 px | 1.32x |
| 20 species / 48h / bare panel, bracketed | 115 px | **1.20x** (shipped) |

Still bigger than the old frame, but the second day eats most of what the bare panel bought. If you would rather have the size than the fade, the levers are `hours = 24` (no fade, back to ~2.16x), or a smaller `fade_hours` so the tail is shorter, or capping how many birds reach the plate.

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
