from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from auth.database import engine, Base
from auth.router import router as auth_router
from api.router import router as tasks_router
import api.models  # noqa: F401 - ensure Task table is created

app = FastAPI(title="Forja Task Manager - Auth")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(tasks_router)


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)


app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
