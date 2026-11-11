import java.util.Properties

// Top-level build file where you can add configuration options common to all sub-projects/modules.
plugins {
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.kotlin.compose) apply false
}

// Opcional y por máquina (local.properties, no versionado): `pef.buildDir=<carpeta>` saca los builds del proyecto.
// Útil cuando el proyecto vive en OneDrive, que bloquea app/build ("Unable to delete directory"). Ver README §10.
val pefBuildDir: String? = providers.fileContents(layout.projectDirectory.file("local.properties")).asText
    .map { text -> Properties().apply { load(text.reader()) }.getProperty("pef.buildDir") ?: "" }
    .getOrElse("")
    .takeIf { it.isNotBlank() }
if (pefBuildDir != null) {
    allprojects {
        layout.buildDirectory.set(file("$pefBuildDir/${project.name}"))
    }
}