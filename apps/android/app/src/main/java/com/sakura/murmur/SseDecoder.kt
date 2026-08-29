package com.sakura.murmur

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/**
 * Hand-written SSE frame decoder, extracted from the API client so the tricky
 * framing rules are unit-testable without a socket.
 *
 * The rules match the iOS `consumeEvents` byte loop exactly:
 *  - a blank line terminates the current event (any helper that drops blank
 *    lines would merge every event into the last one);
 *  - `:` lines are keep-alive comments;
 *  - `id:` / `event:` / `data:` accumulate into the pending event.
 */
internal class SseEventDecoder(
    private val json: Json = Json { ignoreUnknownKeys = true },
) {
    private var eventID: String? = null
    private var eventName = "message"
    private val dataLines = mutableListOf<String>()

    /** Feed one line (without the trailing `\n` / `\r\n`). Returns the decoded
     *  event when a blank line terminates it, otherwise null. */
    fun feed(line: String): MurmurStreamEvent? {
        when {
            line.isEmpty() -> {
                val event = decodeEvent(eventID, eventName, dataLines.joinToString("\n"))
                eventID = null
                eventName = "message"
                dataLines.clear()
                return event
            }
            line.startsWith(":") -> Unit // keep-alive comment
            line.startsWith("id:") -> eventID = line.drop(3).trim()
            line.startsWith("event:") -> eventName = line.drop(6).trim()
            line.startsWith("data:") -> dataLines.add(line.drop(5).trim())
        }
        return null
    }

    /** Flush an event that reached EOF without a terminating blank line. */
    fun flush(): MurmurStreamEvent? {
        if (dataLines.isEmpty()) return null
        val event = decodeEvent(eventID, eventName, dataLines.joinToString("\n"))
        eventID = null
        eventName = "message"
        dataLines.clear()
        return event
    }

    internal fun decodeEvent(id: String?, name: String, data: String): MurmurStreamEvent? = when (name) {
        "accepted" -> MurmurStreamEvent.Accepted(id)
        "bubble" -> MurmurStreamEvent.Bubble(id, json.decodeFromString(BubblePayload.serializer(), data).text)
        "quiet" -> MurmurStreamEvent.Quiet(id)
        "done" -> {
            val done = try {
                json.decodeFromString(DonePayload.serializer(), data)
            } catch (_: Exception) {
                DonePayload()
            }
            MurmurStreamEvent.Done(id, done.move, done.scene)
        }
        "error" -> {
            val failure = json.decodeFromString(StreamFailurePayload.serializer(), data)
            MurmurStreamEvent.Failure(id, MurmurFailure(failure.code, failure.message, failure.retryable))
        }
        else -> null
    }
}

@Serializable
internal data class BubblePayload(val text: String)

@Serializable
internal data class DonePayload(val move: String? = null, val scene: String? = null)

@Serializable
internal data class StreamFailurePayload(val code: String, val message: String, val retryable: Boolean)
