from linkedin_follower import Enrich, setup
import os, asyncio

def main() -> None:
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
    Enrich.main()

main()