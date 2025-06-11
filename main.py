from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict
import pandas as pd
import fitz  # PyMuPDF
import re
from openpyxl import load_workbook

app = FastAPI()

# Permitir CORS desde cualquier origen
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class ConsumoRequest(BaseModel):
    dias_factura: int
    potencia: Dict[str, float]
    energia: Dict[str, float]

@app.post("/analizar-factura")
async def analizar_factura(file: UploadFile = File(...)):
    try:
        contents = await file.read()
        doc = fitz.open(stream=contents, filetype="pdf")
        text = "".join(page.get_text() for page in doc)
        doc.close()

        # Extracción robusta con valores por defecto
        def extract(pat, lbl, default=0.0, fmt=float):
            m = re.search(pat, text, flags=re.IGNORECASE)
            if not m:
                if default is not None:
                    return default
                raise ValueError(f"No se encontró {lbl}")
            return fmt(m.group(1).replace(",", "."))

        dias             = extract(r"DIAS FACTURADOS:\s*(\d+)", "días", fmt=int)
        potencia_punta   = extract(r"Potencia punta:\s*([\d,]+)\s*kW", "potencia punta")
        potencia_valle   = extract(r"Potencia valle:\s*([\d,]+)\s*kW", "potencia valle")
        consumo_punta    = extract(r"punta:\s*([\d,]+)\s*kWh", "consumo punta")
        consumo_llano    = extract(r"llano:\s*([\d,]+)\s*kWh", "consumo llano")
        consumo_valle    = extract(r"valle[: ]\s*([\d,]+)\s*kWh", "consumo valle")

        total_factura    = extract(r"TOTAL IMPORTE FACTURA\D*([\d,]+,[\d]{2})\s*€", "total factura")
        factura_impuesto = extract(r"IVA.*?([\d,]+,[\d]{2})\s*€", "IVA", default=0.0)
        factura_alquiler = extract(r"Alquiler equipos medida.*?([\d,]+,[\d]{2})\s*€", "alquiler", default=0.0)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al extraer datos del PDF: {e}")

    return {
        "dias_factura": dias,
        "potencia": {"punta": potencia_punta, "valle": potencia_valle},
        "energia": {"punta": consumo_punta, "llano": consumo_llano, "valle": consumo_valle},
        "factura_total": round(total_factura, 2),
        "factura_impuesto": round(factura_impuesto, 2),
        "factura_alquiler": round(factura_alquiler, 2)
    }

@app.post("/comparar-tarifas")
async def comparar_tarifas(consumo: ConsumoRequest):
    try:
        excel_path = "Comparador electricidad.v3 (1).xlsx"
        df = pd.read_excel(excel_path, sheet_name="Comparador")

        # Potencia
        pot = df.iloc[[0,6,7],4:].transpose().reset_index(drop=True)
        pot.columns = ["nombre","potencia_punta","potencia_valle"]
        pot = pot.apply(pd.to_numeric, errors="coerce")

        # Energía
        ene = df.iloc[[11,12,13],4:].transpose().reset_index(drop=True)
        ene.columns = ["energia_punta","energia_llano","energia_valle"]
        ene = ene.apply(pd.to_numeric, errors="coerce")

        # Enlaces: cargamos sin read_only
        wb = load_workbook(excel_path)
        ws = wb["Comparador"]
        enlaces = []
        for cell in ws[2][4:]:
            enlaces.append(cell.hyperlink.target if cell.hyperlink else "")
        wb.close()

        tarifas = pd.concat([pot, ene], axis=1).reset_index(drop=True)
        tarifas["enlace"] = enlaces
        tarifas = tarifas.dropna(subset=["potencia_punta","potencia_valle","energia_punta"])

        resultados = []
        for _, r in tarifas.iterrows():
            cp = (
                consumo.potencia["punta"] * r["potencia_punta"] * consumo.dias_factura +
                consumo.potencia["valle"] * r["potencia_valle"] * consumo.dias_factura
            )
            ce = (
                consumo.energia["punta"] * r["energia_punta"] +
                consumo.energia["llano"] * r["energia_llano"] +
                consumo.energia["valle"] * r["energia_valle"]
            )
            var = round(cp + ce, 2)
            total_fijo = consumo.__dict__.get("factura_impuesto", 0) + consumo.__dict__.get("factura_alquiler", 0)
            resultados.append({
                "tarifa": r["nombre"],
                "coste_variable": var,
                "coste_fijo": round(total_fijo, 2),
                "coste_total": round(var + total_fijo, 2),
                "enlace": r["enlace"]
            })

        resultados.sort(key=lambda x: x["coste_total"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al comparar tarifas: {e}")

    return JSONResponse(resultados)
