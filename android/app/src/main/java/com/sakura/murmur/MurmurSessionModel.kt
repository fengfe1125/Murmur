package com.sakura.murmur

import android.os.Build
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
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
 * state. Deliberately absent on Android: the transcript store — the product
 * keeps nothing locally.
 */
class MurmurSessionModel(
    private val api: MurmurApiClient?,
    configurationFailure: MurmurFailure?,
    private val photoLoader: PhotoLoader,
    private val requestTimeoutSeconds: Double = 45.0,
    private val uploadTimeoutSeconds: Double = 300.0,
    private val deviceNameProvider: () -> String = {
        "${Build.MANUFACTURER} ${Build.MODEL} · Android ${Build.VERSION.RELEASE}"
    },
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

    init {
        if (configurationFailure == null) bootstrap()
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
        _uiState.update { it.copy(failure = null, phase = MurmurPhase.PreparingPhoto) }
    }

    fun failPhotoSelection() {
        if (_uiState.value.phase != MurmurPhase.PreparingPhoto) return
        _uiState.update {
            it.copy(
                failure = MurmurFailure("photo_unavailable", "没有读取到这张图片。", retryable = false),
                phase = MurmurPhase.Error,
            )
        }
    }

    fun preparePhoto(input: PhotoInput) {
        val phase = _uiState.value.phase
        if (phase == MurmurPhase.Uploading || phase == MurmurPhase.Responding) return
        photoJob?.cancel()
        _uiState.update { it.copy(phase = MurmurPhase.PreparingPhoto, failure = null) }
        photoJob = viewModelScope.launch {
            val loaded: PhotoAttachment
            try {
                loaded = photoLoader.load(input)
            } catch (cancelled: CancellationException) {
                discardInput(input)
                return@launch
            } catch (error: Throwable) {
                discardInput(input)
                _uiState.update { it.copy(failure = MurmurFailure.from(error), phase = MurmurPhase.Error) }
                return@launch
            }
            if (!coroutineContext.isActive) {
                photoLoader.discard(loaded)
                discardInput(input)
                return@launch
            }
            val old = _uiState.value.draftPhoto
            _uiState.update { it.copy(draftPhoto = loaded, phase = MurmurPhase.Ready) }
            photoLoader.discard(old)
        }
    }

    private suspend fun discardInput(input: PhotoInput) {
        if (input is PhotoInput.FromFile) photoLoader.discardFile(input.file)
    }

    fun removeDraftPhoto() {
        photoJob?.cancel()
        val old = _uiState.value.draftPhoto
        _uiState.update { state ->
            val phase = if (state.phase == MurmurPhase.Ready || state.phase == MurmurPhase.Error || state.phase == MurmurPhase.PreparingPhoto) {
                if (state.draftText.trim().isEmpty()) MurmurPhase.Idle else MurmurPhase.Ready
            } else {
                state.phase
            }
            state.copy(draftPhoto = null, phase = phase)
        }
        viewModelScope.launch { photoLoader.discard(old) }
    }

    fun updateDraftText(value: String) = _uiState.update { it.copy(draftText = value) }

    // ---- submit / retry / cancel ----------------------------------------------

    fun submit() {
        val state = _uiState.value
        if (!state.canSubmit) return
        val note = state.draftText.trim()
        val submission = Submission(
            note = note.ifEmpty { null },
            photo = state.draftPhoto,
            idempotencyKey = UUID.randomUUID().toString().lowercase(),
            replyToProactiveMomentID = if (note.isEmpty()) null else proactiveMomentID,
        )
        begin(submission = submission, replacingCurrent = true)
    }

    fun retry() {
        val state = _uiState.value
        if (state.phase != MurmurPhase.Error || state.failure?.retryable != true) return
        val submission = lastSubmission ?: return
        _uiState.update { it.copy(bubbles = emptyList(), move = null, scene = null, currentMomentID = null) }
        begin(submission = submission, replacingCurrent = false)
    }

    fun cancelCurrentOperation() {
        photoJob?.cancel()
        val cancelledTask = operationJob
        val original = lastSubmission?.photo
        val abandonedDraft = if (_uiState.value.phase == MurmurPhase.PreparingPhoto) {
            _uiState.value.draftPhoto
        } else {
            null
        }
        if (_uiState.value.phase == MurmurPhase.PreparingPhoto) {
            _uiState.update { it.copy(draftPhoto = null) }
        }
        cancelledTask?.cancel()
        operationJob = null
        lastSubmission = null
        val state = _uiState.value
        _uiState.update {
            it.copy(
                failure = null,
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
        operationJob?.cancel()
        val oldDraft = _uiState.value.draftPhoto
        val oldCurrent = _uiState.value.currentPhoto
        proactiveMomentID = null
        lastSubmission = null
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
                phase = MurmurPhase.Idle,
            )
        }
        viewModelScope.launch {
            photoLoader.discard(oldDraft)
            photoLoader.discard(oldCurrent)
        }
    }

    // ---- proactive ------------------------------------------------------------

    fun handleNotification(momentID: String) {
        viewModelScope.launch { refreshProactive(expectedMomentID = momentID) }
    }

    private suspend fun refreshProactive(expectedMomentID: String?) {
        try {
            val proactive = requireApi().currentProactive() ?: return
            if (expectedMomentID != null && proactive.momentID != expectedMomentID) return
            operationJob?.cancel()
            val oldCurrent = _uiState.value.currentPhoto
            proactiveMomentID = proactive.momentID
            _uiState.update {
                it.copy(
                    currentPhoto = null,
                    currentNote = "",
                    currentMomentID = proactive.momentID,
                    bubbles = proactive.resolvedBubbles.mapIndexed { index, text ->
                        MurmurBubble("${proactive.momentID}-$index", text)
                    },
                    move = proactive.move,
                    scene = proactive.scene,
                    phase = if (proactive.resolvedBubbles.isEmpty()) MurmurPhase.Quiet else MurmurPhase.Complete,
                    connection = MurmurConnectionState.Connected,
                )
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
                            is MurmurStreamEvent.Accepted ->
                                _uiState.update { it.copy(phase = MurmurPhase.Responding) }
                            is MurmurStreamEvent.Bubble ->
                                if (event.text.isNotEmpty()) {
                                    val id = eventID ?: UUID.randomUUID().toString()
                                    _uiState.update { it.copy(bubbles = it.bubbles + MurmurBubble(id, event.text)) }
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
        _uiState.update {
            it.copy(
                failure = mapped,
                requiresDeviceReconnect = mapped.requiresDeviceReconnect,
                connection = if (mapped.requiresDeviceReconnect) MurmurConnectionState.Offline(mapped.message) else it.connection,
                phase = MurmurPhase.Error,
            )
        }
    }

    private fun requireApi(): MurmurApiClient = api
        ?: throw MurmurFailure("not_configured", "尚未配置 Murmur 的 HTTPS 服务地址。", retryable = false)

    private val pushEnvironment: String
        get() = if (BuildConfig.ALLOW_DEVELOPMENT) "development" else "production"
}

/** A single send attempt: note + photo + the idempotency key that survives retries. */
private data class Submission(
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

internal enum class NotificationAuthorization {
    Unknown,
    Allowed,
    Denied,
    NotDetermined,
}
