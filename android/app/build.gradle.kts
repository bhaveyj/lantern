plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "app.lantern.tv"
    compileSdk = 35
    defaultConfig {
        applicationId = "app.lantern.tv"
        minSdk = 24
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }
    buildTypes {
        release { isMinifyEnabled = false }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    implementation("androidx.media3:media3-exoplayer:1.6.1")
    implementation("androidx.media3:media3-ui:1.6.1")
    // The WebSocket layer is vendored with allocation/fragment limits.
    implementation("org.nanohttpd:nanohttpd:2.3.1")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}
