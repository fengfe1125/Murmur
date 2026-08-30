package com.sakura.murmur

import android.content.Context
import android.content.Intent

/**
 * Where the 当年今日 library comes from — the RELEASE counterpart: always the
 * real MediaStore library, no stub flags, no debug tuning. (The [intent]
 * parameter keeps the two source-set variants signature-identical.)
 */
object OnThisDayLibraryProvider {
    fun make(context: Context, intent: Intent?): OnThisDayLibrary =
        OnThisDayLibraryResolver.makeDefault(context)
}
