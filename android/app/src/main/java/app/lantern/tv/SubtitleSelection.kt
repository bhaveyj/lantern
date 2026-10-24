package app.lantern.tv

/** Media3 prefixes external track IDs with their merged source index. */
fun matchesSubtitleTrack(formatId: String?, subtitleId: String): Boolean {
    if (formatId == subtitleId) return true
    if (formatId == null) return false
    val separator = formatId.indexOf(':')
    if (separator <= 0) return false
    val sourceIndex = formatId.substring(0, separator).toIntOrNull() ?: return false
    return sourceIndex > 0 && formatId.substring(separator + 1) == subtitleId
}

/** Keeps delayed state/snapshot messages from restoring an older selection. */
class SubtitleSelection {
    var id: String? = null
        private set
    var revision: Long = 0
        private set

    fun reset(id: String?, revision: Long = 0) {
        this.id = id
        this.revision = revision.coerceAtLeast(0)
    }

    fun accept(id: String?, revision: Long): Boolean {
        if (revision < this.revision) return false
        reset(id, revision)
        return true
    }

    fun select(id: String?) {
        if (id != this.id) {
            this.id = id
            revision++
        }
    }
}
