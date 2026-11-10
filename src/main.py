from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.routes import auth, project, templates, user_stories, epics, members, invitations, sprints, backlog, assistant, tasks, jira_mcp
from fastapi.middleware.cors import CORSMiddleware
from src.services.setup.language_setup import reset_request_llm_language, set_request_llm_language

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


@app.middleware("http")
async def llm_response_language_middleware(request: Request, call_next):
    language = request.headers.get("X-LLM-Response-Language") or request.headers.get("X-LLM-Language")
    token = set_request_llm_language(language)
    try:
        return await call_next(request)
    finally:
        reset_request_llm_language(token)


app.include_router(auth.router, prefix="/authenticate", tags=["authenticate"])
app.include_router(project.router, prefix="/projects", tags=["project"])
app.include_router(templates.router, prefix="/projects", tags=["templates"])
app.include_router(sprints.router, prefix="/projects", tags=["sprints"])
app.include_router(tasks.router, prefix="/projects", tags=["tasks"])
app.include_router(user_stories.router, prefix="/user-stories", tags=["user-stories"])
app.include_router(epics.router, prefix="/epics", tags=["epics"])
app.include_router(members.router, prefix="/members", tags=["members"])
app.include_router(invitations.router, prefix="/invitations", tags=["invitations"])
app.include_router(backlog.router, tags=["backlog"])
app.include_router(assistant.router, prefix="/assistant", tags=["assistant"])
app.include_router(jira_mcp.router, prefix="/projects", tags=["jira"])
app.include_router(jira_mcp.callback_router, tags=["jira"])



@app.get("/", description="(For testing) Hello world", response_class=JSONResponse)
async def read_root():
    return {"success": True, "message": "This endpoint is for testing purposes only."}
