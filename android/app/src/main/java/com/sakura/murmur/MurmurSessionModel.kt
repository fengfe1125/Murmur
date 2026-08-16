package com.sakura.murmur

import android.content.Context
import android.graphics.BitmapFactory
import android.net.Uri
import android.os.Build
import android.provider.OpenableColumns
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.util.UUID

/**
 * Phase 0 session: enrollment + single-moment send/stream. The full state
 * machine (cancel, proactive, idempotent reorder matrix) lands in Phase 1,
 * ported from MurmurApp/MurmurSessionModel.swift.
 */
class MurmurSessionModel(
    private val api: MurmurApiClient?,
    configurationFailure: MurmurFailure?,
) : ViewModel() {

    data class UiState(
        val connection: MurmurConnectionState = MurmurConnectionState.Checking,
        val phase: MurmurPhase = MurmurPhase.Idle,
        val inviteCode: String = "",
        val note: String = "",
        val photo: PhotoAttachment? = null,
        val bubbles: List<MurmurBubble> = emptyList(),
        val failure: MurmurFailure? = null,
    )

    private val _uiState = MutableStateFlow(
        if (configurationFailure != null) {
            UiState(connection = MurmurConnectionState.Offline(configurationFailure.message))
        } else {
            UiState()
        },
    )
    val uiState: StateFlow<UiState> = _uiState.asStateFlow()

    private var streamJob: Job? = null
    private var idempotencyKey: String = UUID.randomUUID().toString()

    init {
        if (configurationFailure == null) bootstrap()
    }

    fun bootstrap() {
        val api = api ?: return
        viewModelScope.launch {
            _uiState.update { it.copy(connection = MurmurConnectionState.Checking) }
            try {
                val identity = api.storedIdentity()
                _uiState.update {
                    it.copy(
                        connection = if (identity != null) {
                            MurmurConnectionState.Connected
                        } else {
                            MurmurConnectionState.NeedsEnrollment
                        },
                        phase = if (identity != null) MurmurPhase.Ready else MurmurPhase.Idle,
                    )
                }
            } catch (failure: MurmurFailure) {
                _uiState.update { it.copy(connection = MurmurConnectionState.Offline(failure.message)) }
            }
        }
    }

    fun updateInviteCode(value: String) = _uiState.update { it.copy(inviteCode = value) }

    fun updateNote(value: String) = _uiState.update { it.copy(note = value) }

    fun enroll() {
        val api = api ?: return
        val inviteCode = _uiState.value.inviteCode.trim()
        if (inviteCode.isEmpty()) return
        viewModelScope.launch {
            _uiState.update { it.copy(connection = MurmurConnectionState.Checking, failure = null) }
            try {
                api.enroll(inviteCode, deviceName = Build.MODEL ?: "Android")
                _uiState.update {
                    it.copy(connection = MurmurConnectionState.Connected, phase = MurmurPhase.Ready)
                }
            } catch (failure: MurmurFailure) {
                _uiState.update {
                    it.copy(connection = MurmurConnectionState.NeedsEnrollment, failure = failure)
                }
            }
        }
    }

    /** Copies the picked image into a private cache file; the Uri is not kept. */
    fun attachPhoto(context: Context, uri: Uri) {
        viewModelScope.launch {
            _uiState.update { it.copy(phase = MurmurPhase.PreparingPhoto) }
            try {
                val attachment = withContext(Dispatchers.IO) { stagePhoto(context, uri) }
                _uiState.update { it.copy(photo = attachment, phase = MurmurPhase.Ready) }
            } catch (failure: MurmurFailure) {
                _uiState.update {
                    it.copy(phase = MurmurPhase.Error, failure = failure)
                }
            }
        }
    }

    fun removePhoto() {
        _uiState.value.photo?.file?.delete()
        _uiState.update { it.copy(photo = null) }
    }

    fun send() {
        val api = api ?: return
        val snapshot = _uiState.value
        if (snapshot.connection != MurmurConnectionState.Connected || snapshot.phase.isBusy) return
        if (snapshot.photo == null && snapshot.note.isBlank()) return
        streamJob?.cancel()
        streamJob = viewModelScope.launch {
            _uiState.update { it.copy(phase = MurmurPhase.Uploading, failure = null, bubbles = emptyList()) }
            try {
                val receipt = api.createMoment(
                    note = snapshot.note.ifBlank { null },
                    photo = snapshot.photo,
                    idempotencyKey = idempotencyKey,
                )
                _uiState.update { it.copy(phase = MurmurPhase.Responding) }
                var lastEventID: String? = null
                api.events(receipt.momentID, lastEventID = null).collect { event ->
                    when (event) {
                        is MurmurStreamEvent.Accepted -> lastEventID = event.id ?: lastEventID
                        is MurmurStreamEvent.Bubble -> {
                            lastEventID = event.id ?: lastEventID
                            _uiState.update {
                                it.copy(bubbles = it.bubbles + MurmurBubble(event.id ?: UUID.randomUUID().toString(), event.text))
                            }
                        }
                        is MurmurStreamEvent.Quiet -> _uiState.update { it.copy(phase = MurmurPhase.Quiet) }
                        is MurmurStreamEvent.Done -> _uiState.update { it.copy(phase = MurmurPhase.Complete) }
                        is MurmurStreamEvent.Failure -> _uiState.update {
                            it.copy(phase = MurmurPhase.Error, failure = event.failure)
                        }
                    }
                }
                _uiState.update { current ->
                    if (current.phase == MurmurPhase.Responding) current.copy(phase = MurmurPhase.Complete) else current
                }
                settleMoment()
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (error: Throwable) {
                val failure = MurmurFailure.from(error)
                _uiState.update { it.copy(phase = MurmurPhase.Error, failure = failure) }
            }
        }
    }

    fun retrySend() = send()

    fun cancel() {
        streamJob?.cancel()
        streamJob = null
        settleMoment()
        _uiState.update { it.copy(phase = MurmurPhase.Ready, bubbles = emptyList()) }
    }

    /** A resolved moment clears the staged inputs; nothing is kept locally. */
    private fun settleMoment() {
        _uiState.value.photo?.file?.delete()
        idempotencyKey = UUID.randomUUID().toString()
        _uiState.update { it.copy(photo = null, note = "") }
    }

    private fun stagePhoto(context: Context, uri: Uri): PhotoAttachment {
        val resolver = context.contentResolver
        val mimeType = resolver.getType(uri) ?: "image/jpeg"
        val filename = resolver.query(uri, null, null, null, null)?.use { cursor ->
            val index = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
            if (index >= 0 && cursor.moveToFirst()) cursor.getString(index) else null
        } ?: "photo.jpg"
        val file = File(context.cacheDir, "murmur-photo-${UUID.randomUUID()}")
        resolver.openInputStream(uri)?.use { input ->
            file.outputStream().use { input.copyTo(it) }
        } ?: throw MurmurFailure("photo_unreadable", "这张照片读不出来，换一张试试。", retryable = true)
        val preview = BitmapFactory.decodeFile(file.absolutePath)
        return PhotoAttachment(
            file = file,
            preview = preview,
            filename = filename,
            mimeType = mimeType,
            byteCount = file.length(),
        )
    }
}
