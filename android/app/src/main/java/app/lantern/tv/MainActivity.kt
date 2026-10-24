package app.lantern.tv

import android.app.Activity
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.TextUtils
import android.util.TypedValue
import android.view.Gravity
import android.view.KeyEvent
import android.view.View
import android.view.WindowManager
import android.view.accessibility.CaptioningManager
import android.widget.*
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.MimeTypes
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.TrackSelectionOverride
import androidx.media3.common.Tracks
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.RenderersFactory
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.exoplayer.text.TextRenderer
import androidx.media3.ui.AspectRatioFrameLayout
import androidx.media3.ui.PlayerView
import androidx.media3.ui.SubtitleView
import org.json.JSONArray
import org.json.JSONObject
import java.net.Inet4Address
import java.net.NetworkInterface

@androidx.media3.common.util.UnstableApi
class MainActivity : Activity() {
    companion object {
        // Keep the PIN's lifetime and failed-attempt limits across screen recreation.
        // No Activity or other Android context is retained by the gate.
        private val sessionGate = PairingGate()
    }

    private val handler = Handler(Looper.getMainLooper())
    private val gate = sessionGate
    private lateinit var server: ControlServer
    private lateinit var player: ExoPlayer
    private lateinit var playerView: PlayerView
    private lateinit var root: FrameLayout
    private lateinit var welcome: LinearLayout
    private lateinit var pinView: TextView
    private lateinit var connectionView: TextView
    private lateinit var welcomeBrowse: Button
    private lateinit var controls: LinearLayout
    private lateinit var timeline: SeekBar
    private lateinit var times: TextView
    private lateinit var titleView: TextView
    private lateinit var playButton: Button
    private var drawer: View? = null
    private var items = listOf<JSONObject>()
    private var current: JSONObject? = null
    private val subtitleSelection = SubtitleSelection()
    private var selectedSubtitle: String?
        get() = subtitleSelection.id
        set(value) { subtitleSelection.select(value) }
    private var changingSeek = false
    private var nsd: NsdManager? = null
    private var registration: NsdManager.RegistrationListener? = null
    private var multicast: WifiManager.MulticastLock? = null
    private var connected = false
    private var destroyed = false
    private var captionFontScale = 1f
    private val bg = TvTheme.background
    private val ink = TvTheme.text
    private val accent = TvTheme.blue

    private val ticker = object : Runnable {
        override fun run() {
            if (gate.refresh()) pinView.text = gate.pin.chunked(3).joinToString("  ")
            updateState()
            handler.postDelayed(this, 1000)
        }
    }
    private val hideControls = Runnable { if (player.isPlaying && drawer == null) setControlsVisible(false) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        gate.refresh()
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        window.decorView.systemUiVisibility = View.SYSTEM_UI_FLAG_FULLSCREEN or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
        // Media3 1.6.1's extracted cue samples can lose an in-progress cue
        // after a seek. Its supported legacy path retains the whole subtitle.
        val defaults = DefaultRenderersFactory(this)
        val renderers = RenderersFactory { outputHandler, video, audio, text, metadata ->
            defaults.createRenderers(outputHandler, video, audio, text, metadata).also { built ->
                built.filterIsInstance<TextRenderer>().forEach { it.experimentalSetLegacyDecodingEnabled(true) }
            }
        }
        player = ExoPlayer.Builder(this, renderers)
            .setMediaSourceFactory(DefaultMediaSourceFactory(this).experimentalParseSubtitlesDuringExtraction(false))
            .build()
        createUi()
        player.addListener(object : Player.Listener {
            override fun onIsPlayingChanged(isPlaying: Boolean) { updateState() }
            override fun onPlaybackStateChanged(state: Int) {
                updateState()
                if (state == Player.STATE_ENDED) requestAdjacent(1)
            }
            override fun onPlayerError(error: PlaybackException) {
                showControls()
                val explanation = when (error.errorCode) {
                    in 2000..2999 -> "The TV could not read the stream from your PC. Keep Lantern open and check the home network connection."
                    PlaybackException.ERROR_CODE_DECODING_FAILED -> "The TV's decoder failed. Close other TV video apps or restart the TV, then try again."
                    in 3000..4999 -> "The TV could not decode this file. Check that its container, video and audio are supported."
                    else -> "This file could not play. Try playing it again."
                }
                titleView.text = explanation
                server.publish(JSONObject().put("type", "error").put("id", current?.optString("id")).put("code", error.errorCodeName).put("message", "TV playback failed: ${error.errorCodeName}. $explanation"))
            }
            override fun onTracksChanged(tracks: Tracks) { applySubtitle() }
        })
        server = ControlServer(gate, { message, isCurrent -> handler.post {
            if (!destroyed && isCurrent()) {
                try { handle(message) }
                catch (_: Exception) { server.publish(JSONObject().put("type", "error").put("code", "COMMAND_FAILED").put("message", "The TV could not apply this playback command. Try playing the file again.")) }
            }
        } }, { name -> handler.post {
            if (destroyed) return@post
            connected = name != null
            welcomeBrowse.visibility = if (connected) View.VISIBLE else View.GONE
            if (connected) welcomeBrowse.requestFocus()
            connectionView.text = if (name == null) "Open Lantern on Windows to connect." else "Connected to $name · choose a file on your PC"
            if (name == null) {
                player.stop()
                current = null
                items = emptyList()
                welcome.visibility = View.VISIBLE
                setControlsVisible(false)
                drawer?.let { root.removeView(it) }; drawer = null
            }
        } })
        try {
            // Sender pings every 10 seconds; NanoHTTPD's default 5-second read timeout is too short.
            server.start(30000, false)
            advertise()
        } catch (error: Exception) {
            connectionView.text = "Could not start the receiver: ${error.message}"
        }
        handler.post(ticker)
    }

    private fun text(value: String, size: Float, color: Int = ink): TextView = TextView(this).apply {
        text = value; textSize = size; setTextColor(color); gravity = Gravity.CENTER
        includeFontPadding = false
        setPadding(dp(8), dp(6), dp(8), dp(6))
    }
    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()
    private fun shape(color: Int, stroke: Int = TvTheme.border, radius: Int = 14, width: Int = 1) = GradientDrawable().apply {
        setColor(color)
        cornerRadius = dp(radius).toFloat()
        setStroke(dp(width), stroke)
    }
    private fun button(value: String, primary: Boolean = false, clicked: () -> Unit): Button = Button(this).apply {
        text = value; textSize = 14f; isAllCaps = false; setTextColor(ink)
        typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
        maxLines = 1; ellipsize = TextUtils.TruncateAt.END
        includeFontPadding = false
        setPadding(dp(14), dp(8), dp(14), dp(8))
        backgroundTintList = null
        stateListAnimator = null
        minimumWidth = dp(68); minimumHeight = dp(48)
        val focusStyle: (Boolean) -> Unit = { focused ->
            background = shape(
                if (primary) { if (focused) TvTheme.blueHover else accent } else { if (focused) TvTheme.blueSurface else TvTheme.raised },
                if (focused) { if (primary) Color.rgb(189, 215, 255) else accent } else { if (primary) accent else TvTheme.border },
                8, if (focused) 2 else 1
            )
            scaleX = if (focused) 1.04f else 1f
            scaleY = if (focused) 1.04f else 1f
        }
        setOnClickListener { clicked(); scheduleHide() }
        setOnFocusChangeListener { _, focused -> focusStyle(focused) }
        focusStyle(false)
    }

    private fun createUi() {
        root = FrameLayout(this).apply { setBackgroundColor(bg) }
        setContentView(root)
        playerView = PlayerView(this).apply { player = this@MainActivity.player; useController = false }
        val captions = getSystemService(CAPTIONING_SERVICE) as? CaptioningManager
        captionFontScale = if (captions?.isEnabled == true) captions.fontScale else 1f
        playerView.addOnLayoutChangeListener { _, _, _, _, _, _, _, _, _ -> if (::controls.isInitialized) updateCaptionInset() }
        root.addView(playerView, FrameLayout.LayoutParams(-1, -1))
        welcome = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(40), dp(24), dp(40), dp(20)); setBackgroundColor(bg) }
        val brand = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        brand.addView(ImageView(this).apply { setImageResource(app.lantern.tv.R.drawable.lantern_icon); contentDescription = "Lantern" }, LinearLayout.LayoutParams(dp(38), dp(38)))
        brand.addView(text("lantern", 25f).apply { typeface = Typeface.DEFAULT_BOLD; gravity = Gravity.START }, LinearLayout.LayoutParams(0, -2, 1f))
        brand.addView(text("TV", 11f, accent).apply { typeface = Typeface.DEFAULT_BOLD; setPadding(dp(14), dp(7), dp(14), dp(7)); background = shape(TvTheme.blueSurface, radius = 7) })
        welcome.addView(brand, LinearLayout.LayoutParams(-1, dp(44)))
        val pairing = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER }
        val compact = resources.configuration.screenHeightDp < 600
        pairing.addView(text("Your library. Bigger screen.", if (compact) 28f else 32f).apply { typeface = Typeface.DEFAULT_BOLD })
        pairing.addView(text("Enter the code below in Lantern on your Windows PC.", 15f, TvTheme.muted))
        val codeCard = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER; setPadding(dp(22), dp(16), dp(22), dp(16)); background = shape(TvTheme.surface, radius = 18) }
        codeCard.addView(text("PAIR YOUR TV", 11f, accent).apply { typeface = Typeface.DEFAULT_BOLD; letterSpacing = 0.12f })
        pinView = text(gate.pin.chunked(3).joinToString("  "), if (compact) 60f else 68f, accent).apply { typeface = Typeface.MONOSPACE; setPadding(0, dp(6), 0, dp(6)) }
        codeCard.addView(pinView)
        connectionView = text("Ready to connect to your laptop.", 14f, TvTheme.muted)
        codeCard.addView(connectionView)
        welcomeBrowse = button("Browse your playlist", primary = true) { showPlaylist() }.apply { visibility = View.GONE; textSize = 16f }
        codeCard.addView(welcomeBrowse, LinearLayout.LayoutParams(dp(260), dp(48)).apply { topMargin = dp(10) })
        pairing.addView(codeCard, LinearLayout.LayoutParams(dp(520), -2).apply { topMargin = dp(16) })
        welcome.addView(pairing, LinearLayout.LayoutParams(-1, 0, 1f))
        welcome.addView(text("Same home network  ·  Connect by IP: ${ipAddress()}:8765", 13f, TvTheme.muted))
        root.addView(welcome, FrameLayout.LayoutParams(-1, -1))

        controls = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(20), dp(12), dp(20), dp(16)); background = shape(Color.argb(242, 14, 18, 26), radius = 16); visibility = View.GONE }
        controls.addView(text("PLAYBACK", 11f, accent).apply { gravity = Gravity.START; typeface = Typeface.DEFAULT_BOLD })
        titleView = text("Ready", 20f).apply { gravity = Gravity.START; maxLines = 2; ellipsize = TextUtils.TruncateAt.END }
        controls.addView(titleView)
        val timelineRow = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        timeline = SeekBar(this).apply {
            max = 1000
            progressTintList = android.content.res.ColorStateList.valueOf(accent)
            thumbTintList = android.content.res.ColorStateList.valueOf(accent)
            progressBackgroundTintList = android.content.res.ColorStateList.valueOf(TvTheme.border)
            keyProgressIncrement = 10
            setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
                override fun onStartTrackingTouch(seekBar: SeekBar) { changingSeek = true }
                override fun onStopTrackingTouch(seekBar: SeekBar) { player.seekTo(player.duration.coerceAtLeast(0) * seekBar.progress / 1000); changingSeek = false; scheduleHide() }
                override fun onProgressChanged(seekBar: SeekBar, value: Int, fromUser: Boolean) { if (fromUser && !changingSeek) player.seekTo(player.duration.coerceAtLeast(0) * value / 1000) }
            })
        }
        timelineRow.addView(timeline, LinearLayout.LayoutParams(0, dp(40), 1f))
        times = text("0:00 / 0:00", 13f, TvTheme.muted)
        timelineRow.addView(times)
        controls.addView(timelineRow)
        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER }
        row.addView(button("Previous") { requestAdjacent(-1) })
        row.addView(button("−10s") { player.seekTo((player.currentPosition - 10000).coerceAtLeast(0)) })
        playButton = button("Pause", primary = true) { if (player.isPlaying) player.pause() else resumePlayback() }
        row.addView(playButton)
        row.addView(button("+10s") { player.seekTo(player.currentPosition + 10000) })
        row.addView(button("Next") { requestAdjacent(1) })
        row.addView(button("Playlist") { showPlaylist() })
        row.addView(button("CC") { showSubtitles() })
        row.addView(button("Fit / Fill") { playerView.resizeMode = if (playerView.resizeMode == AspectRatioFrameLayout.RESIZE_MODE_FIT) AspectRatioFrameLayout.RESIZE_MODE_ZOOM else AspectRatioFrameLayout.RESIZE_MODE_FIT })
        for (index in 0 until row.childCount) {
            row.getChildAt(index).layoutParams = LinearLayout.LayoutParams(-2, dp(48)).apply { marginStart = dp(4); marginEnd = dp(4) }
        }
        controls.addView(row, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(8) })
        root.addView(controls, FrameLayout.LayoutParams(-1, -2, Gravity.BOTTOM).apply { setMargins(dp(20), 0, dp(20), dp(20)) })
        controls.addOnLayoutChangeListener { _, _, _, _, _, _, _, _, _ -> updateCaptionInset() }
    }

    private fun handle(message: JSONObject) {
        when (message.optString("type")) {
            "playlist.snapshot" -> {
                items = message.optJSONArray("items").objects()
                val previous = current
                val updated = items.firstOrNull { it.optString("id") == previous?.optString("id") }
                if (previous != null && updated != null) {
                    val tracksChanged = previous.optJSONArray("subtitles")?.toString() != updated.optJSONArray("subtitles")?.toString()
                    current = JSONObject(updated.toString()).put("url", previous.getString("url"))
                    subtitleSelection.accept(updated.subtitleId(), updated.optLong("subtitleRevision", 0))
                    if (tracksChanged) {
                        // Media3 treats replaceMediaItem as a metadata update for the
                        // same URI, so rebuild the source to include new subtitle files.
                        val position = player.currentPosition.coerceAtLeast(0)
                        val autoplay = player.playWhenReady
                        player.setMediaItem(mediaItem(current!!), position)
                        player.playWhenReady = autoplay
                        player.prepare()
                    }
                    applySubtitle()
                }
                if (current == null) connectionView.text = "${items.size} files ready · start playback from Windows"
                if (drawer != null) showPlaylist()
            }
            "playback.load" -> load(message.getJSONObject("item"))
            "playback.command" -> when (message.optString("action")) {
                "play" -> resumePlayback()
                "pause" -> { player.pause(); showControls() }
                "stop" -> { player.stop(); current = null; welcome.visibility = View.VISIBLE; setControlsVisible(false) }
                "seek" -> player.seekTo((message.optDouble("value", 0.0).coerceAtLeast(0.0) * 1000).toLong())
                "volume" -> player.volume = message.optDouble("value", 0.8).toFloat().coerceIn(0f, 1f)
                "next" -> requestAdjacent(1)
                "previous" -> requestAdjacent(-1)
                "fit" -> playerView.resizeMode = if (message.optString("value") == "fill") AspectRatioFrameLayout.RESIZE_MODE_ZOOM else AspectRatioFrameLayout.RESIZE_MODE_FIT
                "subtitle" -> {
                    val selected = if (message.isNull("value")) null else message.optString("value")
                    if (subtitleSelection.accept(selected, message.optLong("revision", subtitleSelection.revision))) applySubtitle()
                }
            }
        }
        updateState()
    }

    private fun load(item: JSONObject) {
        current = item
        subtitleSelection.reset(item.subtitleId(), item.optLong("subtitleRevision", 0))
        player.setMediaItem(mediaItem(item), (item.optDouble("position", 0.0).coerceAtLeast(0.0) * 1000).toLong())
        player.playWhenReady = item.optBoolean("autoplay", true)
        player.prepare()
        titleView.text = item.getString("title")
        welcome.visibility = View.GONE
        closeDrawer()
        showControls()
    }

    private fun JSONObject.subtitleId(): String? = if (isNull("selectedSubtitle")) null else optString("selectedSubtitle").ifBlank { null }

    private fun mediaItem(item: JSONObject): MediaItem {
        val subtitles = item.optJSONArray("subtitles").objects().map { subtitle ->
            MediaItem.SubtitleConfiguration.Builder(Uri.parse(subtitle.getString("url")))
                .setId(subtitle.getString("id")).setLabel(subtitle.getString("label"))
                .setMimeType(MimeTypes.TEXT_VTT).setSelectionFlags(if (subtitle.getString("id") == selectedSubtitle) C.SELECTION_FLAG_DEFAULT else 0).build()
        }
        return MediaItem.Builder().setMediaId(item.getString("id")).setUri(item.getString("url")).setSubtitleConfigurations(subtitles).build()
    }

    private fun applySubtitle() {
        val builder = player.trackSelectionParameters.buildUpon().clearOverridesOfType(C.TRACK_TYPE_TEXT).setTrackTypeDisabled(C.TRACK_TYPE_TEXT, selectedSubtitle == null)
        val subtitleId = selectedSubtitle
        if (subtitleId != null) {
            val group = player.currentTracks.groups.firstOrNull { group -> group.type == C.TRACK_TYPE_TEXT && matchesSubtitleTrack(group.getTrackFormat(0).id, subtitleId) }
            if (group != null) builder.setOverrideForType(TrackSelectionOverride(group.mediaTrackGroup, 0))
        }
        val parameters = builder.build()
        if (parameters != player.trackSelectionParameters) player.trackSelectionParameters = parameters
    }

    private fun requestAdjacent(delta: Int) {
        if (items.isEmpty()) return
        val index = items.indexOfFirst { it.optString("id") == current?.optString("id") }
        val target = index + delta
        if (target in items.indices) requestPlay(items[target])
    }

    private fun requestPlay(item: JSONObject) {
        server.publish(JSONObject().put("type", "request.play").put("id", item.getString("id")))
        titleView.text = "Preparing ${item.getString("title")}…"
        closeDrawer()
        showControls()
    }

    private fun showPlaylist() {
        showDrawer("Your playlist", items.map { item -> Pair((if (item.optString("id") == current?.optString("id")) "▶  " else "") + item.optString("title")) { requestPlay(item) } })
    }

    private fun showSubtitles() {
        val options = mutableListOf(Pair("Off") { selectedSubtitle = null; applySubtitle(); closeDrawer(); updateState() })
        current?.optJSONArray("subtitles").objects().forEach { sub ->
            options.add(Pair(sub.optString("label")) { selectedSubtitle = sub.getString("id"); applySubtitle(); closeDrawer(); updateState() })
        }
        showDrawer("Subtitles", options)
    }

    private fun showDrawer(title: String, options: List<Pair<String, () -> Unit>>) {
        closeDrawer()
        val panel = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(22), dp(18), dp(22), dp(18)); background = shape(TvTheme.surface, radius = 16) }
        panel.addView(text("ON THIS TV", 11f, accent).apply { gravity = Gravity.START; typeface = Typeface.DEFAULT_BOLD; letterSpacing = 0.1f })
        panel.addView(text(title, 25f).apply { gravity = Gravity.START; typeface = Typeface.DEFAULT_BOLD; setPadding(dp(8), dp(8), dp(8), dp(16)) })
        val scroll = ScrollView(this)
        val list = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        options.forEach { (name, action) -> list.addView(button(name, clicked = action).apply { textSize = 16f; gravity = Gravity.START or Gravity.CENTER_VERTICAL }, LinearLayout.LayoutParams(-1, dp(58)).apply { setMargins(dp(8), dp(5), dp(8), dp(5)) }) }
        if (options.isEmpty()) list.addView(text("Add some files on your PC.", 17f, TvTheme.muted))
        scroll.addView(list)
        panel.addView(scroll, LinearLayout.LayoutParams(-1, 0, 1f))
        panel.addView(button("Back to video") { closeDrawer() }, LinearLayout.LayoutParams(-1, dp(48)).apply { topMargin = dp(12) })
        drawer = panel
        root.addView(panel, FrameLayout.LayoutParams(dp(440), -1, Gravity.END).apply { setMargins(0, dp(20), dp(20), dp(20)) })
        list.getChildAt(0)?.requestFocus()
    }

    private fun closeDrawer() {
        drawer?.let { root.removeView(it) }
        drawer = null
        if (::playButton.isInitialized) playButton.requestFocus()
        scheduleHide()
    }

    private fun updateCaptionInset() {
        playerView.subtitleView?.let { captions ->
            // Use the full video surface, not the smaller padded subtitle canvas.
            // This keeps the user's caption size stable while the HUD is visible.
            if (playerView.height > 0) captions.setFixedTextSize(TypedValue.COMPLEX_UNIT_PX, playerView.height * SubtitleView.DEFAULT_TEXT_SIZE_FRACTION * captionFontScale)
            val inset = if (controls.visibility == View.VISIBLE) controls.height + dp(20) else 0
            if (captions.paddingBottom != inset) captions.setPadding(captions.paddingLeft, captions.paddingTop, captions.paddingRight, inset)
        }
    }
    private fun setControlsVisible(visible: Boolean) {
        controls.visibility = if (visible) View.VISIBLE else View.GONE
        updateCaptionInset()
        if (visible) controls.post { updateCaptionInset() }
    }
    private fun showControls() { setControlsVisible(true); playButton.requestFocus(); scheduleHide() }
    private fun scheduleHide() { handler.removeCallbacks(hideControls); handler.postDelayed(hideControls, 8000) }

    private fun updateState() {
        if (!::server.isInitialized) return
        val duration = player.duration.coerceAtLeast(0)
        if (!changingSeek) timeline.progress = if (duration > 0) (player.currentPosition * 1000 / duration).toInt() else 0
        times.text = "${time(player.currentPosition)} / ${time(duration)}"
        playButton.text = if (player.isPlaying) "Pause" else "Play"
        server.publish(JSONObject().put("type", "playback.state").put("id", current?.optString("id") ?: JSONObject.NULL).put("playing", player.isPlaying).put("position", player.currentPosition / 1000.0).put("duration", duration / 1000.0).put("subtitleId", selectedSubtitle ?: JSONObject.NULL).put("subtitleRevision", subtitleSelection.revision))
    }

    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        if (event.action == KeyEvent.ACTION_DOWN) {
            when (event.keyCode) {
                KeyEvent.KEYCODE_BACK -> {
                    if (drawer != null) { closeDrawer(); return true }
                    if (controls.visibility == View.VISIBLE && current != null) { setControlsVisible(false); return true }
                }
                KeyEvent.KEYCODE_DPAD_CENTER, KeyEvent.KEYCODE_ENTER -> if (controls.visibility != View.VISIBLE && current != null && drawer == null) { showControls(); return true }
                KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE -> { if (player.isPlaying) player.pause() else resumePlayback(); return true }
                KeyEvent.KEYCODE_MEDIA_NEXT -> { requestAdjacent(1); return true }
                KeyEvent.KEYCODE_MEDIA_PREVIOUS -> { requestAdjacent(-1); return true }
                KeyEvent.KEYCODE_DPAD_LEFT, KeyEvent.KEYCODE_DPAD_RIGHT -> if (controls.visibility != View.VISIBLE && current != null) {
                    player.seekTo((player.currentPosition + if (event.keyCode == KeyEvent.KEYCODE_DPAD_RIGHT) 10000 else -10000).coerceAtLeast(0)); return true
                }
                KeyEvent.KEYCODE_MENU -> { showPlaylist(); return true }
            }
            scheduleHide()
        }
        return super.dispatchKeyEvent(event)
    }

    private fun advertise() {
        val wifi = applicationContext.getSystemService(WIFI_SERVICE) as WifiManager
        multicast = wifi.createMulticastLock("lantern-discovery").apply { setReferenceCounted(false); acquire() }
        nsd = getSystemService(NSD_SERVICE) as NsdManager
        registration = object : NsdManager.RegistrationListener {
            override fun onServiceRegistered(info: NsdServiceInfo) = Unit
            override fun onRegistrationFailed(info: NsdServiceInfo, code: Int) = Unit
            override fun onServiceUnregistered(info: NsdServiceInfo) = Unit
            override fun onUnregistrationFailed(info: NsdServiceInfo, code: Int) = Unit
        }
        val service = NsdServiceInfo().apply { serviceName = "Lantern · ${Build.MODEL}"; serviceType = "_lantern._tcp."; port = 8765; setAttribute("fn", "${Build.MODEL} · Lantern"); setAttribute("version", "1") }
        nsd?.registerService(service, NsdManager.PROTOCOL_DNS_SD, registration)
    }

    private fun ipAddress(): String = try {
        NetworkInterface.getNetworkInterfaces().toList().flatMap { it.inetAddresses.toList() }.firstOrNull { it is Inet4Address && !it.isLoopbackAddress }?.hostAddress ?: "check TV network settings"
    } catch (_: Exception) { "check TV network settings" }

    private fun resumePlayback() {
        if (player.playbackState == Player.STATE_ENDED) player.seekTo(0)
        player.play()
    }

    private fun time(ms: Long): String { val s = ms.coerceAtLeast(0) / 1000; return if (s >= 3600) "%d:%02d:%02d".format(s / 3600, s / 60 % 60, s % 60) else "%d:%02d".format(s / 60, s % 60) }
    private fun JSONArray?.objects(): List<JSONObject> = if (this == null) emptyList() else (0 until length()).map { getJSONObject(it) }

    override fun onPause() { super.onPause(); player.pause() }
    override fun onDestroy() {
        destroyed = true
        handler.removeCallbacksAndMessages(null)
        registration?.let { try { nsd?.unregisterService(it) } catch (_: Exception) { } }
        if (::server.isInitialized) server.shutdown()
        multicast?.let { if (it.isHeld) it.release() }
        player.release()
        super.onDestroy()
    }
}
