package com.sakura.murmur

import android.graphics.Bitmap
import kotlinx.coroutines.flow.Flow
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import java.io.File
import java.util.UUID

enum class MurmurPhase {
    Idle, PreparingPhoto, Ready, Uploading, Responding, Complete, Quiet, Error;

    val isBusy: Boolean
        get() = this == PreparingPhoto || this == Uploading || this == Responding

    val statusText: String
        get() = when (this) {
            Idle -> "此刻为空"
            PreparingPhoto -> "正在准备照片"
            Ready -> "准备好了"
            Uploading -> "正在送往 Murmur"
            Responding -> "Murmur 正在回应"
            Complete -> "这一刻已完成"
            Quiet -> "Murmur 选择安静陪着"
            Error -> "没有送达"
        }
}

sealed interface MurmurConnectionState {
    data object Checking : MurmurConnectionState
    data object NeedsEnrollment : MurmurConnectionState
    data object Connected : MurmurConnectionState
    data class Offline(val detail: String) : MurmurConnectionState

    val label: String
        get() = when (this) {
            Checking -> "正在连接"
            NeedsEnrollment -> "等待邀请"
            Connected -> "已连接"
            is Offline -> "连接异常"
        }
}

class MurmurFailure(
    val code: String,
    override val message: String,
    val retryable: Boolean,
) : Exception(message) {

    val requiresDeviceReconnect: Boolean
        get() = code == "app_attest_invalid_key" || code == "attestation_key_unknown"

    companion object {
        fun from(error: Throwable): MurmurFailure = when (error) {
            is MurmurFailure -> error
            // TimeoutCancellationException is a CancellationException subclass; it
            // must be mapped before the generic cancellation branch below.
            is kotlinx.coroutines.TimeoutCancellationException ->
                MurmurFailure("timeout", "等待时间有点久，请再试一次。", retryable = true)
            is kotlinx.coroutines.CancellationException ->
                MurmurFailure("cancelled", "已取消。", retryable = true)
            is java.net.SocketTimeoutException, is java.io.InterruptedIOException ->
                MurmurFailure("timeout", "等待时间有点久，请再试一次。", retryable = true)
            is java.io.IOException ->
                MurmurFailure("network_error", "暂时没有连上 Murmur。", retryable = true)
            else ->
                MurmurFailure("network_error", "暂时没有连上 Murmur。", retryable = true)
        }

        fun fromHTTPStatus(statusCode: Int): MurmurFailure = MurmurFailure(
            code = "http_$statusCode",
            message = if (statusCode == 401) "设备验证已失效，请重新连接。" else "服务器暂时无法处理这个请求。",
            retryable = statusCode >= 500 || statusCode == 408 || statusCode == 429,
        )
    }
}

data class MurmurBubble(val id: String, val text: String)

@Serializable
data class MurmurIdentity(
    @SerialName("user_id") val userID: String,
    @SerialName("device_id") val deviceID: String,
    @SerialName("key_id") val keyID: String,
)

@Serializable
data class MurmurDevice(
    val id: String,
    @SerialName("key_id") val keyID: String,
    val environment: String,
    val timezone: String,
    @SerialName("device_name") val deviceName: String? = null,
    @SerialName("push_enabled") val pushEnabled: Boolean = false,
    @SerialName("last_seen_at") val lastSeenAt: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
)

@Serializable
data class MurmurPreferences(
    @SerialName("daily_frequency") var dailyFrequency: Int = 3,
    @SerialName("quiet_start") var quietStart: String = "22:30",
    @SerialName("quiet_end") var quietEnd: String = "08:30",
)

@Serializable
data class MomentReceipt(
    @SerialName("moment_id") val momentID: String,
    val status: String,
)

@Serializable
data class ProactiveMoment(
    @SerialName("moment_id") val momentID: String,
    val bubbles: List<String> = emptyList(),
    val text: String? = null,
    val move: String? = null,
    val scene: String? = null,
) {
    val resolvedBubbles: List<String>
        get() = when {
            bubbles.isNotEmpty() -> bubbles
            !text.isNullOrEmpty() -> listOf(text)
            else -> emptyList()
        }
}

sealed interface MurmurStreamEvent {
    val id: String?

    data class Accepted(override val id: String?) : MurmurStreamEvent
    data class Bubble(override val id: String?, val text: String) : MurmurStreamEvent
    data class Quiet(override val id: String?) : MurmurStreamEvent
    data class Done(override val id: String?, val move: String?, val scene: String?) : MurmurStreamEvent
    data class Failure(override val id: String?, val failure: MurmurFailure) : MurmurStreamEvent
}

/**
 * A photo staged for upload. [file] is a private copy inside `cacheDir`
 * (the original Uri is never kept), deleted right after the moment resolves.
 */
data class PhotoAttachment(
    val id: UUID = UUID.randomUUID(),
    val file: File,
    val preview: Bitmap?,
    val filename: String,
    val mimeType: String,
    val byteCount: Long,
)

interface MurmurApiClient {
    suspend fun storedIdentity(): MurmurIdentity?
    suspend fun enroll(inviteCode: String, deviceName: String): MurmurIdentity
    suspend fun createMoment(note: String?, photo: PhotoAttachment?, idempotencyKey: String): MomentReceipt
    fun events(momentID: String, lastEventID: String?): Flow<MurmurStreamEvent>
    suspend fun currentProactive(): ProactiveMoment?
    suspend fun acknowledge(momentID: String, reply: String?)
    suspend fun updateDevice(pushToken: String?, environment: String, timezone: String, deviceName: String)
    suspend fun devices(): List<MurmurDevice>
    suspend fun removeDevice(deviceID: String)
    suspend fun preferences(): MurmurPreferences
    suspend fun updatePreferences(preferences: MurmurPreferences)
    suspend fun resetLocalIdentity()
    suspend fun deleteAccount()
}
