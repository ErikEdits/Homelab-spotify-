plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

val buildNumber = (System.getenv("GITHUB_RUN_NUMBER") ?: "1").toInt()

android {
    namespace = "de.homify.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "de.homify.app"
        minSdk = 24
        targetSdk = 34
        versionCode = buildNumber
        versionName = "1.0.$buildNumber"
    }

    signingConfigs {
        create("release") {
            // Eigener Schlüssel über GitHub-Secrets möglich, sonst der mitgelieferte Homelab-Schlüssel
            storeFile = file(System.getenv("HOMIFY_KEYSTORE") ?: rootProject.file("homify-release.keystore").path)
            storePassword = System.getenv("HOMIFY_KEYSTORE_PASSWORD") ?: "homify-android"
            keyAlias = System.getenv("HOMIFY_KEY_ALIAS") ?: "homify"
            keyPassword = System.getenv("HOMIFY_KEY_PASSWORD") ?: "homify-android"
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("release")
        }
        debug {
            signingConfig = signingConfigs.getByName("release")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }
}
