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

---

## Estructura del proyecto

- `app.py` – Flask: rutas `/convert-v3` y `/convert-retab`, filtrado de filas para Retab.
- `pdf_converter_ai_v3.py` – Extracción con Groq: detección de columnas y extracción por página (todas las columnas, celdas vacías como `""`).

---

## Licencia

MIT.
