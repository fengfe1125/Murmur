package com.sakura.murmur

import android.Manifest
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.provider.Settings
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat

/**
 * Notification authorization on Android — the counterpart of the iOS
 * `MurmurNotificationBridge` permission plumbing. T1.5: the app only requests
 * POST_NOTIFICATIONS once, after the first complete reply (the session model
 * flips `notificationPromptRequested`), and uploads a push token only when the
 * policy below says so (FCM wiring lands in T2.2).
 */
object NotificationPermission {

    /** Pure mapping, unit-tested on the JVM. */
    fun authorization(granted: Boolean, notificationsEnabled: Boolean): NotificationAuthorization = when {
        granted -> NotificationAuthorization.Allowed
        notificationsEnabled -> NotificationAuthorization.NotDetermined
        else -> NotificationAuthorization.Denied
    }

    fun authorization(context: Context): NotificationAuthorization {
        val granted = if (Build.VERSION.SDK_INT >= 33) {
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) ==
                PackageManager.PERMISSION_GRANTED
        } else {
            NotificationManagerCompat.from(context).areNotificationsEnabled()
        }
        val enabled = NotificationManagerCompat.from(context).areNotificationsEnabled()
        return authorization(granted = granted, notificationsEnabled = enabled)
    }

    fun openSystemSettings(context: Context) {
        val intent = if (Build.VERSION.SDK_INT >= 26) {
            Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                .putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName)
        } else {
            Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
                .putExtra("package", context.packageName)
        }
        context.startActivity(intent)
    }
}
