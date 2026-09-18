import os
from fastapi import FastAPI
from sqlalchemy import create_engine

app = FastAPI()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("No se encontró la variable DATABASE_URL")

engine = create_engine(DATABASE_URL.replace("postgresql://", "postgresql+psycopg://"))

@app.get("/api/health/")
def health_check():
    try:
        with engine.connect() as connection:
            return {
                "status": "ok",
                "message": "API funcionando y Base de Datos conectada exitosamente"
            }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Error conectando a la BD: {str(e)}"
        }