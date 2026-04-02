from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.database import init_postgres, init_neo4j, close_postgres, close_neo4j
from app.events import event_bus
from app.ingest    import router as ingest_router
from app.tools     import router as tools_router
from app.graph     import router as graph_router
from app.timeline  import router as timeline_router
from app.risk      import router as risk_router
from app.heatmap   import router as heatmap_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_postgres()
    await init_neo4j()
    yield
    await close_postgres()
    await close_neo4j()


app = FastAPI(
    title="ARSP — Agent Observability Platform",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ingest_router)
app.include_router(tools_router)
app.include_router(graph_router)
app.include_router(timeline_router)
app.include_router(risk_router)
app.include_router(heatmap_router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "arsp", "version": "2.0.0"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await event_bus.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        event_bus.disconnect(websocket)
    except Exception:
        event_bus.disconnect(websocket)
