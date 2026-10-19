# Fast TV and REA investigation

The implementation follows the workflow described on [myfastv.com](https://myfastv.com/) and its [receiver setup](https://myfastv.com/guides/stream-mac-videos-to-google-tv/), [Google Cast](https://myfastv.com/guides/cast-mac-video-to-chromecast/), and [audio preparation](https://myfastv.com/guides/mkv-no-sound-google-tv/) guides. The website offers a Mac sender and a Google/Android TV receiver. The receiver provides more on-TV controls than the standard Cast route.

REA 6.7.0 was installed locally and run through its CLI with Node 24.22.0, a full JDK 22, and the checksum-verified JADX headless MCP 0.7.1 engine. REA's Android readiness check passed. Native binary engines and Codex MCP registration are not required for these CLI Android operations.

The APK linked from the vendor's homepage during this implementation was Fast TV receiver 1.3.0, version code 16, package `app.fasttv.receiver`. Its SHA-256 was `fcd5aa84b0fd580d982c4f53ba608ad3670ab0e3591d2238a86432dbbe7cb997`. This is newer than the version mentioned in some website guides. REA observed minimum Android API 24, target API 36, a landscape Leanback activity, cleartext traffic enabled, and a network service discovery manager field. Its class inventory included the activity and a TV time-bar control; most application method names were obfuscated.

Evidence was collected with `inspect-android-package`, `search-android-classes`, and `inspect-android-class`. Original REA evidence and download provenance are retained locally in the ignored `artifacts/research` directory. `scripts/research_fasttv.py` reproduces the package and class search inspection against the current official download. The APK was inspected statically, not executed. REA does not validate its APK signature or establish runtime behavior from these results.

These observations support the Android TV platform baseline and local media architecture. They do not establish the private Fast TV wire protocol. Lantern uses its own documented protocol and independently written sender and receiver, as chosen for this project. No Fast TV decompiled implementation or assets are shipped.
