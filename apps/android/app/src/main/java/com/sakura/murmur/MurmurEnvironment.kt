package com.sakura.murmur

import android.content.Context

/**
 * Environment wiring — the counterpart of MurmurApp/MurmurEnvironment.swift.
 *
 * Debug builds may carry a development bypass token (BuildConfig.MURMUR_DEV_TOKEN,
 * sourced from gitignored local.properties) and default to a localhost server;
 * release builds refuse both, exactly like the iOS `#if !DEBUG` checks.
 * The Play Integrity authenticator for release arrives in Phase 2 of
 * docs/android-adaptation-plan.md.
 */
object MurmurEnvironment {

    fun makeApiClient(context: Context): MurmurApiClient {
        val baseURL = BuildConfig.MURMUR_API_BASE_URL.ifBlank {
            if (BuildConfig.ALLOW_DEVELOPMENT) "http://127.0.0.1:8766" else ""
        }
        if (baseURL.isBlank()) {
            throw MurmurFailure("not_configured", "尚未配置 Murmur 的 HTTPS 服务地址。", retryable = false)
        }
        if (!BuildConfig.ALLOW_DEVELOPMENT) {
            check(BuildConfig.MURMUR_DEV_TOKEN.isBlank()) {
                "Release builds cannot include a Murmur development bypass."
            }
            if (!baseURL.lowercase().startsWith("https://")) {
                throw MurmurFailure("not_configured", "正式版只允许连接 HTTPS 服务。", retryable = false)
            }
        }

        val store = AndroidIdentityStore(context.applicationContext)
        val authenticator: MurmurAuthenticator =
            if (BuildConfig.ALLOW_DEVELOPMENT && BuildConfig.MURMUR_DEV_TOKEN.isNotBlank()) {
                DevelopmentAuthenticator(token = BuildConfig.MURMUR_DEV_TOKEN, store = store)
            } else if (BuildConfig.ALLOW_DEVELOPMENT) {
                // Debug without a token still has no attestation path: the
                // emulator's Keystore attestation is software-backed and the
                // production server fails it closed by design.
                throw MurmurFailure(
                    "not_configured",
                    "尚未配置开发令牌（local.properties 的 murmur.devToken）。",
                    retryable = false,
                )
            } else {
                // Release: hardware Key Attestation (T2.1). A device with an
                // unlocked bootloader or a non-TEE key is refused by the
                // server — that is the production gate, not a client bug.
                KeyAttestationAuthenticator(
                    store = store,
                    keys = KeystoreAttestationKeyProvider(),
                )
            }
        return OkHttpMurmurApiClient(baseURL, authenticator, context.cacheDir)
    }
}
