# --- MSDAC Tool: Dockerfile for Render ---
# Uses Debian slim + Python, installs the Linux .deb build of ODA File
# Converter (the Windows .exe from before cannot run on Linux at all).
#
# NOTE ON QT: this ODA build only ships the "xcb" Qt platform plugin
# (confirmed via its own error output: "Available platform plugins
# are: xcb"). QT_QPA_PLATFORM=offscreen is NOT usable here - we must
# give it a real (virtual) X11 display via xvfb-run instead.

FROM python:3.11-slim

# --- System dependencies ---
# xvfb provides the virtual display xcb needs. xauth is required by
# xvfb-run itself (missing it causes "xvfb-run: error: xauth command
# not found"). libxkbcommon0/-x11-0 and libxcb-util1 are Qt/X11
# runtime libraries ODA's binary links against directly.
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

# --- ODA File Converter (Linux .deb, Qt6 build) ---
# NOTE: ODA's download links are versioned and occasionally rotate.
# Check https://www.opendesign.com/guestfiles/oda_file_converter for
# the current filename if this build step fails, and update the URL
# below to match.
ARG ODA_DEB_URL="https://www.opendesign.com/guestfiles/get?filename=ODAFileConverter_QT6_lnxX64_8.3dll_27.1.deb"
RUN wget -q "$ODA_DEB_URL" -O /tmp/odafc.deb \
    && apt-get update \
    && apt-get install -y --no-install-recommends /tmp/odafc.deb \
    && rm -f /tmp/odafc.deb \
    && rm -rf /var/lib/apt/lists/* \
    # some Ubuntu/Debian builds ship libxcb-util.so.1 but ODA's binary
    # expects libxcb-util.so.0 - symlink it if missing
    && (test -f /usr/lib/x86_64-linux-gnu/libxcb-util.so.0 || \
        ln -s /usr/lib/x86_64-linux-gnu/libxcb-util.so.1 /usr/lib/x86_64-linux-gnu/libxcb-util.so.0)

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 10000

# Shell form (not exec-array form) is required so $PORT expands -
# Render assigns this dynamically at runtime and it won't always be
# 10000. xvfb-run wraps the whole app so ODAFileConverter's xcb calls
# have a virtual display to attach to, even though nothing is shown.
CMD xvfb-run -a waitress-serve --listen=0.0.0.0:${PORT:-10000} app:app
