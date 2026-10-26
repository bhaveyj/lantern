import groovy.json.JsonOutput

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

val releaseVariables = listOf("LANTERN_ANDROID_KEYSTORE", "LANTERN_ANDROID_STORE_PASSWORD", "LANTERN_ANDROID_KEY_ALIAS", "LANTERN_ANDROID_KEY_PASSWORD")
val hasReleaseSigning = releaseVariables.all { !System.getenv(it).isNullOrBlank() }

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
    signingConfigs {
        if (hasReleaseSigning) create("managedRelease") {
            storeFile = file(System.getenv("LANTERN_ANDROID_KEYSTORE"))
            storePassword = System.getenv("LANTERN_ANDROID_STORE_PASSWORD")
            keyAlias = System.getenv("LANTERN_ANDROID_KEY_ALIAS")
            keyPassword = System.getenv("LANTERN_ANDROID_KEY_PASSWORD")
        }
    }
    buildTypes {
        release {
            isMinifyEnabled = false
            if (hasReleaseSigning) signingConfig = signingConfigs.getByName("managedRelease")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

tasks.matching { it.name == "preReleaseBuild" }.configureEach {
    doFirst { check(hasReleaseSigning) { "Release signing requires all LANTERN_ANDROID_* signing variables; unsigned releases are disabled." } }
}

dependencies {
    implementation("androidx.media3:media3-exoplayer:1.6.1")
    implementation("androidx.media3:media3-ui:1.6.1")
    // The WebSocket layer is vendored with allocation/fragment limits.
    implementation("org.nanohttpd:nanohttpd:2.3.1")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}

tasks.register("securityDependencyInventory") {
    doLast {
        val packages = listOf("debugRuntimeClasspath", "debugUnitTestRuntimeClasspath").flatMap { name ->
            configurations.getByName(name).resolvedConfiguration.resolvedArtifacts.map { artifact ->
                val id = artifact.moduleVersion.id
                mapOf("name" to "${id.group}:${id.name}", "version" to id.version)
            }
        }.distinct().sortedBy { it["name"] }
        val output = rootProject.file("../.tmp/android-dependencies.json")
        output.parentFile.mkdirs()
        output.writeText(JsonOutput.prettyPrint(JsonOutput.toJson(packages)))
        println("Wrote ${packages.size} resolved Maven dependencies for the OSV audit.")
    }
}
