# --- MSDAC Tool: Dockerfile for Render ---
# Uses Debian slim + Python, installs the Linux .deb build of ODA File
# Converter (the Windows .exe from before cannot run on Linux at all).
#
# NOTE ON QT: this ODA build only ships the "xcb" Qt platform plugin.
# QT_QPA_PLATFORM=offscreen is NOT usable here - we use xvfb-run to
# give it a real (virtual) X11 display instead.
#
# CURRENT ISSUE: at runtime we get
#   "Could not load the Qt platform plugin 'xcb' ... even though it
#   was found" + exit 134 (SIGABRT/core dump)
# This specific message means the plugin .so file itself loads, but
# crashes resolving ITS OWN linked libraries - i.e. libqxcb.so has
# unresolved dependencies. This is different from "no display",
# which xvfb-run already solves. Rather than guess another library,
# this build now runs `ldd` on the plugin during the build so the
# build log tells us exactly which .so is missing ("=> not found").
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    xvfb \
    xauth \
    libxcb-util1 \
    libxkbcommon0 \
    libxkbcommon-x11-0 \
    libgl1 \
    libglx-mesa0 \
    fontconfig \
    && rm -rf /var/lib/apt/lists/*

ARG ODA_DEB_URL="https://www.opendesign.com/guestfiles/get?filename=ODAFileConverter_QT6_lnxX64_8.3dll_27.1.deb"
RUN wget -q "$ODA_DEB_URL" -O /tmp/odafc.deb \
    && apt-get update \
    && apt-get install -y --no-install-recommends /tmp/odafc.deb \
    && rm -f /tmp/odafc.deb \
    && rm -rf /var/lib/apt/lists/* \
    && (test -f /usr/lib/x86_64-linux-gnu/libxcb-util.so.0 || \
        ln -s /usr/lib/x86_64-linux-gnu/libxcb-util.so.1 /usr/lib/x86_64-linux-gnu/libxcb-util.so.0)

# Cache-busting: change this value (or Render will pass a fresh one via
# --build-arg) so the diagnostic step below is forced to actually re-run
# instead of reusing a cached (and therefore silent) previous result.
ARG CACHEBUST=20260827

# --- DIAGNOSTIC STEP ---
# Find the xcb plugin file wherever ODA installed it, ldd it, and
# print any "=> not found" lines directly into the build log. Also
# print the full ldd output so we can see it regardless.
# `|| true` so a missing plugin path doesn't fail the whole build -
# we want the log output either way.
RUN echo "cachebust: $CACHEBUST" \
    && echo "=== Locating libqxcb.so ===" \
    && find / -iname "libqxcb.so*" 2>/dev/null | tee /tmp/qxcb_path.txt \
    && echo "=== ldd output on plugin ===" \
    && ldd "$(head -n1 /tmp/qxcb_path.txt)" 2>&1 | tee /tmp/ldd_out.txt \
    && echo "=== MISSING LIBRARIES (if any) ===" \
    && (grep "not found" /tmp/ldd_out.txt || echo "none reported missing") \
    || true

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 10000
CMD xvfb-run -a waitress-serve --listen=0.0.0.0:${PORT:-10000} app:app
