import java.util.Properties
import java.util.concurrent.TimeUnit

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
}

// URL base del API Gateway del PEF (nginx). Se puede cambiar sin tocar el código:
//   ./gradlew assembleDebug -Ppef.apiBaseUrl.debug=http://127.0.0.1:8080/
// o en ~/.gradle/gradle.properties, o en local.properties (por máquina, no versionado).
// Ver README.md, sección "Configuración del Gateway".
// - Emulador estándar → la máquina anfitriona es 10.0.2.2 y el Gateway de desarrollo escucha en 8080.
// - Release → siempre HTTPS con un certificado válido (docker-compose.gateway-tls.yml).
val localProperties: Properties = Properties().apply {
    providers.fileContents(rootProject.layout.projectDirectory.file("local.properties")).asText.orNull
        ?.let { load(it.reader()) }
}
val debugApiBaseUrl: String = providers.gradleProperty("pef.apiBaseUrl.debug").orNull
    ?: localProperties.getProperty("pef.apiBaseUrl.debug")
    ?: "http://10.0.2.2:8080/"
val releaseApiBaseUrl: String = providers.gradleProperty("pef.apiBaseUrl.release")
    .getOrElse("https://api.pef.invalid/")

fun String.asBuildConfigString(): String = "\"" + replace("\\", "\\\\").replace("\"", "\\\"") + "\""

android {
    namespace = "com.example.cursobasico"
    compileSdk {
        version = release(37)
    }

    defaultConfig {
        applicationId = "com.example.cursobasico"
        minSdk = 26
        targetSdk = 37
        versionCode = 1
        versionName = "1.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    buildTypes {
        debug {
            buildConfigField("String", "API_BASE_URL", debugApiBaseUrl.asBuildConfigString())
        }
        release {
            require(releaseApiBaseUrl.startsWith("https://")) {
                "pef.apiBaseUrl.release debe usar HTTPS (valor actual: $releaseApiBaseUrl)"
            }
            buildConfigField("String", "API_BASE_URL", releaseApiBaseUrl.asBuildConfigString())
            optimization {
                enable = true
                packageScope = setOf("androidx.**", "kotlin.**", "kotlinx.**")
            }
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    androidResources {
        // Idiomas de la interfaz (per-app language, res/xml/locales_config.xml).
        generateLocaleConfig = false
    }
    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

// ----------------------------------------------------------------------------------------------------------------
// Opcional y por máquina: `pef.adbReverse=27080` (o `27080,27900`) en local.properties ejecuta
// `adb reverse tcp:<p> tcp:<p>` en cada dispositivo conectado antes de cada build debug (incluido Run en Android Studio).
// Así la app alcanza el Gateway local en 127.0.0.1 aunque se haya reiniciado el emulador. Nunca falla el build.
// ----------------------------------------------------------------------------------------------------------------
abstract class PefAdbReverseTask : DefaultTask() {
    @get:Input abstract val adbPath: Property<String>
    @get:Input abstract val ports: ListProperty<String>

    init {
        outputs.upToDateWhen { false }
    }

    private fun adb(vararg args: String): Pair<Int, String>? = try {
        val process = ProcessBuilder(adbPath.get(), *args).redirectErrorStream(true).start()
        val output = process.inputStream.bufferedReader().readText()
        if (!process.waitFor(15, TimeUnit.SECONDS)) { process.destroy(); null } else process.exitValue() to output
    } catch (e: Exception) {
        null
    }

    @TaskAction
    fun reverse() {
        val devices = adb("devices")?.second?.lines()?.drop(1)
            ?.map { it.trim().split(Regex("\\s+")) }
            ?.filter { it.size >= 2 && it[1] == "device" }?.map { it[0] }
        if (devices == null) {
            logger.warn("pefAdbReverse: no se pudo ejecutar adb (${adbPath.get()}); se omite.")
            return
        }
        if (devices.isEmpty()) {
            logger.warn("pefAdbReverse: no hay dispositivos conectados; arranca el emulador y vuelve a ejecutar.")
            return
        }
        for (serial in devices) for (port in ports.get()) {
            val result = adb("-s", serial, "reverse", "tcp:$port", "tcp:$port")
            if (result?.first == 0) logger.lifecycle("pefAdbReverse: $serial tcp:$port -> tcp:$port")
            else logger.warn("pefAdbReverse: falló en $serial tcp:$port (${result?.second?.trim()})")
        }
    }
}

val adbReversePorts: List<String> = (localProperties.getProperty("pef.adbReverse") ?: "")
    .split(",").map { it.trim() }.filter { it.matches(Regex("\\d{1,5}")) }
if (adbReversePorts.isNotEmpty()) {
    val adbReverse = tasks.register<PefAdbReverseTask>("pefAdbReverse") {
        group = "pef"
        description = "adb reverse de los puertos de pef.adbReverse (local.properties) en los dispositivos conectados"
        adbPath.set(androidComponents.sdkComponents.adb.map { it.asFile.absolutePath })
        ports.set(adbReversePorts)
    }
    tasks.matching { it.name == "preDebugBuild" }.configureEach { dependsOn(adbReverse) }
}

dependencies {
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.compose.foundation)
    implementation(libs.androidx.compose.foundation.layout)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.ui)
    implementation("androidx.compose.material3:material3")

    implementation(
        "androidx.compose.material:material-icons-extended"
    )
    implementation(libs.androidx.navigation3.runtime)
    implementation(libs.androidx.navigation3.ui)
    implementation(libs.kotlinx.serialization.core)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.material3)

    // ANDROID-01: integración con los microservicios del PEF a través del API Gateway.
    implementation(libs.retrofit)
    implementation(libs.retrofit.kotlinx.serialization)
    implementation(libs.okhttp)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.datastore.preferences)
    // Idioma por aplicación (AppCompatDelegate.setApplicationLocales) con respaldo para API < 33.
    implementation(libs.androidx.appcompat)

    testImplementation(libs.junit)
    testImplementation(libs.okhttp.mockwebserver)
    testImplementation(libs.kotlinx.coroutines.test)
    androidTestImplementation(platform(libs.androidx.compose.bom))
    androidTestImplementation(libs.androidx.compose.ui.test.junit4)
    androidTestImplementation(libs.androidx.espresso.core)
    androidTestImplementation(libs.androidx.junit)
    debugImplementation(libs.androidx.compose.ui.test.manifest)
    debugImplementation(libs.androidx.compose.ui.tooling)
}
