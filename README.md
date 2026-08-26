# AvianVisitors

*A live bird collage from your window.*

See it running at [bird.onethreenine.net](https://bird.onethreenine.net).

<img alt="avianvisitors collage" src="docs/thumb.png" />

---

## BOM

| Qty | Description | Price | Link | Notes |
|-----|-------------|-------|------| ----- |
| 1 | Raspberry Pi (4B / 5 / 3A+ / Zero 2W) | ~$25-80 | [Amazon](https://amzn.to/43yLDZJ) | [See note for 512 MB Pis](https://github.com/mcguirepr89/BirdNET-Pi/wiki/RPi0W2-Installation-Guide) |
| 1 | Micro SD Card (≥32 GB) | ~$10 | [Amazon](https://amzn.to/4eGy7te) | |
| 1 | USB lavalier microphone | $16.95 | [Amazon](https://amzn.to/4vLSaMK) | |
| 1 | Pi power supply | ~$10 | - | |

Optional: a [Gemini API key](https://aistudio.google.com/apikey) to restyle illustrations, an [eBird API key](https://ebird.org/api/keygen) to filter species by region.

### Kits

I offer the bird mic and the wall frame as separate electronics kits. I put up a store for some of my open-source projects and will soon be able to offer kits cheaper than buying all the components individually, once I start buying in bulk.

- [Bird mic kit](https://theodore.net/store/avian-mic/)
- [Frame kit](https://theodore.net/store/avian-visitors/)

---

## 1. Flash the SD card

Use [Raspberry Pi Imager](https://www.raspberrypi.com/software/). Pick Raspberry Pi OS Lite (64-bit). In the customisation dialog set:

- Username
- WiFi SSID + password
- Hostname: `birdnet`
- Enable SSH with password auth

Plug the USB mic into the Pi. Place the capsule in a window or mount it outside. Boot.

---

## 2. Run the installer

Installer assumes passwordless sudo (Raspberry Pi OS Lite default - if you've tightened it, run `sudo raspi-config` -> *System Options* -> restore the default first).

```bash
ssh <your-username>@birdnet.local
curl -s https://raw.githubusercontent.com/Twarner491/AvianVisitors/avian-visitors/newinstaller.sh | bash
```

Clones this fork, installs BirdNET-Pi, symlinks the AvianVisitors overlay into the Caddy web root. Takes 20-40 minutes. Reboots when done.

Collage: `http://birdnet.local/`. Stock BirdNET-Pi UI: `http://birdnet.local/index.php`. The menu button in the top right opens an admin overlay with settings, system, log, and tool panels.

Stock BirdNET-Pi pages still render, but privileged legacy controls are not enabled. Use the Avian Visitors menu for the station controls it exposes, and SSH for remaining maintenance.

Optional Google Drive backups are set up under **Tools → Your data → Drive archive**. Local cleanup stays unavailable until an archive run has been verified.

### Updating an existing station

For the first v1 update, keep the existing checkout and run:

```bash
upgrade=$(mktemp "$HOME/avian-v1-upgrade.XXXXXX")
curl -fsSL https://raw.githubusercontent.com/Twarner491/AvianVisitors/avian-visitors/scripts/bootstrap_v1.sh -o "$upgrade"
sudo bash "$upgrade"
rm -f "$upgrade"
```

After v1, use **Tools → Pull latest** or run:

```bash
cd ~/BirdNET-Pi
./scripts/update_birdnet.sh
```

The updater keeps generated mask data and stops if tracked files have local edits. If its service setup needs repair, use **Tools → Reinstall services** or run:

```bash
cd ~/BirdNET-Pi
./scripts/reinstall_services.sh
```

---

## 3. (Optional) Restyle the illustrations

The repo ships with 666 bundled illustrations (333 species, perched + flight). To restyle them or generate a set for your own region:

```bash
pip install -r ~/BirdNET-Pi/avian/scripts/requirements.txt
export GEMINI_API_KEY='your-key'  # image generation requires billing enabled

# generate on a cream ground, cut the ground off, rebuild the collage masks
python3 ~/BirdNET-Pi/avian/scripts/pregen.py --labels ~/BirdNET-Pi/model/labels.txt --force
python3 ~/BirdNET-Pi/avian/scripts/cutout.py
python3 ~/BirdNET-Pi/avian/scripts/build_masks.py
```

On a Pi with 4 GB of RAM or less, add `--model u2net` to the `cutout.py` command; the default model may be [OOM-killed](https://github.com/Twarner491/AvianVisitors/issues/17).

Filter to your region with `--ebird-region US-CA` (needs `EBIRD_API_KEY`). The full pipeline, prompt, reference images, and per-species tuning live in [`avian/scripts/README.md`](avian/scripts/README.md). Style lives in [`prompt.template.md`](avian/scripts/prompt.template.md).

See [illustration bundles](illustration-bundles.md) for pregenerated bundles shared by other folks in the community, or share your own for others to use!

---

## 4. (Optional) Forward off your LAN

See [`avian/forwarding/`](avian/forwarding/) for three independent recipes:

- **Cloudflare Tunnel** for a public HTTPS URL.
- **Home Assistant REST sensor** that exposes the latest detection.
- **MQTT bridge** that publishes every new detection.

---

## Repo layout

```
avian/                  # everything we add to BirdNET-Pi
├── frontend/           # static HTML/JS/CSS for the collage
├── assets/             # 666 bundled illustrations + photo-cutout fallbacks
├── api/                # PHP shims served by BirdNET-Pi's PHP-FPM
├── scripts/            # generate -> cutout -> masks pipeline + prompt
└── forwarding/         # optional HA / MQTT / Cloudflare configs
frame/                  # optional e-ink wall display
```

Everything outside `avian/` and `frame/` is upstream BirdNET-Pi.

---

## The collage

Every bird carries its common name, written along a run of its own outline rather than captioned beneath it; a bird heard in the last half hour is outlined in red; and a bird that has gone quiet drains its colour over its second day until the window drops it. Names are a per-device toggle under **Settings**; the two time marks are always on, and the frame sets its own windows for them in config.

## Wall frame

An optional e-ink frame mirrors the collage onto a panel by your window. Build it from [`frame/`](frame/README.md). It can run off your own BirdNET mic, or standalone from BirdWeather data for any ZIP code with no mic at all.

It draws into the A4 frame's A5 mat opening, as the kit ships. Take the matboard out and seven config lines let the collage run to the glass instead — 47% of the panel to 94%, and a second day of birds fading out behind the first. [`frame/README.md`](frame/README.md#layout-the-mat-or-the-bare-panel) has them.

---

## License

CC-BY-NC-SA-4.0, inherited from [BirdNET-Pi](https://github.com/Nachtzuster/BirdNET-Pi/blob/main/LICENSE). Non-commercial use only. See the [BirdNET-Pi README](https://github.com/Nachtzuster/BirdNET-Pi/blob/main/README.md) for full Cornell attribution.

---

- [Fork this repository](https://github.com/Twarner491/AvianVisitors/fork)
- [Watch this repo](https://github.com/Twarner491/AvianVisitors/subscription)
- [Create issue](https://github.com/Twarner491/AvianVisitors/issues/new)
