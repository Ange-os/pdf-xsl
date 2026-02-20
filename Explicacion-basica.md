# Backend Flask para Conversión de PDF a Excel

## 📌 Descripción
Este proyecto implementa un **backend en Flask** que recibe un archivo PDF a través de un endpoint y devuelve un archivo Excel (.xls/.xlsx) con la información procesada.  
La finalidad es integrar este servicio en aplicaciones de escritorio (ej. FoxPro), permitiendo automatizar la conversión de catálogos de productos o listas de precios.

---

## 🚀 Flujo de la Aplicación
1. **Carga del PDF**  
   - El usuario accede a un formulario HTML simple (`/`) o envía el archivo vía `POST` a `/convert-v3` (IA Groq) o `/convert-retab` (API Retab).
   - El archivo se recibe como `multipart/form-data`.

2. **Extracción de texto**  
   - Se utiliza **pdfplumber** para leer el contenido del PDF.
   - Cada línea se procesa y se almacena en memoria.

3. **Clasificación inicial**  
   - Actualmente, el sistema guarda dos columnas:  
     - `Página`: número de página del PDF.  
     - `Contenido`: texto extraído.  
   - Se probó integrar modelos de **HuggingFace (NER)** para clasificar entidades, pero los modelos genéricos no reconocen bien patrones específicos como `Producto`, `Precio`, `Descripción`.

4. **Exportación a Excel**  
   - Se genera un archivo Excel en memoria con `pandas` y `openpyxl/xlsxwriter`.
   - El archivo se devuelve como descarga (`application/vnd.ms-excel`).

---

## 📂 Estructura del Proyecto