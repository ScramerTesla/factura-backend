from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict
import pandas as pd
import fitz  # PyMuPDF
import re
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# Permite CORS desde cualquier origen
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],            # <-- aquí
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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

    # Extraer datos de la factura
    dias = int(re.search(r"DIAS FACTURADOS:\s*(\d+)", text).group(1))
    potencia_punta = float(re.search(r"Potencia punta:\s*([\d,]+)", text).group(1).replace(",", "."))
    potencia_valle = float(re.search(r"Potencia valle:\s*([\d,]+)", text).group(1).replace(",", "."))
    consumo_punta = float(re.search(r"punta:\s*([\d,]+)\s*kWh", text).group(1).replace(",", "."))
    consumo_llano = float(re.search(r"llano:\s*([\d,]+)\s*kWh", text).group(1).replace(",", "."))
    consumo_valle = float(re.search(r"valle\s*([\d,]+)\s*kWh", text).group(1).replace(",", "."))

    total_factura = float(
        re.search(r"TOTAL IMPORTE FACTURA\s*([\d,]+,[\d]{2})\s*€", text)
        .group(1)
        .replace(",", ".")
    )
    iva = float(
        re.search(r"IVA.*?([\d,]+,[\d]{2})\s*€", text)
        .group(1)
        .replace(",", ".")
    )
    alquiler = float(
        re.search(r"Alquiler equipos medida.*?([\d,]+,[\d]{2})\s*€", text)
        .group(1)
        .replace(",", ".")
    )

    return {
        "dias_factura": dias,
        "potencia": {"punta": potencia_punta, "valle": potencia_valle},
        "energia": {"punta": consumo_punta, "llano": consumo_llano, "valle": consumo_valle},
        "factura_total": round(total_factura, 2),
        "factura_impuesto": round(iva, 2),
        "factura_alquiler": round(alquiler, 2)
    }

@app.post("/comparar-tarifas/")
async def comparar_tarifas(consumo: ConsumoRequest):
    excel_path = "Comparador electricidad.v3 (1).xlsx"
    df = pd.read_excel(excel_path, sheet_name="Comparador")

    # Precios potencia
    pot = df.iloc[[0, 6, 7], 4:].transpose().reset_index(drop=True)
    pot.columns = ["nombre", "potencia_punta", "potencia_valle"]
    pot["potencia_punta"] = pd.to_numeric(pot["potencia_punta"], errors="coerce")
    pot["potencia_valle"] = pd.to_numeric(pot["potencia_valle"], errors="coerce")
    # Precios energía
    ene = df.iloc[[11, 12, 13], 4:].transpose().reset_index(drop=True)
    ene.columns = ["energia_punta", "energia_llano", "energia_valle"]
    ene = ene.apply(pd.to_numeric, errors="coerce")
    # Enlaces
    wb = load_workbook(excel_path, read_only=True)
    ws = wb["Comparador"]
    hiper = []
    for cell in list(ws.iter_rows(min_row=2, max_row=2, min_col=5, values_only=False))[0]:
        hiper.append(cell.hyperlink.target if cell.hyperlink else "")
    enlaces = pd.Series(hiper, name="enlace")

    tarifas = pd.concat([pot, ene, enlaces], axis=1).dropna(subset=["potencia_punta","potencia_valle","energia_punta"])

    resultados = []
    for _, row in tarifas.iterrows():
        cp = (
            consumo.potencia["punta"] * row["potencia_punta"] * consumo.dias_factura +
            consumo.potencia["valle"] * row["potencia_valle"] * consumo.dias_factura
        )
        ce = (
            consumo.energia["punta"] * row["energia_punta"] +
            consumo.energia["llano"] * row["energia_llano"] +
            consumo.energia["valle"] * row["energia_valle"]
        )
        ct_var = cp + ce
        # coste fijo enviado por front: impuesto + alquiler
        ct_fij = consumo.__dict__.get("factura_impuesto", 0) + consumo.__dict__.get("factura_alquiler", 0)
        resultados.append({
            "tarifa": row["nombre"],
            "coste_variable": round(ct_var, 2),
            "coste_fijo": round(ct_fij, 2),
            "coste_total": round(ct_var + ct_fij, 2),
            "enlace": row["enlace"]
        })

    resultados.sort(key=lambda x: x["coste_total"])
    return resultados
