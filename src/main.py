from fastapi import FastAPI
from fastapi.responses import JSONResponse

from src.routes import auth, project, templates, user_stories, epics, members, invitations, sprints
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="Frida Planner API",
    description="This is an api for the Frida Planner application.",
    version="0.1.0",
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/authenticate", tags=["authenticate"])
app.include_router(project.router, prefix="/project", tags=["project"])
app.include_router(project.router, prefix="/projects", tags=["project"])
app.include_router(templates.router, prefix="/projects", tags=["templates"])
app.include_router(sprints.router, prefix="/projects", tags=["sprints"])
app.include_router(user_stories.router, prefix="/user-stories", tags=["user-stories"])
app.include_router(epics.router, prefix="/epics", tags=["epics"])
app.include_router(members.router, prefix="/members", tags=["members"])
app.include_router(invitations.router, prefix="/invitations", tags=["invitations"])



@app.get("/", description="(For testing) Hello world", response_class=JSONResponse)
async def read_root():
    return {"success": True, "message": "This endpoint is for testing purposes only."}
