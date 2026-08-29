package com.sakura.murmur

import android.content.Context

/**
 * The handoff between the FCM service (which may run with no activity alive)
 * and the session model (which owns device sync + the enrolled identity).
 * The iOS counterpart is `MurmurNotificationBridge`.
 *
 * The token is only uploaded once the permission policy says so
 * ([MurmurNotificationBridge.shouldSyncToken]): allowed → the token; denied /
 * not determined → null so the server stops pushing. [consumePending] runs
 * from the activity lifecycle; [MurmurSessionModel.updatePushRegistration]
 * holds the value until an identity exists, exactly like iOS.
 */
object MurmurPushBridge {

    @Volatile
    private var pendingToken: String? = null

    @Volatile
    private var hasPending = false

    /** Called from `FirebaseMessagingService.onNewToken`. */
    fun onNewToken(token: String?) {
        pendingToken = token
        hasPending = true
    }

    /** Flush any token the service delivered into the session model. */
    fun consumePending(context: Context, session: MurmurSessionModel) {
        if (!hasPending) return
        hasPending = false
        val authorization = NotificationPermission.authorization(context)
        val upload = if (MurmurNotificationBridge.shouldSyncToken(pendingToken, authorization)) {
            pendingToken
        } else {
            null
        }
        session.updatePushRegistration(upload)
    }
}
