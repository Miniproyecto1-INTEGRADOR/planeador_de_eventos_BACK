from fastapi import FastAPI

app = FastAPI()


@app.get("/")
def root():
    return {"message": "API funcionando correctamente"}


@app.get("/saludo")
def saludo():
    return {"message": "Hola desde FastAPI"}