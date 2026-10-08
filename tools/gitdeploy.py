import os
import json
import time
import requests
import secrets

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_USER = os.environ.get("GITHUB_USER", "")
RENDER_KEY = os.environ.get("RENDER_KEY", "")


# ============================================================
# GitHub
# ============================================================
def gh_create_repo(repo_name, private=False):
    """สร้าง repo ใหม่"""
    r = requests.post(
        "https://api.github.com/user/repos",
        headers={
            "Authorization": "token " + GITHUB_TOKEN,
            "Accept": "application/vnd.github+json",
        },
        json={
            "name": repo_name,
            "private": private,
            "auto_init": False,
        },
        timeout=20,
    )
    if r.status_code == 201:
        return {"ok": True, "url": r.json()["html_url"]}
    if r.status_code == 422:
        # already exists
        return {"ok": True, "url": "https://github.com/" + GITHUB_USER + "/" + repo_name}
    return {"ok": False, "error": r.text[:300]}


def gh_upload_file(repo, path, content, message="add"):
    """อัปโหลดไฟล์เดียว"""
    import base64
    url = "https://api.github.com/repos/" + GITHUB_USER + "/" + repo + "/contents/" + path
    headers = {
        "Authorization": "token " + GITHUB_TOKEN,
        "Accept": "application/vnd.github+json",
    }
    # check existing
    sha = None
    r = requests.get(url, headers=headers, timeout=15)
    if r.status_code == 200:
        sha = r.json().get("sha")

    data = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode(),
    }
    if sha:
        data["sha"] = sha

    r = requests.put(url, headers=headers, json=data, timeout=20)
    return r.status_code in (200, 201)


def gh_upload_files(repo, files):
    """อัปโหลดหลายไฟล์"""
    results = []
    for f in files:
        path = f.get("path", "")
        content = f.get("content", "")
        if not path:
            continue
        ok = gh_upload_file(repo, path, content)
        results.append({"path": path, "ok": ok})
    return results


# ============================================================
# Render
# ============================================================
def render_create_service(repo_name, service_name, env_vars=None):
    """สร้าง Render service"""
    payload = {
        "type": "web_service",
        "name": service_name,
        "ownerId": os.environ.get("RENDER_OWNER", ""),
        "repo": "https://github.com/" + GITHUB_USER + "/" + repo_name,
        "branch": "main",
        "autoDeploy": "yes",
        "serviceDetails": {
            "env": "python",
            "plan": "free",
            "region": "singapore",
            "envSpecificDetails": {
                "buildCommand": "pip install -r requirements.txt",
                "startCommand": "uvicorn server:app --host 0.0.0.0 --port $PORT",
            },
        },
        "envVars": env_vars or [],
    }
    r = requests.post(
        "https://api.render.com/v1/services",
        headers={
            "Authorization": "Bearer " + RENDER_KEY,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    if r.status_code in (200, 201):
        data = r.json()
        return {
            "ok": True,
            "id": data.get("id") or data.get("service", {}).get("id"),
            "url": (data.get("serviceDetails", {}).get("url")
                    or data.get("service", {}).get("serviceDetails", {}).get("url")
                    or data.get("dashboardUrl", "")),
        }
    return {"ok": False, "error": r.text[:300]}


def render_get_status(service_id):
    """ดูสถานะ service"""
    r = requests.get(
        "https://api.render.com/v1/services/" + service_id,
        headers={"Authorization": "Bearer " + RENDER_KEY},
        timeout=15,
    )
    if r.status_code == 200:
        return r.json()
    return None


# ============================================================
# MAIN FLOW — สร้างเว็บใหม่ทั้งชุด
# ============================================================
def create_and_deploy(prompt, project_name=None, env_vars=None):
    """สร้างโปรเจกต์ใหม่ + push GitHub + deploy Render"""
    from ai import ask_ai, strip_fence

    log = []

    # 1. AI ออกแบบ
    log.append("🧠 AI กำลังออกแบบ...")
    system = (
        "สร้างโปรเจกต์ Flask ที่ deploy ได้บน Render ตอบ JSON:\n"
        '{"project":"shop","title":"x",'
        '"requirements":["flask"],'
        '"files":['
        '{"path":"server.py","content":"..."},'
        '{"path":"requirements.txt","content":"flask\\n"},'
        '{"path":"Procfile","content":"web: gunicorn server:app"},'
        '{"path":"templates/index.html","content":"..."}'
        ']}\n'
        "กฎ:\n"
        "- server.py: Flask app + host=0.0.0.0 + PORT จาก env\n"
        "- ต้องมี Procfile\n"
        "- ต้องมี requirements.txt\n"
        "- ภาษาไทย สวย responsive\n"
        "- ห้ามใช้ external API ที่ต้อง key"
    )
    raw = ask_ai(prompt, system=system, json_mode=True, timeout=300)

    try:
        d = json.loads(strip_fence(raw))
    except Exception as e:
        return {"ok": False, "log": "AI parse error: " + str(e)}

    project = project_name or d.get("project", "app-" + secrets.token_hex(3))
    project = "".join(c for c in project.lower() if c.isalnum() or c == "-")[:30]
    files = d.get("files", [])

    if not files:
        return {"ok": False, "log": "no files"}

    log.append("📦 " + project + " (" + str(len(files)) + " ไฟล์)")

    # 2. สร้าง repo
    log.append("📤 สร้าง GitHub repo...")
    repo_result = gh_create_repo(project)
    if not repo_result["ok"]:
        return {"ok": False, "log": "repo: " + repo_result.get("error", "")}
    log.append("✅ repo: " + repo_result["url"])

    # 3. Upload ไฟล์ทั้งหมด
    log.append("📤 อัปโหลดไฟล์...")
    results = gh_upload_files(project, files)
    ok_count = sum(1 for r in results if r["ok"])
    log.append("✅ " + str(ok_count) + "/" + str(len(results)) + " ไฟล์")

    # 4. Deploy บน Render
    log.append("🚀 สร้าง Render service...")
    service_name = project
    r_env = env_vars or []
    render_result = render_create_service(project, service_name, r_env)

    if not render_result["ok"]:
        log.append("⚠️ Render: " + render_result.get("error", ""))
        return {
            "ok": False,
            "log": "\n".join(log),
            "repo": repo_result["url"],
        }

    service_id = render_result.get("id", "")
    log.append("✅ Render ID: " + str(service_id))

    # 5. รอ deploy
    log.append("⏳ รอ deploy (3-5 นาที)...")
    final_url = ""
    for i in range(30):
        time.sleep(10)
        status = render_get_status(service_id)
        if not status:
            continue
        url = status.get("serviceDetails", {}).get("url", "")
        if url:
            final_url = url
            log.append("🌐 " + url)
            break

    if not final_url:
        # fallback: สร้าง URL จาก pattern
        final_url = "https://" + service_name + ".onrender.com"

    return {
        "ok": True,
        "log": "\n".join(log),
        "url": final_url,
        "repo": repo_result["url"],
        "answer": (
            "✅ สร้างเว็บสำเร็จ!\n"
            "🌐 URL: " + final_url + "\n"
            "📁 Repo: " + repo_result["url"] + "\n"
            "(รอ 3-5 นาทีให้ Render deploy เสร็จ)"
        ),
    }


# ============================================================
# Register tools
# ============================================================
def create_website(prompt):
    """สร้างเว็บ + deploy จริง"""
    result = create_and_deploy(prompt)
    if result["ok"]:
        return ("OK\n" + result["log"] +
                "\nURL: " + result["url"] +
                "\nREPO: " + result["repo"] +
                "\nPREVIEW:" + result["url"])
    return "FAILED\n" + result["log"]


def check_deploy(service_id):
    """เช็คสถานะ deploy"""
    s = render_get_status(service_id)
    if not s:
        return "not found"
    d = s.get("serviceDetails", {})
    return ("status: " + str(s.get("suspended", "?")) +
            "\nurl: " + d.get("url", "?"))


def list_my_repos():
    """ดู repo ทั้งหมด"""
    r = requests.get(
        "https://api.github.com/user/repos?per_page=100",
        headers={"Authorization": "token " + GITHUB_TOKEN},
        timeout=15,
    )
    if r.status_code != 200:
        return "error"
    repos = r.json()
    return "\n".join([x["name"] for x in repos]) or "(ว่าง)"
