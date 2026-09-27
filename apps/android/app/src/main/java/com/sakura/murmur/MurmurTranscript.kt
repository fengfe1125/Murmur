package com.sakura.murmur

import kotlinx.serialization.KSerializer
import kotlinx.serialization.Serializable
import kotlinx.serialization.SerializationException
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.descriptors.PrimitiveKind
import kotlinx.serialization.descriptors.PrimitiveSerialDescriptor
import kotlinx.serialization.encodeToString
import kotlinx.serialization.encoding.Decoder
import kotlinx.serialization.encoding.Encoder
import kotlinx.serialization.json.Json
import java.io.File
import java.io.IOException
import java.nio.file.AtomicMoveNotSupportedException
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.time.Instant
import java.time.format.DateTimeFormatter
import java.util.UUID

/** Who said a row. Wire values match iOS (`you`, `murmur`). */
enum class MurmurMessageAuthor { you, murmur }

/**
 * Only meaningful on messages the person sent.  The ticks describe transport,
 * not comprehension: one when the server took the upload, two when Murmur
 * began composing.  Nothing here claims that anybody "read" anything.
 */
enum class MurmurDeliveryState { sending, sent, answered, failed }

/**
 * ISO-8601 on the wire, matching iOS `JSONEncoder.dateEncodingStrategy =
 * .iso8601` (`2026-08-20T09:00:00Z`).
 */
private object InstantIso8601Serializer : KSerializer<Instant> {
    override val descriptor = PrimitiveSerialDescriptor("InstantIso8601", PrimitiveKind.STRING)

    override fun serialize(encoder: Encoder, value: Instant) {
        encoder.encodeString(DateTimeFormatter.ISO_INSTANT.format(value))
    }

    override fun deserialize(decoder: Decoder): Instant =
        Instant.parse(decoder.decodeString())
}

/**
 * One row of the on-device history.  The JSON field names are the wire
 * contract with the iOS transcript (`id`, `author`, `text`, `imageFile`,
 * `sentAt`, `archiveDay`, `delivery`, `momentID`, `idempotencyKey`) and must
 * not drift; nullable fields decode when absent so transcripts written before
 * they existed still load.
 */
@Serializable
data class MurmurMessage(
    val id: String = UUID.randomUUID().toString(),
    val author: MurmurMessageAuthor,
    val text: String,
    /**
     * File name inside the transcript's image directory, not a full path: the
     * container path changes between installs, so storing one would rot.
     */
    val imageFile: String? = null,
    @Serializable(with = InstantIso8601Serializer::class)
    val sentAt: Instant = Instant.now(),
    /**
     * The calendar day this row belongs to when it resumes an older room.
     * `sentAt` remains the real send time; older transcripts omit this field
     * and continue to group by `sentAt`.
     */
    @Serializable(with = InstantIso8601Serializer::class)
    val archiveDay: Instant? = null,
    val delivery: MurmurDeliveryState = MurmurDeliveryState.sent,
    val momentID: String? = null,
    /**
     * The key this row's send went up under.  Kept so that a row still marked
     * failed after a relaunch can be sent again as the *same* moment rather
     * than a second one — without it a resend across a restart risks saying
     * the same thing twice to a server that did quietly accept the first go.
     * Optional because transcripts written before this existed decode without
     * it; those rows fall back to a fresh key.
     */
    val idempotencyKey: String? = null,
)

/**
 * The on-device chat history.
 *
 * Murmur's server keeps private memory, never a transcript, so the history a
 * person scrolls through exists only here.  Deleting the app deletes it, and
 * [clear] is what the settings screen calls.
 *
 * The chat store lives at `File(context.filesDir, "transcript")`; the 当年今日
 * archive lives beside it via [archive].  iOS isolates the store with an
 * actor; here every public entry point is `@Synchronized`, which covers the
 * same ground because the room recorder is the only writer and file I/O at
 * this size is quick.
 */
class MurmurTranscriptStore(
    val directory: File,
    private val limit: Int = HISTORY_LIMIT,
) {
    private val file = File(directory, TRANSCRIPT_FILE_NAME)
    private val imageDirectory = File(directory, IMAGES_DIRECTORY_NAME)

    /**
     * Photos copied in but not yet named by any saved message.  A send saves
     * the transcript at least twice — once when the line appears, again when
     * the reply lands — and the copy finishes somewhere in between.  Without
     * this, the save in the middle prunes the photo it has not been told
     * about, and the message ends up pointing at a file that no longer exists.
     */
    private var pendingAdoptions: Set<String> = emptySet()

    companion object {
        /**
         * Old turns are dropped rather than kept forever: the transcript is a
         * convenience for the reader, not an archive, and an unbounded JSON
         * file would eventually cost a visible pause on launch.
         */
        const val HISTORY_LIMIT = 600

        /**
         * 当年今日's archive is the one place that *is* an archive — a day you
         * talked about a photo should still be on the calendar next year — so
         * it takes a far higher ceiling.  Still a ceiling: an unbounded file
         * would eventually be read on every launch.
         */
        const val ARCHIVE_LIMIT = 6_000

        private const val TRANSCRIPT_FILE_NAME = "transcript.json"
        private const val IMAGES_DIRECTORY_NAME = "images"
        private const val ARCHIVE_DIRECTORY_NAME = "archive"

        // iOS parity: JSONEncoder writes every field but omits nil optionals,
        // and dates ride as ISO-8601 strings (see InstantIso8601Serializer).
        private val json = Json {
            encodeDefaults = true
            explicitNulls = false
        }

        /**
         * The store 当年今日's rooms write into.  A directory of its own, beside
         * the conversation and never mixed into it: what was said about an old
         * photo belongs to the day it was said on, not to the chat.  Pass the
         * same base the chat store was built from (`context.filesDir`): the
         * archive lands at `<base>/archive`, the chat at `<base>/transcript`.
         */
        fun archive(baseDirectory: File): MurmurTranscriptStore =
            MurmurTranscriptStore(File(baseDirectory, ARCHIVE_DIRECTORY_NAME), ARCHIVE_LIMIT)
    }

    private fun ensureDirectories() {
        directory.mkdirs()
        imageDirectory.mkdirs()
    }

    /**
     * The saved rows, oldest first.  Missing or unreadable storage reads as
     * empty, and a send interrupted by a crash or a force quit comes back
     * failed rather than spinning forever — it never reached the server.
     */
    @Synchronized
    fun load(): List<MurmurMessage> {
        val raw = try {
            file.readText()
        } catch (e: IOException) {
            return emptyList()
        }
        val messages = try {
            json.decodeFromString<List<MurmurMessage>>(raw)
        } catch (e: SerializationException) {
            return emptyList()
        } catch (e: IOException) {
            return emptyList()
        }
        return messages.map { message ->
            if (message.delivery == MurmurDeliveryState.sending)
                message.copy(delivery = MurmurDeliveryState.failed)
            else message
        }
    }

    /**
     * Writes the rows, keeping only the most recent [limit].  Failures are
     * silent, matching iOS's `try?`: a full disk must not crash the room.
     * After the snapshot lands, image files no row names are pruned —
     * except copies still awaiting adoption (see [adoptImage]).
     */
    @Synchronized
    fun save(messages: List<MurmurMessage>) {
        ensureDirectories()
        val trimmed = messages.takeLast(limit)
        val data = try {
            json.encodeToString(trimmed).toByteArray()
        } catch (e: SerializationException) {
            return
        }
        val scratch = File(directory, "$TRANSCRIPT_FILE_NAME.tmp")
        try {
            scratch.writeBytes(data)
            moveAtomically(scratch, file)
        } catch (e: IOException) {
            scratch.delete()
            return
        }
        val referenced = trimmed.mapNotNullTo(mutableSetOf()) { it.imageFile }
        pruneImages(keeping = referenced + pendingAdoptions)
        // Anything this snapshot names is durable now and no longer pending.
        pendingAdoptions -= referenced
    }

    /**
     * Copies a picked photo out of the temporary directory, which the photo
     * loader clears, and into storage the transcript controls.  Returns the
     * file name the row should store, or null when the copy failed.
     */
    @Synchronized
    fun adoptImage(source: File, id: String): String? {
        ensureDirectories()
        val extension = source.extension.ifEmpty { "jpg" }
        val name = "$id.$extension"
        val destination = File(imageDirectory, name)
        try {
            source.copyTo(destination, overwrite = true)
        } catch (e: IOException) {
            return null
        }
        pendingAdoptions += name
        return name
    }

    /** The stored copy of an adopted image; rows only ever name files here. */
    fun imageFile(name: String): File = File(imageDirectory, name)

    @Synchronized
    fun clear() {
        pendingAdoptions = emptySet()
        file.delete()
        imageDirectory.deleteRecursively()
    }

    private fun moveAtomically(from: File, to: File) {
        try {
            Files.move(
                from.toPath(), to.toPath(),
                StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING,
            )
        } catch (e: AtomicMoveNotSupportedException) {
            Files.move(from.toPath(), to.toPath(), StandardCopyOption.REPLACE_EXISTING)
        }
    }

    private fun pruneImages(keeping: Set<String>) {
        val files = imageDirectory.listFiles() ?: return
        for (image in files) {
            if (image.name !in keeping) image.delete()
        }
    }
}
