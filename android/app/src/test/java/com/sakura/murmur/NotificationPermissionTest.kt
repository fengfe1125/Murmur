package com.sakura.murmur

import org.junit.Assert.assertEquals
import org.junit.Test

/** The permission-policy mapping from T1.5, without the Android runtime. */
class NotificationPermissionTest {

    @Test
    fun grantedPermissionIsAllowedRegardlessOfChannelState() {
        assertEquals(NotificationAuthorization.Allowed, NotificationPermission.authorization(granted = true, notificationsEnabled = true))
        assertEquals(NotificationAuthorization.Allowed, NotificationPermission.authorization(granted = true, notificationsEnabled = false))
    }

    @Test
    fun ungrantedButEnabledMeansNotYetDetermined() {
        assertEquals(
            NotificationAuthorization.NotDetermined,
            NotificationPermission.authorization(granted = false, notificationsEnabled = true),
        )
    }

    @Test
    fun ungrantedAndDisabledMeansDenied() {
        assertEquals(
            NotificationAuthorization.Denied,
            NotificationPermission.authorization(granted = false, notificationsEnabled = false),
        )
    }

    @Test
    fun tokenSyncPolicyWaitsForAnAllowedToken() {
        // T1.5/T2.2: an allowed device uploads its token; denied / not
        // determined upload null so the server stops pushing; unknown holds.
        assertEquals(false, MurmurNotificationBridge.shouldSyncToken(null, NotificationAuthorization.Unknown))
        assertEquals(false, MurmurNotificationBridge.shouldSyncToken(null, NotificationAuthorization.Allowed))
        assertEquals(true, MurmurNotificationBridge.shouldSyncToken("token", NotificationAuthorization.Allowed))
        assertEquals(true, MurmurNotificationBridge.shouldSyncToken(null, NotificationAuthorization.Denied))
        assertEquals(true, MurmurNotificationBridge.shouldSyncToken(null, NotificationAuthorization.NotDetermined))
    }
}
