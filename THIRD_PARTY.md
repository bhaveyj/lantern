# Third-party components

Lantern-authored source is MIT licensed. External software keeps its own license. The personal build uses the following separate components; exact Python versions are recorded in `requirements-lock.txt`.

| Component | License | Source |
| --- | --- | --- |
| Python | Python Software Foundation License | https://www.python.org/ |
| PySide6 / Qt | LGPL v3 / GPL v3 / commercial; dynamically loaded Qt libraries | https://code.qt.io/pyside/pyside-setup.git/ and https://code.qt.io/qt/ |
| aiohttp | Apache 2.0 | https://github.com/aio-libs/aiohttp |
| python-zeroconf | LGPL 2.1+ | https://github.com/python-zeroconf/python-zeroconf |
| PyChromecast | MIT | https://github.com/home-assistant-libs/pychromecast |
| Media3 | Apache 2.0 | https://github.com/androidx/media |
| NanoHTTPD / NanoWSD | BSD 3-Clause | https://github.com/NanoHttpd/nanohttpd |
| FFmpeg | Depends on build; Gyan essentials is GPL v3+; BtbN LGPL builds are available | https://ffmpeg.org/ and https://www.gyan.dev/ffmpeg/builds/ |
| REA (development only) | MIT plus third-party notices | https://github.com/morluto/rea |
| JADX engine (development only) | Apache 2.0 | https://github.com/1013503897/jadx-headless-mcp |

FFmpeg and ffprobe run as separate programs. The current personal build can use the Gyan FFmpeg 9.0.2 essentials executables, whose configuration and license are printed by `ffmpeg -version` and `ffmpeg -L`. Its license file is included beside the executable. Do not assume an FFmpeg binary is LGPL solely because FFmpeg's base library is LGPL. Before public distribution, pin the exact binary source, preserve its notices, and provide the corresponding source and any additional required material.

The Windows packager preserves Python distribution metadata/license directories and Qt license texts. REA, Node, Java, Gradle, and analysis targets are development tools and are not bundled with the application. No Fast TV executable, artwork, decompiled source, or branding is included in Lantern.
