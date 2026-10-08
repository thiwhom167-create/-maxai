from .gitdeploy import create_website, check_deploy, list_my_repos

register("create_website", create_website)
register("check_deploy", check_deploy)
register("list_repos", list_my_repos)

TOOLS_DESC += """
- create_website(prompt): สร้างเว็บ + push GitHub + deploy Render
- check_deploy(service_id): เช็คสถานะ deploy
- list_repos(): ดู repo ทั้งหมด
"""
