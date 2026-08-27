# --- MSDAC Tool: Dockerfile for Render ---
# Uses Debian slim + Python, installs the Linux .deb build of ODA File
# Converter (the Windows .exe from before cannot run on Linux at all).
#
# NOTE ON QT: this ODA build only ships the "xcb" Qt platform plugin.
# QT_QPA_PLATFORM=offscreen is NOT usable here - we use xvfb-run to
# give it a real (virtual) X11 display instead.
#
# PAST ISSUE (resolved): at runtime we previously got
#   "Could not load the Qt platform plugin 'xcb' ... even though it
#   was found" + exit 134 (SIGABRT/core dump)
# Diagnosed via `ldd` against libqxcb.so directly in the container:
# the plugin file itself loaded fine, but 6 of its own linked
# libraries were missing (see list below). Now installed explicitly.
FROM python:3.11-slim

# Confirmed via ldd against libqxcb.so in a prior build (see diagnostic
# step below): the xcb plugin was missing libxcb-icccm4, libxcb-image0,
# libxcb-keysyms1, libxcb-render0, libxcb-render-util0, and libxcb-shape0.
# These are added explicitly here - Debian's --no-install-recommends
# does not pull them in automatically even though Qt6's xcb plugin
# links against them directly.
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    xvfb \
    xauth \
    libxcb-util1 \
    libxcb-icccm4 \
    libxcb-image0 \
    libxcb-keysyms1 \
    libxcb-render0 \
    libxcb-render-util0 \
    libxcb-shape0 \
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

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 10000
CMD xvfb-run -a waitress-serve --listen=0.0.0.0:${PORT:-10000} app:app
