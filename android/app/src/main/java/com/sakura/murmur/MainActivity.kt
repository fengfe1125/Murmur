package com.sakura.murmur

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
            MurmurSessionModel(app.container.apiClient, app.container.configurationFailure)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val session = ViewModelProvider(this, sessionFactory)[MurmurSessionModel::class.java]
        setContent {
            MurmurTheme {
                MurmurChatScreen(session = session)
            }
        }
    }
}
