import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    // AGP 9 built-in Kotlin — no org.jetbrains.kotlin.android plugin needed.
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
}

// Local, gitignored overrides — the Android counterpart of `*.local.xcconfig`.
// Recognised keys (all optional):
//   murmur.apiBaseUrl   e.g. http://127.0.0.1:8766  (debug default)
//   murmur.devToken     development bypass token, debug builds only
val localProps = Properties().apply {
    val file = rootProject.file("local.properties")
    if (file.exists()) file.inputStream().use { load(it) }
}
fun localProp(key: String): String? =
    (localProps.getProperty(key) ?: System.getenv(key.uppercase().replace('.', '_')))
        ?.takeIf { it.isNotBlank() }

val isCi = System.getenv("CI") != null

android {
    namespace = "com.sakura.murmur"
    // compileSdk 36: current okhttp/androidx releases require compiling against
    // API 36; targetSdk stays 35 per docs/android-adaptation-plan.md.
    compileSdk = 36

    defaultConfig {
        applicationId = "com.sakura.murmur"
        minSdk = 28
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"

        buildConfigField(
            "String",
            "MURMUR_API_BASE_URL",
            "\"${localProp("murmur.apiBaseUrl") ?: ""}\"",
        )
    }

    buildTypes {
        debug {
            // Debug mirrors the iOS DEBUG path: localhost default + dev-token bypass allowed.
            val devToken = localProp("murmur.devToken") ?: ""
            buildConfigField("String", "MURMUR_DEV_TOKEN", "\"$devToken\"")
            buildConfigField("boolean", "ALLOW_DEVELOPMENT", "true")
        }
        release {
            // Release builds must never carry a development bypass (iOS parity:
            // MurmurEnvironment refuses to launch such a build).
            buildConfigField("String", "MURMUR_DEV_TOKEN", "\"\"")
            buildConfigField("boolean", "ALLOW_DEVELOPMENT", "false")
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.compose.foundation)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.material.icons)
    implementation(libs.androidx.security.crypto)
    implementation(libs.androidx.exifinterface)
    implementation(libs.okhttp)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.kotlinx.coroutines.android)
    debugImplementation(libs.androidx.compose.ui.tooling)

    testImplementation(libs.junit)
    testImplementation(libs.kotlinx.coroutines.test)
    testImplementation(libs.okhttp.mockwebserver)

    androidTestImplementation(platform(libs.androidx.compose.bom))
    androidTestImplementation(libs.androidx.compose.ui.test.junit4)
    androidTestImplementation(libs.androidx.test.ext.junit)
    androidTestImplementation(libs.androidx.test.runner)
    debugImplementation(libs.androidx.compose.ui.test.manifest)
}
