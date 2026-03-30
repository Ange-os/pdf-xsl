"""
Backend API sin formularios HTML: conversión V3, jobs asíncronos y gestión de prompts .md en el VPS.
Ejecutar: python app2.py  o  flask --app app2 run
"""
from pathlib import Path

# Cargar .env (GROQ_API_KEY, PDF2XLS_API_KEY, PROMPTS_LOCAL_DIR, etc.)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

import hmac
import io
import os
import re
import subprocess
from typing import Optional
import tempfile
import threading
import uuid

import pandas as pd
import pdfplumber
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS

app = Flask(__name__)


def _cors_allowed_origins():
    """
    Orígenes del *frontend* que pueden llamar a la API desde el navegador (CORS).
    Definí CORS_ORIGINS en .env (lista separada por comas), p. ej.:
      CORS_ORIGINS=https://pdf2xls.xia.ar
    La API puede estar en otro host (ej. https://startup.xia.ar); el origen sigue siendo el del panel.
    Si CORS_ORIGINS está vacío, se usan valores por defecto solo para desarrollo local.
    """
    raw = (os.environ.get("CORS_ORIGINS") or "").strip()
    if raw:
        return [o.strip() for o in raw.split(",") if o.strip()]
    return [
        "https://pdf2xls.xia.ar",
        "http://pdf2xls.xia.ar",
    ]


CORS(
    app,
    origins=_cors_allowed_origins(),
    allow_headers=["Content-Type", "Authorization", "X-API-Key"],
    expose_headers=["Content-Disposition", "Content-Type"],
    max_age=600,
)

# API Key para endpoints protegidos (jobs + prompts)
PDF2XLS_API_KEY = (os.environ.get("PDF2XLS_API_KEY") or "").strip().strip('"\'')
# Deploy: POST /deploy con header X-Deploy-Token (ver DEPLOY_SECRET en .env)
DEPLOY_SECRET = (os.environ.get("DEPLOY_SECRET") or "").strip()
_JOBS = {}
_JOBS_LOCK = threading.Lock()

# Nombre de archivo permitido para prompts (evita path traversal)
_PROMPT_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*\.md$")

# Slug estable → archivo usado por pdf_converter_ai_v3 (GET/PUT sin buscar el nombre del .md)
PROMPT_SLUGS = {
    "detectar-encabezados-columnas": "detectar_encabezados_columnas.md",
    "extraer-filas-tabla": "extraer_filas_tabla.md",
    "reintentar-pagina-vacia": "reintentar_pagina_vacia.md",
    "reintentar-pagina-incompleta": "reintentar_pagina_incompleta.md",
}


def _deploy_repo_path() -> Path:
    """Raíz del repo para `git pull`. DEPLOY_REPO_PATH en .env o carpeta de app2.py."""
    raw = (os.environ.get("DEPLOY_REPO_PATH") or "").strip()
    if raw:
        return Path(raw).resolve()
    return Path(__file__).resolve().parent


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


def _prompts_dir() -> Path:
    """
    Misma resolución que pdf_converter_ai_v3: PROMPTS_LOCAL_DIR o carpeta ./prompts junto al proyecto.
    """
    d = (os.environ.get("PROMPTS_LOCAL_DIR") or "").strip()
    if d:
        return Path(d).resolve()
    default = Path(__file__).resolve().parent / "prompts"
    return default


def _safe_prompt_filename(name: str) -> Optional[str]:
    if not name or not _PROMPT_NAME_RE.match(name):
        return None
    if ".." in name or "/" in name or "\\" in name:
        return None
    return name


def _prompt_resolved_path(filename: str):
    """Path seguro bajo prompts_dir o None si la ruta sale del directorio."""
    base = _prompts_dir()
    path = (base / filename).resolve()
    try:
        path.relative_to(base.resolve())
    except ValueError:
        return None, base
    return path, base


def _prompt_get_response(filename: str, slug: Optional[str] = None):
    path, _base = _prompt_resolved_path(filename)
    if path is None:
        return jsonify({"error": "Ruta inválida"}), 400
    if not path.is_file():
        return jsonify({"error": "No encontrado", "filename": filename}), 404
    text = path.read_text(encoding="utf-8")
    out = {"filename": filename, "content": text}
    if slug:
        out["slug"] = slug
    return jsonify(out)


def _prompt_put_response(filename: str):
    base = _prompts_dir()
    if not base.is_dir():
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return jsonify({"error": f"No se pudo crear la carpeta de prompts: {e}", "path": str(base)}), 500
    path, _ = _prompt_resolved_path(filename)
    if path is None:
        return jsonify({"error": "Ruta inválida"}), 400

    if request.is_json and request.json is not None:
        content = request.json.get("content")
        if content is None:
            return jsonify({"error": "JSON requiere la clave 'content' (string)"}), 400
        if not isinstance(content, str):
            return jsonify({"error": "'content' debe ser un string"}), 400
    else:
        content = request.get_data(as_text=True)
        if content is None:
            content = ""

    path.write_text(content, encoding="utf-8")
    from pdf_converter_ai_v3 import clear_prompt_cache

    clear_prompt_cache()
    return jsonify({"ok": True, "filename": filename, "bytes": len(content.encode("utf-8"))})


def _v3_df_to_excel_single_sheet(df):
    """Genera Excel con una hoja 'Principal'. Sin columna Página en el archivo final."""
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        if df is not None and not df.empty:
            out = df.drop(columns=["Página"], errors="ignore")
            out.to_excel(writer, sheet_name="Principal", index=False)
        else:
            pd.DataFrame().to_excel(writer, sheet_name="Principal", index=False)
    return output.getvalue()


@app.route("/")
def home():
    return_string = "dasdeqw"
    return return_string, 200, {"Content-Type": "text/plain; charset=utf-8"}
@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200


@app.route("/deploy", methods=["POST"])
def deploy():
    """
    git pull en el VPS + reinicio del servicio systemd (Gunicorn).
    Requiere DEPLOY_SECRET en .env y cabecera X-Deploy-Token con el mismo valor.
    El restart usa Popen para que la respuesta HTTP se envíe antes de que muera el worker.
    """
    token = request.headers.get("X-Deploy-Token", "")
    if not DEPLOY_SECRET or not hmac.compare_digest(token, DEPLOY_SECRET):
        return jsonify({"error": "Unauthorized"}), 401

    repo = _deploy_repo_path()
    unit = (os.environ.get("DEPLOY_SYSTEMD_UNIT") or "pdf2xls.service").strip()
    if not unit or not re.fullmatch(r"[a-zA-Z0-9_\-]+\.service", unit):
        return jsonify({"error": "DEPLOY_SYSTEMD_UNIT inválido"}), 500

    try:
        pull = subprocess.run(
            ["git", "pull"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if pull.returncode != 0:
            return (
                jsonify(
                    {
                        "error": "git pull falló",
                        "stderr": (pull.stderr or "")[:8000],
                        "stdout": (pull.stdout or "")[:8000],
                    }
                ),
                500,
            )

        popen_kw = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if os.name != "nt":
            popen_kw["start_new_session"] = True
        subprocess.Popen(
                ["sudo", "-n", "/bin/systemctl", "stop", "pdf2xls.service"],
            )

        return jsonify(
            {
                "ok": True,
                "git": pull.stdout or "",
                "note": "reiniciando servicio…",
                "unit": unit,
            }
        )

    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timeout en git pull"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/convert-v3", methods=["POST"])
def convert_pdf_to_xls_v3():
    """
    Endpoint v3: todas las columnas detectadas y celdas vacías como "".
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
    Inicia conversión en segundo plano. Requiere X-API-Key o Authorization: Bearer.
    Body: multipart/form-data con campo "file" (PDF).
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


@app.route("/prompts", methods=["GET"])
def prompts_list():
    """Lista archivos .md y los slugs fijos /prompts/v/<slug>."""
    ok, err = _require_api_key()
    if not ok:
        return err[0], err[1]
    base = _prompts_dir()
    if not base.is_dir():
        return jsonify({"error": "Carpeta de prompts no existe", "path": str(base)}), 404
    files = sorted(p.name for p in base.iterdir() if p.is_file() and p.suffix.lower() == ".md")
    by_slug = [{"slug": s, "file": f} for s, f in PROMPT_SLUGS.items()]
    return jsonify({"prompts_dir": str(base), "files": files, "prompt_slugs": by_slug})


@app.route("/prompts/v/<slug>", methods=["GET", "PUT"])
def prompts_by_slug(slug):
    """
    Un endpoint por prompt V3 (sin recordar el nombre del .md).
    GET/PUT https://startup.xia.ar/prompts/v/extraer-filas-tabla
    """
    ok, err = _require_api_key()
    if not ok:
        return err[0], err[1]
    filename = PROMPT_SLUGS.get(slug)
    if not filename:
        return jsonify({"error": "Slug desconocido", "known_slugs": list(PROMPT_SLUGS.keys())}), 404
    if request.method == "GET":
        return _prompt_get_response(filename, slug=slug)
    return _prompt_put_response(filename)


@app.route("/prompts/<name>", methods=["GET"])
def prompts_get(name):
    ok, err = _require_api_key()
    if not ok:
        return err[0], err[1]
    safe = _safe_prompt_filename(name)
    if not safe:
        return jsonify({"error": "Nombre de archivo no permitido"}), 400
    return _prompt_get_response(safe)


@app.route("/prompts/<name>", methods=["PUT"])
def prompts_put(name):
    ok, err = _require_api_key()
    if not ok:
        return err[0], err[1]
    safe = _safe_prompt_filename(name)
    if not safe:
        return jsonify({"error": "Nombre de archivo no permitido"}), 400
    return _prompt_put_response(safe)


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
