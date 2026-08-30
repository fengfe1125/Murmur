package com.sakura.murmur

import android.content.Context
import android.content.Intent
import com.sakura.murmur.ui.OnThisDayDebugTuning

/**
 * Where the 当年今日 library comes from — the DEBUG counterpart. Debug builds
 * layer the `--murmur-stub-onthisday*` stub from the launching intent over
 * MediaStore, and honour `--murmur-slow-dissolve` for the send-off animation
 * (iOS `#if DEBUG` parity; release never sees the stubs).
 */
object OnThisDayLibraryProvider {
    fun make(context: Context, intent: Intent?): OnThisDayLibrary {
        val flags = OnThisDayDebug.stubFlags(intent)
        OnThisDayDebugTuning.slowDissolve = "--murmur-slow-dissolve" in flags
        return OnThisDayLibraryResolver.makeDefault(context, flags)
    }
}
