from app.database import init_db
from app.agents.career_agents import run_poland_career_scan

if __name__ == "__main__":
    init_db()
    result = run_poland_career_scan()
    print("Cloud scan completed:", result)