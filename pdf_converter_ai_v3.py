#!/usr/bin/env python3
"""
Módulo v3: Misma lógica que v2 (detección de columnas + extracción por IA) pero con
prompts que piden TODAS las columnas detectadas (sin límite de 7) y rellenar con ""
las celdas vacías (ej. Detalle, Cant. x bulto). Pensado para catálogos como Tornillos Toro
con 11 columnas: Ord, Código, Producto, Detalle, Packaging, Un. x pack, Origen,
Cant. x bulto, Precio de lista, Precio NETO, Precio NETO unitario.
"""

import base64
import json
import os
import re
import sys

import pandas as pd

# Límite razonable de columnas para incluir en el prompt (v2 usaba 7 y cortaba 4)
MAX_COLUMNS_IN_PROMPT = 20


def _pdf_page_to_image(pdf_path, page_num, dpi=180):
    """Convierte una página del PDF a imagen PNG (base64) usando pymupdf."""
    try:
        import fitz
    except ImportError:
        return None
    try:
        doc = fitz.open(pdf_path)
        page = doc[page_num - 1]
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        png_bytes = pix.tobytes("png")
        doc.close()
        return base64.b64encode(png_bytes).decode("utf-8")
    except Exception:
        return None


GROQ_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
DEFAULT_COLUMNS = ["Ord", "Código", "Producto", "Detalle", "Packaging", "Un. x pack",
                  "Origen", "Cant. x bulto", "Precio de lista", "Precio NETO", "Precio NETO unitario"]

# Detección: igual que v2 pero aceptando tablas con muchas columnas (encabezado oscuro/gris)
PROMPT_DETECT_HEADERS_V3 = """Lista de precios o catálogo con tabla de productos.

Estructura típica:
- Una fila de encabezados (fondo oscuro o gris) con los nombres de cada columna.
- Puede haber títulos de sección (ej. nombre del fabricante) que NO son columnas; solo la fila con los nombres de datos es el encabezado.
- La tabla puede tener muchas columnas (ej. Ord, Código, Producto, Detalle, Packaging, Un. x pack, Origen, Cant. x bulto, Precio de lista, Precio NETO, Precio NETO unitario).

Identifica TODOS los títulos de la fila de encabezados de la tabla, en el orden en que aparecen.
Devuelve JSON: {"columnas": ["Nombre1", "Nombre2", ...]} con exactamente los nombres de esa fila.
Si no hay tabla, devuelve: {"columnas": []}"""


def _build_extract_prompt_v3(columns):
    """Prompt V3: pide TODAS las columnas y exige "" para celdas vacías."""
    if not columns:
        columns = DEFAULT_COLUMNS
    # Usar todas las columnas detectadas (hasta un máximo razonable), no solo 7
    cols_slice = columns[:MAX_COLUMNS_IN_PROMPT]
    cols_list = ", ".join(f'"{c}"' for c in cols_slice)
    obj = "{" + ", ".join(f'"{c}":"valor o vacío"' for c in cols_slice) + "}"
    return f"""Extrae TODAS las filas de la tabla de esta imagen.

Reglas importantes:
1. Columnas a devolver (usa exactamente estos nombres, en cada fila): {cols_list}
2. Devuelve SIEMPRE todas esas columnas en cada objeto. Si una celda está vacía (ej. Detalle, Cant. x bulto cuando no aplica), usa cadena vacía "".
3. No omitas columnas aunque estén vacías en parte de la tabla (ej. "Cant. x bulto" vacío para algunos productos y con número para otros).
4. Incluye desde la primera hasta la última fila de datos. No omitas filas.
5. Precios: conserva el formato que veas (ej. $ 1,234.5 o número).

Devuelve SOLO un JSON array: [{obj}, ...] — sin texto antes ni después. [] solo si la página no tiene tabla."""


PROMPT_RETRY_EMPTY_V3 = """Esta imagen puede contener una tabla de productos. Revisa de nuevo.

Extrae TODAS las filas. Para cada fila devuelve TODAS las columnas que se detectaron antes (mismo orden y nombres). Si una celda está vacía, usa "".
Devuelve SOLO un JSON array de objetos. [] solo si la página está vacía (portada, sin tabla)."""


INCOMPLETE_PAGE_THRESHOLD = 55


def _build_retry_incomplete_prompt_v3(columns):
    """Prompt para recuperar filas de la parte inferior; pide todas las columnas y "" si vacío."""
    cols = columns[:MAX_COLUMNS_IN_PROMPT] if columns else DEFAULT_COLUMNS
    cols_str = ", ".join(f'"{c}"' for c in cols)
    return f"""En esta MISMA imagen hay MÁS filas en la tabla (parte inferior). Extrae TODOS los productos de la mitad inferior.

Usa exactamente estas columnas en cada objeto: {cols_str}. Si una celda está vacía, usa "".
Devuelve SOLO un JSON array de objetos, sin texto antes ni después."""


def _get_codigo(row):
    """Obtiene el código de producto de una fila (cualquier variante de nombre)."""
    for key in ("Codigo", "codigo", "Código", "código", "ARTICULO", "Articulo"):
        v = row.get(key)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _merge_page_products(first_list, second_list, page_num):
    """Fusiona dos listas de productos de la misma página; evita duplicados por Código."""
    seen_codigos = {_get_codigo(p) for p in first_list}
    out = list(first_list)
    for p in second_list:
        cod = _get_codigo(p)
        if cod and cod not in seen_codigos:
            seen_codigos.add(cod)
            p["Página"] = page_num
            out.append(p)
    return out


def _extract_json_from_text(text):
    """Extrae JSON de texto (array o dict), tolerando preámbulos de la IA."""
    if not text or not isinstance(text, str):
        return None
    text = text.strip()
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    m = re.search(r'\{[^{}]*"columnas"\s*:\s*\[[^\]]*\]\s*\}', text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("[")
    if start >= 0:
        depth = 0
        for i, c in enumerate(text[start:], start=start):
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
    return None


def _parse_column_headers_response(text):
    """Extrae lista de columnas del texto de respuesta."""
    data = _extract_json_from_text(text or "")
    if data and isinstance(data, dict):
        cols = data.get("columnas")
        if cols and isinstance(cols, list):
            return [str(c).strip() for c in cols if c]
    return []


def _concept_of_column(col):
    """Identifica el concepto de una columna para mapeo de aliases (V3: más columnas)."""
    c = (col or "").lower()
    if "ord" in c and ("orden" in c or c.strip() == "ord"):
        return "ord"
    if "codigo" in c or "código" in c or "articulo" in c or "modelo" in c or "sku" in c:
        return "codigo"
    if "descripcion" in c or "descripción" in c or "producto" in c or "nombre" in c:
        return "descripcion"
    if "detalle" in c:
        return "detalle"
    if "packaging" in c or "pack" in c:
        return "packaging"
    if "un. x pack" in c or "un x pack" in c or "unidades" in c:
        return "un_x_pack"
    if "origen" in c:
        return "origen"
    if "cant. x bulto" in c or "cant x bulto" in c or "bulto" in c:
        return "cant_x_bulto"
    if "precio de lista" in c or "precio lista" in c:
        return "precio_lista"
    if "precio neto" in c and "unitario" not in c:
        return "precio_neto"
    if "precio neto unitario" in c or "neto unitario" in c:
        return "precio_neto_unitario"
    if "precio" in c or "costo" in c or "importe" in c or "p.s.v" in c or "lista" in c:
        return "precio"
    if "iva" in c or "i.v.a" in c:
        return "iva"
    if "modificado" in c:
        return "precios_mod"
    if "ean" in c or "barras" in c:
        return "ean"
    if "fecha" in c:
        return "fecha"
    return None


def _best_match_expected(key, expected_columns):
    """Encuentra la columna esperada que mejor coincide con la clave."""
    k = (key or "").lower().replace(" ", "").replace(".", "")
    for exp in expected_columns:
        e = (exp or "").lower().replace(" ", "").replace(".", "")
        if k == e or k in e or e in k:
            return exp
    return None


def _normalize_row(row, expected_columns):
    """Mapea alias hacia los nombres en expected_columns."""
    if not expected_columns:
        return
    expected_by_concept = {}
    for col in expected_columns:
        conc = _concept_of_column(col)
        if conc and conc not in expected_by_concept:
            expected_by_concept[conc] = col

    alias_concept = {
        "ord": "ord", "orden": "ord",
        "codigo": "codigo", "código": "codigo", "modelo": "codigo", "articulo": "codigo",
        "descripcion": "descripcion", "descripción": "descripcion", "producto": "descripcion",
        "detalle": "detalle",
        "packaging": "packaging", "pack": "packaging",
        "un_x_pack": "un_x_pack", "un. x pack": "un_x_pack", "unidades": "un_x_pack",
        "origen": "origen",
        "cant_x_bulto": "cant_x_bulto", "cant. x bulto": "cant_x_bulto", "bulto": "cant_x_bulto",
        "precio_lista": "precio_lista", "precio de lista": "precio_lista",
        "precio_neto": "precio_neto", "precio neto": "precio_neto",
        "precio_neto_unitario": "precio_neto_unitario", "precio neto unitario": "precio_neto_unitario",
        "i.v.a": "iva", "iva": "iva",
        "precios modificados": "precios_mod", "preciosmodificados": "precios_mod",
        "fecha": "fecha", "ean": "ean",
    }
    keys = list(row.keys())
    for k in keys:
        if k in expected_columns:
            continue
        k_low = k.lower().strip()
        target = _best_match_expected(k, expected_columns)
        if not target:
            conc = alias_concept.get(k_low) or alias_concept.get(k_low.replace(" ", ""))
            if conc:
                target = expected_by_concept.get(conc)
        if not target or target == k:
            continue
        val = row.pop(k, None)
        if val is not None and (target not in row or not str(row.get(target, "")).strip()):
            row[target] = val


def _parse_ai_products(text, page_num, expected_columns=None, debug=False):
    """Parsea la respuesta de IA a lista de dicts."""
    products = []
    data = _extract_json_from_text(text or "")
    if data is None and text:
        try:
            data = json.loads((text or "").strip())
        except json.JSONDecodeError:
            pass
    if data is None and debug:
        preview = (text or "")[:600]
        print(f"[V3] ⚠️ No se pudo parsear JSON. Preview: {preview}...", flush=True)
    if isinstance(data, list):
        for p in data:
            if isinstance(p, dict):
                row = p.copy()
                _normalize_row(row, expected_columns or [])
                row["Página"] = page_num
                products.append(row)
    return products


def _detect_column_headers(pdf_path, client, num_pages=3):
    """Detecta columnas en las primeras páginas (V3: prompt que acepta muchas columnas)."""
    pages_to_try = [p for p in [1, 2, 3] if p <= num_pages]
    for page_num in pages_to_try:
        img_b64 = _pdf_page_to_image(pdf_path, page_num)
        if not img_b64:
            continue
        try:
            data_url = f"data:image/png;base64,{img_b64}"
            completion = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": PROMPT_DETECT_HEADERS_V3},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    }
                ],
            )
            text = completion.choices[0].message.content if completion.choices else ""
            cols = _parse_column_headers_response(text or "")
            if cols and isinstance(cols, list) and len(cols) >= 2:
                print(f"[V3] 📋 Columnas detectadas en pág. {page_num}: {', '.join(cols)}", flush=True)
                sys.stdout.flush()
                return cols
        except Exception as e:
            if page_num == 1:
                print(f"[V3] ⚠️ Pág. 1: {e}. Probando siguiente...", flush=True)
                sys.stdout.flush()
            continue
    print("[V3] ⚠️ No se detectaron columnas. Usando estándar (11 columnas tipo Tornillos Toro).", flush=True)
    sys.stdout.flush()
    return DEFAULT_COLUMNS


def extract_with_groq_ai_v3(pdf_path, page_numbers, debug_zero=True):
    """
    Extrae productos usando Groq con prompts V3: todas las columnas detectadas
    y celdas vacías como "" (para completar Detalle, Cant. x bulto, etc.).
    """
    api_key = (os.environ.get("GROQ_API_KEY") or "").strip().strip('"\'')
    if not api_key:
        print("   ⚠️  GROQ_API_KEY no configurada.")
        return None

    try:
        from groq import Groq
    except ImportError:
        print("   ⚠️  groq no instalado. Ejecuta: pip install groq")
        return None

    client = Groq(api_key=api_key)
    num_pages_pdf = max(page_numbers) if page_numbers else 3

    print("[V3] 📋 Detectando estructura de columnas (págs. 1-3)...", flush=True)
    sys.stdout.flush()
    columns = _detect_column_headers(pdf_path, client, num_pages=num_pages_pdf)
    extract_prompt = _build_extract_prompt_v3(columns)

    all_products = []
    total = len(page_numbers)

    for i, page_num in enumerate(page_numbers, 1):
        print(f"[V3] 📄 Página {page_num} [{i}/{total}] → Enviando a API...", flush=True)
        sys.stdout.flush()
        img_b64 = _pdf_page_to_image(pdf_path, page_num)
        if not img_b64:
            print(f"[V3] ❌ Página {page_num}: No se pudo convertir a imagen", flush=True)
            sys.stdout.flush()
            continue

        try:
            data_url = f"data:image/png;base64,{img_b64}"
            completion = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": extract_prompt},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    }
                ],
                max_tokens=8192,
            )
            text = completion.choices[0].message.content if completion.choices else "[]"
            products = _parse_ai_products(text, page_num, columns, debug=debug_zero and len(all_products) == 0)

            if len(products) == 0:
                print(f"[V3] ⚠️ Página {page_num}: 0 productos. Reintentando con prompt alternativo...", flush=True)
                sys.stdout.flush()
                completion = client.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": PROMPT_RETRY_EMPTY_V3},
                                {"type": "image_url", "image_url": {"url": data_url}},
                            ],
                        }
                    ],
                    max_tokens=8192,
                )
                text = completion.choices[0].message.content if completion.choices else "[]"
                products = _parse_ai_products(text, page_num, columns, debug=False)
            elif 0 < len(products) < INCOMPLETE_PAGE_THRESHOLD:
                print(f"[V3] ⚠️ Página {page_num}: {len(products)} productos (posible incompleto). Reintentando parte inferior...", flush=True)
                sys.stdout.flush()
                retry_prompt = _build_retry_incomplete_prompt_v3(columns)
                completion2 = client.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": retry_prompt},
                                {"type": "image_url", "image_url": {"url": data_url}},
                            ],
                        }
                    ],
                    max_tokens=8192,
                )
                text2 = completion2.choices[0].message.content if completion2.choices else "[]"
                extra = _parse_ai_products(text2, page_num, columns, debug=False)
                if extra:
                    products = _merge_page_products(products, extra, page_num)
                    print(f"[V3]   → Fusionados: {len(products)} productos en pág. {page_num}", flush=True)
                    sys.stdout.flush()

            all_products.extend(products)

            if len(products) == 0 and debug_zero and page_num == 1:
                preview = (text or "")[:500]
                print(f"[V3] ⚠️ Pág. 1: 0 productos. Respuesta (primeros 500 chars): {preview}...", flush=True)
                sys.stdout.flush()

            print(f"[V3] ✓ Página {page_num}: {len(products)} productos", flush=True)
            sys.stdout.flush()
        except Exception as e:
            print(f"[V3] ❌ Página {page_num} Error: {e}", flush=True)
            sys.stdout.flush()

    if not all_products:
        return None

    df = pd.DataFrame(all_products)
    cols = list(columns) + ["Página"]
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    # Rellenar celdas vacías (NaN) con "" para que todas las columnas queden completas
    df = df.fillna("")
    # Orden: primero las columnas detectadas en orden, luego Página
    col_order = [c for c in cols if c in df.columns]
    df = df[col_order]
    return df


if __name__ == "__main__":
    import sys as _sys
    if len(_sys.argv) < 2:
        print("Uso: python pdf_converter_ai_v3.py <ruta.pdf>")
        _sys.exit(1)
    path = _sys.argv[1]
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        n = len(pdf.pages)
    result = extract_with_groq_ai_v3(path, list(range(1, n + 1)))
    if result is not None and not result.empty:
        out = path.replace(".pdf", "_v3.xlsx")
        result.to_excel(out, index=False)
        print(f"Guardado: {out}")
    else:
        print("No se extrajeron productos.")
