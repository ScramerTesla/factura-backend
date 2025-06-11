from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict
import pandas as pd
import fitz  # PyMuPDF
import re

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

class ConsumoRequest(BaseModel):
    dias_factura: int
    potencia: Dict[str, float]
    energia: Dict[str, float]

@app.post("/analizar-factura")
async def analizar_factura(file: UploadFile = File(...)):
    contents = await file.read()
    text = ""
    with fitz.open(stream=contents, filetype="pdf") as doc:
        for page in doc:
            text += page.get_text()

    # Extraer datos
    dias = int(re.search(r"DIAS FACTURADOS:\s*(\d+)", text).group(1))
    potencia_punta = float(re.search(r"Potencia punta:\s*([\d,]+)", text).group(1).replace(",", "."))
    potencia_valle = float(re.search(r"Potencia valle:\s*([\d,]+)", text).group(1).replace(",", "."))
    consumo_punta = float(re.search(r"punta:\s*([\d,]+)\s*kWh", text).group(1).replace(",", "."))
    consumo_llano = float(re.search(r"llano:\s*([\d,]+)\s*kWh", text).group(1).replace(",", "."))
    consumo_valle = float(re.search(r"valle\s*([\d,]+)\s*kWh", text).group(1).replace(",", "."))
    # Extraer total factura
    total_factura = float(re.search(r"TOTAL IMPORTE FACTURA\s*([\d,]+,[\d]{2})\s*€", text).group(1).replace(",", "."))

    return {
        "dias_factura": dias,
        "potencia": {"punta": potencia_punta, "valle": potencia_valle},
        "energia": {"punta": consumo_punta, "llano": consumo_llano, "valle": consumo_valle},
        "factura_total": round(total_factura, 2)
    }

@app.post("/comparar-tarifas/")
async def comparar_tarifas(consumo: ConsumoRequest):
    # Carga tarifas desde Excel
    df = pd.read_excel("Comparador electricidad.v3 (1).xlsx", sheet_name="Comparador")
    # Precios de potencia
    pot = df.iloc[[0,6,7],4:].transpose().reset_index(drop=True)
    pot.columns = ["nombre","potencia_punta","potencia_valle"]
    pot["potencia_punta"] = pd.to_numeric(pot["potencia_punta"], errors="coerce")
    pot["potencia_valle"] = pd.to_numeric(pot["potencia_valle"], errors="coerce")
    # Precios de energía
    ene = df.iloc[[11,12,13],4:].transpose().reset_index(drop=True)
    ene.columns = ["energia_punta","energia_llano","energia_valle"]
    ene = ene.apply(pd.to_numeric, errors="coerce")
    # Enlaces (si hay una fila con URL, p.ej. fila 1)
    enlaces = df.iloc[1,4:].astype(str).transpose().reset_index(drop=True)
    enlaces = enlaces.to_frame(name="enlace")

    tarifas = pd.concat([pot, ene, enlaces], axis=1).dropna(subset=["potencia_punta","potencia_valle","energia_punta"])

    resultados = []
    for _, row in tarifas.iterrows():
        # Coste potencia
        cp = consumo.potencia["punta"]*row["potencia_punta"]*consumo.dias_factura \
           + consumo.potencia["valle"]*row["potencia_valle"]*consumo.dias_factura
        # Coste energía
        ep = consumo.energia["punta"]*row["energia_punta"]
        el = consumo.energia["llano"]*row["energia_llano"]
        ev = consumo.energia["valle"]*row["energia_valle"]
        ce = ep+el+ev
        ct = cp+ce
        resultados.append({
            "tarifa": row["nombre"],
            "coste_potencia": round(cp,2),
            "coste_energia": round(ce,2),
            "coste_total": round(ct,2),
            "enlace": row.get("enlace","").replace("\n","")
        })
    resultados.sort(key=lambda x: x["coste_total"])
    return resultados
