package app.lantern.tv

import java.net.URI

object ControlLimits {
    fun json(value: String, maximum: Int = 1024 * 1024) {
        require(value.length <= maximum && value.toByteArray(Charsets.UTF_8).size <= maximum) { "Message too large" }
        var depth = 0
        var quoted = false
        var escaped = false
        for (character in value) {
            if (quoted) {
                if (escaped) escaped = false
                else if (character == '\\') escaped = true
                else if (character == '"') quoted = false
            } else when (character) {
                '"' -> quoted = true
                '{', '[' -> { depth++; require(depth <= 32) { "JSON too deeply nested" } }
                '}', ']' -> { depth--; require(depth >= 0) { "Invalid JSON" } }
            }
        }
        require(depth == 0 && !quoted) { "Invalid JSON" }
    }

    fun text(value: String, maximum: Int = 1024) {
        require(value.isNotBlank() && value.length <= maximum) { "Invalid text field" }
    }

    fun url(value: String) {
        text(value, 8192)
        val parsed = URI(value)
        require(parsed.scheme in setOf("http", "https") && !parsed.host.isNullOrBlank()) { "Invalid media URL" }
    }

    fun count(size: Int, maximum: Int) { require(size in 0..maximum) { "Too many items" } }

    fun command(action: String, value: Any?) {
        when (action) {
            "play", "pause", "stop", "next", "previous" -> Unit
            "seek", "volume" -> {
                require(value is Number) { "Invalid numeric value" }
                val number = value.toDouble()
                require(number.isFinite() && number >= 0 && (action != "volume" || number <= 1)) { "Invalid numeric value" }
            }
            "subtitle" -> require(value == null || (value is String && value.length <= 256)) { "Invalid subtitle" }
            "fit" -> require(value in setOf("fit", "fill")) { "Invalid picture fit" }
            else -> throw IllegalArgumentException("Unknown playback command")
        }
    }
}
