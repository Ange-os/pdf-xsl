#!/usr/bin/env python3
"""
Script de prueba para conversión PDF → Excel (Modo V3).
Útil para probar sin levantar el servidor Flask.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from pdf_converter_ai_v3 import extract_with_groq_ai_v3
import pdfplumber
import pandas as pd


def test_v3(pdf_path, output_excel=None):
    """Prueba la conversión con Modo V3 (Groq)."""
    print("\n" + "=" * 60)
    print(f"  TEST V3: {Path(pdf_path).name}")
    print("=" * 60)

    if not os.path.exists(pdf_path):
        print(f"  Error: No se encuentra {pdf_path}")
        return

    groq_ok = bool((os.environ.get("GROQ_API_KEY") or "").strip())
    if not groq_ok:
        print("  Error: GROQ_API_KEY no configurada en .env")
        return

    with pdfplumber.open(pdf_path) as pdf:
        num_pages = len(pdf.pages)
    print(f"  Páginas: {num_pages}")

    df = extract_with_groq_ai_v3(pdf_path, list(range(1, num_pages + 1)), debug_zero=True)

    if df is None or df.empty:
        print("  No se extrajeron datos.")
        print("=" * 60 + "\n")
        return

    print(f"  Filas: {len(df)}")
    print(f"  Columnas: {', '.join(df.columns)}")
    if "Página" in df.columns:
        print(f"  Páginas con datos: {df['Página'].nunique()}")

    print("\n  Muestra (primeras 3 filas):")
    print(df.head(3).to_string())

    if output_excel:
        with pd.ExcelWriter(output_excel, engine="openpyxl") as writer:
            if "Página" in df.columns:
                for page_num, group in df.groupby("Página", sort=True):
                    group_clean = group.drop(columns=["Página"], errors="ignore")
                    if not group_clean.empty:
                        sheet_name = f"Pág {int(page_num)}"[:31]
                        group_clean.to_excel(writer, sheet_name=sheet_name, index=False)
            else:
                df.to_excel(writer, sheet_name="Datos", index=False)
        print(f"\n  Excel guardado: {output_excel}")

    print("=" * 60 + "\n")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Probar conversión PDF → Excel (V3)")
    p.add_argument("pdf_path", help="Ruta al PDF")
    p.add_argument("-o", "--output", help="Ruta del Excel de salida (opcional)")
    args = p.parse_args()
    test_v3(args.pdf_path, args.output)
