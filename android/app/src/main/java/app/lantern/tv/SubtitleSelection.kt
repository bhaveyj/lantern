package app.lantern.tv

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
