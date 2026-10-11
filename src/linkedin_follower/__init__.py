import os, asyncio
from dotenv import load_dotenv

from linkedin_follower import hatz, Enrich, setup

load_dotenv()
HATZ_API_URL = "https://ai.hatz.ai/v1/chat/completions"
HATZ_API_KEY = os.environ.get("HATZ_API_KEY", "")
HATZ_MODEL = "anthropic.claude-haiku-4-5"

ORG_NAME = "Organization"
SKIP_COL = ["Phone"]
BREAK_NAME = "Data Confidence"

SUCEED_LIST = "./data/suceedlist.csv"

def main() -> None:
    adapter: hatz.HatzAdapter = hatz.HatzAdapter(HATZ_API_URL, HATZ_API_KEY, HATZ_MODEL)
    enrich: Enrich.Enrichinator = Enrich.Enrichinator(adapter, ORG_NAME, SKIP_COL, BREAK_NAME, SUCEED_LIST)
    print("Hello from linkedin-follower!")
    if not os.path.exists('./data'):
        print("Looks like the program hasn't been run before.")
        print("Setup = Y, Skip = N. Make sure to press enter to submit.")
        choice: str = input()
        if(choice.lower() == "n"):
            print("Alright! Let's get going!")
            asyncio.run(setup.launch_browser())
        else:
            print("Skipping setup!")
    print("Let's get following!!")
    enrich.main()

main()