plugins {
    alias(libs.plugins.android.application) apply false
    // No org.jetbrains.kotlin.android: AGP 9 has Kotlin built in.
    alias(libs.plugins.kotlin.compose) apply false
    alias(libs.plugins.kotlin.serialization) apply false
    // Firebase config is generated from google-services.json (T2.2); the
    // plugin is only applied in :app and only when that file exists, so a
    // checkout without Firebase credentials still builds.
    alias(libs.plugins.google.services) apply false
}
