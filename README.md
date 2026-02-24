# Catálogo PDF → Excel

Aplicación web para convertir catálogos PDF a Excel. Incluye dos modos:

- **Modo V3 (IA – Groq)**: Extrae tablas con visión por IA. Detecta todas las columnas y rellena celdas vacías. Ideal para catálogos con muchas columnas (ej. Tornillos Toro). Requiere `GROQ_API_KEY`.
- **Retab**: Extrae tablas usando la API de Retab. Requiere `RETAB_API_KEY`.

---

## Instalación

### Dependencias Python

```bash
pip install -r requirements.txt
```

### Variables de entorno

Copia `.env.example` a `.env` y configura las claves que vayas a usar:

```bash
# Para Modo V3 (obtener en https://console.groq.com/keys)
GROQ_API_KEY=tu-clave-groq

# Para Retab (obtener en https://retab.com)
RETAB_API_KEY=tu-clave-retab
```

---

## Uso

Iniciar el servidor:

```bash
python app.py
```

Abre http://127.0.0.1:5000, sube un PDF y elige **Convertir con V3** o **Convertir con Retab**. La descarga será un Excel con una hoja por página.

### Uso desde aplicación de escritorio (API con progreso)

Para integrar desde FoxPro, .NET u otra app de escritorio: se usa un flujo asíncrono con **progreso en porcentaje** y protección por **API Key**. Detalle completo en **[API-USO.md](API-USO.md)**.

- **POST /convert-v3/job** → envías el PDF con la cabecera `X-API-Key`; respuesta `job_id` y `total_pages`.
- **GET /convert-v3/job/<job_id>/status** → consultas cada poco; respuesta `progress` (0–100), `message`, `status` (processing/completed/failed).
- **GET /convert-v3/job/<job_id>/result** → cuando `status` es `completed`, descargas el Excel.

En el servidor se configura `PDF2XLS_API_KEY` en `.env`; la misma clave la usa la app de escritorio en cada petición.

---

## Despliegue (Gunicorn + systemd, puerto 8090)

Para producción con Gunicorn y un servicio systemd, ver **[DEPLOY.md](DEPLOY.md)**. Incluye `deploy/pdf2xls.service` y pasos para instalar, habilitar y usar el servicio.

## Estructura del proyecto

- `app.py` – Flask: rutas `/convert-v3` y `/convert-retab`, filtrado de filas para Retab.
- `pdf_converter_ai_v3.py` – Extracción con Groq: detección de columnas y extracción por página (todas las columnas, celdas vacías como `""`).
- `gunicorn.conf.py` – Configuración de Gunicorn (bind 0.0.0.0:8090 por defecto).
- `deploy/pdf2xls.service` – Unit systemd para instalar en `/etc/systemd/system/`.

---

## Licencia

MIT.
