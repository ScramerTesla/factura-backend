from fastapi import FastAPI, UploadFile, File, Response
from pydantic import BaseModel
from typing import Dict
import pandas as pd
import fitz  # PyMuPDF
import re
from openpyxl import load_workbook

app = FastAPI()

class ConsumoRequest(BaseModel):
    dias_factura: int
    potencia: Dict[str, float]
    energia: Dict[str, float]

async def with_cors(resp: Response):
    """Añade el header CORS antes de devolver."""
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp

@app.post("/analizar-factura")
async def analizar_factura(file: UploadFile = File(...)):
    # Leer PDF y extraer datos...
    contents = await file.read()
    text = "".join(page.get_text() for page in fitz.open(stream=contents, filetype="pdf"))
    dias = int(re.search(r"DIAS FACTURADOS:\s*(\d+)", text).group(1))
    potencia_punta = float(re.search(r"Potencia punta:\s*([\d,]+)", text).group(1).replace(",", "."))
    potencia_valle = float(re.search(r"Potencia valle:\s*([\d,]+)", text).group(1).replace(",", "."))
    consumo_punta = float(re.search(r"punta:\s*([\d,]+)\s*kWh", text).group(1).replace(",", "."))
    consumo_llano = float(re.search(r"llano:\s*([\d,]+)\s*kWh", text).group(1).replace(",", "."))
    consumo_valle = float(re.search(r"valle\s*([\d,]+)\s*kWh", text).group(1).replace(",", "."))
    total = float(re.search(r"TOTAL IMPORTE FACTURA\s*([\d,]+,[\d]{2})\s*€", text).group(1).replace(",", "."))

    data = {
        "dias_factura": dias,
        "potencia": {"punta": potencia_punta, "valle": potencia_valle},
        "energia": {"punta": consumo_punta, "llano": consumo_llano, "valle": consumo_valle},
        "factura_total": round(total, 2)
    }
    return await with_cors(Response(content=pd.json.dumps(data), media_type="application/json"))

@app.post("/comparar-tarifas/")
async def comparar_tarifas(consumo: ConsumoRequest):
    # Leer Excel y calcular ranking...
    excel = "Comparador electricidad.v3 (1).xlsx"
    df = pd.read_excel(excel, sheet_name="Comparador")
    pot = df.iloc[[0,6,7],4:].transpose().reset_index(drop=True)
    pot.columns = ["nombre","potencia_punta","potencia_valle"]
    pot = pot.apply(pd.to_numeric, errors="coerce")

    ene = df.iloc[[11,12,13],4:].transpose().reset_index(drop=True)
    ene.columns = ["energia_punta","energia_llano","energia_valle"]
    ene = ene.apply(pd.to_numeric, errors="coerce")

    wb = load_workbook(excel, read_only=True)
    ws = wb["Comparador"]
    enlaces = [c.hyperlink.target if c.hyperlink else "" for c in ws[2][4:]]
    
    tarifas = pd.concat([pot, ene], axis=1).reset_index(drop=True)
    tarifas["enlace"] = enlaces
    tarifas = tarifas.dropna(subset=["potencia_punta","potencia_valle","energia_punta"])

    res = []
    for _, r in tarifas.iterrows():
        cp = consumo.potencia["punta"]*r["potencia_punta"]*consumo.dias_factura + \
             consumo.potencia["valle"]*r["potencia_valle"]*consumo.dias_factura
        ce = consumo.energia["punta"]*r["energia_punta"] + \
             consumo.energia["llano"]*r["energia_llano"] + \
             consumo.energia["valle"]*r["energia_valle"]
        var = round(cp+ce,2)
        res.append({
            "tarifa": r["nombre"],
            "coste_variable": var,
            "enlace": r["enlace"]
        })
    res.sort(key=lambda x: x["coste_variable"])
    
    return await with_cors(Response(content=pd.json.dumps(res), media_type="application/json"))
