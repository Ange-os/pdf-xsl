from pathlib import Path

# Cargar .env (GROQ_API_KEY, RETAB_API_KEY)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

from flask import Flask, request, send_file, render_template_string, jsonify
import base64
import io
import os
import re
import tempfile
import threading
import uuid

import pandas as pd
import pdfplumber

app = Flask(__name__)

# API Key para endpoints de escritorio (solo quien tenga la clave puede usar /convert-v3/job/*)
PDF2XLS_API_KEY = (os.environ.get("PDF2XLS_API_KEY") or "").strip().strip('"\'')
# Jobs asíncronos: job_id -> { status, progress, current_page, total_pages, message, result_bytes, filename, error }
_JOBS = {}
_JOBS_LOCK = threading.Lock()


def _require_api_key():
    """Comprueba X-API-Key o Authorization: Bearer. Retorna (True, None) o (False, response_401)."""
    if not PDF2XLS_API_KEY:
        return False, (jsonify({"error": "PDF2XLS_API_KEY no configurada en el servidor"}), 500)
    key = request.headers.get("X-API-Key") or ""
    if not key and request.headers.get("Authorization", "").startswith("Bearer "):
        key = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    if key != PDF2XLS_API_KEY:
        return False, (jsonify({"error": "API key inválida o faltante"}), 401)
    return True, None

# Patrones para filter_data_rows (usado por Retab)
PRICE_PATTERN = re.compile(r"\$[\d.]+")
EAN_PATTERN = re.compile(r"\b(\d{12,13})\b")

# Patrones para detectar filas que son títulos/subtítulos (no datos de producto)
ROW_TITLE_PATTERNS = [
    re.compile(r"^LISTA DE PRECIOS", re.IGNORECASE),
    re.compile(r"^COCINAS\s", re.IGNORECASE),
    re.compile(r"^HORNOS\s", re.IGNORECASE),
    re.compile(r"^ANAFES\s", re.IGNORECASE),
    re.compile(r"^BTA AIR TOOLS", re.IGNORECASE),
    re.compile(r"^Descripción expresada", re.IGNORECASE),
    re.compile(r"^Costo sin IVA\s*$", re.IGNORECASE),
    re.compile(r"^Modelo\s*$", re.IGNORECASE),
    re.compile(r"^Código\s*$", re.IGNORECASE),
    re.compile(r"^P\.S\.V\.", re.IGNORECASE),
    re.compile(r"^Pág\.\s*\d+", re.IGNORECASE),
]


def _is_title_row(row_text):
    """Indica si el texto de una fila parece ser título/subtítulo."""
    if not row_text or not str(row_text).strip():
        return True
    s = str(row_text).strip()
    for pat in ROW_TITLE_PATTERNS:
        if pat.search(s):
            return True
    return False


def _row_has_data_indicator(row_text):
    """Indica si la fila tiene precio o código de producto (señales de dato real)."""
    s = str(row_text)
    if PRICE_PATTERN.search(s):
        return True
    if re.search(r"\b\d{4,}\b", s):  # código 279000, 9936004, EAN, etc.
        return True
    return False


def _v3_df_to_excel_single_sheet(df):
    """
    Genera un Excel con una sola hoja 'Principal' con todas las filas del DataFrame.
    No incluye la columna Página en el archivo final. Sin IA, solo pandas/openpyxl.
    """
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        if df is not None and not df.empty:
            out = df.drop(columns=["Página"], errors="ignore")
            out.to_excel(writer, sheet_name="Principal", index=False)
        else:
            pd.DataFrame().to_excel(writer, sheet_name="Principal", index=False)
    return output.getvalue()


def filter_data_rows(df):
    """
    Filtra filas que son títulos/subtítulos.
    Conserva solo filas que parecen datos de producto (tienen precio o código).
    """
    if df is None or df.empty:
        return df
    keep_mask = []
    for idx, row in df.iterrows():
        row_text = " ".join(str(v) for v in row.values if pd.notna(v) and str(v).strip())
        if _is_title_row(row_text):
            keep_mask.append(False)
        elif _row_has_data_indicator(row_text):
            keep_mask.append(True)
        else:
            # Sin precio ni código: probablemente subtítulo, descartar
            keep_mask.append(False)
    return df[keep_mask].reset_index(drop=True)


UPLOAD_FORM = """
<!doctype html>
<html lang="es">
  <head>
    <meta charset="utf-8">
    <title>Conversión PDF → Excel</title>
  </head>
  <body>
    <h2>Catálogo PDF → Excel</h2>
    <h3>Modo V3 – IA (Groq)</h3>
    <p>Extrae tablas con todas las columnas detectadas. Ideal para catálogos con muchas columnas (ej. Tornillos Toro). Requiere GROQ_API_KEY.</p>
    <form method="POST" action="/convert-v3" enctype="multipart/form-data">
      <input type="file" name="file" accept=".pdf" required>
      <br><br>
      <button type="submit">Convertir con V3</button>
    </form>
    <hr>
    <h3>Retab – API Parse</h3>
    <p>Extrae tablas con la API de Retab. Requiere RETAB_API_KEY.</p>
    <form method="POST" action="/convert-retab" enctype="multipart/form-data">
      <input type="file" name="file" accept=".pdf" required>
      <br><br>
      <button type="submit">Convertir con Retab</button>
    </form>
    <hr>
    <p><a href="/convert-v3/test">Probar API (job + API Key + progreso)</a></p>
  </body>
</html>
"""


API_TEST_PAGE = """
<!doctype html>
<html lang="es">
  <head>
    <meta charset="utf-8">
    <title>Probar API (job + progreso)</title>
    <style>
      body { font-family: sans-serif; max-width: 520px; margin: 2rem auto; padding: 0 1rem; }
      label { display: block; margin-top: 1rem; font-weight: bold; }
      input[type="password"], input[type="file"] { margin-top: 0.25rem; width: 100%; }
      button { margin-top: 1rem; padding: 0.5rem 1rem; cursor: pointer; }
      #jobId { font-family: monospace; background: #f0f0f0; padding: 0.5rem; word-break: break-all; margin-top: 0.25rem; }
      #progressArea { margin-top: 1rem; display: none; }
      #progressBar { width: 100%; height: 24px; margin: 0.5rem 0; }
      #progressMsg { color: #666; font-size: 0.9rem; }
      #downloadArea { margin-top: 1rem; display: none; }
      .error { color: #c00; }
    </style>
  </head>
  <body>
    <h2>Probar API (job + API Key)</h2>
    <p>Envía un PDF al endpoint <code>/convert-v3/job</code> y sigue el progreso por <code>job_id</code>.</p>
    <label>API Key (PDF2XLS_API_KEY)</label>
    <input type="password" id="apiKey" placeholder="Pega aquí la clave">
    <label>Archivo PDF</label>
    <input type="file" id="pdfFile" accept=".pdf">
    <button type="button" id="btnStart">Enviar y seguir progreso</button>

    <div id="jobArea" style="display: none;">
      <label>job_id</label>
      <div id="jobId"></div>
    </div>
    <div id="progressArea">
      <label>Progreso</label>
      <progress id="progressBar" value="0" max="100">0%</progress>
      <div id="progressMsg"></div>
    </div>
    <div id="downloadArea">
      <a id="downloadLink" href="#" download>Descargar Excel</a>
    </div>
    <div id="errorArea" class="error"></div>

    <script>
      const apiKey = document.getElementById('apiKey');
      const pdfFile = document.getElementById('pdfFile');
      const btnStart = document.getElementById('btnStart');
      const jobArea = document.getElementById('jobArea');
      const jobIdEl = document.getElementById('jobId');
      const progressArea = document.getElementById('progressArea');
      const progressBar = document.getElementById('progressBar');
      const progressMsg = document.getElementById('progressMsg');
      const downloadArea = document.getElementById('downloadArea');
      const downloadLink = document.getElementById('downloadLink');
      const errorArea = document.getElementById('errorArea');

      function showError(msg) {
        errorArea.textContent = msg;
      }
      function clearError() {
        errorArea.textContent = '';
      }

      btnStart.addEventListener('click', async function() {
        const key = apiKey.value.trim();
        const file = pdfFile.files[0];
        clearError();
        jobArea.style.display = 'none';
        downloadArea.style.display = 'none';
        progressArea.style.display = 'block';
        progressBar.value = 0;
        progressMsg.textContent = '';

        if (!key) {
          showError('Escribe la API Key.');
          return;
        }
        if (!file) {
          showError('Elige un archivo PDF.');
          return;
        }

        const formData = new FormData();
        formData.append('file', file);
        const headers = { 'X-API-Key': key };

        try {
          const res = await fetch('/convert-v3/job', { method: 'POST', headers, body: formData });
          const data = await res.json().catch(() => ({}));
          if (res.status === 401) {
            showError('API Key inválida o faltante.');
            return;
          }
          if (res.status !== 202) {
            showError(data.error || 'Error al crear el job. ' + res.status);
            return;
          }
          const id = data.job_id;
          jobIdEl.textContent = id;
          jobArea.style.display = 'block';
          progressMsg.textContent = 'Iniciando... (0/' + (data.total_pages || '?') + ')';

          const poll = setInterval(async () => {
            const sRes = await fetch('/convert-v3/job/' + id + '/status', { headers: { 'X-API-Key': key } });
            const sData = await sRes.json().catch(() => ({}));
            if (sRes.status !== 200) {
              clearInterval(poll);
              showError(sData.error || 'Error al consultar estado.');
              return;
            }
            progressBar.value = sData.progress || 0;
            progressMsg.textContent = (sData.message || '') + ' (' + (sData.progress || 0) + '%)';
            if (sData.status === 'completed') {
              clearInterval(poll);
              progressMsg.textContent = 'Listo. Haz clic en "Descargar Excel".';
              downloadLink.onclick = function(e) {
                e.preventDefault();
                fetch('/convert-v3/job/' + id + '/result', { headers: { 'X-API-Key': key } })
                  .then(r => r.blob())
                  .then(blob => {
                    const a = document.createElement('a');
                    a.href = URL.createObjectURL(blob);
                    a.download = (file.name || 'catalogo').replace(/\\.pdf$/i, '') + '_v3.xlsx';
                    a.click();
                    URL.revokeObjectURL(a.href);
                  })
                  .catch(err => showError('Error al descargar: ' + err.message));
              };
              downloadArea.style.display = 'block';
            } else if (sData.status === 'failed') {
              clearInterval(poll);
              showError('Error: ' + (sData.error || 'unknown'));
            }
          }, 1500);
        } catch (e) {
          showError('Error de red: ' + e.message);
        }
      });
    </script>
  </body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(UPLOAD_FORM)


@app.route("/convert-v3/test")
def convert_v3_test_page():
    """Página para probar el endpoint asíncrono con API Key y visualización de job_id y progreso."""
    return render_template_string(API_TEST_PAGE)


@app.route("/convert-v3", methods=["POST"])
def convert_pdf_to_xls_v3():
    """
    Endpoint v3: todas las columnas detectadas y celdas vacías como "".
    Para catálogos tipo Tornillos Toro (11 columnas: Ord, Código, Producto, Detalle, etc.).
    """
    import sys as _sys
    pdf_file = request.files["file"]
    groq_ok = bool((os.environ.get("GROQ_API_KEY") or "").strip())

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_file.read())
        tmp_path = tmp.name

    try:
        with pdfplumber.open(tmp_path) as pdf:
            num_pages = len(pdf.pages)

        print(f"\n[CONVERT-V3] PDF: {pdf_file.filename}, Páginas: {num_pages}", flush=True)
        _sys.stdout.flush()

        if not groq_ok:
            print("[CONVERT-V3] ⚠️ GROQ_API_KEY no configurada.", flush=True)
            _sys.stdout.flush()
            return "GROQ_API_KEY no configurada. Añádela a .env", 400

        from pdf_converter_ai_v3 import extract_with_groq_ai_v3
        df = extract_with_groq_ai_v3(tmp_path, list(range(1, num_pages + 1)), debug_zero=True)

        total_rows = len(df) if df is not None and not df.empty else 0
        print(f"[CONVERT-V3] ✓ Extracción completada. Total filas: {total_rows}", flush=True)
        _sys.stdout.flush()

    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    excel_bytes = _v3_df_to_excel_single_sheet(df)
    return send_file(
        io.BytesIO(excel_bytes),
        as_attachment=True,
        download_name="catalogo_v3.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def _run_job(job_id, tmp_path, num_pages, filename_pdf):
    """Ejecuta la conversión en segundo plano y actualiza el job con progreso y resultado."""
    import sys as _sys
    try:
        from pdf_converter_ai_v3 import extract_with_groq_ai_v3

        def on_progress(current_page, total_pages, progress_pct, message):
            with _JOBS_LOCK:
                if job_id not in _JOBS:
                    return
                _JOBS[job_id]["current_page"] = current_page
                _JOBS[job_id]["total_pages"] = total_pages
                _JOBS[job_id]["progress"] = progress_pct
                _JOBS[job_id]["message"] = message

        df = extract_with_groq_ai_v3(
            tmp_path,
            list(range(1, num_pages + 1)),
            debug_zero=True,
            progress_callback=on_progress,
        )

        with _JOBS_LOCK:
            if job_id not in _JOBS:
                return
            if df is None or df.empty:
                _JOBS[job_id]["status"] = "completed"
                _JOBS[job_id]["progress"] = 100
                _JOBS[job_id]["message"] = "Sin datos extraídos"
                _JOBS[job_id]["result_bytes"] = None
                return
            excel_bytes = _v3_df_to_excel_single_sheet(df)
            _JOBS[job_id]["status"] = "completed"
            _JOBS[job_id]["progress"] = 100
            _JOBS[job_id]["message"] = "Listo"
            _JOBS[job_id]["result_bytes"] = excel_bytes
            _JOBS[job_id]["filename"] = (filename_pdf or "catalogo").replace(".pdf", "") + "_v3.xlsx"
    except Exception as e:
        with _JOBS_LOCK:
            if job_id in _JOBS:
                _JOBS[job_id]["status"] = "failed"
                _JOBS[job_id]["error"] = str(e)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


@app.route("/convert-v3/job", methods=["POST"])
def convert_v3_job_create():
    """
    Inicia una conversión en segundo plano (para app de escritorio).
    Requiere cabecera: X-API-Key: <tu_clave> o Authorization: Bearer <tu_clave>
    Body: multipart/form-data con campo "file" (PDF).
    Respuesta 202: { "job_id": "...", "total_pages": N }. Luego consultar estado y resultado.
    """
    ok, err = _require_api_key()
    if not ok:
        return err[0], err[1]
    pdf_file = request.files.get("file")
    if not pdf_file:
        return jsonify({"error": "Falta el archivo PDF (campo 'file')"}), 400
    groq_ok = bool((os.environ.get("GROQ_API_KEY") or "").strip())
    if not groq_ok:
        return jsonify({"error": "GROQ_API_KEY no configurada en el servidor"}), 400

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_file.read())
        tmp_path = tmp.name
    try:
        with pdfplumber.open(tmp_path) as pdf:
            num_pages = len(pdf.pages)
    except Exception as e:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return jsonify({"error": f"No se pudo leer el PDF: {e}"}), 400

    job_id = str(uuid.uuid4())
    with _JOBS_LOCK:
        _JOBS[job_id] = {
            "status": "processing",
            "progress": 0,
            "current_page": 0,
            "total_pages": num_pages,
            "message": "Iniciando...",
            "result_bytes": None,
            "filename": "catalogo_v3.xlsx",
            "error": None,
        }
    t = threading.Thread(target=_run_job, args=(job_id, tmp_path, num_pages, pdf_file.filename or ""))
    t.daemon = True
    t.start()
    return jsonify({"job_id": job_id, "total_pages": num_pages}), 202


@app.route("/convert-v3/job/<job_id>/status", methods=["GET"])
def convert_v3_job_status(job_id):
    """
    Consulta el estado de un job. Requiere X-API-Key o Authorization: Bearer.
    Respuesta: { "status": "processing"|"completed"|"failed", "progress": 0-100, "current_page", "total_pages", "message" [, "error" ] }.
    """
    ok, err = _require_api_key()
    if not ok:
        return err[0], err[1]
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Job no encontrado"}), 404
    out = {
        "status": job["status"],
        "progress": job["progress"],
        "current_page": job["current_page"],
        "total_pages": job["total_pages"],
        "message": job["message"],
    }
    if job.get("error"):
        out["error"] = job["error"]
    return jsonify(out)


@app.route("/convert-v3/job/<job_id>/result", methods=["GET"])
def convert_v3_job_result(job_id):
    """
    Descarga el Excel cuando el job está completado. Requiere X-API-Key o Authorization: Bearer.
    Si status no es "completed", responde 404.
    """
    ok, err = _require_api_key()
    if not ok:
        return err[0], err[1]
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Job no encontrado"}), 404
    if job["status"] != "completed":
        return jsonify({"error": "El job aún no ha terminado", "status": job["status"]}), 404
    if not job.get("result_bytes"):
        return jsonify({"error": "No se generó archivo (sin datos)"}), 404
    return send_file(
        io.BytesIO(job["result_bytes"]),
        as_attachment=True,
        download_name=job.get("filename", "catalogo_v3.xlsx"),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


RETAB_EXPECTED_COLUMNS = [
    "Codigo", "Descripcion", "I.V.A", "Precios modificados",
    "Precio Lista", "Precio Lista (-DSV-Contado) Sin I.V.A", "P.S.V. Con I.V.A",
]


def _clean_retab_table(df, page_num):
    """
    Corrige tablas de Retab: elimina columnas extra (0,1,2...) y realinea datos
    cuando la estructura está desplazada (p. ej. página 2+ con datos en columnas numéricas).
    """
    if df is None or df.empty:
        return df
    df = df.copy()

    # Columnas con nombres numéricos (0, 1, 2, ...) que Retab a veces añade
    numeric_cols = [c for c in df.columns if str(c).strip() in [str(i) for i in range(25)]]

    # Caso 1: Datos en columnas nombradas (Codigo, Descripcion tienen valor)
    first_data_idx = 0
    for i, row in df.iterrows():
        codigo = row.get("Codigo", "")
        if pd.notna(codigo) and str(codigo).strip() and re.match(r"^\d", str(codigo)):
            first_data_idx = i
            break

    has_data_in_named = False
    if first_data_idx < len(df):
        row = df.iloc[first_data_idx]
        codigo_val = row.get("Codigo", "")
        if pd.notna(codigo_val) and str(codigo_val).strip():
            has_data_in_named = True

    if has_data_in_named:
        # Quitar columnas numéricas (0,1,2...) que son artefactos
        df = df.drop(columns=[c for c in numeric_cols if c in df.columns], errors="ignore")
    else:
        # Caso 2: Datos desplazados - están en columnas 0, 1, 2...
        # Buscar columna de inicio (primera con código numérico)
        start_col = None
        for idx, row in df.iterrows():
            for j in range(15):
                col = j if j in df.columns else (str(j) if str(j) in df.columns else None)
                if col is None or col not in df.columns:
                    continue
                val = row.get(col, "")
                if pd.notna(val) and str(val).strip():
                    s = str(val).replace(",", "").replace(".", "").strip()
                    if s.isdigit() and len(s) >= 4:
                        start_col = j
                        break
            if start_col is not None:
                break

        if start_col is not None:
            new_rows = []
            for _, row in df.iterrows():
                codigo_val = row.get(start_col, row.get(str(start_col), ""))
                if pd.isna(codigo_val) or not str(codigo_val).strip():
                    continue
                s = str(codigo_val).replace(",", "").replace(".", "").strip()
                if not (s.isdigit() and len(s) >= 4):
                    continue
                new_row = {}
                for k, col_name in enumerate(RETAB_EXPECTED_COLUMNS):
                    src = start_col + k
                    col = src if src in df.columns else (str(src) if str(src) in df.columns else None)
                    if col is not None:
                        new_row[col_name] = row.get(col, "")
                    else:
                        new_row[col_name] = ""
                new_row["Página"] = page_num
                new_rows.append(new_row)
            df = pd.DataFrame(new_rows) if new_rows else pd.DataFrame(columns=RETAB_EXPECTED_COLUMNS + ["Página"])

    df["Página"] = page_num
    # Conservar solo columnas esperadas + Página
    keep = [c for c in RETAB_EXPECTED_COLUMNS if c in df.columns] + ["Página"]
    extra = [c for c in df.columns if c not in keep]
    if extra:
        df = df.drop(columns=extra, errors="ignore")
    return df


def _retab_parse_single_page(requests, api_key, pdf_b64, page_num, total_pages, filename, table_format, timeout):
    """Envía una sola página a Retab y retorna el contenido (o None)."""
    payload = {
        "document": {"filename": filename, "url": f"data:application/pdf;base64,{pdf_b64}"},
        "model": "retab-small",
        "table_parsing_format": table_format,
        "image_resolution_dpi": 192,
    }
    try:
        resp = requests.post(
            "https://api.retab.com/v1/documents/parse",
            headers={"Api-Key": api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    data = resp.json()
    pages_content = data.get("pages") or []
    return pages_content[0] if pages_content else None


def extract_with_retab(pdf_path, table_format="html"):
    """
    Extrae tablas del PDF usando la API Retab Parse.
    https://docs.retab.com/api-reference/documents/parse
    Para PDFs de varias páginas: procesa página por página (evita timeout).
    Retorna DataFrame con columna Página o None.
    """
    import sys
    api_key = (os.environ.get("RETAB_API_KEY") or "").strip().strip('"\'')
    if not api_key:
        print("[RETAB] ⚠️ RETAB_API_KEY no configurada.", flush=True)
        sys.stdout.flush()
        return None
    try:
        import requests
    except ImportError:
        print("[RETAB] ⚠️ requests no instalado. Ejecuta: pip install requests", flush=True)
        sys.stdout.flush()
        return None

    try:
        import fitz  # pymupdf
    except ImportError:
        print("[RETAB] ⚠️ pymupdf no instalado para dividir páginas.", flush=True)
        sys.stdout.flush()
        return None

    filename = os.path.basename(pdf_path) or "document.pdf"
    timeout_seconds = 90  # por página
    doc = fitz.open(pdf_path)
    num_pages = len(doc)

    print(f"[RETAB] Procesando {num_pages} página(s) una por una...", flush=True)
    sys.stdout.flush()

    dfs = []
    for page_idx in range(num_pages):
        page_num = page_idx + 1
        single = fitz.open()
        single.insert_pdf(doc, from_page=page_idx, to_page=page_idx)
        pdf_bytes = single.write()
        single.close()
        pdf_b64 = base64.b64encode(pdf_bytes).decode("utf-8")

        print(f"[RETAB] Página {page_num}/{num_pages} → Enviando...", flush=True)
        sys.stdout.flush()
        content = _retab_parse_single_page(
            requests, api_key, pdf_b64, page_num, num_pages, filename, table_format, timeout_seconds
        )

        if content and isinstance(content, str):
            if table_format in ("html", "markdown"):
                try:
                    tables = pd.read_html(io.StringIO(content))
                    for t in tables:
                        if t is not None and not t.empty:
                            t = _clean_retab_table(t, page_num)
                            if t is not None and not t.empty:
                                dfs.append(t)
                except Exception:
                    pass
            else:
                dfs.append(pd.DataFrame([{"Contenido": content.strip(), "Página": page_num}]))
        print(f"[RETAB] ✓ Página {page_num}: extraída", flush=True)
        sys.stdout.flush()

    doc.close()

    if not dfs:
        return None
    full_df = pd.concat(dfs, ignore_index=True)
    return filter_data_rows(full_df)


@app.route("/convert-retab", methods=["POST"])
def convert_pdf_to_xls_retab():
    """Endpoint Retab: usa la API Parse de Retab para extraer tablas del PDF."""
    import sys as _sys
    pdf_file = request.files.get("file")
    if not pdf_file:
        return "No se envió archivo.", 400

    retab_ok = bool((os.environ.get("RETAB_API_KEY") or "").strip())
    if not retab_ok:
        return "RETAB_API_KEY no configurada. Añádela a .env", 400

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_file.read())
        tmp_path = tmp.name

    try:
        table_format = request.form.get("table_format", "html")
        df = extract_with_retab(tmp_path, table_format=table_format)

        total_rows = len(df) if df is not None and not df.empty else 0
        print(f"[CONVERT-RETAB] ✓ Extracción completada. Total filas: {total_rows}", flush=True)
        _sys.stdout.flush()

        sheets = {}
        if df is not None and not df.empty and "Página" in df.columns:
            for page_num, group in df.groupby("Página", sort=True):
                group_clean = group.drop(columns=["Página"], errors="ignore")
                if not group_clean.empty:
                    sheets[int(page_num)] = group_clean
        elif df is not None and not df.empty:
            sheets[1] = df
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        if sheets:
            for page_num in sorted(sheets.keys()):
                df_page = sheets[page_num]
                sheet_name = f"Pág {page_num}"[:31]
                df_page.to_excel(writer, sheet_name=sheet_name, index=False)
        else:
            pd.DataFrame().to_excel(writer, sheet_name="Vacío", index=False)

    output.seek(0)
    return send_file(
        output,
        as_attachment=True,
        download_name="catalogo_retab.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
