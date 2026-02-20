from pathlib import Path

# Cargar .env (GROQ_API_KEY, RETAB_API_KEY)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

from flask import Flask, request, send_file, render_template_string
import base64
import io
import os
import re
import tempfile

import pandas as pd
import pdfplumber

app = Flask(__name__)

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
  </body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(UPLOAD_FORM)


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
        download_name="catalogo_v3.xlsx",
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
