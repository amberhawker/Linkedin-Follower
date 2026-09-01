import time, random
import os, csv
import asyncio
import json
import threading
import uuid

import requests as rq
from pathlib import Path
from dotenv import load_dotenv
from typing import List, Optional
from collections import defaultdict

import pandas as pd
from ddgs import DDGS
from ddgs.exceptions import DDGSException
from stagehand import Stagehand, local_browser


load_dotenv()

HATZ_API_URL = "https://ai.hatz.ai/v1/chat/completions"
HATZ_API_KEY = os.environ.get("HATZ_API_KEY", "")
HATZ_MODEL = "gpt-4o"
BROWSERBASE_API_KEY = os.environ.get("BROWSERBASE_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

ORG_NAME = "Organization"
SKIP_COL = "Phone"
BREAK_NAME = "Data Confidence"

SUCEED_LIST = "./data/suceedlist.csv"

def import_data():
    unenriched = pd.read_excel("data/unenriched.xlsx")
    dataset = defaultdict(set) # form of: {"company": (unique1, unique2, unique3), ...}
    for idx, row in unenriched.iterrows():

        org = row[ORG_NAME]

        for colname, val in row.items():
            if colname == ORG_NAME: continue
            if colname == SKIP_COL: continue
            if colname == BREAK_NAME: break
            
            name = row[colname]

            names = str.split(str(name), ",")
            for nayme in names:
                substr = str.partition(str(nayme), "(")
                nayme = substr[0]

                if "not" in str(nayme).lower(): continue
                if ")" in str(nayme): continue
                if "-" in str(nayme): continue
                if "nan" == str(nayme): continue

                dataset[org].add(nayme.strip())
    return dataset

def get_finished_names():
    names = []
    with open(Path(SUCEED_LIST), "r", newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            names.append(row[0])
    return names

def build_queries(excel):
    # form of: {"company": [[name, query], []]...}
    dataset = defaultdict(list)
    for org in excel.keys():
        names = excel[org]
        done_names = get_finished_names()
        for done_name in done_names:
            if done_name in names:
                print(f"Removed '{done_name}' from list of users to search for")
                excel[org].discard(done_name)
        for name in names:
            #dataset[org].append([name, f'site:linkedin.com/in/ "{name}" {org}'])
            dataset[org].append([name, f'linkedin {name} {org}'])
    return dataset # List of queries

def search_profile(engine: DDGS, query: str):
    #backends = ["bing", "brave", "duckduckgo", "google", "mojeek", "startpage", "yandex", "yahoo"] Bing sucks shit
    backends = ["brave", "duckduckgo", "google", "yandex", "yahoo"]
    backendIdx = 0

    print(f"query: {query}")
    time.sleep(random.uniform(1.5, 3.0)) # Jitter

    for attempt in range(5):
        try:
            print(f"backend: {backends[backendIdx]}")
            results = list(engine.text(query, max_results=5, backends=backends[backendIdx]))
            return [ r for r in results if "linkedin.com/in/" in r.get("href", "")] # Filter to only profiles
        except DDGSException as e:
            engine = DDGS(timeout=10)
            print(f"Error searching for {query}: {e}")
            backendIdx += 1
            if backendIdx >= len(backends): backendIdx = 0
            print(f"Swi: {backends[backendIdx]}")
            sleep = min(10 * (2**attempt), 30)
            print(f"Sleep for: {sleep}")
            time.sleep(sleep)
    return []

def print_results(queries: List):
    for query_name in queries:
        query_results = results.get(query_name, [])
        print(f"\n--- Results for: {query_name} ---")
        if not query_results:
            print("No results found.")
            continue
        for idx, item in enumerate(query_results, start=1):
            print(f"Result #{idx}:")
            print(f"  Title: {item.get('title')}")
            print(f"  URL: {item.get('href')}")
            print(f"  Snippet: {item.get('body')}")

def chud_ai(search_results: List, person_name: str = "", org_name: str = "") -> Optional[str]:
    """
    Uses the Hatz AI API to pick the best LinkedIn profile URL from search results.
    Returns the chosen URL string, or None if the AI decides none are correct.
    """

    # Build a numbered list of candidates for the AI
    candidates_text = ""
    for i, result in enumerate(search_results, start=1):
        title   = result.get("title", "(no title)")
        url     = result.get("href", "(no url)")
        snippet = result.get("body", "(no snippet)")
        candidates_text += f"\n[{i}] Title: {title}\n    URL: {url}\n    Snippet: {snippet}\n"

    system_prompt = (
        "You are a strict data-matching engine that identifies the correct personal LinkedIn profile URL for an individual.\n"
        "Rules:\n"
        "1. The URL MUST be a personal LinkedIn profile containing '/in/'. Never return company pages, posts, or non-LinkedIn domains.\n"
        "2. If none of the candidates clearly match the person and their organization, output exactly: none\n"
        "3. Output MUST be strictly raw plain text (the single URL string or 'none'). No markdown, no backticks, no quotes, no extra whitespace, no conversational text."
    )

    user_message = (
        f"Person: {person_name}\n"
        f"Organization: {org_name}\n\n"
        f"Search results:{candidates_text}\n"
        "Which URL is the best LinkedIn profile match? Reply with just the URL, or 'none'."
    )

    headers = {
        "X-API-Key": HATZ_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "model": HATZ_MODEL,
        "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_message},
        ],
    }

    try:
        response = rq.post(HATZ_API_URL, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        ai_answer = data["choices"][0]["message"]["content"].strip()
        print(f"AI picked: {ai_answer}")
        return None if ai_answer.lower() == "none" else ai_answer
    except Exception as e:
        print(f"Hatz AI error: {e}")
        return None

async def browser_time(url_list):
    success_list = []

    print("Launching local Chrome browser...", flush=True)
    try:
        browser = await local_browser.launch(headless=False, user_data_dir="./data/chrome_profile")
    except Exception as e:
        print(f"Failed to launch Chrome: {e}", flush=True)
        return

    try:
        print("Creating Stagehand session...", flush=True)
        stagehand = await Stagehand.create(browser=browser, api_key=BROWSERBASE_API_KEY,
                                           model_api_key=GEMINI_API_KEY,
                                           model="google/gemini-flash-lite-latest")
        print("Opened stagehand & browser", flush=True)
        try:
            pages = await browser.context.pages()
            page = pages[0] if pages else await browser.context.new_page()
            counter = 0
            for url in url_list.keys():
                if "linkedin/in/" not in url: print(f"Skipping URL: {url}")
                if counter % 20 == 0 and counter != 0:
                    print("Sleeping for 1 day!!!!")
                    await asyncio.sleep(86400)
                print(f"Navigating to {url}...", flush=True)
                await page.goto(url)
                await asyncio.sleep(5)
                attempts = 0
                while True:
                    if attempts >= 10:
                        print(f"URL: {url}, Name: {url_list[url]} has FAILED.")
                        break
                    observe = await stagehand.observe(
                        f'''find the button to connect with this profile. prioritize:
                        1. close or dismiss button if a blocking modal or popup overlay is visible
                        2. direct "connect" button in the profile header
                        3. "more" or "..." button in the profile header if connect is hidden in the menu
                        4. "send without a note" or "send now" button if a connection note dialog is open
                        ignore if the profile is already a 1st-degree connection or connection request is pending'''
                    )
                    print(f"Observe returned: {observe.data}")
                    if observe.data == []:
                        success_list.append(url_list[url])
                        break
                    try:
                        result = None
                        for action in observe.data:
                            result = await stagehand.act(action)
                        attempts += 1
                    except Exception as e:
                        print(f"Exception occured: {e}")
                    finally:
                        print(f"Action result: {result}", flush=True)
                        time.sleep(10)

        finally:
            print("Closing stagehand", flush=True)
            await stagehand.close()
    except Exception as e:
        print(f"Stagehand error: {e}", flush=True)
    finally:
        print("Closing browser", flush=True)
        await browser.close()
    return success_list

def suceedlist_exists():
    if Path(SUCEED_LIST).exists(): return True
    else: return False

def write_succeeded(names):
    mode = None
    if(suceedlist_exists()): mode = "a"
    else: mode = "w"

    with open(Path(SUCEED_LIST), mode, newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for item in names:
            writer.writerow([item])

def cache_result(url, name):
    file = Path("./data/urlcache.json")
    cache = json.loads(file.read_text()) if file.exists() else {}
    cache[url] = name
    file.write_text(json.dumps(cache, indent=2))

def check_cache(name):
    file = Path("./data/urlcache.json")
    cache = json.loads(file.read_text()) if file.exists() else {} #url: name

    inverted = {v: k for k, v in cache.items()} # name: url
    return inverted.get(name)
    
    return None
    
def main() -> None:
    excel = import_data()
    print("Data imported from sheet.")

    engine = DDGS(timeout=10)
    queries = build_queries(excel) # Returns: {"company": [[name, query], []]...}
    print("Built queries.")

    results = {}
    chosen_urls = {} # {url: name}
    for org in queries.keys():
        for guy in queries[org]:
            name = guy[0]
            query = guy[1]
            cache_url = check_cache(name)
            if cache_url:
                print(f"HIT cache for: {name}")
                chosen_urls[cache_url] = name
            else:
                print(f"MISS cache for: {name}")

                results[query] = search_profile(engine, query)
                print(f"Searched for: {query}")
                chosen_url = chud_ai(results[query], person_name=name, org_name=org)
                print(chosen_url)
                if chosen_url:
                    chosen_urls[chosen_url] = name
                    cache_result(chosen_url, name)
                else:
                    chosen_urls[str(uuid.uuid4())] = name

    connected = asyncio.run(browser_time(chosen_urls))
    print(f"Succeeded on: {connected}")
    write_succeeded(connected)

if __name__ == "__main__":
    main()