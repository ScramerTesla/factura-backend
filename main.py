from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict
import pandas as pd
import fitz  # PyMuPDF
import re

print("✅ Backend arrancado con /analizar-factura y /comparar-tarifas")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ConsumoRequest(BaseModel):
    dias_factura: int
    potencia: Dict[str, float]
    energia: Dict[str, float]

# -------------------------------
# /analizar-factura
# -------------------------------
@app.post("/analizar-factura")
async def analizar_factura(file: UploadFile = File(...)):
    contents = await file.read()
    text = ""
    with fitz.open(stream=contents, filetype="pdf") as doc:
        for page in doc:
            text += page.get_text()

    dias = int(re.search(r"DIAS FACTURADOS:\s*(\d+)", text).group(1))
    potencia_punta = float(re.search(r"Potencia punta:\s*([\d,]+)", text).group(1).replace(",", "."))
    potencia_valle = float(re.search(r"Potencia valle:\s*([\d,]+)", text).group(1).replace(",", "."))
    consumo_punta = float(re.search(r"punta:\s*([\d,]+)\s*kWh", text).group(1).replace(",", "."))
    consumo_llano = float(re.search(r"llano:\s*([\d,]+)\s*kWh", text).group(1).replace(",", "."))
    consumo_valle = float(re.search(r"valle\s*([\d,]+)\s*kWh", text).group(1).replace(",", "."))

    return {
        "dias_factura": dias,
        "potencia": {"punta": potencia_punta, "valle": potencia_valle},
        "energia": {"punta": consumo_punta, "llano": consumo_llano, "valle": consumo_valle}
    }

# -------------------------------
# /comparar-tarifas
# -------------------------------
@app.post("/comparar-tarifas/")
async def comparar_tarifas(consumo: ConsumoRequest):
    path_excel = "Comparador electricidad.v3 (1).xlsx"
    df = pd.read_excel(path_excel, sheet_name="Comparador")

    tarifas_df = df.iloc[[0, 6, 7], 4:]
    tarifas_df.index = ['nombre', 'potencia_punta', 'potencia_valle']
    tarifas = tarifas_df.transpose().reset_index(drop=True)
    tarifas['potencia_punta'] = pd.to_numeric(tarifas['potencia_punta'], errors='coerce')
    tarifas['potencia_valle'] = pd.to_numeric(tarifas['potencia_valle'], errors='coerce')

    energia_df = df.iloc[[11, 12, 13], 4:]
    energia_df.index = ['energia_punta', 'energia_llano', 'energia_valle']
    energia = energia_df.transpose().reset_index(drop=True)
    energia['energia_punta'] = pd.to_numeric(energia['energia_punta'], errors='coerce')
    energia['energia_llano'] = pd.to_numeric(energia['energia_llano'], errors='coerce')
    energia['energia_valle'] = pd.to_numeric(energia['energia_valle'], errors='coerce')

    tarifas = pd.concat([tarifas, energia], axis=1)
    tarifas = tarifas.dropna(subset=['potencia_punta', 'potencia_valle', 'energia_punta'])

    resultados = []

    for _, row in tarifas.iterrows():
        try:
            coste_potencia = (
                consumo.potencia['punta'] * row['potencia_punta'] * consumo.dias_factura +
                consumo.potencia['valle'] * row['potencia_valle'] * consumo.dias_factura
            )
            energia_punta = consumo.energia['punta'] * row['energia_punta']
            energia_llano = consumo.energia['llano'] * (
                row['energia_llano'] if pd.notna(row['energia_llano']) else row['energia_punta']
            )
            energia_valle = consumo.energia['valle'] * row['energia_valle']
            coste_energia = energia_punta + energia_llano + energia_valle
            coste_total = coste_potencia + coste_energia

            resultados.append({
                "tarifa": row['nombre'],
                "coste_potencia": round(coste_potencia, 2),
                "coste_energia": round(coste_energia, 2),
                "coste_total": round(coste_total, 2)
            })
        except Exception:
            continue

    resultados.sort(key=lambda x: x['coste_total'])
    return resultados
