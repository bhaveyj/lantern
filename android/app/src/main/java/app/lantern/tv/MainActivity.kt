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
import android.view.Gravity
import android.view.KeyEvent
import android.view.View
import android.view.WindowManager
import android.widget.*
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.MimeTypes
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.TrackSelectionOverride
import androidx.media3.common.Tracks
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.AspectRatioFrameLayout
import androidx.media3.ui.PlayerView
import org.json.JSONArray
import org.json.JSONObject
import java.net.Inet4Address
import java.net.NetworkInterface

@androidx.media3.common.util.UnstableApi
class MainActivity : Activity() {
    private val handler = Handler(Looper.getMainLooper())
    private val gate = PairingGate()
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
    private var selectedSubtitle: String? = null
    private var changingSeek = false
    private var nsd: NsdManager? = null
    private var registration: NsdManager.RegistrationListener? = null
    private var multicast: WifiManager.MulticastLock? = null
    private var connected = false
    private val bg = Color.rgb(17, 37, 33)
    private val ink = Color.rgb(237, 243, 233)
    private val gold = Color.rgb(237, 183, 107)

    private val ticker = object : Runnable {
        override fun run() {
            if (gate.refresh()) pinView.text = gate.pin.chunked(3).joinToString("  ")
            updateState()
            handler.postDelayed(this, 1000)
        }
    }
    private val hideControls = Runnable { if (player.isPlaying && drawer == null) controls.visibility = View.GONE }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        window.decorView.systemUiVisibility = View.SYSTEM_UI_FLAG_FULLSCREEN or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
        player = ExoPlayer.Builder(this).build()
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
        server = ControlServer(gate, { message -> handler.post { handle(message) } }, { name -> handler.post {
            connected = name != null
            welcomeBrowse.visibility = if (connected) View.VISIBLE else View.GONE
            if (connected) welcomeBrowse.requestFocus()
            connectionView.text = if (name == null) "Open Lantern on Windows to connect." else "Connected to $name · choose a file on your PC"
            if (name == null) {
                player.stop()
                current = null
                items = emptyList()
                welcome.visibility = View.VISIBLE
                controls.visibility = View.GONE
                drawer?.let { root.removeView(it) }; drawer = null
            }
        } })
        try {
            // Sender pings every 20 seconds; NanoHTTPD's default 5-second read timeout is too short.
            server.start(30000, false)
            advertise()
        } catch (error: Exception) {
            connectionView.text = "Could not start the receiver: ${error.message}"
        }
        handler.post(ticker)
    }

    private fun text(value: String, size: Float, color: Int = ink): TextView = TextView(this).apply {
        text = value; textSize = size; setTextColor(color); gravity = Gravity.CENTER
        setPadding(dp(8), dp(6), dp(8), dp(6))
    }
    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()
    private fun button(value: String, clicked: () -> Unit): Button = Button(this).apply {
        text = value; textSize = 14f; isAllCaps = false; setTextColor(ink)
        setOnClickListener { clicked(); scheduleHide() }
        setOnFocusChangeListener { _, focused -> setTextColor(if (focused) bg else ink); backgroundTintList = android.content.res.ColorStateList.valueOf(if (focused) gold else Color.rgb(40, 70, 58)) }
        backgroundTintList = android.content.res.ColorStateList.valueOf(Color.rgb(40, 70, 58))
        minimumWidth = dp(62)
    }

    private fun createUi() {
        root = FrameLayout(this).apply { setBackgroundColor(bg) }
        setContentView(root)
        playerView = PlayerView(this).apply { player = this@MainActivity.player; useController = false }
        root.addView(playerView, FrameLayout.LayoutParams(-1, -1))
        welcome = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER; setPadding(dp(50), dp(30), dp(50), dp(30)); setBackgroundColor(bg) }
        welcome.addView(text("◩  lantern", 30f, gold).apply { typeface = Typeface.DEFAULT_BOLD })
        welcome.addView(text("Your next movie night starts here.", 28f).apply { setPadding(0, dp(20), 0, dp(8)) })
        welcome.addView(text("Enter this code in Lantern on your Windows PC", 17f, Color.LTGRAY))
        pinView = text(gate.pin.chunked(3).joinToString("  "), 58f, gold).apply { typeface = Typeface.MONOSPACE }
        welcome.addView(pinView)
        connectionView = text("Open Lantern on Windows to connect.", 16f)
        welcome.addView(connectionView)
        welcomeBrowse = button("Browse your playlist") { showPlaylist() }.apply { visibility = View.GONE }
        welcome.addView(welcomeBrowse)
        welcome.addView(text("Same home network  ·  Connect by IP: ${ipAddress()}:8765", 14f, Color.LTGRAY))
        root.addView(welcome, FrameLayout.LayoutParams(-1, -1))

        controls = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(24), dp(12), dp(24), dp(18)); setBackgroundColor(Color.argb(235, 17, 37, 33)); visibility = View.GONE }
        titleView = text("Ready", 20f).apply { gravity = Gravity.START }
        controls.addView(titleView)
        val timelineRow = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        timeline = SeekBar(this).apply {
            max = 1000
            progressTintList = android.content.res.ColorStateList.valueOf(gold)
            thumbTintList = android.content.res.ColorStateList.valueOf(gold)
            keyProgressIncrement = 10
            setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
                override fun onStartTrackingTouch(seekBar: SeekBar) { changingSeek = true }
                override fun onStopTrackingTouch(seekBar: SeekBar) { player.seekTo(player.duration.coerceAtLeast(0) * seekBar.progress / 1000); changingSeek = false; scheduleHide() }
                override fun onProgressChanged(seekBar: SeekBar, value: Int, fromUser: Boolean) { if (fromUser && !changingSeek) player.seekTo(player.duration.coerceAtLeast(0) * value / 1000) }
            })
        }
        timelineRow.addView(timeline, LinearLayout.LayoutParams(0, dp(40), 1f))
        times = text("0:00 / 0:00", 13f)
        timelineRow.addView(times)
        controls.addView(timelineRow)
        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER }
        row.addView(button("Previous") { requestAdjacent(-1) })
        row.addView(button("−10s") { player.seekTo((player.currentPosition - 10000).coerceAtLeast(0)) })
        playButton = button("Pause") { if (player.isPlaying) player.pause() else resumePlayback() }
        row.addView(playButton)
        row.addView(button("+10s") { player.seekTo(player.currentPosition + 10000) })
        row.addView(button("Next") { requestAdjacent(1) })
        row.addView(button("Playlist") { showPlaylist() })
        row.addView(button("CC") { showSubtitles() })
        row.addView(button("Fit / Fill") { playerView.resizeMode = if (playerView.resizeMode == AspectRatioFrameLayout.RESIZE_MODE_FIT) AspectRatioFrameLayout.RESIZE_MODE_ZOOM else AspectRatioFrameLayout.RESIZE_MODE_FIT })
        controls.addView(row)
        root.addView(controls, FrameLayout.LayoutParams(-1, -2, Gravity.BOTTOM))
    }

    private fun handle(message: JSONObject) {
        when (message.optString("type")) {
            "playlist.snapshot" -> {
                items = message.optJSONArray("items").objects()
                if (current == null) connectionView.text = "${items.size} files ready · start playback from Windows"
                if (drawer != null) showPlaylist()
            }
            "playback.load" -> load(message.getJSONObject("item"))
            "playback.command" -> when (message.optString("action")) {
                "play" -> resumePlayback()
                "pause" -> { player.pause(); showControls() }
                "stop" -> { player.stop(); current = null; welcome.visibility = View.VISIBLE; controls.visibility = View.GONE }
                "seek" -> player.seekTo((message.optDouble("value", 0.0).coerceAtLeast(0.0) * 1000).toLong())
                "volume" -> player.volume = message.optDouble("value", 0.8).toFloat().coerceIn(0f, 1f)
                "next" -> requestAdjacent(1)
                "previous" -> requestAdjacent(-1)
                "fit" -> playerView.resizeMode = if (message.optString("value") == "fill") AspectRatioFrameLayout.RESIZE_MODE_ZOOM else AspectRatioFrameLayout.RESIZE_MODE_FIT
                "subtitle" -> { selectedSubtitle = if (message.isNull("value")) null else message.optString("value"); applySubtitle() }
            }
        }
        updateState()
    }

    private fun load(item: JSONObject) {
        current = item
        selectedSubtitle = if (item.isNull("selectedSubtitle")) null else item.optString("selectedSubtitle").ifBlank { null }
        val subtitles = item.optJSONArray("subtitles").objects().map { subtitle ->
            MediaItem.SubtitleConfiguration.Builder(Uri.parse(subtitle.getString("url")))
                .setId(subtitle.getString("id")).setLabel(subtitle.getString("label"))
                .setMimeType(MimeTypes.TEXT_VTT).setSelectionFlags(if (subtitle.getString("id") == selectedSubtitle) C.SELECTION_FLAG_DEFAULT else 0).build()
        }
        player.setMediaItem(MediaItem.Builder().setMediaId(item.getString("id")).setUri(item.getString("url")).setSubtitleConfigurations(subtitles).build())
        player.prepare()
        player.play()
        titleView.text = item.getString("title")
        welcome.visibility = View.GONE
        closeDrawer()
        showControls()
    }

    private fun applySubtitle() {
        val builder = player.trackSelectionParameters.buildUpon().clearOverridesOfType(C.TRACK_TYPE_TEXT).setTrackTypeDisabled(C.TRACK_TYPE_TEXT, selectedSubtitle == null)
        if (selectedSubtitle != null) {
            val group = player.currentTracks.groups.firstOrNull { group -> group.type == C.TRACK_TYPE_TEXT && group.getTrackFormat(0).id == selectedSubtitle }
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
        val panel = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(30), dp(20), dp(30), dp(20)); setBackgroundColor(bg) }
        panel.addView(text(title, 25f, gold))
        val scroll = ScrollView(this)
        val list = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        options.forEach { (name, action) -> list.addView(button(name, action), LinearLayout.LayoutParams(-1, dp(60))) }
        if (options.isEmpty()) list.addView(text("Add some files on your PC.", 18f))
        scroll.addView(list)
        panel.addView(scroll, LinearLayout.LayoutParams(-1, 0, 1f))
        panel.addView(button("Back to video") { closeDrawer() })
        drawer = panel
        root.addView(panel, FrameLayout.LayoutParams(dp(440), -1, Gravity.END))
        list.getChildAt(0)?.requestFocus()
    }

    private fun closeDrawer() {
        drawer?.let { root.removeView(it) }
        drawer = null
        if (::playButton.isInitialized) playButton.requestFocus()
        scheduleHide()
    }

    private fun showControls() { controls.visibility = View.VISIBLE; playButton.requestFocus(); scheduleHide() }
    private fun scheduleHide() { handler.removeCallbacks(hideControls); handler.postDelayed(hideControls, 8000) }

    private fun updateState() {
        if (!::server.isInitialized) return
        val duration = player.duration.coerceAtLeast(0)
        if (!changingSeek) timeline.progress = if (duration > 0) (player.currentPosition * 1000 / duration).toInt() else 0
        times.text = "${time(player.currentPosition)} / ${time(duration)}"
        playButton.text = if (player.isPlaying) "Pause" else "Play"
        server.publish(JSONObject().put("type", "playback.state").put("id", current?.optString("id") ?: JSONObject.NULL).put("playing", player.isPlaying).put("position", player.currentPosition / 1000.0).put("duration", duration / 1000.0).put("subtitleId", selectedSubtitle ?: JSONObject.NULL))
    }

    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        if (event.action == KeyEvent.ACTION_DOWN) {
            when (event.keyCode) {
                KeyEvent.KEYCODE_BACK -> {
                    if (drawer != null) { closeDrawer(); return true }
                    if (controls.visibility == View.VISIBLE && current != null) { controls.visibility = View.GONE; return true }
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
        handler.removeCallbacksAndMessages(null)
        registration?.let { try { nsd?.unregisterService(it) } catch (_: Exception) { } }
        if (::server.isInitialized) server.shutdown()
        multicast?.let { if (it.isHeld) it.release() }
        player.release()
        super.onDestroy()
    }
}
