# Python Standard Library Imports

# Third-party Imports
from fastapi import Depends, FastAPI
from typing_extensions import Annotated

# Local Imports
import config

app = FastAPI()


def get_settings():
    return config.Settings()


@app.get("/ping")
def ping():
    return {"message": "pong"}


@app.get("/info")
async def info(settings: Annotated[config.Settings, Depends(get_settings)]):
    return {
        "api_version": settings.api_version,
        "host": settings.host,
        "port": settings.port
    }
