package com.sakura.murmur

import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage

/**
 * FCM entry point (T2.2). Token refreshes go to [MurmurPushBridge]; a
 * foreground data message opens the proactive moment directly through the
 * same deep link the notification tap uses. Background taps are handled by
 * the tray notification + `click_action` (see the server payload), which
 * lands on `MainActivity` with the `moment_id` extra.
 */
class MurmurFirebaseMessagingService : FirebaseMessagingService() {

    override fun onNewToken(token: String) {
        MurmurPushBridge.onNewToken(token)
    }

    override fun onMessageReceived(message: RemoteMessage) {
        val momentID = message.data["moment_id"] ?: return
        startActivity(MainActivity.proactiveIntent(this, momentID))
    }
}
