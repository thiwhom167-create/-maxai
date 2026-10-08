# MAXAI v2
import os, io, json, ast, zipfile, subprocess, sys, secrets, shlex, tempfile
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
- projects(): list available projects
- shell(cmd, project="default"): run shell command inside the selected project
- python(code, project="default"): run python code inside the selected project
- write(path, content, project="default"): write file inside the selected project
- read(path, project="default"): read file inside the selected project
- ls(path=".", project="default"): list directory inside the selected project
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
        "projects": _projects,
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


SAFE_SHELL_COMMANDS = {
    "pwd", "ls", "find", "cat", "head", "tail", "grep", "sed", "awk",
    "mkdir", "touch", "cp", "mv", "rm", "tar", "zip", "unzip",
    "python", "python3", "pip", "pip3", "node", "npm", "npx",
    "git", "pytest", "uvicorn"
}


def _safe_project_name(project):
    project = str(project or "").strip()
    if not project or project in (".", ".."):
        raise ValueError("invalid project")
    if any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-." for ch in project):
        raise ValueError("invalid project name")
    if ".." in project:
        raise ValueError("invalid project name")
    return project


def _project_root(project="default"):
    name = _safe_project_name(project)
    root = os.path.abspath(os.path.join(WORKSPACE, name))
    workspace_root = os.path.abspath(WORKSPACE)
    if os.path.commonpath([workspace_root, root]) != workspace_root:
        raise ValueError("project outside workspace")
    os.makedirs(root, exist_ok=True)
    return root


def _project_path(project="default", path=".", allow_root=True):
    root = _project_root(project)
    path = str(path if path is not None else ".").strip()
    if os.path.isabs(path):
        raise ValueError("absolute paths are not allowed")
    if "\x00" in path:
        raise ValueError("invalid path")
    normalized = os.path.normpath(path or ".")
    if normalized == ".." or normalized.startswith(".." + os.sep):
        raise ValueError("path traversal blocked")
    full = os.path.abspath(os.path.join(root, normalized))
    if os.path.commonpath([root, full]) != root:
        raise ValueError("path outside project")
    real_root = os.path.realpath(root)
    real_full = os.path.realpath(full)
    if os.path.commonpath([real_root, real_full]) != real_root:
        raise ValueError("path outside project")
    if not allow_root and real_full == real_root:
        raise ValueError("project root is not a file")
    return full


def _projects():
    try:
        names = []
        for name in sorted(os.listdir(WORKSPACE)):
            if name.startswith("_") or ".." in name:
                continue
            p = os.path.join(WORKSPACE, name)
            if os.path.isdir(p):
                names.append(name)
        return "\n".join(names) or "(empty)"
    except Exception as e:
        return "error: " + str(e)


def _validate_shell(cmd, project):
    if not isinstance(cmd, str) or not cmd.strip():
        raise ValueError("empty command")
    blocked = ("..", "&&", "||", ";", "$(", "\n", "\r")
    if any(x in cmd for x in blocked) or chr(96) in cmd:
        raise ValueError("shell escape/traversal syntax blocked")
    try:
        tokens = shlex.split(cmd)
    except Exception as e:
        raise ValueError("invalid shell syntax: " + str(e))
    if not tokens:
        raise ValueError("empty command")
    exe = os.path.basename(tokens[0])
    if exe not in SAFE_SHELL_COMMANDS:
        raise ValueError("command not allowed: " + exe)
    if exe in ("python", "python3", "node", "npx") and any(x in tokens for x in ("-c", "-e", "--eval")):
        raise ValueError("inline code execution through shell is blocked")
    for token in tokens[1:]:
        if token.startswith("/") or token.startswith("~"):
            raise ValueError("absolute paths are not allowed")
        if token in (".", "./"):
            continue
        if "/" in token or token.startswith("."):
            if token.startswith("-"):
                continue
            _project_path(project, token)
    return tokens


def _shell(cmd, project="default"):
    try:
        root = _project_root(project)
        _validate_shell(cmd, project)
        r = subprocess.run(cmd, shell=True, capture_output=True,
                          text=True, timeout=600, cwd=root)
        out = "Exit " + str(r.returncode) + "\n"
        if r.stdout:
            out += r.stdout[:3000] + "\n"
        if r.stderr:
            out += r.stderr[:1000]
        return out
    except Exception as e:
        return "error: " + str(e)


def _python(code, project="default"):
    root = _project_root(project)
    path = None
    try:
        if not isinstance(code, str):
            return "error: invalid code"
        if ".." in code or "\x00" in code:
            return "error: traversal/invalid syntax blocked"
        for marker in ("open('/", 'open("/', "os.system(", "subprocess.", "shutil.rmtree("):
            if marker in code:
                return "error: code attempts unrestricted filesystem/process access"
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".py", prefix=".maxai_", dir=root, delete=False) as f:
            path = f.name
            f.write(code)
        r = subprocess.run([sys.executable, path], capture_output=True,
                          text=True, timeout=120, cwd=root)
        out = "Exit " + str(r.returncode) + "\n"
        if r.stdout:
            out += r.stdout[:2500] + "\n"
        if r.stderr:
            out += r.stderr[:1000]
        return out
    except Exception as e:
        return "error: " + str(e)
    finally:
        if path:
            try:
                os.remove(path)
            except Exception:
                pass


def _write(path, content, project="default"):
    try:
        full = _project_path(project, path, allow_root=False)
        os.makedirs(os.path.dirname(full) or _project_root(project), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return "written " + os.path.relpath(full, _project_root(project))
    except Exception as e:
        return "error: " + str(e)


def _read(path, project="default"):
    try:
        full = _project_path(project, path, allow_root=False)
    except Exception as e:
        return "error: " + str(e)
    if not os.path.isfile(full):
        return "not found"
    try:
        with open(full, encoding="utf-8") as f:
            return f.read()[:8000]
    except Exception as e:
        return "error: " + str(e)


def _ls(path=".", project="default"):
    try:
        full = _project_path(project, path)
        items = os.listdir(full)
        return "\n".join(items[:200]) or "(empty)"
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


def _download(url, path="", project="default"):
    if not path:
        path = url.split("/")[-1].split("?")[0] or "download.bin"
    try:
        full = _project_path(project, path, allow_root=False)
        r = requests.get(url, stream=True, timeout=60,
                        headers={"User-Agent": "Mozilla/5.0"})
        os.makedirs(os.path.dirname(full) or _project_root(project), exist_ok=True)
        with open(full, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        return "downloaded " + os.path.relpath(full, _project_root(project))
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
                     if c.isalnum() or c in "_-") or "site"
    files = d.get("files", [])
    if not files:
        return "no files"
    try:
        base = _project_root(project)
    except Exception as e:
        return "error: " + str(e)
    written = 0
    for f in files:
        p = f.get("path", "").strip()
        content = f.get("content", "")
        if not p:
            continue
        try:
            full = _project_path(project, p, allow_root=False)
        except Exception:
            continue
        os.makedirs(os.path.dirname(full) or base, exist_ok=True)
        with open(full, "w", encoding="utf-8") as fp:
            fp.write(content)
        written += 1
    return "OK " + project + " (" + str(written) + " files)\n/site/" + project + "/index.html"


def _gen_code(desc, filename="main.py", project="default"):
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
    _write(filename, code, project=project)
    return "OK " + project + "/" + filename


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
    for name in sorted(os.listdir(WORKSPACE)):
        if name.startswith("_") or ".." in name:
            continue
        p = os.path.join(WORKSPACE, name)
        if not os.path.isdir(p):
            continue
        try:
            root = _project_root(name)
        except Exception:
            continue
        files = []
        for walk_root, _, fs in os.walk(root):
            for fname in fs:
                fp = os.path.join(walk_root, fname)
                try:
                    safe = _project_path(name, os.path.relpath(fp, root), allow_root=False)
                    files.append(os.path.relpath(safe, root))
                except Exception:
                    continue
        out.append({"name": name, "files": files})
    return {"projects": out}


@app.get("/api/download/{project}")
async def download(project: str, authorization: str = Header(None)):
    check_auth(authorization)
    try:
        full = _project_root(project)
    except Exception:
        raise HTTPException(400)
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
    try:
        full = _project_path(project, path)
    except Exception:
        raise HTTPException(403)
    if os.path.isdir(full):
        full = _project_path(project, os.path.join(path, "index.html"))
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
