package com.sakura.murmur

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.ImageDecoder
import android.net.Uri
import android.provider.OpenableColumns
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.util.UUID

/** The source of a photo about to be staged. Production only ever hands a
 *  Photo Picker [Uri]; [FromFile] exists for tests and for parity with the
 *  iOS `PhotoLoader.load(fileURL:)` entry point. */
sealed interface PhotoInput {
    data class FromUri(val uri: Uri) : PhotoInput
    data class FromFile(val file: File) : PhotoInput
}

/**
 * Staging for one photo — the Android counterpart of `PhotoLoader.swift`.
 *
 * Guarantees the product contract end to end:
 *  - the original bytes are copied into a private `cacheDir` file with a
 *    `murmur-upload-` prefix, used once and deleted;
 *  - the preview is downsampled to at most [PhotoPolicies.MAX_PREVIEW_PIXELS]
 *    on the long edge (ImageDecoder applies EXIF orientation);
 *  - files above [PhotoPolicies.MAX_UPLOAD_BYTES] and empty files are refused
 *    before any decoding happens.
 */
interface PhotoLoader {
    suspend fun load(input: PhotoInput): PhotoAttachment
    suspend fun discard(attachment: PhotoAttachment?)
    suspend fun discardFile(file: File)
    suspend fun cleanupStaleFiles()
}

internal object PhotoPolicies {
    const val MAX_UPLOAD_BYTES: Long = 25L * 1024 * 1024
    const val MAX_PREVIEW_PIXELS: Int = 1800

    /** Throws the same failures the iOS loader raises for empty / oversized files. */
    fun checkSize(byteCount: Long) {
        if (byteCount <= 0) {
            throw MurmurFailure("empty_image", "这张图片是空的。", retryable = false)
        }
        if (byteCount > MAX_UPLOAD_BYTES) {
            throw MurmurFailure("image_too_large", "图片不能超过 25 MB。", retryable = false)
        }
    }

    /** iOS `safeFilename`: alphanumerics, `-`, `_`; 80 characters; a sensible
     *  default when nothing survives. */
    fun safeFilename(raw: String, extension: String): String {
        val base = raw.substringBeforeLast('.', raw)
            .filter { it.isLetterOrDigit() || it == '-' || it == '_' }
        val safeBase = base.ifEmpty { "moment" }.take(80)
        val ext = extension.filter { it.isLetterOrDigit() }.lowercase()
        return "$safeBase.${ext.ifEmpty { "jpg" }}"
    }

    /** Long-edge-constrained target size for the preview decode. */
    fun decodeTargetSize(width: Int, height: Int): Pair<Int, Int> {
        val longEdge = maxOf(width, height)
        if (longEdge <= MAX_PREVIEW_PIXELS) return width to height
        val scale = MAX_PREVIEW_PIXELS.toFloat() / longEdge.toFloat()
        return (maxOf(1, (width * scale).toInt())) to (maxOf(1, (height * scale).toInt()))
    }
}

class AndroidPhotoLoader(context: Context) : PhotoLoader {

    private val cacheDir: File = context.cacheDir
    private val resolver = context.contentResolver

    override suspend fun load(input: PhotoInput): PhotoAttachment = withContext(Dispatchers.IO) {
        when (input) {
            is PhotoInput.FromUri -> loadFromUri(input.uri)
            is PhotoInput.FromFile -> loadFromFile(input.file)
        }
    }

    private fun loadFromUri(uri: Uri): PhotoAttachment {
        val mimeType = resolver.getType(uri) ?: "image/jpeg"
        val displayName = resolver.query(uri, null, null, null, null)?.use { cursor ->
            val index = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
            if (index >= 0 && cursor.moveToFirst()) cursor.getString(index) else null
        } ?: "photo.jpg"
        val extension = extensionFor(displayName, mimeType)
        val managed = File(cacheDir, "murmur-upload-${UUID.randomUUID()}.$extension")
        return try {
            var byteCount = 0L
            resolver.openInputStream(uri)?.use { input ->
                managed.outputStream().buffered().use { output ->
                    val buffer = ByteArray(64 * 1024)
                    while (true) {
                        val count = input.read(buffer)
                        if (count < 0) break
                        byteCount += count
                        // Stop copying once we know the file is too large.
                        if (byteCount > PhotoPolicies.MAX_UPLOAD_BYTES) break
                        output.write(buffer, 0, count)
                    }
                }
            } ?: throw MurmurFailure("photo_unreadable", "这张照片读不出来，换一张试试。", retryable = true)
            PhotoPolicies.checkSize(byteCount)
            val preview = decodePreview(managed)
            PhotoAttachment(
                file = managed,
                preview = preview,
                filename = PhotoPolicies.safeFilename(displayName, extension),
                mimeType = mimeType,
                byteCount = byteCount,
            )
        } catch (error: Throwable) {
            managed.delete()
            throw error
        }
    }

    private fun loadFromFile(file: File): PhotoAttachment {
        PhotoPolicies.checkSize(file.length())
        val extension = file.extension.lowercase().ifBlank { "jpg" }
        val managed = if (file.parentFile == cacheDir && file.name.startsWith("murmur-upload-")) {
            file
        } else {
            val copy = File(cacheDir, "murmur-upload-${UUID.randomUUID()}.$extension")
            file.inputStream().use { input ->
                copy.outputStream().use { input.copyTo(it) }
            }
            copy
        }
        return try {
            val preview = decodePreview(managed)
            PhotoAttachment(
                file = managed,
                preview = preview,
                filename = PhotoPolicies.safeFilename(file.name, extension),
                mimeType = mimeTypeFor(extension),
                byteCount = managed.length(),
            )
        } catch (error: Throwable) {
            if (managed != file) managed.delete()
            throw error
        }
    }

    private fun decodePreview(file: File): Bitmap? {
        return try {
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeFile(file.absolutePath, bounds)
            val (width, height) = PhotoPolicies.decodeTargetSize(bounds.outWidth, bounds.outHeight)
            ImageDecoder.decodeBitmap(
                ImageDecoder.createSource(file),
            ) { decoder, info, _ ->
                if (width < info.size.width || height < info.size.height) {
                    decoder.setTargetSize(width, height)
                }
            }
        } catch (error: Throwable) {
            // The preview is decorative; a photo that uploads but cannot be
            // previewed is still a usable moment.
            null
        }
    }

    override suspend fun discard(attachment: PhotoAttachment?) {
        attachment?.let { discardFile(it.file) }
    }

    override suspend fun discardFile(file: File) {
        if (file.name.startsWith("murmur-upload-") || file.name.startsWith("murmur-photo-")) {
            file.delete()
        }
    }

    override suspend fun cleanupStaleFiles() {
        cacheDir.listFiles()
            ?.filter { it.name.startsWith("murmur-upload-") || it.name.startsWith("murmur-photo-") || it.name.startsWith("murmur-multipart-") }
            ?.forEach { it.delete() }
    }

    private fun extensionFor(displayName: String, mimeType: String): String {
        val fromName = displayName.substringAfterLast('.', "").filter { it.isLetterOrDigit() }.lowercase()
        if (fromName.length in 1..5) return fromName
        return when (mimeType) {
            "image/png" -> "png"
            "image/webp" -> "webp"
            "image/heic", "image/heif" -> "heic"
            "image/gif" -> "gif"
            else -> "jpg"
        }
    }

    private fun mimeTypeFor(extension: String): String = when (extension) {
        "png" -> "image/png"
        "webp" -> "image/webp"
        "heic", "heif" -> "image/heic"
        "gif" -> "image/gif"
        else -> "image/jpeg"
    }
}
