package com.sakura.murmur

import android.graphics.Bitmap
import java.io.File
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow

/**
 * The UI tests' stand-ins for the wire and the photo pipeline, shared by
 * `MurmurChatScreenTest` (the shell-level matrix) and `MurmurShellTest` (the
 * tab bar and its stops). `PhotoRoomViewTest` reuses the same pair with the
 * reading-stream knobs turned on: a photo-room guess (`replyText`) and the
 * three openers (`angles`) the reading moment carries.
 */
internal class UiFakeApi(
    private val identity: MurmurIdentity?,
    private val failDevicesWithUnknownKey: Boolean = false,
    private val replyText: String = "reply-1",
    private val angles: List<String>? = null,
) : MurmurApiClient {
    override suspend fun storedIdentity(): MurmurIdentity? = identity
    override suspend fun enroll(inviteCode: String, deviceName: String): MurmurIdentity =
        identity ?: MurmurIdentity("test-user", "test-device", "test-key")
    override suspend fun createMoment(
        note: String?,
        photo: PhotoAttachment?,
        idempotencyKey: String,
        intent: String?,
        contextMomentIDs: List<String>?,
    ): MomentReceipt = MomentReceipt("moment-1", "queued")
    override fun events(momentID: String, lastEventID: String?): Flow<MurmurStreamEvent> = flow {
        emit(MurmurStreamEvent.Accepted("accepted-1"))
        emit(MurmurStreamEvent.Bubble("bubble-1", replyText))
        angles?.let { emit(MurmurStreamEvent.Angles("angles-1", it)) }
        emit(MurmurStreamEvent.Done("done-1", null, null))
    }
    override suspend fun currentProactive(): ProactiveMoment? = null
    override suspend fun acknowledge(momentID: String, reply: String?) = Unit
    override suspend fun updateDevice(pushToken: String?, environment: String, timezone: String, deviceName: String) = Unit
    override suspend fun devices(): List<MurmurDevice> {
        if (failDevicesWithUnknownKey) {
            throw MurmurFailure("attestation_key_unknown", "Device binding is unknown.", retryable = false)
        }
        return listOf(
            MurmurDevice("test-device", "test-key", "development", "Asia/Shanghai", "Test Phone", true, null, null),
            MurmurDevice("other-device", "other-key", "development", "Asia/Shanghai", "Test Pad", false, null, null),
        )
    }
    override suspend fun removeDevice(deviceID: String) = Unit
    override suspend fun preferences(): MurmurPreferences = MurmurPreferences()
    override suspend fun updatePreferences(preferences: MurmurPreferences) = Unit
    override suspend fun resetLocalIdentity() = Unit
    override suspend fun deleteAccount() = Unit
}

/** [preview] stands in for the downsampled bitmap the real loader decodes:
 *  the photo band only renders its test-tagged image when a preview landed. */
internal class UiFakePhotoLoader(
    private val preview: Bitmap? = null,
) : PhotoLoader {
    private val directory = File(System.getProperty("java.io.tmpdir"), "murmur-ui-test")
    override suspend fun load(input: PhotoInput): PhotoAttachment {
        directory.mkdirs()
        val file = File(directory, "murmur-upload-${java.util.UUID.randomUUID()}.jpg").apply { writeBytes(ByteArray(4)) }
        return PhotoAttachment(file = file, preview = preview, filename = "test.jpg", mimeType = "image/jpeg", byteCount = 4)
    }
    override suspend fun discard(attachment: PhotoAttachment?) {
        attachment?.file?.delete()
    }
    override suspend fun discardFile(file: File) = Unit
    override suspend fun cleanupStaleFiles() = Unit
}
