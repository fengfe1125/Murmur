package com.sakura.murmur

import android.graphics.Bitmap
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.takeWhile
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeout

/**
 * The Android counterpart of iOS `MurmurBubblePacing` (MurmurModels.swift).
 *
 * The server streams a whole reply the instant it is ready, which reads as a
 * machine emptying a buffer. Holding each bubble back by roughly the time it
 * would take somebody to type it — measured from when the previous one
 * landed, so real server latency counts towards the wait rather than adding
 * to it — is what gives the rhythm a person's shape.
 *
 * Character count is UTF-16 length rather than Swift's grapheme count; for
 * the Chinese text this product is written in the two agree.
 */
data class MurmurBubblePacing(
    val perCharacterSeconds: Double,
    val minimumSeconds: Double,
    val maximumSeconds: Double,
) {
    fun delaySecondsFor(text: String): Double {
        if (perCharacterSeconds <= 0.0) return 0.0
        return (text.length * perCharacterSeconds)
            .coerceIn(minimumSeconds, maximumSeconds)
    }

    companion object {
        val HUMAN = MurmurBubblePacing(perCharacterSeconds = 0.075, minimumSeconds = 0.7, maximumSeconds = 2.8)
        /** Tests want the transcript, not the theatre. */
        val INSTANT = MurmurBubblePacing(perCharacterSeconds = 0.0, minimumSeconds = 0.0, maximumSeconds = 0.0)
    }
}

/** One SSE bubble after its per-moment event identity has been scoped for a
 *  room that can span several moments. */
data class MurmurRoomBubble(val id: String, val text: String)

/**
 * The room's shared SSE consumer — the Kotlin counterpart of iOS
 * `MurmurRoomEventConsumer`. Both the live photo room and an archived-day
 * continuation consume the wire through this path, so pacing, duplicate
 * suppression and terminal handling cannot drift between them.
 *
 * Deliberately independent from `MurmurSessionModel`'s续传循环: a room
 * consumes one moment to its terminal event with no reconnect (SSE sequence
 * numbers restart per moment and a room spans several moments, so resume
 * bookkeeping belongs to the moment, not the room), while keeping the same
 * parsing and dedupe semantics — per-consume seen-ID suppression, empty
 * bubbles skipped, `failure` events thrown, a stream that ends without
 * `done` surfaces as a retryable `stream_ended`.
 */
class RoomEventConsumer(
    private val api: MurmurApiClient,
    private val pacing: MurmurBubblePacing,
) {
    suspend fun consume(
        momentID: String,
        onBubble: suspend (MurmurRoomBubble) -> Unit,
        onAngles: (List<String>) -> Unit = {},
    ) {
        var terminal = false
        var lastBubbleAt = System.nanoTime()
        val seenEventIDs = mutableSetOf<String>()
        // `takeWhile` is the loop's `break`: `done` flips `terminal`, the next
        // emission (or a still-open socket) cancels upstream, and we fall
        // through to the terminal check below.
        api.events(momentID, lastEventID = null)
            .takeWhile { !terminal }
            .collect { event ->
                val eventID = event.id
                if (eventID != null && !seenEventIDs.add(eventID)) return@collect
                when (event) {
                    is MurmurStreamEvent.Accepted ->
                        lastBubbleAt = System.nanoTime()
                    is MurmurStreamEvent.Bubble -> {
                        if (event.text.isEmpty()) return@collect
                        paceFor(event.text, since = lastBubbleAt)
                        onBubble(
                            MurmurRoomBubble(
                                id = bubbleID(momentID, eventID),
                                text = event.text,
                            ),
                        )
                        lastBubbleAt = System.nanoTime()
                    }
                    is MurmurStreamEvent.Angles ->
                        onAngles(event.texts.take(3))
                    is MurmurStreamEvent.Quiet -> Unit
                    is MurmurStreamEvent.Done -> terminal = true
                    is MurmurStreamEvent.Failure -> throw event.failure
                }
            }
        if (!terminal) {
            throw MurmurFailure("stream_ended", "回应中断了。", retryable = true)
        }
    }

    companion object {
        /**
         * SSE sequence numbers belong to one moment and restart on the next.
         * A room spans several moments, so a bare sequence would make later
         * replies reuse and overwrite the first bubble — scope it.
         */
        fun bubbleID(momentID: String, eventID: String?): String =
            "$momentID-${eventID ?: UUID.randomUUID()}"
    }

    private suspend fun paceFor(text: String, since: Long) {
        val targetMillis = pacing.delaySecondsFor(text) * 1_000
        if (targetMillis <= 0.0) return
        val elapsedMillis = (System.nanoTime() - since) / 1_000_000.0
        val remaining = targetMillis - elapsedMillis
        if (remaining <= 0.0) return
        kotlinx.coroutines.delay(remaining.toLong())
    }
}

/**
 * One photo, and the exchange about it — the Android counterpart of iOS
 * `PhotoRoomModel`.
 *
 * The room is not the conversation and does not share its model: no queue, no
 * resend offers, and `lines` ends with the screen. What it does keep is the
 * exchange — the photo and everything said about it are written into the
 * archive through [RoomRecorder] as they happen, filed under the day they
 * happened on. The conversation never sees any of it; the calendar is where
 * you find it again.
 *
 * The photo arrives as a [PhotoInput] and is staged once through
 * [PhotoLoader]; the staged file is the upload body and the copy the archive
 * adopts, and every path out of the room deletes it. The downsampled preview
 * stays in memory for the screen, the way iOS keeps the `UIImage`.
 */
class PhotoRoomModel(
    private val input: PhotoInput,
    private val api: MurmurApiClient,
    private val photoLoader: PhotoLoader,
    private val scope: CoroutineScope,
    private val uploadTimeoutSeconds: Double = 300.0,
    private val requestTimeoutSeconds: Double = 45.0,
    private val bubblePacing: MurmurBubblePacing = MurmurBubblePacing.HUMAN,
    private val recorder: RoomRecorder? = null,
) {
    enum class Phase {
        /** The photo is on the wire and the server has not answered yet. */
        Reading,
        /** The reading never landed. The room is not open: the only thing on
         *  offer is sending the same photo again. Typing into a room whose
         *  photo the server never saw would get an answer about nothing. */
        Unopened,
        /** Murmur has spoken; the field is live. */
        Listening,
        /** Something the person said is on the wire. */
        Sending,
    }

    enum class LineAuthor { Mine, Murmur }

    data class Line(
        val id: String,
        val author: LineAuthor,
        val text: String,
    )

    private val _lines = MutableStateFlow<List<Line>>(emptyList())
    val lines: StateFlow<List<Line>> = _lines.asStateFlow()

    /** The three openers under Murmur's guess. One tap lifts an opener into
     *  the field, where the person decides whether it leaves — they are doors
     *  into the next line, not messages of their own. Cleared the moment the
     *  person says anything, because by then they no longer need a way in. */
    private val _openers = MutableStateFlow<List<String>>(emptyList())
    val openers: StateFlow<List<String>> = _openers.asStateFlow()

    private val _phase = MutableStateFlow(Phase.Reading)
    val phase: StateFlow<Phase> = _phase.asStateFlow()

    private val _failure = MutableStateFlow<MurmurFailure?>(null)
    val failure: StateFlow<MurmurFailure?> = _failure.asStateFlow()

    val draft = MutableStateFlow("")

    /** The staged photo's downsampled preview; lives as long as the screen. */
    private val _photo = MutableStateFlow<Bitmap?>(null)
    val photo: StateFlow<Bitmap?> = _photo.asStateFlow()

    val canSend: Boolean
        get() = _phase.value == Phase.Listening && draft.value.trim().isNotEmpty()

    /** Whether 再试一次 is on offer, which it is for exactly one failure: the
     *  one that kept the room from opening. A line that did not land is put
     *  back in the field instead, where the send button is the retry. */
    val canRetryOpening: Boolean
        get() = _phase.value == Phase.Unopened

    /** True while Murmur owes an answer, which is what the typing dots read. */
    val isAwaitingReply: Boolean
        get() = _phase.value == Phase.Reading || _phase.value == Phase.Sending

    /** The scrollback row the photo went into, written once however many times
     *  再试一次 is pressed — the retry re-sends the same moment, not a second one. */
    private var photoRowID: String? = null
    /** Rows the room is holding open, keyed by the line on screen, so a line
     *  pulled back out of the room comes back out of the history with it. */
    private var rowForLine: Map<String, String> = emptyMap()
    /** Rows whose receipt has not landed yet — as far as this device knows,
     *  those words never left. A row leaves this the moment the server takes
     *  it, which is what keeps [close] from calling a sent line failed. */
    private var unsentRows: Set<String> = emptySet()
    /** The upload's temporary original. It exists from the moment the photo is
     *  staged until the reading is over or the room closes, and every one of
     *  those paths deletes it — see [finishOpening] and [close]. */
    private var attachment: PhotoAttachment? = null
    /** The opening upload's key, kept so that 再试一次 is a retry of the same
     *  moment rather than a second one. */
    private val openingKey = UUID.randomUUID().toString()
    private var turn: Job? = null
    private var closed = false

    // ---- Opening ------------------------------------------------------------

    /** Sends the photo up and waits for the server to read it. Safe to call
     *  again: 再试一次 lands here with the same idempotency key. */
    fun open() {
        if (closed) return
        turn?.cancel()
        _phase.value = Phase.Reading
        _failure.value = null
        turn = scope.launch {
            try {
                val photo = prepareAttachment()
                ensureActive()
                val receipt = withTimeout((uploadTimeoutSeconds * 1_000).toLong()) {
                    api.createMoment(
                        note = null,
                        photo = photo,
                        idempotencyKey = openingKey,
                        intent = "photo_reading",
                    )
                }
                // The photo goes into the history here, while its original is
                // still on disk to be copied from and before the first bubble
                // can land — the picture has to sit above the reading of it.
                recordPhoto(receipt.momentID, photo)
                consume(receipt.momentID)
                finishOpening()
            } catch (timeout: TimeoutCancellationException) {
                // A timed-out opening is an opening that did not land: the
                // room stays shut with 再试一次 on it, not spinning forever.
                fail(timeout)
            } catch (cancelled: CancellationException) {
                // Real cancellation (close, a newer turn): no state change.
            } catch (error: Throwable) {
                fail(error)
            }
        }
    }

    /** Stages the photo once. A retry after a network failure reuses the same
     *  file rather than writing a second copy of the same pixels. */
    private suspend fun prepareAttachment(): PhotoAttachment {
        attachment?.let { return it }
        val loaded = photoLoader.load(input)
        if (closed) {
            photoLoader.discard(loaded)
            throw CancellationException()
        }
        attachment = loaded
        _photo.value = loaded.preview
        return loaded
    }

    /** The reading is over, whichever way it went: the full-resolution
     *  original has nothing left to do and goes now, not when the screen
     *  closes. */
    private fun finishOpening() {
        _phase.value = Phase.Listening
        photoRowID?.let { recorder?.setDelivery(MurmurDeliveryState.answered, it) }
        discardAttachment()
    }

    // ---- Saying something ---------------------------------------------------

    fun send() {
        val text = draft.value.trim()
        if (text.isEmpty() || _phase.value != Phase.Listening || closed) return
        draft.value = ""
        // The way in has been used; the doors close behind it.
        _openers.value = emptyList()
        val line = Line(id = UUID.randomUUID().toString(), author = LineAuthor.Mine, text = text)
        _lines.update { it + line }
        _phase.value = Phase.Sending
        _failure.value = null
        turn?.cancel()
        val key = UUID.randomUUID().toString()
        turn = scope.launch {
            // On screen the instant it is said, in the history the same way the
            // composer does it — one tick when the server takes it.
            val row = MurmurMessage(author = MurmurMessageAuthor.you, text = text, delivery = MurmurDeliveryState.sending)
            rowForLine = rowForLine + (line.id to row.id)
            unsentRows = unsentRows + row.id
            recorder?.record(row, photoFile = null)
            try {
                val receipt = withTimeout((requestTimeoutSeconds * 1_000).toLong()) {
                    // No photo: the room's photo is already in Murmur's memory
                    // from the opening moment, so every line after it is an
                    // ordinary moment that lands in the same thread.
                    api.createMoment(note = text, photo = null, idempotencyKey = key)
                }
                unsentRows = unsentRows - row.id
                recorder?.setMomentID(receipt.momentID, row.id)
                recorder?.setDelivery(MurmurDeliveryState.sent, row.id)
                consume(receipt.momentID)
                recorder?.setDelivery(MurmurDeliveryState.answered, row.id)
                rowForLine = rowForLine - line.id
                _phase.value = Phase.Listening
            } catch (timeout: TimeoutCancellationException) {
                failLine(line, timeout)
            } catch (cancelled: CancellationException) {
                // Real cancellation (close, a newer send): `close` owns the row.
            } catch (error: Throwable) {
                failLine(line, error)
            }
        }
    }

    fun pick(opener: String) {
        draft.value = opener
    }

    // ---- The history --------------------------------------------------------

    /** Puts the room's photo into the archive. Called once the server has the
     *  moment and while the original is still on disk; 再试一次 lands here
     *  again and must not write a second copy of the picture. */
    private suspend fun recordPhoto(momentID: String, photo: PhotoAttachment) {
        if (closed || photoRowID != null || recorder == null) return
        // One tick now — the server has the photo. The second one waits for
        // the reading, the same as any other turn in the conversation.
        val row = MurmurMessage(
            author = MurmurMessageAuthor.you,
            text = "",
            delivery = MurmurDeliveryState.sent,
            momentID = momentID,
        )
        photoRowID = row.id
        recorder.record(row, photoFile = photo.file)
    }

    private suspend fun recordBubble(line: Line, momentID: String) {
        recorder?.record(
            MurmurMessage(
                id = line.id,
                author = MurmurMessageAuthor.murmur,
                text = line.text,
                momentID = momentID,
            ),
            photoFile = null,
        )
    }

    // ---- Leaving ------------------------------------------------------------

    /** Closing the room is a terminal path like any other, and it owns the
     *  same cleanup: the turn in flight is cancelled and the original goes
     *  with it. */
    fun close() {
        closed = true
        turn?.cancel()
        turn = null
        // Leaving mid-send: the receipt never landed, so as far as this device
        // knows the line never left. Same call the conversation makes when it
        // cancels its queue — a row stuck on a spinner forever is the worse lie.
        unsentRows.forEach { recorder?.setDelivery(MurmurDeliveryState.failed, it) }
        unsentRows = emptySet()
        rowForLine = emptyMap()
        discardAttachment()
    }

    private fun discardAttachment() {
        val old = attachment
        attachment = null
        if (old == null) return
        scope.launch { photoLoader.discard(old) }
    }

    // ---- The stream ---------------------------------------------------------

    private suspend fun consume(momentID: String) {
        RoomEventConsumer(api, bubblePacing).consume(
            momentID = momentID,
            onBubble = { bubble ->
                val line = Line(id = bubble.id, author = LineAuthor.Murmur, text = bubble.text)
                _lines.update { it + line }
                recordBubble(line, momentID)
            },
            onAngles = { texts -> _openers.value = texts },
        )
    }

    /** The reading did not land. The room stays shut, with one offer on it. */
    private fun fail(error: Throwable) {
        if (closed) return
        val mapped = MurmurFailure.from(error)
        _failure.value = mapped
        _phase.value = Phase.Unopened
        // A photo that could not be read still has a file behind it, and a
        // retryable failure is an offer to use it again. A permanent one is
        // not, so it goes now rather than waiting for the screen to close.
        if (!mapped.retryable) discardAttachment()
    }

    /** A line the person wrote did not land. Nothing was said, so nothing
     *  stays on screen claiming it was: the row comes back out and the words
     *  go back into the field, where the send button is the retry. Making
     *  them retype a sentence they already wrote is the worse failure. */
    private fun failLine(line: Line, error: Throwable) {
        if (closed) return
        val rowID = rowForLine[line.id]
        if (rowID != null) {
            rowForLine = rowForLine - line.id
            val wasUnsent = rowID in unsentRows
            unsentRows = unsentRows - rowID
            if (wasUnsent) {
                _lines.update { rows -> rows.filterNot { it.id == line.id } }
                // Only a line with no receipt comes back to the field. Once the
                // server accepted it, a later stream failure must not rewrite
                // history and claim those words never left.
                recorder?.withdraw(rowID)
                if (draft.value.isBlank()) draft.value = line.text
            }
        }
        _failure.value = MurmurFailure.from(error)
        _phase.value = Phase.Listening
    }
}
