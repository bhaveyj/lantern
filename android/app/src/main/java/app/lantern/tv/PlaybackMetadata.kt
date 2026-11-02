package app.lantern.tv

import java.util.Locale
import org.json.JSONObject

/** Optional source hints; absent fields retain the existing local-file behavior. */
data class PlaybackMetadata(val mimeType: String?, val live: Boolean, val seekable: Boolean) {
    companion object {
        const val HLS_MIME = "application/x-mpegURL"
        const val LIVE_TARGET_OFFSET_MS = 15000L

        fun from(item: JSONObject): PlaybackMetadata {
            val mime = if (item.has("mimeType")) {
                require(item.get("mimeType") is String) { "Invalid media type" }
                when (val value = item.getString("mimeType").lowercase(Locale.ROOT)) {
                    "" -> null // Local items may be sent before the sender probes them.
                    "application/x-mpegurl", "application/vnd.apple.mpegurl", "audio/mpegurl", "audio/x-mpegurl" -> HLS_MIME
                    "video/mp4", "audio/mp4", "video/mp2t", "video/webm", "audio/mpeg", "audio/aac", "audio/flac", "audio/ogg", "audio/wav", "video/x-matroska" -> value
                    else -> throw IllegalArgumentException("Unsupported media type")
                }
            } else null
            for (field in listOf("live", "seekable")) {
                if (item.has(field)) require(item.get(field) is Boolean) { "Invalid $field flag" }
            }
            return PlaybackMetadata(mime, item.optBoolean("live", false), item.optBoolean("seekable", true))
        }

        // Playlist changes update labels/captions without erasing active source hints.
        fun mergePlaying(previous: JSONObject, updated: JSONObject): JSONObject {
            val merged = JSONObject(updated.toString()).put("url", previous.getString("url"))
            for (field in listOf("mimeType", "live", "seekable")) {
                if (!updated.has(field) && previous.has(field)) merged.put(field, previous.get(field))
            }
            return merged
        }
    }
}

/** Timeline guards shared by remote, touch and keyboard seeking. */
object PlaybackTimeline {
    fun duration(raw: Long): Long = raw.coerceAtLeast(0)

    fun position(raw: Long, rawDuration: Long, seekable: Boolean): Long? {
        val duration = duration(rawDuration)
        return if (!seekable || duration == 0L) null else raw.coerceIn(0, duration)
    }

    fun fromProgress(progress: Int, rawDuration: Long, seekable: Boolean): Long? {
        val duration = duration(rawDuration)
        if (!seekable || duration == 0L) return null
        return (duration.toDouble() * progress.coerceIn(0, 1000) / 1000).toLong()
    }

    fun progress(raw: Long, rawDuration: Long): Int {
        val duration = duration(rawDuration)
        return if (duration == 0L) 0 else (raw.coerceIn(0, duration).toDouble() / duration * 1000).toInt()
    }
}
