from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict
import pandas as pd
import fitz  # PyMuPDF
import re
from openpyxl import load_workbook

app = FastAPI()

# --- CORS para permitir llamadas desde frontend en cualquier dominio ---
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

        # Extraer datos
        dias = int(re.search(r"DIAS FACTURADOS:\s*(\d+)", text).group(1))
        potencia_punta = float(re.search(r"Potencia punta:\s*([\d,]+)", text).group(1).replace(",", "."))
        potencia_valle = float(re.search(r"Potencia valle:\s*([\d,]+)", text).group(1).replace(",", "."))
        consumo_punta = float(re.search(r"punta:\s*([\d,]+)\s*kWh", text).group(1).replace(",", "."))
        consumo_llano = float(re.search(r"llano:\s*([\d,]+)\s*kWh", text).group(1).replace(",", "."))
        consumo_valle = float(re.search(r"valle\s*([\d,]+)\s*kWh", text).group(1).replace(",", "."))

        total_factura = float(
            re.search(r"TOTAL IMPORTE FACTURA\s*([\d,]+,[\d]{2})\s*€", text).group(1).replace(",", ".")
        )
        iva = float(
            re.search(r"IVA.*?([\d,]+,[\d]{2})\s*€", text).group(1).replace(",", ".")
        )
        alquiler = float(
            re.search(r"Alquiler equipos medida.*?([\d,]+,[\d]{2})\s*€", text).group(1).replace(",", ".")
        )
    except Exception as e:
        # Devuelve el error para que puedas verlo en el frontend
        raise HTTPException(status_code=500, detail=f"Error al extraer datos del PDF: {str(e)}")

    return {
        "dias_factura": dias,
        "potencia": {"punta": potencia_punta, "valle": potencia_valle},
        "energia": {"punta": consumo_punta, "llano": consumo_llano, "valle": consumo_valle},
        "factura_total": round(total_factura, 2),
        "factura_impuesto": round(iva, 2),
        "factura_alquiler": round(alquiler, 2)
    }

@app.post("/comparar-tarifas")
async def comparar_tarifas(consumo: ConsumoRequest):
    try:
        excel_path = "Comparador electricidad.v3 (1).xlsx"
        df = pd.read_excel(excel_path, sheet_name="Comparador")
        # Precios potencia
        pot = df.iloc[[0,6,7],4:].transpose().reset_index(drop=True)
        pot.columns = ["nombre","potencia_punta","potencia_valle"]
        pot = pot.apply(pd.to_numeric, errors="coerce")
        # Precios energía
        ene = df.iloc[[11,12,13],4:].transpose().reset_index(drop=True)
        ene.columns = ["energia_punta","energia_llano","energia_valle"]
        ene = ene.apply(pd.to_numeric, errors="coerce")
        # Enlaces
        wb = load_workbook(excel_path, read_only=True)
        ws = wb["Comparador"]
        enlaces = [c.hyperlink.target if c.hyperlink else "" for c in ws[2][4:]]
        wb.close()
        # Unir
        tarifas = pd.concat([pot, ene], axis=1).reset_index(drop=True)
        tarifas["enlace"] = enlaces
        tarifas = tarifas.dropna(subset=["potencia_punta","potencia_valle","energia_punta"])

        resultados = []
        for _, r in tarifas.iterrows():
            cp = (
                consumo.potencia["punta"]*r["potencia_punta"]*consumo.dias_factura +
                consumo.potencia["valle"]*r["potencia_valle"]*consumo.dias_factura
            )
            ce = (
                consumo.energia["punta"]*r["energia_punta"] +
                consumo.energia["llano"]*r["energia_llano"] +
                consumo.energia["valle"]*r["energia_valle"]
            )
            var = round(cp+ce,2)
            resultados.append({
                "tarifa": r["nombre"],
                "coste_variable": var,
                "enlace": r["enlace"]
            })
        resultados.sort(key=lambda x: x["coste_variable"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al comparar tarifas: {str(e)}")

    return resultados
