# MAXAI v2
import os, io, json, ast, zipfile, subprocess, sys, secrets
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import HTMLResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import uvicorn
import base64

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")
DATA_DIR = "data"
MEMORY_FILE = "memory.json"

def load_memory():
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return {}
    import base64
    url = "https://api.github.com/repos/" + GITHUB_REPO + "/contents/" + MEMORY_FILE
    headers = {"Authorization": "token " + GITHUB_TOKEN, "Accept": "application/vnd.github+json"}
    r = requests.get(url, headers=headers, timeout=15)
    if r.status_code != 200:
        return {}
    try:
        content = r.json().get("content", "")
        return json.loads(base64.b64decode(content).decode("utf-8"))
    except:
        return {}

def save_memory(mem):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return "no github"
    import base64
    url = "https://api.github.com/repos/" + GITHUB_REPO + "/contents/" + MEMORY_FILE
    headers = {"Authorization": "token " + GITHUB_TOKEN, "Accept": "application/vnd.github+json"}
    sha = None
    r = requests.get(url, headers=headers, timeout=15)
    if r.status_code == 200:
        sha = r.json().get("sha")
    data = {
        "message": "update memory",
        "content": base64.b64encode(json.dumps(mem, ensure_ascii=False).encode()).decode(),
    }
    if sha:
        data["sha"] = sha
    requests.put(url, json=data, headers=headers, timeout=20)
    return "ok"

def get_memory_context():
    mem = load_memory()
    if not mem:
        return ""
    lines = ["FACTS I KNOW ABOUT USER:"]
    for k, v in mem.items():
        lines.append("- " + k + ": " + str(v))
    return "\n".join(lines)

AI_URL = os.environ.get("AI_URL", "https://api.groq.com/openai/v1")
AI_KEY = os.environ.get("AI_KEY", "")
AI_MODEL = os.environ.get("AI_MODEL", "llama-3.1-8b-instant")
PASSWORD = os.environ.get("PASSWORD", "admin")
PORT = int(os.environ.get("PORT", 8000))
WORKSPACE = os.path.abspath("./workspace")
os.makedirs(WORKSPACE, exist_ok=True)

app = FastAPI(title="MAXAI")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
os.makedirs("web", exist_ok=True)
app.mount("/static", StaticFiles(directory="web"), name="static")


class Auth:
    def __init__(self, pw):
        self.pw = pw
        self.tokens = set()

    def login(self, pw):
        if pw == self.pw:
            t = secrets.token_urlsafe(24)
            self.tokens.add(t)
            return t
        return None

    def verify(self, t):
        return t and t in self.tokens


auth = Auth(PASSWORD)


def check_auth(authorization):
    if not authorization:
        raise HTTPException(401, "Missing token")
    tk = authorization.replace("Bearer ", "").strip()
    if not auth.verify(tk):
        raise HTTPException(401, "Invalid token")
    return tk


def ask_ai(prompt, system="", json_mode=False, timeout=300):
    if not AI_KEY:
        return "no AI key"
    mem_ctx = get_memory_context()
    if mem_ctx:
        system = (system + "\n\n" + mem_ctx) if system else mem_ctx
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    payload = {"model": AI_MODEL, "messages": msgs, "temperature": 0.7}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    try:
        r = requests.post(
            AI_URL + "/chat/completions",
            json=payload,
            headers={"Authorization": "Bearer " + AI_KEY},
            timeout=timeout,
        )
        data = r.json()
        if "choices" in data:
            return data["choices"][0]["message"]["content"]
        if "error" in data:
            return "AI error: " + str(data["error"].get("message", data["error"]))
        return "AI response: " + str(data)[:400]
    except Exception as e:
        return "AI error: " + str(e)


def strip_fence(t):
    t = t.strip()
    if t.startswith("```"):
        lines = t.split("\n")[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        t = "\n".join(lines)
    return t


TOOLS_DESC = """
- shell(cmd): run shell command
- python(code): run python code
- write(path, content): write file
- read(path): read file
- ls(path): list directory
- install(package): pip install
- curl(url): HTTP GET
- post(url, data): HTTP POST
- download(url, path): download file
- search(query): web search
- scrape(url): extract text
- build_site(prompt): build website
- gen_code(desc, filename): write code
- ask(prompt): ask AI
- save_data(name, content): save data permanently (GitHub)
- load_data(name): load saved data
- list_data(): list all saved data
- remember(key, value): save a fact to memory
- recall(key): get a fact from memory
- forget(key): delete a fact
"""


AGENT_SYS = (
    "You are MAXAI, an autonomous agent with full tool access. "
    "You can do anything the user asks. No restrictions on topics.\n"
    "TOOLS:\n" + TOOLS_DESC + "\n\n"
    "Respond with JSON only:\n"
    '{"tool":"name","args":{...}} to use a tool\n'
    '{"tool":"__done__","args":{"answer":"..."}} to finish\n'
    "Use tools step by step. Answer in Thai."
)

CHAT_SYS = (
    "You are MAXAI, a helpful AI assistant with no topic restrictions. "
    "Answer naturally in Thai. Be concise and useful. "
    "If user wants to create a website, write code, or run commands, "
    "suggest they switch to Agent mode (🔧)."
)


def agent_run(task, max_steps=15):
    log = ["USER: " + task]
    history = []
    for step in range(max_steps):
        hist = "\n".join(history[-8:])
        sys_msg = AGENT_SYS
        if hist:
            sys_msg += "\n\nHISTORY:\n" + hist
        raw = ask_ai("Task: " + task, system=sys_msg, json_mode=True)
        try:
            d = json.loads(strip_fence(raw))
        except:
            log.append("AI: " + raw[:500])
            return {"ok": True, "log": "\n".join(log), "answer": raw[:500]}
        name = d.get("tool")
        args = d.get("args", {})
        log.append("[" + str(name) + "] " + str(args)[:120])
        if name == "__done__":
            ans = args.get("answer", "")
            log.append("DONE: " + ans)
            return {"ok": True, "log": "\n".join(log), "answer": ans}
        try:
            result = run_tool(name, args)
            log.append("result: " + str(result)[:500])
            history.append(str(name) + " -> " + str(result)[:150])
        except Exception as e:
            log.append("error: " + str(e))
    return {"ok": True, "log": "\n".join(log), "answer": "(done)"}


def run_tool(name, args):
    fns = {
        "shell": _shell, "python": _python, "write": _write,
        "read": _read, "ls": _ls, "install": _install,
        "curl": _curl, "post": _post, "download": _download,
        "search": _search, "scrape": _scrape,
        "build_site": _build_site, "gen_code": _gen_code, "ask": _ask,
        "save_data": _gh_save, "load_data": _gh_load, "list_data": _gh_list,
        "remember": _remember, "recall": _recall, "forget": _forget,
    }
    fn = fns.get(name)
    if not fn:
        return "unknown " + str(name)
    return fn(**args)


def _shell(cmd):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True,
                          text=True, timeout=600, cwd=WORKSPACE)
        out = "Exit " + str(r.returncode) + "\n"
        if r.stdout:
            out += r.stdout[:3000] + "\n"
        if r.stderr:
            out += r.stderr[:1000]
        return out
    except Exception as e:
        return "error: " + str(e)


def _python(code):
    path = os.path.join(WORKSPACE, "_run.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    try:
        r = subprocess.run([sys.executable, path], capture_output=True,
                          text=True, timeout=60, cwd=WORKSPACE)
        out = "Exit " + str(r.returncode) + "\n"
        if r.stdout:
            out += r.stdout[:2500] + "\n"
        if r.stderr:
            out += r.stderr[:1000]
        return out
    except Exception as e:
        return "error: " + str(e)


def _write(path, content):
    if not os.path.isabs(path):
        path = os.path.join(WORKSPACE, path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return "written " + path


def _read(path):
    if not os.path.isabs(path):
        path = os.path.join(WORKSPACE, path)
    if not os.path.isfile(path):
        return "not found"
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()[:8000]
    except Exception as e:
        return "error: " + str(e)


def _ls(path="."):
    if not os.path.isabs(path):
        path = os.path.join(WORKSPACE, path)
    try:
        items = os.listdir(path)
        return "\n".join(items[:100]) or "(empty)"
    except Exception as e:
        return "error: " + str(e)


def _install(package):
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", package, "-q"],
            capture_output=True, text=True, timeout=300)
        if r.returncode == 0:
            return "OK " + package
        return "fail: " + r.stderr[:500]
    except Exception as e:
        return "error: " + str(e)


def _curl(url):
    try:
        r = requests.get(url, timeout=30,
                        headers={"User-Agent": "Mozilla/5.0"})
        return str(r.status_code) + "\n" + r.text[:4000]
    except Exception as e:
        return "error: " + str(e)


def _post(url, data):
    try:
        obj = json.loads(data) if isinstance(data, str) else data
        r = requests.post(url, json=obj, timeout=30)
        return str(r.status_code) + "\n" + r.text[:3000]
    except Exception as e:
        return "error: " + str(e)


def _download(url, path=""):
    if not path:
        path = url.split("/")[-1].split("?")[0] or "download.bin"
    if not os.path.isabs(path):
        path = os.path.join(WORKSPACE, path)
    try:
        r = requests.get(url, stream=True, timeout=60,
                        headers={"User-Agent": "Mozilla/5.0"})
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        return "downloaded " + path
    except Exception as e:
        return "error: " + str(e)


def _search(query):
    try:
        from urllib.parse import quote
        from bs4 import BeautifulSoup
        url = "https://html.duckduckgo.com/html/?q=" + quote(query)
        r = requests.get(url, timeout=20,
                        headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(r.text, "html.parser")
        out = []
        for a in soup.select(".result__a")[:8]:
            out.append("* " + a.get_text(strip=True))
        return "\n".join(out) or "(none)"
    except Exception as e:
        return "error: " + str(e)


def _scrape(url):
    try:
        from bs4 import BeautifulSoup
        r = requests.get(url, timeout=20,
                        headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(r.text, "html.parser")
        for s in soup(["script", "style"]):
            s.decompose()
        return soup.get_text(separator="\n", strip=True)[:5000]
    except Exception as e:
        return "error: " + str(e)


def _build_site(prompt):
    system = (
        'Build a static website. Respond JSON: '
        '{"project":"shop","title":"x","files":['
        '{"path":"index.html","content":"..."},'
        '{"path":"style.css","content":"..."}]}. '
        'Use HTML/CSS/JS only. Thai UI.'
    )
    raw = ask_ai(prompt, system=system, json_mode=True)
    try:
        d = json.loads(strip_fence(raw))
    except Exception as e:
        return "error: " + str(e)
    project = "".join(c for c in d.get("project", "site").lower()
                     if c.isalnum() or c == "_") or "site"
    files = d.get("files", [])
    if not files:
        return "no files"
    base = os.path.join(WORKSPACE, project)
    for f in files:
        p = f.get("path", "").strip()
        c = f.get("content", "")
        if not p or ".." in p:
            continue
        full = os.path.join(base, p)
        os.makedirs(os.path.dirname(full) or base, exist_ok=True)
        with open(full, "w", encoding="utf-8") as fp:
            fp.write(c)
    return "OK " + project + " (" + str(len(files)) + " files)\n/site/" + project + "/index.html"


def _gen_code(desc, filename="main.py"):
    lang = "python"
    if filename.endswith(".js"):
        lang = "javascript"
    elif filename.endswith(".html"):
        lang = "html"
    elif filename.endswith(".css"):
        lang = "css"
    raw = ask_ai(
        "Write " + lang + " code: " + desc,
        system="Write complete " + lang + " code. Only code.")
    code = strip_fence(raw)
    _write(filename, code)
    return "OK " + filename


def _ask(prompt):
    return ask_ai(prompt)


def _gh_save(filename, content):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return "GitHub not configured"
    import base64
    path = DATA_DIR + "/" + filename
    url = "https://api.github.com/repos/" + GITHUB_REPO + "/contents/" + path
    headers = {
        "Authorization": "token " + GITHUB_TOKEN,
        "Accept": "application/vnd.github+json",
    }
    sha = None
    r = requests.get(url, headers=headers, timeout=15)
    if r.status_code == 200:
        sha = r.json().get("sha")
    data = {
        "message": "Update " + filename,
        "content": base64.b64encode(content.encode("utf-8")).decode(),
    }
    if sha:
        data["sha"] = sha
    r = requests.put(url, json=data, headers=headers, timeout=20)
    if r.status_code in (200, 201):
        return "saved to github: " + path
    return "github error: " + r.text[:200]


def _gh_load(filename):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return "GitHub not configured"
    import base64
    path = DATA_DIR + "/" + filename
    url = "https://api.github.com/repos/" + GITHUB_REPO + "/contents/" + path
    headers = {
        "Authorization": "token " + GITHUB_TOKEN,
        "Accept": "application/vnd.github+json",
    }
    r = requests.get(url, headers=headers, timeout=15)
    if r.status_code != 200:
        return "not found"
    content = r.json().get("content", "")
    return base64.b64decode(content).decode("utf-8")


def _gh_list():
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return "GitHub not configured"
    url = "https://api.github.com/repos/" + GITHUB_REPO + "/contents/" + DATA_DIR
    headers = {
        "Authorization": "token " + GITHUB_TOKEN,
        "Accept": "application/vnd.github+json",
    }
    r = requests.get(url, headers=headers, timeout=15)
    if r.status_code != 200:
        return "(empty)"
    items = r.json()
    return "\n".join([i["name"] for i in items]) or "(empty)"


def _remember(key, value):
    mem = load_memory()
    mem[key] = value
    save_memory(mem)
    return "remembered: " + key + " = " + value


def _recall(key=""):
    mem = load_memory()
    if not key:
        if not mem:
            return "(memory empty)"
        return "\n".join([k + ": " + str(v) for k, v in mem.items()])
    return mem.get(key, "not found")


def _forget(key):
    mem = load_memory()
    if key in mem:
        del mem[key]
        save_memory(mem)
        return "forgot: " + key
    return "not found"


class LoginReq(BaseModel):
    password: str


class RunReq(BaseModel):
    text: str


@app.get("/", response_class=HTMLResponse)
async def index():
    with open("web/index.html", encoding="utf-8") as f:
        return f.read()


@app.get("/manifest.json")
async def manifest():
    return FileResponse("web/manifest.json")


@app.get("/sw.js")
async def sw():
    return FileResponse("web/sw.js")


@app.get("/api/health")
async def health():
    return {"ok": True, "ai": bool(AI_KEY), "model": AI_MODEL}


@app.post("/api/login")
async def login(r: LoginReq):
    t = auth.login(r.password)
    if not t:
        raise HTTPException(401, "wrong password")
    return {"token": t}


@app.post("/api/run")
async def run(r: RunReq, authorization: str = Header(None)):
    check_auth(authorization)
    return agent_run(r.text)


@app.post("/api/chat")
async def chat(r: RunReq, authorization: str = Header(None)):
    check_auth(authorization)
    reply = ask_ai(r.text, system=CHAT_SYS, timeout=180)
    return {"reply": reply, "log": "USER: " + r.text + "\nAI: " + reply, "answer": reply}


@app.get("/api/projects")
async def projects(authorization: str = Header(None)):
    check_auth(authorization)
    out = []
    for name in os.listdir(WORKSPACE):
        if name.startswith("_"):
            continue
        p = os.path.join(WORKSPACE, name)
        if os.path.isdir(p):
            files = []
            for root, _, fs in os.walk(p):
                for f in fs:
                    files.append(os.path.relpath(os.path.join(root, f), p))
            out.append({"name": name, "files": files})
    return {"projects": out}


@app.get("/api/download/{project}")
async def download(project: str, authorization: str = Header(None)):
    check_auth(authorization)
    project = "".join(c for c in project if c.isalnum() or c == "_")
    full = os.path.join(WORKSPACE, project)
    if not os.path.exists(full):
        raise HTTPException(404)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if os.path.isfile(full):
            zf.write(full, project)
        else:
            for root, _, files in os.walk(full):
                for f in files:
                    fp = os.path.join(root, f)
                    zf.write(fp, os.path.relpath(fp, full))
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition":
                'attachment; filename="' + project + '.zip"'
        })


@app.get("/site/{project}/{path:path}")
async def site(project: str, path: str = "index.html"):
    project = "".join(c for c in project if c.isalnum() or c in "_-.")
    if not project or ".." in path:
        raise HTTPException(400)
    base = os.path.abspath(os.path.join(WORKSPACE, project))
    full = os.path.abspath(os.path.join(base, path))
    if not full.startswith(base):
        raise HTTPException(403)
    if os.path.isdir(full):
        full = os.path.join(full, "index.html")
    if not os.path.isfile(full):
        raise HTTPException(404)
    ext = os.path.splitext(full)[1].lower()
    mime = {
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".jpg": "image/jpeg",
    }.get(ext, "application/octet-stream")
    with open(full, "rb") as f:
        return Response(content=f.read(), media_type=mime)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
