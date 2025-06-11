from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Dict
import pandas as pd
import fitz
import re
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

app = FastAPI()

# CORS abierto para todos los orígenes
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

def extract(pattern: str, text: str, fmt=float, label=None, default=None):
    m = re.search(pattern, text, flags=re.IGNORECASE)
    if not m:
        if default is not None:
            return default
        raise ValueError(f"No se encontró el campo '{label}'")
    return fmt(m.group(1).replace(",", "."))

@app.post("/analizar-factura")
async def analizar_factura(file: UploadFile = File(...)):
    try:
        data = await file.read()
        doc = fitz.open(stream=data, filetype="pdf")
        text = "".join(p.get_text() for p in doc)
        doc.close()

        dias  = extract(r"DIAS FACTURADOS:\s*(\d+)", text, int, "Días")
        pp    = extract(r"Potencia punta:\s*([\d,]+)\s*kW", text, float, "Potencia punta")
        pv    = extract(r"Potencia valle:\s*([\d,]+)\s*kW", text, float, "Potencia valle")
        cp    = extract(r"punta:\s*([\d,]+)\s*kWh", text, float, "Consumo punta")
        cl    = extract(r"llano:\s*([\d,]+)\s*kWh", text, float, "Consumo llano")
        cv    = extract(r"valle[: ]\s*([\d,]+)\s*kWh", text, float, "Consumo valle")

        tf    = extract(r"TOTAL IMPORTE FACTURA\D*([\d,]+,[\d]{2})\s*€", text, float, "Total factura")
        iva   = extract(r"IVA.*?([\d,]+,[\d]{2})\s*€", text, float, "IVA", default=0.0)
        alqu  = extract(r"Alquiler equipos medida.*?([\d,]+,[\d]{2})\s*€", text, float, "Alquiler", default=0.0)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error extrayendo PDF: {e}")

    return {
        "dias_factura": dias,
        "potencia": {"punta": pp, "valle": pv},
        "energia": {"punta": cp, "llano": cl, "valle": cv},
        "factura_total": round(tf, 2),
        "factura_impuesto": round(iva, 2),
        "factura_alquiler": round(alqu, 2)
    }

@app.post("/comparar-tarifas")
async def comparar_tarifas(consumo: ConsumoRequest):
    try:
        excel = "Comparador electricidad.v3 (1).xlsx"
        df = pd.read_excel(excel, sheet_name="Comparador")

        # Precios: potencia y energía
        pot = df.iloc[[0,6,7],4:].transpose().reset_index(drop=True)
        pot.columns = ["nombre","potencia_punta","potencia_valle"]
        pot = pot.apply(pd.to_numeric, errors="coerce")
        ene = df.iloc[[11,12,13],4:].transpose().reset_index(drop=True)
        ene.columns = ["energia_punta","energia_llano","energia_valle"]
        ene = ene.apply(pd.to_numeric, errors="coerce")

        # Enlaces: usamos ws._hyperlinks
        wb = load_workbook(excel)
        ws = wb["Comparador"]
        links = {}
        for hl in ws._hyperlinks:
            # hl.ref es la celda, hl.target la URL
            links[hl.ref] = hl.target
        wb.close()

        # Recorrer columnas desde E (col 5) hasta el final
        n = pot.shape[0]
        enlace_list = []
        for i in range(n):
            col = get_column_letter(5 + i)  # col E es 5
            ref = f"{col}2"
            enlace_list.append(links.get(ref, ""))

        # Combinar DataFrame
        tarifas = pd.concat([pot, ene], axis=1).reset_index(drop=True)
        tarifas["enlace"] = enlace_list

        resultados = []
        for _, r in tarifas.iterrows():
            cost_p = consumo.potencia["punta"]*r["potencia_punta"]*consumo.dias_factura \
                   + consumo.potencia["valle"]*r["potencia_valle"]*consumo.dias_factura
            cost_e = consumo.energia["punta"]*r["energia_punta"] \
                   + consumo.energia["llano"]*r["energia_llano"] \
                   + consumo.energia["valle"]*r["energia_valle"]
            var = round(cost_p + cost_e, 2)
            resultados.append({
                "tarifa": r["nombre"],
                "coste_variable": var,
                "enlace": r["enlace"]
            })

        resultados.sort(key=lambda x: x["coste_variable"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error comparando tarifas: {e}")

    return JSONResponse(resultados)
