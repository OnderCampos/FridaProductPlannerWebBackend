"""
This file is the entry point for the Azure Function. It creates an instance of the FastAPI app and wraps it in an Azure Function app. IT SHOULD NOT BE MODIFIED.
"""

import azure.functions as func
from fastapi.middleware.cors import CORSMiddleware
from src.main import app as fastapi_app


# Add CORS middleware to the FastAPI app
fastapi_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app = func.AsgiFunctionApp(app=fastapi_app, http_auth_level=func.AuthLevel.ANONYMOUS)
