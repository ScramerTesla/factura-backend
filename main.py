from fastapi import FastAPI, UploadFile, File, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Dict
import pandas as pd
import fitz  # PyMuPDF
import re
from openpyxl import load_workbook

app = FastAPI()

# Middleware HTTP para inyectar CORS en TODAS las respuestas
@app.middleware("http")
async def add_cors_header(request: Request, call_next):
    response = await call_next(request)
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response

class ConsumoRequest(BaseModel):
    dias_factura: int
    potencia: Dict[str, float]
    energia: Dict[str, float]

@app.post("/analizar-factura")
async def analizar_factura(file: UploadFile = File(...)):
    contents = await file.read()
    text = "".join(page.get_text() for page in fitz.open(stream=contents, filetype="pdf"))

    dias = int(re.search(r"DIAS FACTURADOS:\s*(\d+)", text).group(1))
    potencia_punta = float(re.search(r"Potencia punta:\s*([\d,]+)", text).group(1).replace(",", "."))
    potencia_valle = float(re.search(r"Potencia valle:\s*([\d,]+)", text).group(1).replace(",", "."))

    consumo_punta = float(re.search(r"punta:\s*([\d,]+)\s*kWh", text).group(1).replace(",", "."))
    consumo_llano = float(re.search(r"llano:\s*([\d,]+)\s*kWh", text).group(1).replace(",", "."))
    consumo_valle = float(re.search(r"valle\s*([\d,]+)\s*kWh", text).group(1).replace(",", "."))

    total_factura = float(
        re.search(r"TOTAL IMPORTE FACTURA\s*([\d,]+,[\d]{2})\s*€", text)
        .group(1).replace(",", ".")
    )
    iva = float(
        re.search(r"IVA.*?([\d,]+,[\d]{2})\s*€", text)
        .group(1).replace(",", ".")
    )
    alquiler = float(
        re.search(r"Alquiler equipos medida.*?([\d,]+,[\d]{2})\s*€", text)
        .group(1).replace(",", ".")
    )

    return JSONResponse({
        "dias_factura": dias,
        "potencia": {"punta": potencia_punta, "valle": potencia_valle},
        "energia": {"punta": consumo_punta, "llano": consumo_llano, "valle": consumo_valle},
        "factura_total": round(total_factura, 2),
        "factura_impuesto": round(iva, 2),
        "factura_alquiler": round(alquiler, 2)
    })

@app.post("/comparar-tarifas/")
async def comparar_tarifas(consumo: ConsumoRequest):
    excel_path = "Comparador electricidad.v3 (1).xlsx"
    df = pd.read_excel(excel_path, sheet_name="Comparador")

    # Precios de potencia
    pot = df.iloc[[0,6,7],4:].transpose().reset_index(drop=True)
    pot.columns = ["nombre","potencia_punta","potencia_valle"]
    pot = pot.apply(pd.to_numeric, errors="coerce")

    # Precios de energía
    ene = df.iloc[[11,12,13],4:].transpose().reset_index(drop=True)
    ene.columns = ["energia_punta","energia_llano","energia_valle"]
    ene = ene.apply(pd.to_numeric, errors="coerce")

    # Hipervínculos (fila 2, columnas E en adelante)
    wb = load_workbook(excel_path, read_only=True)
    ws = wb["Comparador"]
    enlaces = [cell.hyperlink.target if cell.hyperlink else "" for cell in ws[2][4:]]

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
    return JSONResponse(resultados)
