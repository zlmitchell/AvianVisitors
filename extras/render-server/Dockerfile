# The frame's browser, somewhere that can afford one.
#
# Playwright's own image rather than a Debian base: it ships chromium and the
# library dependencies already matched to the playwright version, which is the
# fiddly part and the part that breaks when the two drift.
#
# Built from the REPOSITORY ROOT, not this directory, because the image carries
# frame/ and the collage's label font. That makes it self-contained enough to
# pull onto a NAS and run with no checkout - which is the point: a renderer that
# lives on the machine that happens to be someone's desktop is a frame that goes
# stale every time that desktop sleeps.
FROM mcr.microsoft.com/playwright/python:v1.56.0-noble

# --break-system-packages because noble marks its python externally managed
# (PEP 668), and without it pip declines quietly enough that the first sign of
# trouble is ModuleNotFoundError at render time, two layers down. playwright is
# pinned to the image tag so the client matches the browsers already baked into
# /ms-playwright and nothing is downloaded at build. Pillow is for display.py,
# which this imports for its config, signature and species helpers - the same
# code the Pi runs, so the two cannot disagree about what counts as a change or
# how large the lettering should be.
RUN pip install --no-cache-dir --break-system-packages playwright==1.56.0 "Pillow>=10,<12"

# mDNS, so a station called birdnet.local can be reached by that name.
#
# Not academic: Docker Desktop forwards a container's DNS to the host resolver,
# which on a Mac or Windows box already speaks mDNS - so `.local` resolves there
# and the problem is invisible. On a Linux NAS it does not, the query goes to a
# unicast nameserver that has never heard of `.local`, and the renderer fails
# with "Name or service not known".
#
# libnss-mdns puts mdns4_minimal into the hosts line ahead of dns, with
# [NOTFOUND=return] so a real DNS lookup is not delayed by it. It still needs
# multicast to reach the network, which a bridged container does not get - so
# this only helps with --network host, and the IP remains the reliable answer.
RUN apt-get update  && apt-get install -y --no-install-recommends libnss-mdns  && rm -rf /var/lib/apt/lists/*  && sed -i 's/^hosts:.*/hosts: files mdns4_minimal [NOTFOUND=return] dns/' /etc/nsswitch.conf

# Put the headless shell on PATH under both of its names. This image keeps its
# browsers in /ms-playwright; frame/cdp.py looks in ~/.cache/ms-playwright and
# then falls back to `which`. A symlink is all it needs, and shoot.py then runs
# here whether it is driving playwright or the CDP client.
RUN exe=$(find /ms-playwright -type f -perm -u+x -name 'chrome-headless-shell' -o \
              -type f -perm -u+x -name 'headless_shell' | head -1) && \
    test -n "$exe" && \
    ln -sf "$exe" /usr/local/bin/chrome-headless-shell && \
    ln -sf "$exe" /usr/local/bin/headless_shell

# frame/ is the renderer and the shared code; the font is what the bird names
# are set in, and shoot.py fails the render outright without it. The rest of
# avian/ is excluded by .dockerignore - the illustrations are half a gigabyte
# and are fetched from the station at render time, not read from here.
WORKDIR /app
COPY frame/ /app/frame/
COPY avian/frontend/fonts/ /app/avian/frontend/fonts/
COPY extras/render-server/render_server.py /app/extras/render-server/render_server.py

# A bind mount over /app shadows all of this, which is how development works:
# same paths, live edits, no rebuild.
ENV FRAME_DIR=/app/frame OUT_DIR=/out PORT=8080 \
    FRAME_CONFIG=/config/config.toml
EXPOSE 8080
CMD ["python3", "/app/extras/render-server/render_server.py"]
