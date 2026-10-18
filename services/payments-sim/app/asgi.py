"""uvicorn app.asgi:app. Building the app here fails at boot if GATEWAY_SECRET is missing."""

import logging

from .main import create_app

logging.basicConfig(level=logging.INFO,
                    format='{"ts":"%(asctime)s","level":"%(levelname)s","service":"payments-sim","msg":"%(message)s"}')
app = create_app()
