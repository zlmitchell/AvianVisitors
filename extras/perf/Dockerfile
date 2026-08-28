# A Pi 3 A+ that fits on a laptop.
#
# The frame's render stopped fitting on the real Pi once the station moved onto
# the same box, and iterating there costs seven minutes an attempt and takes a
# live station down with it. This is somewhere to iterate instead.
#
# systemd is PID 1 rather than the app, because the thing being reproduced is
# two systemd services competing for one memory ceiling. scripts/install_birdnet.sh
# calls systemctl about twenty times unguarded, and the smoke tests already stub
# systemctl for the cases that only need to observe the call - but a stub cannot
# start the analyser, and the analyser is the other half of the experiment.
#
# What this does NOT reproduce, and it matters when reading a number off it:
# aarch64, the A53's very weak cores (BogoMIPS 38.4), and swap that lands on an
# SD card. Absolute timings here are not comparable to the Pi. The memory
# figures and the RANKING of phases are, and those are what the lab is for.
# Every candidate fix gets confirmed on the real Pi before it counts.
FROM debian:trixie

ENV DEBIAN_FRONTEND=noninteractive \
    container=docker \
    LANG=C.UTF-8

RUN apt-get update && apt-get install -y --no-install-recommends \
        systemd systemd-sysv dbus dbus-user-session \
        sudo git ca-certificates curl jq procps psmisc \
        python3 python3-venv python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Units that either cannot work in a container or would fight the host for
# hardware. Masking beats deleting: an installer that asks for one of these
# gets a clean no rather than a missing-unit error.
RUN systemctl mask --  \
        systemd-udevd.service systemd-udevd-kernel.socket systemd-udevd-control.socket \
        systemd-modules-load.service sys-kernel-debug.mount sys-kernel-tracing.mount \
        getty.target console-getty.service \
    && rm -f /lib/systemd/system/multi-user.target.wants/systemd-update-utmp-runlevel.service

# The two commands the installers call that have no meaning here. Stubs that log
# their argv, in /usr/local/bin so they win on PATH - the same trick
# tests/smoke_reinstall_services.sh and friends use for systemctl and apt.
#
# reboot:      both newinstaller.sh and frame/install.sh end in `sudo reboot`.
# raspi-config: frame/install.sh:99 enables SPI and I2C for the Inky panel.
#              There is no panel here and --preview never touches one.
RUN set -eux; \
    for cmd in reboot raspi-config; do \
      printf '#!/bin/sh\nprintf "%%s %%s\n" "%s" "$*" >> /var/log/perf-stubs.log\nexit 0\n' "$cmd" \
        > "/usr/local/bin/$cmd"; \
      chmod 0755 "/usr/local/bin/$cmd"; \
    done; \
    : > /var/log/perf-stubs.log; chmod 0666 /var/log/perf-stubs.log

# scripts/install_birdnet.sh refuses to run as root and requires passwordless
# sudo, so it needs a real unprivileged user - not a convenience, a constraint.
ARG PI_USER=bird
RUN useradd -m -s /bin/bash "$PI_USER" \
    && printf '%s ALL=(ALL) NOPASSWD:ALL\n' "$PI_USER" > /etc/sudoers.d/"$PI_USER" \
    && chmod 0440 /etc/sudoers.d/"$PI_USER"

COPY install-inside.sh /usr/local/bin/perf-install
COPY render.sh /usr/local/bin/perf-render
RUN chmod 0755 /usr/local/bin/perf-install /usr/local/bin/perf-render

STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
