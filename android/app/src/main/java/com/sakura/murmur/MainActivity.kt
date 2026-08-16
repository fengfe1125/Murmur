package com.sakura.murmur

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import com.sakura.murmur.ui.MurmurChatScreen
import com.sakura.murmur.ui.MurmurTheme

class MainActivity : ComponentActivity() {

    private val sessionFactory: ViewModelProvider.Factory = viewModelFactory {
        initializer {
            val app = application as MurmurApp
            MurmurSessionModel(
                api = app.container.apiClient,
                configurationFailure = app.container.configurationFailure,
                photoLoader = AndroidPhotoLoader(app),
            )
        }
    }

    private lateinit var session: MurmurSessionModel

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        session = ViewModelProvider(this, sessionFactory)[MurmurSessionModel::class.java]
        setContent {
            MurmurTheme {
                MurmurChatScreen(session = session)
            }
        }
        handleIntent(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleIntent(intent)
    }

    /**
     * Notification deep link (T1.3): the FCM payload carries
     * `data.moment_id`; tapping it opens the app and pulls that proactive
     * moment. The session model silently ignores moments that no longer match.
     */
    private fun handleIntent(intent: Intent?) {
        val momentID = intent?.getStringExtra(EXTRA_MOMENT_ID) ?: return
        session.handleNotification(momentID = momentID)
    }

    companion object {
        const val EXTRA_MOMENT_ID = "moment_id"

        /** The intent FCM / notifications must build for a proactive moment. */
        fun proactiveIntent(context: android.content.Context, momentID: String): Intent =
            Intent(context, MainActivity::class.java)
                .putExtra(EXTRA_MOMENT_ID, momentID)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
    }
}
