package com.sakura.murmur

import android.app.Application

class MurmurApp : Application() {

    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        // Cold-start cleanup: staged photo copies and multipart bodies are
        // use-once artifacts; anything left behind was orphaned by a crash.
        cacheDir.listFiles()
            ?.filter { it.name.startsWith("murmur-") }
            ?.forEach { it.delete() }
        container = AppContainer(this)
    }
}

class AppContainer(context: Application) {
    val apiClient: MurmurApiClient?
    val configurationFailure: MurmurFailure?

    init {
        var client: MurmurApiClient? = null
        var failure: MurmurFailure? = null
        try {
            client = MurmurEnvironment.makeApiClient(context)
        } catch (error: MurmurFailure) {
            failure = error
        }
        apiClient = client
        configurationFailure = failure
    }
}
