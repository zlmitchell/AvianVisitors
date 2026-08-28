# The frame's browser, somewhere that can afford one.
#
# Playwright's own image rather than a Debian base: it ships chromium and the
# library dependencies already matched to the playwright version, which is the
# fiddly part and the part that breaks when the two drift.
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

# The server is NOT copied in. It lives in the repository, which is mounted, so
# there is one copy of it and editing it does not need an image rebuild - which
# is exactly the trap this fell into once already: --force-recreate restarts a
# container without rebuilding, so a fixed script kept failing with the old
# error and looked like the fix had not worked.
ENV FRAME_DIR=/repo/frame OUT_DIR=/out PORT=8080
EXPOSE 8080
CMD ["python3", "/repo/extras/render-server/render_server.py"]
