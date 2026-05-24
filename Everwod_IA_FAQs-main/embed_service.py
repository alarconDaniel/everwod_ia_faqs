from typing import List

from fastapi import FastAPI
from sentence_transformers import SentenceTransformer

from faq_common import configure_cors, normalize_text
from faq_models import EncodeRequest, EncodeResponse

# Aplicacion FastAPI del servicio de embeddings.
app = FastAPI(title="Everwod FAQ Embedding Service")
configure_cors(app)

# Modelo liviano de sentence-transformers para ejecutar localmente en CPU.
MODEL_NAME = "all-MiniLM-L6-v2"

# Se inicializa en startup para cargar el modelo una sola vez.
model: SentenceTransformer


@app.on_event("startup")
def startup_event() -> None:
    """Carga el modelo cuando arranca el servicio."""
    global model
    model = SentenceTransformer(MODEL_NAME)


@app.get("/health")
def health() -> dict:
    """Endpoint simple para confirmar que el servicio esta activo."""
    return {"status": "ok", "service": "embed", "model": MODEL_NAME}


@app.post("/encode", response_model=EncodeResponse)
def encode(request: EncodeRequest) -> EncodeResponse:
    """Convierte textos limpios en embeddings numericos."""
    # Normaliza y descarta textos vacios antes de enviarlos al modelo.
    texts: List[str] = [normalize_text(text) for text in request.texts if normalize_text(text)]

    # El modelo devuelve arreglos NumPy; se convierten a listas para responder JSON.
    embeddings = model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
    return EncodeResponse(embeddings=embeddings.tolist(), count=len(texts))


if __name__ == "__main__":
    import uvicorn

    # Levanta el servicio localmente en el puerto 8002.
    uvicorn.run("embed_service:app", host="127.0.0.1", port=8002, log_level="info")
