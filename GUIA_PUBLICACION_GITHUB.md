# Publicar el monorepo PEF en GitHub

Este paquete está listo para incorporar **Android dentro de `apps/PEF-Android/`** sin cambiar los servicios Docker ni los puertos. El archivo ZIP **no incluye `.git`**: para conservar el historial, utilice el clon existente de `PEF-Instrumental-quirurgico`.

## Ruta recomendada: conservar el clon Git existente

1. Haga un respaldo del repositorio local, incluidos sus cambios pendientes. Revise `git status` y `git branch --show-current` antes de copiar archivos.
2. Descomprima este ZIP en una carpeta temporal. Copie **el contenido** de `PEF-Instrumental-quirurgico/` sobre su clon existente, sin borrar `.git`, `Legacy` ni ningún archivo local no incluido en el paquete.
3. Desde la raíz del clon, en PowerShell:

```powershell
git status
git branch --show-current
git remote -v
git add -A
git diff --cached --stat
git diff --cached --name-status
```

4. Revise que no se prepararon archivos `.env`, tokens, certificados, bases de datos, pesos YOLO (`.pt`), videos, compilaciones Android ni evidencias sensibles. Si aparecen, **no** haga commit hasta retirarlos del staging.
5. Cuando haya confirmado el contenido y la rama:

```powershell
git commit -m "Integrate Android client under apps and update documentation"
git push origin microservices_phase2
```

**Importante:** el nombre `microservices_phase2` es el de la rama utilizada anteriormente; confirme su rama real antes del `push`, y no ejecute el último comando si no desea publicar esa rama.

## Estructura

```text
PEF-Instrumental-quirurgico/
├── apps/
│   ├── PEF-Android/        # proyecto Android Studio / Gradle
│   ├── BackendWebFlask/
│   ├── VisionWorker/
│   └── ...                 # microservicios existentes
├── docker-compose.yml
├── README.md
├── GUIA_ARRANQUE_DEMO.md
└── docs/
```

- Para el backend y Docker Compose: consulte [README.md](README.md).
- Para Android Studio, Retrofit y el emulador: consulte [apps/PEF-Android/README.md](apps/PEF-Android/README.md).
- El proyecto Android **no** se levanta con Docker Compose: se abre en Android Studio desde `apps/PEF-Android/`.
- La configuración local de Docker, `.env`, modelos `best.pt` y propiedades Android (`local.properties`) se mantienen en sus entornos locales, fuera de Git.
- No se ejecutó Docker ni Android Studio durante el reempaquetado; se verificó la estructura y la integridad del ZIP.
