"""
Meeks Family Radar - Unified App
"""

import os
import importlib
import time
import logging
from fastapi import FastAPI, Request, Query, APIRouter
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from typing import Optional

from workers.supervisor import getSupervisor, WorkerStatus
from Classes.Ingestors.adsblol.runAdsbLolReApi import AdsbLolReApiIngestor
from Classes.AdsbLolGroundSweep import AdsbLolGroundSweepIngestor
from Classes.Ingestors.faa.SwimIngestor import SwimIngestor

load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger("API")

DEBUG = os.getenv("API_DEBUG", "false").lower() == "true"
ENABLE_INGESTORS = os.getenv("ENABLE_INGESTORS", "true").lower() == "true"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing lifespan...")
    
    # Register internal workers with health windows
    supervisor = getSupervisor()
    supervisor.register_worker("adsblol-reapi", AdsbLolReApiIngestor, "ENABLE_INTERNAL_ADSBLOL_REAPI", health_window=45)
    supervisor.register_worker("adsblol-ground", AdsbLolGroundSweepIngestor, "ENABLE_INTERNAL_ADSBLOL_GROUND", health_window=600)
    supervisor.register_worker("swim-ingestor", SwimIngestor, "ENABLE_INTERNAL_SWIM", health_window=60)
    
    # Start supervisor
    supervisor.start_all()

    if ENABLE_INGESTORS:
        from Classes.Ingestors import getProcessor
        logger.info("[API] Starting legacy ingestors...")
        getProcessor().start()
        from Classes.Ingestors import getOpenSkyIngestor
        getOpenSkyIngestor().start()
        logger.info("[API] Legacy ingestors started")
    
    yield
    
    # Stop supervisor
    getSupervisor().stop_all()

    if ENABLE_INGESTORS:
        from Classes.Ingestors import getProcessor
        logger.info("[API] Stopping legacy ingestors...")
        getProcessor().stop()


app = FastAPI(
    title="Meeks Family Radar",
    description="Unified Flight Tracking App",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API Router ──────────────────────────────────────────────────────────────

api_router = APIRouter(prefix="/api")

def loadModule(action: str):
    moduleName = f"Modules.Api{action}"
    className = f"Api{action}"
    try:
        module = importlib.import_module(moduleName)
        return getattr(module, className)
    except (ImportError, AttributeError):
        return None

async def showDocumentation():
    modulesDir = os.path.join(os.path.dirname(__file__), "Modules")
    endpoints = []
    if os.path.exists(modulesDir):
        for filename in os.listdir(modulesDir):
            if filename.startswith("Api") and filename.endswith(".py") and filename != "__init__.py":
                actionName = filename[3:-3]
                ModuleClass = loadModule(actionName)
                if ModuleClass:
                    endpoints.append({
                        "action": actionName,
                        "description": getattr(ModuleClass, "description", ""),
                        "parameters": getattr(ModuleClass, "apiParameters", {}),
                        "url": f"/api?action={actionName}"
                    })
    
    endpoints.append({
        "action": "Stream",
        "description": "SSE stream for live plane updates",
        "parameters": {},
        "url": "/api/stream"
    })
    
    return JSONResponse(content={
        "api": "Meeks Family Radar API",
        "version": "1.0.0",
        "endpoints": endpoints,
        "usage": "Use ?action=<ModuleName> or specific /api/* paths"
    })

@api_router.get("/health")
async def healthCheck():
    return {
        "status": "ok", 
        "timestamp": time.time(),
        "workers_status_url": "/api/workers/status"
    }

@api_router.get("/workers/status")
async def workersStatus():
    return getSupervisor().get_status()

@api_router.get("/stream")
async def api_stream():
    """SSE endpoint for live plane updates"""
    from Classes.Redis import getRedis
    def generate():
        r = getRedis()
        pubsub_client = r.get_pubsub_client()
        pubsub = pubsub_client.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe("planes_out")
        try:
            for message in pubsub.listen():
                if message["type"] == "message" and message.get("data"):
                    yield f"data: {message['data']}\n\n"
        except GeneratorExit:
            pass
        except Exception as e:
            logger.error(f"[SSE] Error: {e}")
        finally:
            try:
                pubsub.close()
                pubsub_client.close()
            except Exception:
                pass
    return StreamingResponse(generate(), media_type="text/event-stream")

@api_router.api_route("", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@api_router.api_route("/", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def api_index(
    request: Request,
    action: Optional[str] = Query(None, description="The API action to perform")
):
    startTime = time.time()
    if not action:
        return await showDocumentation()
    
    ModuleClass = loadModule(action)
    if not ModuleClass:
        return JSONResponse(
            status_code=400,
            content={"response": {"error": True, "errorMessage": f"Invalid action '{action}'"}}
        )
    
    params = dict(request.query_params)
    params.pop("action", None)
    if request.method in ["POST", "PUT", "PATCH"]:
        try:
            params.update(await request.json())
        except:
            pass
    
    try:
        instance = ModuleClass(action)
        instance.setDebug(DEBUG)
        instance.setMethod(request.method)
        result = instance.processApi(params)
        result["response"]["responseTime"] = f"{(time.time() - startTime) * 1000:.2f}ms"
        return JSONResponse(status_code=result.get("statusCode", 200), content=result)
    except Exception as e:
        logger.exception(f"API Error ({action}): {e}")
        return JSONResponse(status_code=500, content={"response": {"error": True, "errorMessage": str(e)}})

app.include_router(api_router)

# ── Static & Templates ───────────────────────────────────────────────────────

os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# ── Frontend Routes ───────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def ui_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/stats", response_class=HTMLResponse)
async def ui_stats(request: Request):
    return templates.TemplateResponse("stats.html", {"request": request})

@app.get("/airports", response_class=HTMLResponse)
async def ui_airports(request: Request):
    return templates.TemplateResponse("airports.html", {"request": request})

@app.get("/aircraft", response_class=HTMLResponse)
async def ui_aircraft(request: Request):
    return templates.TemplateResponse("aircraft.html", {"request": request})

@app.get("/ground", response_class=HTMLResponse)
async def ui_ground(request: Request):
    return templates.TemplateResponse("ground.html", {"request": request})

@app.get("/alerts", response_class=HTMLResponse)
async def ui_alerts(request: Request):
    return templates.TemplateResponse("alerts.html", {"request": request})

@app.get("/settings", response_class=HTMLResponse)
async def ui_settings(request: Request):
    return templates.TemplateResponse("settings.html", {"request": request})

@app.get("/health", response_class=HTMLResponse)
async def ui_health(request: Request):
    return templates.TemplateResponse("health.html", {"request": request})

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("API_PORT", 8081))
    uvicorn.run(app, host="0.0.0.0", port=port)
