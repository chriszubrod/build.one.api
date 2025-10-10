# Python Standard Library Imports

# Third-party Imports
from fastapi import Depends, FastAPI
from typing_extensions import Annotated

# Local Imports
import config
from modules.organization.api.router import router as organization_api_router
from modules.organization.web.controller import router as organization_web_router

app = FastAPI()


app.include_router(organization_api_router)
app.include_router(organization_web_router)


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
