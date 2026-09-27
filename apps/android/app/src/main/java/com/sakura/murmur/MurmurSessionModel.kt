package com.sakura.murmur

import android.graphics.Bitmap
import android.os.Build
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import java.time.LocalDate
import java.util.TimeZone
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeout

/**
 * Full port of `MurmurApp/MurmurSessionModel.swift`. The state machine is the
 * same: draft vs current, [Submission]-based retry with a stable idempotency
 * key, SSE resume with `Last-Event-ID` and seen-ID dedupe, quiet as a legal
 * state. On top of the kernel sits the local transcript: every outgoing row
 * lands in [transcriptStore] before anything is queued, replies append as
 * they stream, and failed rows carry their own mark and resend offer.
 */
class MurmurSessionModel(
    private val api: MurmurApiClient?,
    configurationFailure: MurmurFailure?,
    private val photoLoader: PhotoLoader,
    val transcriptStore: MurmurTranscriptStore,
    private val requestTimeoutSeconds: Double = 45.0,
    private val uploadTimeoutSeconds: Double = 300.0,
    private val deviceNameProvider: () -> String = {
        "${Build.MANUFACTURER} ${Build.MODEL} · Android ${Build.VERSION.RELEASE}"
    },
    /** What photo rooms record into and the 当年今日 calendar reads — the
     *  day-filed history beside the conversation (iOS `Murmur/archive`).
     *  Defaults to the `archive` directory next to [transcriptStore]. */
    val archive: MurmurArchive = MurmurArchive(
        MurmurTranscriptStore.archive(
            transcriptStore.directory.parentFile ?: transcriptStore.directory
        )
    ),
) : ViewModel() {

    data class UiState(
        val draftText: String = "",
        val draftPhoto: PhotoAttachment? = null,
        val currentPhoto: PhotoAttachment? = null,
        val currentNote: String = "",
        val bubbles: List<MurmurBubble> = emptyList(),
        val move: String? = null,
        val scene: String? = null,
        val currentMomentID: String? = null,
        val phase: MurmurPhase = MurmurPhase.Idle,
        val connection: MurmurConnectionState = MurmurConnectionState.Checking,
        val failure: MurmurFailure? = null,
        val identity: MurmurIdentity? = null,
        val preferences: MurmurPreferences = MurmurPreferences(),
        val devices: List<MurmurDevice> = emptyList(),
        val devicesLoaded: Boolean = false,
        val settingsMessage: String? = null,
        val notificationPromptRequested: Boolean = false,
        val requiresDeviceReconnect: Boolean = false,
        /** The scrollback the person reads, oldest first. Loaded from
         *  [MurmurSessionModel.transcriptStore] on a cold start and appended
         *  on every send and every streamed bubble. */
        val messages: List<MurmurMessage> = emptyList(),
        /** Why an outgoing row never landed, keyed by that row. In memory
         *  only — the verdict persists in the transcript, the wording does
         *  not. */
        val sendFailures: Map<String, MurmurSendFailure> = emptyMap(),
        /** A problem with what is still in the composer — a photo that could
         *  not be read. Kept apart from [sendFailures] because a draft has no
         *  transcript row to carry a mark. */
        val draftFailure: String? = null,
    ) {
        val canSubmit: Boolean
            get() = !phase.isBusy && (draftText.trim().isNotEmpty() || draftPhoto != null)

        val hasCurrentMoment: Boolean
            get() = currentMomentID != null || currentPhoto != null || currentNote.isNotEmpty() ||
                bubbles.isNotEmpty() || phase.isBusy || phase == MurmurPhase.Quiet
    }

    private val _uiState = MutableStateFlow(
        if (configurationFailure != null) {
            UiState(connection = MurmurConnectionState.Offline(configurationFailure.message))
        } else {
            UiState()
        },
    )
    val uiState: StateFlow<UiState> = _uiState.asStateFlow()

    private var operationJob: Job? = null
    private var photoJob: Job? = null
    private var lastSubmission: Submission? = null
    private var didBootstrap = false
    private var proactiveMomentID: String? = null
    private var pendingPushToken: String? = null
    private var hasPendingPushRegistration = false
    private var preferencesLoaded = false
    private var didRequestNotificationPrompt = false
    /** Index of the outgoing message whose ticks the running moment drives. */
    private var pendingMessageID: String? = null
    /** Failed sends that can still be tried again, keyed by the row they
     *  belong to.  A row's mark is only an offer while its submission is
     *  here: the same idempotency key and, for a photo, a temporary file that
     *  has not been swept up yet.  Everything else shows the mark and no
     *  offer, and [resend] rebuilds the submission from the transcript. */
    private val resendable = mutableMapOf<String, Submission>()
    /** The one photo-rebuilding resend in flight, if any. */
    private var rebuildJob: Job? = null

    init {
        viewModelScope.launch { loadTranscript() }
        if (configurationFailure == null) bootstrap()
    }

    // ---- transcript ------------------------------------------------------------

    /** Cold start: read the on-device history.  A row still marked sending
     *  was interrupted by a crash or a force quit and comes back failed — it
     *  never reached the server ([MurmurTranscriptStore.load] says so). */
    private suspend fun loadTranscript() {
        if (_uiState.value.messages.isNotEmpty()) return
        _uiState.update { it.copy(messages = transcriptStore.load()) }
    }

    /** What the settings screen's 「清空聊天记录」 calls.  The archive beside
     *  the conversation is a different history and stays untouched. */
    fun clearTranscript() {
        photoJob?.cancel()
        discardPendingPhotoInput()
        rebuildJob?.cancel()
        rebuildJob = null
        withdrawResendOffers()
        pendingMessageID = null
        _uiState.update { it.copy(messages = emptyList(), sendFailures = emptyMap()) }
        transcriptStore.clear()
    }

    private fun persistTranscript() {
        transcriptStore.save(_uiState.value.messages)
    }

    /** A room for one photo swiped up out of 当年今日 — the Android
     *  counterpart of iOS `MurmurSessionModel.makePhotoRoom(image:)`: the
     *  same authenticated client, photo loader and timeouts as every other
     *  upload, and rows recorded into [archive], never the conversation. */
    fun makePhotoRoom(image: Bitmap): PhotoRoomModel = PhotoRoomModel(
        input = PhotoInput.FromBitmap(image),
        api = requireApi(),
        photoLoader = photoLoader,
        scope = viewModelScope,
        uploadTimeoutSeconds = uploadTimeoutSeconds,
        requestTimeoutSeconds = requestTimeoutSeconds,
        bubblePacing = MurmurBubblePacing.HUMAN,
        recorder = archive,
    )

    /** One calendar day's continuation — iOS `makeArchiveDay(day:)`: same
     *  client, timeout and bubble rhythm as the photo room and the chat. */
    fun makeArchiveDay(day: LocalDate): ArchiveDayModel = ArchiveDayModel(
        day = day,
        archive = archive,
        api = requireApi(),
        scope = viewModelScope,
        requestTimeoutSeconds = requestTimeoutSeconds,
        bubblePacing = MurmurBubblePacing.HUMAN,
    )

    private fun appendMessage(message: MurmurMessage) {
        _uiState.update { it.copy(messages = it.messages + message) }
        persistTranscript()
    }

    private fun updateMessage(id: String, mutate: (MurmurMessage) -> MurmurMessage) {
        val index = _uiState.value.messages.indexOfFirst { it.id == id }
        if (index < 0) return
        _uiState.update { state ->
            state.copy(messages = state.messages.mapIndexed { i, m -> if (i == index) mutate(m) else m })
        }
        persistTranscript()
    }

    /** The running moment's outgoing row, if it is still in the transcript. */
    private fun updatePending(mutate: (MurmurMessage) -> MurmurMessage) {
        val id = pendingMessageID ?: return
        updateMessage(id, mutate)
    }

    /** Drop every held submission and delete the temporary originals they
     *  were holding.  The marks stay, and so do their offers: what a resend
     *  needs is in the transcript — the words, its own copy of the photo,
     *  and the key — so losing the in-memory submission no longer costs the
     *  person the send. */
    private fun withdrawResendOffers() {
        if (resendable.isEmpty()) return
        val photos = resendable.values.mapNotNull { it.photo }
        resendable.clear()
        viewModelScope.launch {
            for (photo in photos) photoLoader.discard(photo)
        }
    }

    /** Send one failed row again.
     *
     *  The row itself is the handle, not "the last error": by the time
     *  somebody reaches for the mark they may have sent two more lines, and
     *  the one they pressed is the one that has to go.  The submission keeps
     *  its original idempotency key, so a moment the server did accept before
     *  the wire broke is picked back up rather than said twice.
     *
     *  A submission still in memory is used as it stands.  Otherwise the row
     *  itself is enough to build one: the transcript holds the words, its own
     *  copy of the photo, and the key the send went up under. */
    fun resend(messageID: String) {
        if (_uiState.value.phase.isBusy) return
        _uiState.update { it.copy(sendFailures = it.sendFailures - messageID) }
        updateMessage(messageID) { it.copy(delivery = MurmurDeliveryState.sending) }
        resendable.remove(messageID)?.let { standing ->
            enqueueResend(standing)
            return
        }
        val row = _uiState.value.messages.firstOrNull { it.id == messageID } ?: return
        // Re-reading the photo is I/O, so the row spins from the moment the
        // button is pressed rather than after the file comes back.
        rebuildJob?.cancel()
        rebuildJob = viewModelScope.launch {
            val photo = reloadPhoto(row)
            rebuildJob = null
            val note = row.text.ifEmpty { null }
            if (note == null && photo == null) {
                // Nothing left to send: the picture this row carried is gone
                // from transcript storage and there were never any words.
                updateMessage(messageID) { it.copy(delivery = MurmurDeliveryState.failed) }
                _uiState.update {
                    it.copy(sendFailures = it.sendFailures + (messageID to MurmurSendFailure("这条的内容已经不在了。", canResend = false)))
                }
                return@launch
            }
            enqueueResend(
                Submission(
                    messageID = messageID,
                    note = note,
                    photo = photo,
                    idempotencyKey = row.idempotencyKey ?: UUID.randomUUID().toString().lowercase(),
                    replyToProactiveMomentID = null,
                ),
            )
        }
    }

    private fun enqueueResend(submission: Submission) {
        // A reply already in flight is swept the way retry() has always done.
        _uiState.update { it.copy(bubbles = emptyList(), move = null, scene = null, currentMomentID = null) }
        begin(submission = submission, replacingCurrent = false)
    }

    /** Copies a row's photo back out of transcript storage into a temporary
     *  original the uploader can use.  The transcript keeps its own copy, so
     *  the send's cleanup deletes only the temporary one. */
    private suspend fun reloadPhoto(row: MurmurMessage): PhotoAttachment? {
        val name = row.imageFile ?: return null
        val stored = transcriptStore.imageFile(name)
        if (!stored.exists()) return null
        return try {
            photoLoader.load(PhotoInput.FromFile(stored))
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (error: Throwable) {
            null
        }
    }

    // ---- lifecycle -----------------------------------------------------------

    fun bootstrap() {
        if (didBootstrap) return
        didBootstrap = true
        viewModelScope.launch {
            photoLoader.cleanupStaleFiles()
            _uiState.update { it.copy(connection = MurmurConnectionState.Checking) }
            try {
                val identity = requireApi().storedIdentity()
                if (identity == null) {
                    _uiState.update {
                        it.copy(requiresDeviceReconnect = false, connection = MurmurConnectionState.NeedsEnrollment)
                    }
                    return@launch
                }
                // Identity lands before the device round-trip: a failure there
                // must still show who we are (iOS sets `identity` first too).
                _uiState.update { it.copy(identity = identity) }
                val devices = withTimeout((requestTimeoutSeconds * 1_000).toLong()) { requireApi().devices() }
                _uiState.update {
                    it.copy(devices = devices, devicesLoaded = true, connection = MurmurConnectionState.Connected)
                }
                try {
                    val preferences = withTimeout((requestTimeoutSeconds * 1_000).toLong()) { requireApi().preferences() }
                    preferencesLoaded = true
                    _uiState.update { it.copy(preferences = preferences) }
                } catch (timeout: TimeoutCancellationException) {
                    _uiState.update { it.copy(settingsMessage = MurmurFailure.from(timeout).message) }
                } catch (cancelled: CancellationException) {
                    throw cancelled
                } catch (error: Throwable) {
                    _uiState.update { it.copy(settingsMessage = MurmurFailure.from(error).message) }
                }
                if (hasPendingPushRegistration) syncDevice(pendingPushToken)
            } catch (timeout: TimeoutCancellationException) {
                recordBootstrapFailure(timeout)
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (error: Throwable) {
                recordBootstrapFailure(error)
            }
        }
    }

    private fun recordBootstrapFailure(error: Throwable) {
        val mapped = MurmurFailure.from(error)
        _uiState.update {
            it.copy(
                requiresDeviceReconnect = mapped.requiresDeviceReconnect,
                connection = MurmurConnectionState.Offline(mapped.message),
            )
        }
    }

    fun enroll(inviteCode: String) {
        val code = inviteCode.trim()
        if (code.isEmpty() || _uiState.value.phase.isBusy) return
        viewModelScope.launch {
            _uiState.update { it.copy(phase = MurmurPhase.Uploading, failure = null) }
            try {
                val identity = withTimeout((requestTimeoutSeconds * 1_000).toLong()) {
                    requireApi().enroll(inviteCode = code, deviceName = deviceNameProvider())
                }
                _uiState.update {
                    it.copy(
                        identity = identity,
                        connection = MurmurConnectionState.Connected,
                        requiresDeviceReconnect = false,
                        phase = MurmurPhase.Idle,
                    )
                }
                if (hasPendingPushRegistration) syncDevice(pendingPushToken)
            } catch (timeout: TimeoutCancellationException) {
                recordEnrollmentFailure(timeout)
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (error: Throwable) {
                recordEnrollmentFailure(error)
            }
        }
    }

    private fun recordEnrollmentFailure(error: Throwable) {
        val mapped = MurmurFailure.from(error)
        _uiState.update {
            it.copy(
                failure = mapped,
                connection = MurmurConnectionState.NeedsEnrollment,
                phase = MurmurPhase.Error,
            )
        }
    }

    // ---- photo staging -------------------------------------------------------

    fun beginPhotoSelection() {
        val phase = _uiState.value.phase
        if (phase == MurmurPhase.Uploading || phase == MurmurPhase.Responding) return
        photoJob?.cancel()
        discardPendingPhotoInput()
        _uiState.update { it.copy(failure = null, draftFailure = null, phase = MurmurPhase.PreparingPhoto) }
    }

    fun failPhotoSelection() {
        if (_uiState.value.phase != MurmurPhase.PreparingPhoto) return
        _uiState.update {
            it.copy(
                failure = MurmurFailure("photo_unavailable", "没有读取到这张图片。", retryable = false),
                draftFailure = "没有读取到这张图片。",
                phase = MurmurPhase.Error,
            )
        }
    }

    fun preparePhoto(input: PhotoInput) {
        val phase = _uiState.value.phase
        if (phase == MurmurPhase.Uploading || phase == MurmurPhase.Responding) return
        photoJob?.cancel()
        _uiState.update { it.copy(phase = MurmurPhase.PreparingPhoto, failure = null) }
        pendingPhotoInput = input
        photoJob = viewModelScope.launch {
            val loaded: PhotoAttachment
            try {
                loaded = photoLoader.load(input)
            } catch (cancelled: CancellationException) {
                discardPhotoInput(input)
                return@launch
            } catch (error: Throwable) {
                discardPhotoInput(input)
                _uiState.update {
                    it.copy(
                        failure = MurmurFailure.from(error),
                        draftFailure = MurmurFailure.from(error).message,
                        phase = MurmurPhase.Error,
                    )
                }
                return@launch
            }
            if (!coroutineContext.isActive) {
                photoLoader.discard(loaded)
                discardPhotoInput(input)
                return@launch
            }
            pendingPhotoInput = null
            val old = _uiState.value.draftPhoto
            _uiState.update { it.copy(draftPhoto = loaded, phase = MurmurPhase.Ready) }
            photoLoader.discard(old)
        }
    }

    /** The input a cancelled prepare was reading, if it never got to clean up
     *  after itself.  A cancel can land before the load coroutine ever runs,
     *  and its catch block would never fire — the picked file still has to go. */
    private var pendingPhotoInput: PhotoInput? = null

    /** Discards whatever input is still being staged, whatever state the
     *  staging coroutine is in. */
    private fun discardPendingPhotoInput() {
        val input = pendingPhotoInput ?: return
        pendingPhotoInput = null
        viewModelScope.launch { discardInput(input) }
    }

    /** The load coroutine's own cleanup: discards the input unless a newer
     *  selection has replaced it in the meantime. */
    private fun discardPhotoInput(input: PhotoInput) {
        if (pendingPhotoInput != input) return
        pendingPhotoInput = null
        viewModelScope.launch { discardInput(input) }
    }

    private suspend fun discardInput(input: PhotoInput) {
        if (input is PhotoInput.FromFile) photoLoader.discardFile(input.file)
    }

    fun removeDraftPhoto() {
        photoJob?.cancel()
        discardPendingPhotoInput()
        val old = _uiState.value.draftPhoto
        _uiState.update { state ->
            val phase = if (state.phase == MurmurPhase.Ready || state.phase == MurmurPhase.Error || state.phase == MurmurPhase.PreparingPhoto) {
                if (state.draftText.trim().isEmpty()) MurmurPhase.Idle else MurmurPhase.Ready
            } else {
                state.phase
            }
            state.copy(draftPhoto = null, draftFailure = null, phase = phase)
        }
        viewModelScope.launch { photoLoader.discard(old) }
    }

    fun updateDraftText(value: String) = _uiState.update { it.copy(draftText = value) }

    // ---- submit / retry / cancel ----------------------------------------------

    fun submit() {
        val state = _uiState.value
        if (!state.canSubmit) return
        val note = state.draftText.trim()
        // The outgoing turn joins the transcript before anything is queued, so
        // the bubble is on screen the instant the send button is pressed.
        val key = UUID.randomUUID().toString().lowercase()
        val outgoing = MurmurMessage(
            author = MurmurMessageAuthor.you,
            text = note,
            delivery = MurmurDeliveryState.sending,
            idempotencyKey = key,
        )
        val photo = state.draftPhoto
        val submission = Submission(
            messageID = outgoing.id,
            note = note.ifEmpty { null },
            photo = photo,
            idempotencyKey = key,
            replyToProactiveMomentID = if (note.isEmpty()) null else proactiveMomentID,
        )
        appendMessage(outgoing)
        if (photo != null) {
            // The adoption is synchronous on purpose: it never suspends, so it
            // lands before the upload can finish and delete the temporary
            // original this copy reads from (iOS races a Task here and has the
            // discard wait on it — same guarantee, no bookkeeping).
            val name = transcriptStore.adoptImage(photo.file, outgoing.id)
            if (name != null) updateMessage(outgoing.id) { it.copy(imageFile = name) }
        }
        _uiState.update { it.copy(draftFailure = null) }
        begin(submission = submission, replacingCurrent = true)
    }

    fun retry() {
        val state = _uiState.value
        if (state.phase != MurmurPhase.Error || state.failure?.retryable != true) return
        val submission = lastSubmission ?: return
        // The same row goes around again: it spins from here, and the old
        // verdict no longer describes it.
        updateMessage(submission.messageID) { it.copy(delivery = MurmurDeliveryState.sending) }
        _uiState.update { it.copy(bubbles = emptyList(), move = null, scene = null, currentMomentID = null, sendFailures = it.sendFailures - submission.messageID) }
        begin(submission = submission, replacingCurrent = false)
    }

    fun cancelCurrentOperation() {
        photoJob?.cancel()
        discardPendingPhotoInput()
        val original = lastSubmission?.photo
        val abandonedDraft = if (_uiState.value.phase == MurmurPhase.PreparingPhoto) {
            _uiState.value.draftPhoto
        } else {
            null
        }
        if (_uiState.value.phase == MurmurPhase.PreparingPhoto) {
            _uiState.update { it.copy(draftPhoto = null) }
        }
        cancelPump()
        withdrawResendOffers()
        val state = _uiState.value
        _uiState.update {
            it.copy(
                failure = null,
                draftFailure = null,
                phase = if (state.currentMomentID == null && state.currentNote.isEmpty() && state.currentPhoto == null) {
                    MurmurPhase.Idle
                } else {
                    MurmurPhase.Ready
                },
            )
        }
        viewModelScope.launch {
            photoLoader.discard(original)
            photoLoader.discard(abandonedDraft)
        }
    }

    fun clearCurrent() {
        photoJob?.cancel()
        discardPendingPhotoInput()
        cancelPump()
        withdrawResendOffers()
        val oldDraft = _uiState.value.draftPhoto
        val oldCurrent = _uiState.value.currentPhoto
        proactiveMomentID = null
        _uiState.update {
            it.copy(
                draftText = "",
                draftPhoto = null,
                currentPhoto = null,
                currentNote = "",
                bubbles = emptyList(),
                move = null,
                scene = null,
                currentMomentID = null,
                failure = null,
                draftFailure = null,
                phase = MurmurPhase.Idle,
            )
        }
        viewModelScope.launch {
            photoLoader.discard(oldDraft)
            photoLoader.discard(oldCurrent)
        }
    }

    /** Stops the line and tells the truth about the turns that never left: a
     *  row stuck on a spinner forever is worse than a row marked failed.  A
     *  row whose moment already completed keeps its ticks — cancelling an
     *  empty composer must not rewrite history. */
    private fun cancelPump() {
        operationJob?.cancel()
        operationJob = null
        val stranded = lastSubmission
        lastSubmission = null
        pendingMessageID = null
        if (stranded == null) return
        val row = _uiState.value.messages.firstOrNull { it.id == stranded.messageID }
        if (row != null && row.delivery == MurmurDeliveryState.sending) {
            updateMessage(stranded.messageID) { it.copy(delivery = MurmurDeliveryState.failed) }
            // Cancelled on purpose, and no offer to say it again: the photo
            // the turn carried is being thrown away in the same breath, so a
            // mark that promised a resend would be promising an upload with
            // nothing left to upload.
            _uiState.update {
                it.copy(sendFailures = it.sendFailures + (stranded.messageID to MurmurSendFailure("已取消发送。", canResend = false)))
            }
        }
        resendable.remove(stranded.messageID)
    }

    // ---- proactive ------------------------------------------------------------

    fun handleNotification(momentID: String) {
        viewModelScope.launch { refreshProactive(expectedMomentID = momentID) }
    }

    private suspend fun refreshProactive(expectedMomentID: String?) {
        try {
            val proactive = requireApi().currentProactive() ?: return
            if (expectedMomentID != null && proactive.momentID != expectedMomentID) return
            cancelPump()
            val oldCurrent = _uiState.value.currentPhoto
            proactiveMomentID = proactive.momentID
            val bubbles = proactive.resolvedBubbles.mapIndexed { index, text ->
                MurmurBubble("${proactive.momentID}-$index", text)
            }
            _uiState.update {
                it.copy(
                    currentPhoto = null,
                    currentNote = "",
                    currentMomentID = proactive.momentID,
                    bubbles = bubbles,
                    move = proactive.move,
                    scene = proactive.scene,
                    phase = if (proactive.resolvedBubbles.isEmpty()) MurmurPhase.Quiet else MurmurPhase.Complete,
                    connection = MurmurConnectionState.Connected,
                )
            }
            // A message Murmur sent on its own belongs in the scrollback like
            // any other; without this it only ever existed in the notification.
            for (bubble in bubbles) {
                if (_uiState.value.messages.none { it.id == bubble.id }) {
                    appendMessage(
                        MurmurMessage(
                            id = bubble.id,
                            author = MurmurMessageAuthor.murmur,
                            text = bubble.text,
                            momentID = proactive.momentID,
                        ),
                    )
                }
            }
            photoLoader.discard(oldCurrent)
            requireApi().acknowledge(proactive.momentID, null)
        } catch (timeout: TimeoutCancellationException) {
            recordProactiveFailure(expectedMomentID, timeout)
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (error: Throwable) {
            recordProactiveFailure(expectedMomentID, error)
        }
    }

    private fun recordProactiveFailure(expectedMomentID: String?, error: Throwable) {
        if (expectedMomentID == null) return
        val mapped = MurmurFailure.from(error)
        _uiState.update {
            it.copy(
                failure = mapped,
                requiresDeviceReconnect = it.requiresDeviceReconnect || mapped.requiresDeviceReconnect,
                connection = if (mapped.requiresDeviceReconnect) MurmurConnectionState.Offline(mapped.message) else it.connection,
                phase = MurmurPhase.Error,
            )
        }
    }

    // ---- push / device / preferences -------------------------------------------

    fun updatePushRegistration(token: String?) {
        pendingPushToken = token
        hasPendingPushRegistration = true
        if (_uiState.value.identity == null) return
        viewModelScope.launch { syncDevice(token) }
    }

    private suspend fun syncDevice(token: String?) {
        try {
            requireApi().updateDevice(
                pushToken = token,
                environment = pushEnvironment,
                timezone = TimeZone.getDefault().id,
                deviceName = deviceNameProvider(),
            )
            _uiState.update { it.copy(connection = MurmurConnectionState.Connected) }
        } catch (timeout: TimeoutCancellationException) {
            recordSettingsFailure(timeout)
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (error: Throwable) {
            recordSettingsFailure(error)
        }
    }

    fun loadPreferences() {
        viewModelScope.launch {
            _uiState.update { it.copy(settingsMessage = null) }
            try {
                val preferences = requireApi().preferences()
                preferencesLoaded = true
                _uiState.update { it.copy(preferences = preferences) }
            } catch (timeout: TimeoutCancellationException) {
                recordSettingsFailure(timeout)
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (error: Throwable) {
                recordSettingsFailure(error)
            }
        }
    }

    fun updatePreferencesField(transform: (MurmurPreferences) -> MurmurPreferences) {
        _uiState.update { it.copy(preferences = transform(it.preferences)) }
    }

    fun savePreferences() {
        viewModelScope.launch {
            _uiState.update { it.copy(settingsMessage = null) }
            if (!preferencesLoaded) {
                _uiState.update { it.copy(settingsMessage = "还没有读到当前设置。") }
                return@launch
            }
            try {
                requireApi().updatePreferences(_uiState.value.preferences)
                _uiState.update { it.copy(settingsMessage = "已保存") }
            } catch (timeout: TimeoutCancellationException) {
                recordSettingsFailure(timeout)
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (error: Throwable) {
                recordSettingsFailure(error)
            }
        }
    }

    fun refreshDevices() {
        viewModelScope.launch {
            _uiState.update { it.copy(settingsMessage = null) }
            try {
                val devices = requireApi().devices()
                _uiState.update { it.copy(devices = devices, devicesLoaded = true) }
            } catch (timeout: TimeoutCancellationException) {
                _uiState.update { it.copy(devicesLoaded = true) }
                recordSettingsFailure(timeout)
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (error: Throwable) {
                _uiState.update { it.copy(devicesLoaded = true) }
                recordSettingsFailure(error)
            }
        }
    }

    fun removeDevice(device: MurmurDevice) {
        viewModelScope.launch {
            _uiState.update { it.copy(settingsMessage = null) }
            try {
                requireApi().removeDevice(device.id)
                _uiState.update { state -> state.copy(devices = state.devices.filterNot { it.id == device.id }) }
                if (device.id == _uiState.value.identity?.deviceID) {
                    _uiState.update { it.copy(identity = null, connection = MurmurConnectionState.NeedsEnrollment) }
                    clearCurrent()
                }
            } catch (timeout: TimeoutCancellationException) {
                recordSettingsFailure(timeout)
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (error: Throwable) {
                recordSettingsFailure(error)
            }
        }
    }

    fun deleteAccount() {
        viewModelScope.launch {
            _uiState.update { it.copy(settingsMessage = null) }
            try {
                requireApi().deleteAccount()
                _uiState.update {
                    it.copy(identity = null, devices = emptyList(), connection = MurmurConnectionState.NeedsEnrollment)
                }
                clearCurrent()
            } catch (timeout: TimeoutCancellationException) {
                recordSettingsFailure(timeout)
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (error: Throwable) {
                recordSettingsFailure(error)
            }
        }
    }

    fun resetLocalDeviceIdentity() {
        viewModelScope.launch {
            _uiState.update { it.copy(settingsMessage = null) }
            try {
                requireApi().resetLocalIdentity()
                _uiState.update {
                    it.copy(
                        identity = null,
                        devices = emptyList(),
                        requiresDeviceReconnect = false,
                        connection = MurmurConnectionState.NeedsEnrollment,
                    )
                }
                clearCurrent()
            } catch (timeout: TimeoutCancellationException) {
                _uiState.update { it.copy(settingsMessage = MurmurFailure.from(timeout).message) }
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (error: Throwable) {
                _uiState.update { it.copy(settingsMessage = MurmurFailure.from(error).message) }
            }
        }
    }

    fun consumeNotificationPromptRequest() {
        _uiState.update { it.copy(notificationPromptRequested = false) }
    }

    private fun recordSettingsFailure(error: Throwable) {
        val mapped = MurmurFailure.from(error)
        _uiState.update {
            it.copy(
                settingsMessage = mapped.message,
                requiresDeviceReconnect = it.requiresDeviceReconnect || mapped.requiresDeviceReconnect,
                connection = if (mapped.requiresDeviceReconnect) MurmurConnectionState.Offline(mapped.message) else it.connection,
            )
        }
    }

    // ---- internals -------------------------------------------------------------

    private fun begin(submission: Submission, replacingCurrent: Boolean) {
        if (_uiState.value.phase.isBusy) return
        operationJob?.cancel()
        if (replacingCurrent) {
            val oldCurrent = _uiState.value.currentPhoto
            proactiveMomentID = null
            _uiState.update {
                it.copy(
                    currentPhoto = submission.photo,
                    currentNote = submission.note ?: "",
                    currentMomentID = null,
                    bubbles = emptyList(),
                    move = null,
                    scene = null,
                    draftText = "",
                    draftPhoto = null,
                    failure = null,
                )
            }
            if (oldCurrent?.id != submission.photo?.id) {
                viewModelScope.launch { photoLoader.discard(oldCurrent) }
            }
        }
        lastSubmission = submission
        pendingMessageID = submission.messageID
        _uiState.update { it.copy(phase = MurmurPhase.Uploading) }
        operationJob = viewModelScope.launch { run(submission) }
    }

    private suspend fun run(submission: Submission) {
        val client = requireApi()
        try {
            if (submission.replyToProactiveMomentID != null && submission.note != null) {
                withTimeout((requestTimeoutSeconds * 1_000).toLong()) {
                    client.acknowledge(submission.replyToProactiveMomentID, submission.note)
                }
            }
            val receipt = withTimeout((uploadTimeoutSeconds * 1_000).toLong()) {
                client.createMoment(
                    note = submission.note,
                    photo = submission.photo,
                    idempotencyKey = submission.idempotencyKey,
                )
            }
            _uiState.update {
                it.copy(currentMomentID = receipt.momentID, connection = MurmurConnectionState.Connected, phase = MurmurPhase.Responding)
            }
            // One tick: the server has the moment.
            updatePending { it.copy(delivery = MurmurDeliveryState.sent, momentID = receipt.momentID) }

            var lastEventID: String? = null
            var retries = 0
            var terminal = false
            var wasQuiet = false
            val seenEventIDs = mutableSetOf<String>()
            while (!terminal) {
                val stream = client.events(receipt.momentID, lastEventID)
                try {
                    stream.collect { event ->
                        val eventID = event.id
                        if (eventID != null) {
                            if (!seenEventIDs.add(eventID)) return@collect
                            lastEventID = eventID
                        }
                        when (event) {
                            // Photo-room openers; the chat session ignores them
                            // (the photo room's shared consumer handles angles).
                            is MurmurStreamEvent.Angles -> Unit
                            is MurmurStreamEvent.Accepted -> {
                                _uiState.update { it.copy(phase = MurmurPhase.Responding) }
                                // Two ticks: Murmur has started composing.
                                updatePending { it.copy(delivery = MurmurDeliveryState.answered) }
                            }
                            is MurmurStreamEvent.Bubble ->
                                if (event.text.isNotEmpty()) {
                                    val id = eventID ?: UUID.randomUUID().toString()
                                    _uiState.update { it.copy(bubbles = it.bubbles + MurmurBubble(id, event.text)) }
                                    appendMessage(
                                        MurmurMessage(
                                            id = "${receipt.momentID}-$id",
                                            author = MurmurMessageAuthor.murmur,
                                            text = event.text,
                                            momentID = receipt.momentID,
                                        ),
                                    )
                                }
                            is MurmurStreamEvent.Quiet -> {
                                wasQuiet = true
                                _uiState.update { it.copy(phase = MurmurPhase.Quiet) }
                            }
                            is MurmurStreamEvent.Done -> {
                                _uiState.update { state ->
                                    state.copy(
                                        move = event.move,
                                        scene = event.scene,
                                        phase = if (wasQuiet && state.bubbles.isEmpty()) MurmurPhase.Quiet else MurmurPhase.Complete,
                                    )
                                }
                                terminal = true
                            }
                            is MurmurStreamEvent.Failure -> throw event.failure
                        }
                    }
                    // The stream ended without `done`: that is an interruption,
                    // never a completed moment (T0.1).
                    if (!terminal) {
                        throw MurmurFailure("stream_ended", "回应中断了。", retryable = true)
                    }
                } catch (cancelled: CancellationException) {
                    throw cancelled
                } catch (error: Throwable) {
                    if (retries >= 2) throw error
                    retries += 1
                    delay(350L * retries)
                }
            }

            photoLoader.discard(submission.photo)
            pendingMessageID = null
            if (_uiState.value.bubbles.isNotEmpty() && !didRequestNotificationPrompt) {
                didRequestNotificationPrompt = true
                _uiState.update { it.copy(notificationPromptRequested = true) }
            }
        } catch (cancelled: CancellationException) {
            if (cancelled is TimeoutCancellationException) {
                recordRunFailure(submission, cancelled)
            } else {
                throw cancelled
            }
        } catch (error: Throwable) {
            recordRunFailure(submission, error)
        }
    }

    private suspend fun recordRunFailure(submission: Submission, error: Throwable) {
        val mapped = MurmurFailure.from(error)
        if (!mapped.retryable) {
            photoLoader.discard(submission.photo)
            lastSubmission = null
        }
        // The row carries its own verdict from here on.  It is only an offer
        // while the submission survives with it — a turn that cannot be
        // retried has its photo swept up just above.
        updatePending { it.copy(delivery = MurmurDeliveryState.failed) }
        pendingMessageID = null
        _uiState.update {
            it.copy(
                failure = mapped,
                sendFailures = it.sendFailures + (submission.messageID to MurmurSendFailure(mapped.message, canResend = mapped.retryable)),
                requiresDeviceReconnect = mapped.requiresDeviceReconnect,
                connection = if (mapped.requiresDeviceReconnect) MurmurConnectionState.Offline(mapped.message) else it.connection,
                phase = MurmurPhase.Error,
            )
        }
        if (mapped.retryable) resendable[submission.messageID] = submission
    }

    private fun requireApi(): MurmurApiClient = api
        ?: throw MurmurFailure("not_configured", "尚未配置 Murmur 的 HTTPS 服务地址。", retryable = false)

    private val pushEnvironment: String
        get() = if (BuildConfig.ALLOW_DEVELOPMENT) "development" else "production"
}

/** A single send attempt: note + photo + the idempotency key that survives retries. */
private data class Submission(
    /** The transcript row this send owns, so a retry re-uses the bubble the
     *  person already saw instead of saying the same thing twice. */
    val messageID: String,
    val note: String?,
    val photo: PhotoAttachment?,
    val idempotencyKey: String,
    val replyToProactiveMomentID: String?,
)

/**
 * When a push token is worth syncing — the pure counterpart of iOS
 * `MurmurNotificationBridge.shouldSyncToken`: an allowed device uploads its
 * token; denied / not-determined uploads `null` so the server stops pushing;
 * unknown means the permission state is not known yet, so hold off.
 */
internal object MurmurNotificationBridge {
    fun shouldSyncToken(token: String?, authorization: NotificationAuthorization): Boolean = when {
        token != null && authorization == NotificationAuthorization.Allowed -> true
        token == null && (authorization == NotificationAuthorization.Denied || authorization == NotificationAuthorization.NotDetermined) -> true
        else -> false
    }
}

enum class NotificationAuthorization {
    Unknown,
    Allowed,
    Denied,
    NotDetermined,
}
