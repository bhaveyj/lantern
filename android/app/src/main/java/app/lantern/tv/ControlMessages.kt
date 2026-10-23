package app.lantern.tv

import org.json.JSONObject

object ControlMessages {
    fun validate(message: JSONObject) {
        when (message.getString("type")) {
            "playlist.snapshot" -> {
                val items = message.getJSONArray("items")
                ControlLimits.count(items.length(), 200)
                val ids = mutableSetOf<String>()
                for (index in 0 until items.length()) {
                    val item = items.getJSONObject(index)
                    validateItem(item)
                    require(ids.add(item.getString("id"))) { "Duplicate media id" }
                }
            }
            "playback.load" -> validateItem(message.getJSONObject("item"))
            "playback.command" -> {
                ControlLimits.command(message.getString("action"), if (message.isNull("value")) null else message.get("value"))
                if (message.has("revision")) require(message.getLong("revision") >= 0)
            }
            else -> throw IllegalArgumentException("Unknown control message")
        }
    }

    private fun validateItem(item: JSONObject) {
        ControlLimits.text(item.getString("id"), 256)
        ControlLimits.text(item.getString("title"))
        ControlLimits.url(item.getString("url"))
        val subtitles = item.optJSONArray("subtitles")
        ControlLimits.count(subtitles?.length() ?: 0, 1024)
        val ids = mutableSetOf<String>()
        if (subtitles != null) for (index in 0 until subtitles.length()) {
            val subtitle = subtitles.getJSONObject(index)
            val id = subtitle.getString("id")
            ControlLimits.text(id, 256)
            require(ids.add(id)) { "Duplicate subtitle id" }
            ControlLimits.text(subtitle.getString("label"))
            ControlLimits.url(subtitle.getString("url"))
        }
        if (!item.isNull("selectedSubtitle")) require(item.getString("selectedSubtitle") in ids)
        if (item.has("subtitleRevision")) require(item.getLong("subtitleRevision") >= 0)
        if (item.has("position")) ControlLimits.command("seek", item.get("position"))
        if (item.has("autoplay")) require(item.get("autoplay") is Boolean)
    }
}
