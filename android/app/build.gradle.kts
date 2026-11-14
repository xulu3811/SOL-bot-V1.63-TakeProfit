plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("com.google.devtools.ksp")
    id("com.google.dagger.hilt.android")
    id("com.google.gms.google-services")
}
android {
    namespace = "com.lu.cryptobot"
    compileSdk = 36
    defaultConfig { applicationId = "com.lu.cryptobot"; minSdk = 26; targetSdk = 36; versionCode = 143; versionName = "1.43"; testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner" }
    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("debug")
        }
    }
    buildFeatures { compose = true; buildConfig = true }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    kotlinOptions { jvmTarget = "17" }
    packaging { resources.excludes += "/META-INF/{AL2.0,LGPL2.1}" }
}
dependencies {
    implementation(platform("androidx.compose:compose-bom:2025.06.01"))
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.compose.ui:ui-tooling-preview")
    debugImplementation("androidx.compose.ui:ui-tooling")
    implementation("androidx.activity:activity-compose:1.10.1")
    implementation("androidx.fragment:fragment-ktx:1.8.8")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.9.1")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.9.1")
    implementation("androidx.navigation:navigation-compose:2.9.0")
    implementation("com.google.dagger:hilt-android:2.57.2")
    ksp("com.google.dagger:hilt-compiler:2.57.2")
    implementation("androidx.room:room-runtime:2.6.1")
    implementation("androidx.room:room-ktx:2.6.1")
    ksp("androidx.room:room-compiler:2.6.1")
    implementation("androidx.datastore:datastore-preferences:1.1.7")
    implementation("com.squareup.retrofit2:retrofit:2.11.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")
    implementation(platform("com.google.firebase:firebase-bom:33.16.0"))
    implementation("com.google.firebase:firebase-messaging")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}

val exportReleaseApk by tasks.registering(Copy::class) {
    val ver = android.defaultConfig.versionName ?: "1.01"
    from(layout.buildDirectory.file("outputs/apk/release/app-release.apk"))
    into(rootProject.projectDir.parentFile.resolve("release"))
    rename { "SOL-bot v$ver.apk" }
}

tasks.matching { it.name == "assembleRelease" }.configureEach {
    finalizedBy(exportReleaseApk)
}

tasks.register("bumpVersion") {
    doLast {
        val f = project.buildFile
        val text = f.readText()
        val verMatch = Regex("""versionName\s*=\s*"(\d+)\.(\d+)"""").find(text)
            ?: error("versionName not found")
        val major = verMatch.groupValues[1].toInt()
        val minor = verMatch.groupValues[2].toInt() + 1
        val nextVer = if (minor >= 100) "${major + 1}.00" else "$major.${minor.toString().padStart(2, '0')}"
        val codeMatch = Regex("""versionCode\s*=\s*(\d+)""").find(text)
            ?: error("versionCode not found")
        val nextCode = codeMatch.groupValues[1].toInt() + 1
        val updated = text
            .replaceFirst(verMatch.value, """versionName = "$nextVer"""")
            .replaceFirst(codeMatch.value, """versionCode = $nextCode""")
        f.writeText(updated)
        println("Bumped version -> $nextVer (versionCode=$nextCode)")
    }
}

