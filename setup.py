from stagehand import Stagehand, local_browser
import asyncio

async def launch_browser():
    print("Launching browser...")
    browser = await local_browser.launch(headless=False, user_data_dir="./data/chrome_profile")
    stagehand = await Stagehand.create(
        browser=browser
    )
    pages = await browser.context.pages()
    print(pages)
    page = pages[0] if pages else await browser.context.new_page()
    await page.goto("https://linkedin.com")

    print("Press enter when you've successfully logged in:")
    input()
    browser.close()

if __name__ == "__main__":
    asyncio.run(launch_browser())