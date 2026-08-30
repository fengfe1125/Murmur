package com.sakura.murmur

import java.io.File
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeout

/**
 * Where a room's rows go. The Kotlin counterpart of iOS `MurmurRoomRecorder`:
 * the photo room and the archived-day continuation both write through this
 * surface, so the archive is the only writer and the stores stay swappable in
 * tests. `record` adopts the photo (copies it into archive storage) before
 * the row lands, and saves before returning so an optimistic row is durable.
 */
interface RoomRecorder {
    suspend fun record(message: MurmurMessage, photoFile: File?)
    fun setDelivery(delivery: MurmurDeliveryState, messageID: String)
    fun setMomentID(momentID: String, messageID: String)
    fun withdraw(messageID: String)
}

/**
 * What was said in the photo rooms, kept by the day it was said on.
 *
 * Deliberately not the conversation. A room is about one old photo, and the
 * thing worth coming back to is "which day did we talk about this" — so the
 * archive is indexed by day and the chat stays a chat. Same file format as
 * the chat transcript; a directory of its own via
 * `MurmurTranscriptStore.archive(baseDir)`.
 *
 * iOS isolates the recorder with an actor; here the room models drive one
 * turn coroutine at a time and [MurmurTranscriptStore] is `@Synchronized`,
 * so a plain class with `MutableStateFlow`s covers the same ground while
 * staying constructible on the JVM in tests.
 */
class MurmurArchive(
    private val store: MurmurTranscriptStore,
    /** The device's own calendar, injectable in tests: filing days are
     *  computed in this zone, never UTC. */
    val zone: ZoneId = ZoneId.systemDefault(),
) : RoomRecorder {

    private val _rows = MutableStateFlow<List<MurmurMessage>>(emptyList())
    /** Every row, oldest first. Days are a view over this rather than a second
     *  structure to keep in step. */
    val rows: StateFlow<List<MurmurMessage>> = _rows.asStateFlow()

    private val _isLoaded = MutableStateFlow(false)
    val isLoaded: StateFlow<Boolean> = _isLoaded.asStateFlow()

    fun load() {
        if (_isLoaded.value) return
        _rows.value = store.load()
        _isLoaded.value = true
    }

    /** Which photo file a row names, resolved against the archive's own images. */
    fun imageFile(name: String): File = store.imageFile(name)

    // ---- Reading -------------------------------------------------------------

    /** The days that have anything on them, in the device's own calendar. */
    val daysWithRooms: Set<LocalDate>
        get() = _rows.value.mapTo(mutableSetOf()) { filingDay(it) }

    /** One day's thread, in the order it happened. */
    fun rows(on: LocalDate): List<MurmurMessage> =
        _rows.value.filter { filingDay(it) == on }

    /** How many photos that day carried. The count the calendar's day row
     *  shows — rows without a picture are the talk about one, not another one. */
    fun photoCount(on: LocalDate): Int = rows(on).count { it.imageFile != null }

    /** The most recent days that have anything on them, newest first. */
    fun recentDays(limit: Int = 60): List<LocalDate> =
        daysWithRooms.sortedDescending().take(limit)

    /**
     * The durable server moments that make up the most recent photo room on
     * one archive day. Rows are ordered by their real send time, duplicate
     * moment IDs collapse in first-seen order, and the newest bounded window
     * is returned so a long room does not crowd out its latest turns.
     */
    fun contextMomentIDs(on: LocalDate, limit: Int = 8): List<String> {
        if (limit <= 0) return emptyList()
        val ordered = rows(on).sortedBy { it.sentAt }
        // `lastIndexOf`-with-fallback-to-zero: a day with no photo row still
        // contributes its whole thread (legacy data predates photo rows).
        var roomStart = ordered.indexOfLast { it.imageFile != null }
        if (roomStart < 0) roomStart = 0
        val seen = mutableSetOf<String>()
        val moments = ordered.asSequence().drop(roomStart).mapNotNull { row ->
            val momentID = row.momentID
            if (momentID.isNullOrEmpty() || !seen.add(momentID)) null else momentID
        }.toList()
        return moments.takeLast(limit)
    }

    // ---- Writing -------------------------------------------------------------

    override suspend fun record(message: MurmurMessage, photoFile: File?) {
        var row = message
        if (photoFile != null) {
            row = row.copy(imageFile = store.adoptImage(photoFile, row.id))
        }
        // A row with neither words nor a picture is an empty bubble; the copy
        // failing is not a reason to put one in the archive.
        if (row.text.isEmpty() && row.imageFile == null) return
        _rows.update { it + row }
        // The row is durable before `record` returns: a caller that
        // immediately reloads — or an app suspension directly after a send —
        // must not lose the optimistic row.
        store.save(_rows.value)
    }

    override fun setDelivery(delivery: MurmurDeliveryState, messageID: String) {
        val index = _rows.value.indexOfFirst { it.id == messageID }
        if (index < 0 || _rows.value[index].delivery == delivery) return
        _rows.update { rows ->
            rows.mapIndexed { i, row -> if (i == index) row.copy(delivery = delivery) else row }
        }
        persist()
    }

    override fun setMomentID(momentID: String, messageID: String) {
        val index = _rows.value.indexOfFirst { it.id == messageID }
        if (index < 0 || _rows.value[index].momentID == momentID) return
        _rows.update { rows ->
            rows.mapIndexed { i, row -> if (i == index) row.copy(momentID = momentID) else row }
        }
        persist()
    }

    override fun withdraw(messageID: String) {
        if (_rows.value.none { it.id == messageID }) return
        _rows.update { rows -> rows.filterNot { it.id == messageID } }
        persist()
    }

    fun clear() {
        _rows.value = emptyList()
        store.clear()
    }

    /**
     * The store writes are quick `@Synchronized` file swaps, and the only
     * writer is the room turn — same persist point as iOS, minus the extra
     * hop because nothing here needs the main actor.
     */
    private fun persist() {
        store.save(_rows.value)
    }

    private fun filingDay(row: MurmurMessage): LocalDate =
        (row.archiveDay ?: row.sentAt).atZone(zone).toLocalDate()
}

/**
 * The composer attached to one archived day. It deliberately owns no second
 * transcript: every optimistic row and every streamed bubble is written to
 * [MurmurArchive], tagged with the selected logical day and its real send time.
 *
 * The day is a [LocalDate]; rows carry `archiveDay` as that day's start
 * (instantiated in [MurmurArchive]'s zone) while `sentAt` stays the real
 * send time — continuing an old day must not rewrite when the words left.
 */
class ArchiveDayModel(
    val day: LocalDate,
    val archive: MurmurArchive,
    private val api: MurmurApiClient,
    private val scope: CoroutineScope,
    private val requestTimeoutSeconds: Double = 45.0,
    private val bubblePacing: MurmurBubblePacing = MurmurBubblePacing.HUMAN,
) {
    enum class Phase { Listening, Sending }

    /** The archive-day tag rows from this composer carry: the selected day's
     *  first instant, matching how [MurmurArchive] groups rows onto days. */
    val dayStart: Instant = day.atStartOfDay(archive.zone).toInstant()

    val draft = MutableStateFlow("")

    private val _phase = MutableStateFlow(Phase.Listening)
    val phase: StateFlow<Phase> = _phase.asStateFlow()

    private val _failure = MutableStateFlow<MurmurFailure?>(null)
    val failure: StateFlow<MurmurFailure?> = _failure.asStateFlow()

    val canSend: Boolean
        get() = _phase.value == Phase.Listening && draft.value.trim().isNotEmpty()

    /** True while Murmur owes an answer, which is what the typing dots read. */
    val isAwaitingReply: Boolean
        get() = _phase.value == Phase.Sending

    private var turn: Job? = null
    private var closed = false
    /** The row this turn put in the archive before any receipt came back.
     *  Cleared the moment the server takes it; until then it is the row that
     *  leaving the day has to account for. */
    private var unsentRowID: String? = null

    fun send() {
        val text = draft.value.trim()
        if (text.isEmpty() || _phase.value != Phase.Listening || closed) return
        val contextMomentIDs = archive.contextMomentIDs(day)
        val key = UUID.randomUUID().toString()
        val row = MurmurMessage(
            author = MurmurMessageAuthor.you,
            text = text,
            sentAt = Instant.now(),
            archiveDay = dayStart,
            delivery = MurmurDeliveryState.sending,
            idempotencyKey = key,
        )
        draft.value = ""
        _failure.value = null
        _phase.value = Phase.Sending
        turn?.cancel()
        turn = scope.launch {
            unsentRowID = row.id
            archive.record(row, photoFile = null)
            var receiptMomentID: String? = null
            try {
                val receipt = withTimeout((requestTimeoutSeconds * 1_000).toLong()) {
                    api.createMoment(
                        note = text,
                        photo = null,
                        idempotencyKey = key,
                        contextMomentIDs = contextMomentIDs,
                    )
                }
                receiptMomentID = receipt.momentID
                unsentRowID = null
                archive.setMomentID(receipt.momentID, row.id)
                archive.setDelivery(MurmurDeliveryState.sent, row.id)
                RoomEventConsumer(api, bubblePacing).consume(
                    momentID = receipt.momentID,
                    onBubble = { bubble ->
                        archive.record(
                            MurmurMessage(
                                id = bubble.id,
                                author = MurmurMessageAuthor.murmur,
                                text = bubble.text,
                                sentAt = Instant.now(),
                                archiveDay = dayStart,
                                momentID = receipt.momentID,
                            ),
                            photoFile = null,
                        )
                    },
                )
                archive.setDelivery(MurmurDeliveryState.answered, row.id)
                _phase.value = Phase.Listening
            } catch (timeout: TimeoutCancellationException) {
                finishFailedTurn(row, text, receiptMomentID, MurmurFailure.from(timeout))
            } catch (cancelled: CancellationException) {
                // `close` owns the row's fate; there is no composer to
                // restore a draft into once it has run.
                finishFailedTurn(row, text, receiptMomentID, null)
            } catch (error: Throwable) {
                finishFailedTurn(row, text, receiptMomentID, MurmurFailure.from(error))
            }
        }
    }

    fun close() {
        closed = true
        turn?.cancel()
        turn = null
        // Leaving mid-send is not the same as a line that came back: the
        // receipt never landed, so as far as this device knows it never left,
        // but the words are on their way out of the composer with the screen.
        // The day keeps the row and says it failed, rather than quietly
        // deleting what was typed.
        unsentRowID?.let { archive.setDelivery(MurmurDeliveryState.failed, it) }
        unsentRowID = null
    }

    private fun finishFailedTurn(
        row: MurmurMessage,
        text: String,
        receiptMomentID: String?,
        failure: MurmurFailure?,
    ) {
        // `close` has already filed this row and there is no composer left to
        // put the words back into.
        if (closed) return
        unsentRowID = null
        if (receiptMomentID == null) {
            archive.withdraw(row.id)
            if (draft.value.isBlank()) draft.value = text
        }
        _failure.value = failure
        _phase.value = Phase.Listening
    }
}
