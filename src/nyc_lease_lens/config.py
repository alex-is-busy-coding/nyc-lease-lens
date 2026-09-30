import os

MODEL = os.getenv("MODEL", "vertex_ai/gemini-3.5-flash-lite")
VERTEXAI_PROJECT = os.getenv("VERTEXAI_PROJECT")
VERTEXAI_LOCATION = os.getenv("VERTEXAI_LOCATION", "global")
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))
