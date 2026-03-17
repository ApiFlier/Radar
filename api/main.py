"""
Meeks Family Radar API
"""

import os
import importlib
import time
from fastapi import FastAPI, Request, Query
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from typing import Optional

load_dotenv()

DEBUG = os.getenv("API_DEBUG", "false").lower() == "true"
ENABLE_INGESTORS = os.getenv("ENABLE_INGESTORS", "true").lower() == "true"


@asynccontextmanager
async def lifespan(app: FastAPI):
    if ENABLE_INGESTORS:
        from Classes.Ingestors import getProcessor
        
        print("[API] Starting ingestors...")
        getProcessor().start()
        from Classes.Ingestors import getOpenSkyIngestor
        getOpenSkyIngestor().start()
        # SwimIngestor disabled - using standalone container
        print("[API] Ingestors started")
    
    yield
    
    if ENABLE_INGESTORS:
        from Classes.Ingestors import getProcessor
        
        print("[API] Stopping ingestors...")
        getProcessor().stop()


app = FastAPI(
    title="Meeks Family Radar API",
    description="REST API for FAA flight tracking",
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


def loadModule(action: str):
    moduleName = f"Modules.Api{action}"
    className = f"Api{action}"
    
    try:
        module = importlib.import_module(moduleName)
        return getattr(module, className)
    except (ImportError, AttributeError):
        return None


@app.get("/api/stream")
async def api_stream():
    """SSE endpoint for live plane updates"""
    from Classes.Redis import getRedis
    
    def generate():
        r = getRedis()
        # Use dedicated pubsub client without timeout
        pubsub_client = r.get_pubsub_client()
        pubsub = pubsub_client.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe("planes_out")
        
        try:
            for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                data = message.get("data")
                if not data:
                    continue
                yield f"data: {data}\n\n"
        except GeneratorExit:
            pass
        except Exception as e:
            print(f"[SSE] Error: {e}")
        finally:
            try:
                pubsub.close()
                pubsub_client.close()
            except Exception:
                pass
    
    return StreamingResponse(generate(), media_type="text/event-stream")


@app.api_route("/", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def index(
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
            content={
                "response": {
                    "debug": [] if not DEBUG else {"router": ["Module not found"]},
                    "error": True,
                    "errorMessage": f"Invalid action '{action}'. Use ?action=<ModuleName>",
                    "data": {}
                }
            }
        )
    
    body = {}
    if request.method in ["POST", "PUT", "PATCH"]:
        try:
            body = await request.json()
        except:
            pass
    
    params = dict(request.query_params)
    params.pop("action", None)
    params.update(body)
    
    try:
        instance = ModuleClass(action)
        instance.setDebug(DEBUG)
        instance.setMethod(request.method)
        result = instance.processApi(params)
        
        responseTime = f"{(time.time() - startTime) * 1000:.2f}ms"
        result["response"]["responseTime"] = responseTime
        
        return JSONResponse(
            status_code=result.get("statusCode", 200),
            content=result
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "response": {
                    "debug": [] if not DEBUG else {"router": [str(e)]},
                    "error": True,
                    "errorMessage": str(e),
                    "data": {}
                }
            }
        )


async def showDocumentation():
    modulesDir = os.path.join(os.path.dirname(__file__), "Modules")
    endpoints = []
    
    for filename in os.listdir(modulesDir):
        if filename.startswith("Api") and filename.endswith(".py") and filename != "__init__.py":
            actionName = filename[3:-3]
            
            ModuleClass = loadModule(actionName)
            if ModuleClass:
                description = getattr(ModuleClass, "description", "")
                parameters = getattr(ModuleClass, "apiParameters", {})
                
                endpoints.append({
                    "action": actionName,
                    "description": description,
                    "parameters": parameters,
                    "url": f"?action={actionName}"
                })
    
    endpoints.append({
        "action": "Stream",
        "description": "Server-Sent Events stream for live plane updates",
        "parameters": {},
        "url": "/api/stream"
    })
    
    return JSONResponse(content={
        "api": "Meeks Family Radar API",
        "version": "1.0.0",
        "endpoints": endpoints,
        "usage": "Add ?action=<EndpointName> to call an endpoint"
    })


@app.get("/health")
async def healthCheck():
    return {"status": "ok", "timestamp": time.time()}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("API_PORT", 8081))
    uvicorn.run(app, host="0.0.0.0", port=port)
