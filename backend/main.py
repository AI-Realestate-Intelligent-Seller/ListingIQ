import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from routes.telnyx import router as telnyx_router


app = FastAPI(
    title="Telnyx Call Test",
    version="1.0.0",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(telnyx_router)


@app.get("/")
def root():
    return {
        "message": "Telnyx Call Test API",
        "status": "running",
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "missing_configuration": [
            name for name in (
                "TELNYX_API_KEY",
                "TELNYX_CONNECTION_ID",
                "TELNYX_PHONE_NUMBER",
                "TELNYX_WEBRTC_CREDENTIAL_ID",
            ) if not os.getenv(name)
        ],
    }
