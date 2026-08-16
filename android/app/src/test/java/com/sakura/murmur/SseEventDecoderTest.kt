package com.sakura.murmur

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The framing rules from the iOS `consumeEvents` comment, one line at a time. */
class SseEventDecoderTest {

    private val decoder get() = SseEventDecoder()

    @Test
    fun blankLineTerminatesAnEvent() {
        val decoder = SseEventDecoder()
        assertNull(decoder.feed("id: 1"))
        assertNull(decoder.feed("event: bubble"))
        assertNull(decoder.feed("data: {\"text\":\"你好\"}"))
        val event = decoder.feed("") as MurmurStreamEvent.Bubble
        assertEquals("1", event.id)
        assertEquals("你好", event.text)
    }

    @Test
    fun commentLinesAreIgnored() {
        val decoder = SseEventDecoder()
        assertNull(decoder.feed(": keep-alive"))
        assertNull(decoder.feed(": another"))
        assertNull(decoder.feed("event: quiet"))
        assertNull(decoder.feed("data: {}"))
        assertTrue(decoder.feed("") is MurmurStreamEvent.Quiet)
    }

    @Test
    fun multipleDataLinesJoinWithNewline() {
        val decoder = SseEventDecoder()
        assertNull(decoder.feed("event: done"))
        assertNull(decoder.feed("data: broken"))
        assertNull(decoder.feed("data: json"))
        // `done` tolerates malformed JSON by falling back to defaults.
        val event = decoder.feed("") as MurmurStreamEvent.Done
        assertNull(event.move)
        assertNull(event.scene)
    }

    @Test
    fun idAndNameResetAfterEachEvent() {
        val decoder = SseEventDecoder()
        decoder.feed("id: a")
        decoder.feed("event: accepted")
        decoder.feed("data: {}")
        assertTrue(decoder.feed("") is MurmurStreamEvent.Accepted)
        // No `id:` on the second event: it must not inherit "a".
        decoder.feed("event: quiet")
        decoder.feed("data: {}")
        val second = decoder.feed("") as MurmurStreamEvent.Quiet
        assertNull(second.id)
    }

    @Test
    fun unknownEventNamesAreDropped() {
        val decoder = SseEventDecoder()
        decoder.feed("event: something-else")
        decoder.feed("data: {}")
        assertNull(decoder.feed(""))
    }

    @Test
    fun errorPayloadBecomesFailureEvent() {
        val decoder = SseEventDecoder()
        decoder.feed("event: error")
        decoder.feed("data: {\"code\":\"boom\",\"message\":\"坏了\",\"retryable\":true}")
        val event = decoder.feed("") as MurmurStreamEvent.Failure
        assertEquals("boom", event.failure.code)
        assertEquals("坏了", event.failure.message)
        assertTrue(event.failure.retryable)
    }

    @Test
    fun flushEmitsATrailingUnterminatedEventOnce() {
        val decoder = SseEventDecoder()
        decoder.feed("id: 9")
        decoder.feed("event: bubble")
        decoder.feed("data: {\"text\":\"尾巴\"}")
        val event = decoder.flush() as MurmurStreamEvent.Bubble
        assertEquals("9", event.id)
        assertEquals("尾巴", event.text)
        assertNull(decoder.flush())
    }

    @Test
    fun flushWithoutPendingDataEmitsNothing() {
        val decoder = SseEventDecoder()
        assertNull(decoder.flush())
        decoder.feed(": only a comment")
        assertNull(decoder.flush())
    }
}
