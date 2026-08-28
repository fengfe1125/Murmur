plugins {
    alias(libs.plugins.android.application) apply false
    // No org.jetbrains.kotlin.android: AGP 9 has Kotlin built in.
    alias(libs.plugins.kotlin.compose) apply false
    alias(libs.plugins.kotlin.serialization) apply false
}
