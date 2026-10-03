# PEF-Instrumental-quirurgico

Conteo y trazabilidad asistidos por vision de instrumental quirurgico (PEF UDEM).

Stack actual: **Flask** (portal operador/SPD/admin), **Auth Service** (Flask/JWT), **PostgreSQL**, **Redis**, **MongoDB**, **Garage** (S3-compatible; reemplaza MinIO porque su imagen ya no esta en Docker Hub).

## Equipo


| Integrante                 | Matricula |
| -------------------------- | --------- |
| Benjamin Charles Legorreta | 599860    |
| Pedro Elidio Sora Gonzalez | 596630    |
| Angel Uriel Muñoz Moreno   | 604386    |


Equipo de apoyo: Luis Carlos Rodriguez Medrano, Carlos Ignacio Huerta Carrizales, Juan Hermilo Reyes Perez. Asesor: Dr. Raul Morales Salcedo.

## Prerrequisitos

- Docker Desktop en ejecucion
- Python 3.11+ en PATH
- Git
- Puertos libres: `5433`, `6379`, `27017`, `3900`, `3901`, `3903`, `5000`, `5001`

```powershell
docker --version
docker compose version
python --version
```



## Arranque rapido

Desde la **raiz** del repo (`PEF-Instrumental-quirurgico`).

### 1) Entorno

Solo la **primera vez**, o cuando cambies `.env.example` / tu `.env` de raiz:

```powershell
copy .env.example .env
copy .env apps\BackendAuthService\.env
copy .env apps\BackendWebFlask\.env
```

Nota: No hace falta repetir `copy` en cada terminal nueva. Auth y Web leen el `.env` **de su carpeta** al arrancar; una terminal nueva solo necesita `cd` + activar venv + `python ...`.  
Volver a hacer `copy` sobrescribe esos `.env` con el de la raiz (util si editaste la raiz; malo si solo habias tocado el de un app).

### 2) Contenedores

Si antes fallaste en alguno o dio error al iniciar puedes intentarlo de nuevo (comando ps valida status)

```powershell
docker compose down
docker compose up -d
docker compose ps
```

**No tienes que ejecutar SQL a mano.** Al subir Postgres por primera vez (volumen vacio), Docker monta y corre solo, en este orden:

1. `data/migrations/001_init.sql`
2. `data/seeds/002_seed_catalogs.sql`
3. `data/seeds/003_seed_demo.sql`

Eso pasa **dentro** del contenedor al inicializar la BD. En arranques siguientes, si el volumen ya existe, Postgres **no** vuelve a correr esos archivos.

Para forzar que se apliquen de nuevo (borra datos de todos los volumenes del compose):

```powershell
docker compose down -v
docker compose up -d
```

Servicios esperados Up: `pef_postgres`, `pef_redis`, `pef_mongo`, `pef_garage`.

Tras el primer `up` de Garage, crea el segundo bucket:

```powershell
docker exec pef_garage /garage status
docker exec pef_garage /garage bucket create privacy-notices
docker exec pef_garage /garage bucket allow --read --write --owner privacy-notices --key MedAdmin3
docker exec pef_garage /garage bucket list
docker exec pef_garage /garage key list
```

El bucket `evidence` y la key `MedAdmin3` se crean solos con `--default-bucket` si `S3_SECRET_KEY` es valida.

### 3) Auth y Web (dos terminales)

**Terminal A — Auth**

```powershell
cd apps\BackendAuthService
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

**Terminal B — Web**

```powershell
cd apps\BackendWebFlask
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Opcional Windows: `.\start-services.ps1` (compose + abre las dos terminales).

### 4) URLs y puertos


| Servicio      | Donde                                          |
| ------------- | ---------------------------------------------- |
| Portal web    | [http://127.0.0.1:5000](http://127.0.0.1:5000) |
| Auth          | [http://127.0.0.1:5001](http://127.0.0.1:5001) |
| Garage S3 API | [http://127.0.0.1:3900](http://127.0.0.1:3900) |
| PostgreSQL    | `localhost:5433` / db `InstruMed`              |
| Redis         | `localhost:6379`                               |
| MongoDB       | `localhost:27017`                              |



| Puerto | Uso              |
| ------ | ---------------- |
| 5433   | PostgreSQL       |
| 6379   | Redis            |
| 27017  | MongoDB          |
| 3900   | Garage S3        |
| 3901   | Garage RPC       |
| 3903   | Garage admin API |
| 5000   | Backend Web      |
| 5001   | Auth             |




### 5) Credenciales

**Object storage (Garage / S3)**


| Campo      | Valor                                                         |
| ---------- | ------------------------------------------------------------- |
| Endpoint   | `http://localhost:3900`                                       |
| Region     | `garage`                                                      |
| Access key | `MedAdmin3`                                                   |
| Secret key | `StorageKeySecret027` (minimo 16 caracteres; lo exige Garage) |
| Path style | si (`S3_FORCE_PATH_STYLE=true`)                               |
| Buckets    | `evidence`, `privacy-notices`                                 |


Prueba S3 (opcional, con AWS CLI v2):

```powershell
$env:AWS_ACCESS_KEY_ID="MedAdmin3"
$env:AWS_SECRET_ACCESS_KEY="StorageKeySecret027"
$env:AWS_DEFAULT_REGION="garage"
aws --endpoint-url http://127.0.0.1:3900 s3 ls
aws --endpoint-url http://127.0.0.1:3900 s3 ls s3://evidence
```

**Usuarios demo (Postgres seed; hashes Werkzeug 3.1.8)**  
Códigos de rol RF: `station_operator`, `spd_supervisor`, `it_admin`.


| Rol (RF)         | Email                      | Password        |
| ---------------- | -------------------------- | --------------- |
| station_operator | `operator@instrumed.com`   | `DemopwdOP78!`  |
| spd_supervisor   | `supervisor@instrumed.com` | `DemopwdSPD78!` |
| it_admin         | `admin@instrumed.com`      | `DemopwdADM78!` |


```powershell
curl -i -X POST http://127.0.0.1:5001/login -H "Content-Type: application/json" -d "{\"email\":\"operator@instrumed.com\",\"password\":\"DemopwdOP78!\"}"
```

Smoke Auth + Redis (tras `pip install -r requirements.txt` en Auth y contenedores Up):

```powershell
.\scripts\smoke_auth.ps1
```

Login por UI: [http://127.0.0.1:5000/sign-in](http://127.0.0.1:5000/sign-in) con las mismas credenciales.

## Estructura

```
PEF-Instrumental-quirurgico/
├── docker-compose.yml
├── .env.example
├── start-services.ps1
├── start-services.sh
├── infra/garage.toml
├── data/
└── apps/
    ├── BackendAuthService/
    └── BackendWebFlask/
```



## Problemas frecuentes

**Garage no lista buckets**  
Espera unos segundos y revisa logs: `docker compose logs garage`. Luego `docker exec pef_garage /garage status`.

**Garage reinicia en bucle**  
Revisa `docker logs pef_garage`. Si dice `Secret keys should be at least 16 characters long`, alarga `S3_SECRET_KEY` en `.env`, luego:

```powershell
docker compose down
docker volume rm pef-instrumental-quirurgico_garage_meta pef-instrumental-quirurgico_garage_data
docker compose up -d garage
```

**Bucket privacy-notices falta**  

```powershell
docker exec pef_garage /garage bucket create privacy-notices
docker exec pef_garage /garage bucket allow --read --write --owner privacy-notices --key MedAdmin3
```

**Postgres sin seeds**  
Init solo en volumen nuevo: `docker compose down -v` y `up -d` de nuevo.

**Login 401**  
Auth debe tener `Werkzeug==3.1.8` y el seed actual

## Estado del desarrollo


| Fase | Contenido                                                                          | Estado    |
| ---- | ---------------------------------------------------------------------------------- | --------- |
| 0–1  | Empaque Docker (PG/Redis/Mongo/Garage) + seeds + README                            | Listo     |
| 2    | Auth JWT en Redis (DS01), cookies demo, `ui_preferences.locale`, smoke login       | Listo     |
| 3.1  | Contrato RF: estados, `capture_mode`, roles RF, seeds ± aviso, OP-01/OP-02 lectura | Listo     |
| 3.2  | SP-02 programar + privacy Via A, OP-03 Start, OP-04M/06M manual                    | Listo     |
| 3.3  | Seeds D/E + SP-05/06 cierre + detalle RF supervisor                                | Listo     |
| 4    | Cablear resto de plantillas a logica real                                          | Pendiente |
| 5    | Checklist demo LAN (dos laptops)                                                   | Pendiente |
| 6    | i18n real (Flask-Babel)                                                            | Diferido  |


Idioma: **ingles** como fuente; Babel = Fase 6. UI pre-RF del operador (dashboard / New Session) vive en `/legacy/...` (ver `apps/BackendWebFlask/legacy/README.md`).

### Pruebas (usabilidad y debugging)

Credenciales: seccion **5) Credenciales**. Si la semilla quedo sucia tras smokes o demos, recrea volumenes (`down -v` + `up -d`) y vuelve a crear `privacy-notices`. Reinicia Auth/Web despues.

**1) Infra y semilla**

```powershell
docker compose ps
.\scripts\smoke_auth.ps1
docker exec -i pef_postgres psql -U MedAdmin -d InstruMed -c "SELECT code FROM role ORDER BY 1;"
docker exec -i pef_postgres psql -U MedAdmin -d InstruMed -c "SELECT LEFT(id::text,8) AS sid, (SELECT code FROM cat_session_status s WHERE s.id=w.status_id) AS status, capture_mode FROM work_session w ORDER BY id;"
docker exec -i pef_postgres psql -U MedAdmin -d InstruMed -c "SELECT f.code, COUNT(*) FILTER (WHERE c.code='available') AS available FROM kit_item ki JOIN instrument_family f ON f.id=ki.family_id JOIN instrument i ON i.family_id=f.id JOIN cat_instrument_cycle_status c ON c.id=i.cycle_status_id WHERE ki.kit_id='51515151-0000-4000-8000-000000000001' GROUP BY f.code ORDER BY 1;"
```

Esperado con semilla limpia: roles RF (`station_operator`, `spd_supervisor`, `it_admin`); sesiones demo en estados variados (`closed`, `scheduled`, `awaiting_spd_review`, `correction_required`); ≥5 `available` por familia del kit demo.

**2) Login UI**

1. [http://127.0.0.1:5000/sign-in](http://127.0.0.1:5000/sign-in) con cada rol demo.
2. Operador → `/operator/sessions` (lista propia). Supervisor → sesiones + New session. Admin → panel admin.

**3) Ciclo SPD → OP → SPD (ruta manual)**

1. Supervisor: Sessions → **New session** (si el kit pide mas FARABEUF de los disponibles, baja la cantidad o fallara stock BLOCK). Sin privacy → OP vera Manual; con privacy Via A → Vision.
2. Operador: sesion **No notice · Manual** → Begin → Start → reporte de cantidades.
   - Match → `awaiting_spd_review`.
   - Baja una cantidad + reason → `correction_required` + discrepancia.
3. Supervisor:
   - **Correction Required** → Review → resolve (notes; opcional mark lost) → `awaiting_spd_review`.
   - **Awaiting Review** → Confirm close (material recovered) → `closed`.
4. Operador/Supervisor: View details muestra expected vs reported, privacy y timeline cuando aplica.

**4) Rama vision (parcial)**

Sesion con Notice OK → Start congela `capture_mode=vision` y abre Capture. Subida/inferencia con modelo aun no es el camino completo de demo.

**5) Smokes CLI (opcionales; ensucian seed)**

Desde `apps/BackendWebFlask` con venv activo:

```powershell
python ..\..\scripts\smoke_rf_32.py
python ..\..\scripts\smoke_rf_33.py
```

Despues, si necesitas demos UI con seeds predecibles, vuelve a `down -v` + `up -d` + `privacy-notices`.