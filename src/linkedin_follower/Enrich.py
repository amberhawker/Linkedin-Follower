import time, random
import os, csv
import asyncio
import json
import uuid
import re

from pathlib import Path
from dotenv import load_dotenv
from typing import List, Optional
from collections import defaultdict
import requests as re

import pandas as pd
from ddgs import DDGS
from ddgs.exceptions import DDGSException
from stagehand import Stagehand, local_browser
from pydantic import BaseModel, Field

from linkedin_follower.hatz import HatzAdapter

class ConnectionStatus(BaseModel):
    """Schema for LinkedIn connection status extraction."""
    is_connected_or_pending: bool = Field(description="true if button now says Pending, 1st, or invitation was sent")
    hit_limit: bool = Field(description="true if weekly invitation limit modal or email verification popped up")
    action_taken: str = Field(description="sent_request, already_connected, hit_limit, or failed")

load_dotenv()
class Enrichinator:
    def __init__(self, hatz: HatzAdapter, org_name: str, skip_cols: list[str], break_name: str, suceed_list: str):
        self.hatz = hatz
        self.org_name = org_name
        self.skip_cols = skip_cols
        self.break_name = break_name
        self.suceed_list = suceed_list


    def import_data(self):
        unenriched = pd.read_excel("data/unenriched.xlsx")
        dataset = defaultdict(set) # form of: {"company": (unique1, unique2, unique3), ...}
        for idx, row in unenriched.iterrows():

            org = row[self.org_name]

            for colname, val in row.items():
                if colname == self.org_name: continue
                if colname in self.skip_cols: continue
                if colname == self.break_name: break
                
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

    def get_finished_names(self):
        names = []
        with open(Path(self.suceed_list), "r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            for row in reader:
                names.append(row[0])
        return names

    def build_queries(self, excel):
        # form of: {"company": [[name, query], []]...}
        dataset = defaultdict(list)
        for org in excel.keys():
            names = excel[org]
            done_names = self.get_finished_names()
            for done_name in done_names:
                if done_name in names:
                    print(f"Removed '{done_name}' from list of users to search for")
                    excel[org].discard(done_name)
            for name in names:
                #dataset[org].append([name, f'site:linkedin.com/in/ "{name}" {org}'])
                dataset[org].append([name, f'linkedin {name} {org}'])
        return dataset # List of queries

    def search_profile(self, engine: DDGS, query: str):
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

    def print_results(self, queries: List):
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


    async def browser_time(self, url_list):
        success_list = []

        print("Launching local Chrome browser...", flush=True)
        try:
            browser = await local_browser.launch(headless=False, user_data_dir="./data/chrome_profile")
        except Exception as e:
            print(f"Failed to launch Chrome: {e}", flush=True)
            return

        try:
            print("Creating Stagehand session...", flush=True)
            stagehand = await Stagehand.create(
                browser=browser,
                model=self.hatz.hatz_llm_generate,
            )
            print("Opened stagehand & browser", flush=True)
            try:
                pages = await browser.context.pages()
                page = pages[0] if pages else await browser.context.new_page()
                for orig_url in list(url_list.keys()):
                    if not re.search(r"linkedin\.com/in/", orig_url, re.IGNORECASE):
                        print(f"Skipping URL: {orig_url}")
                        continue
                    name = url_list[orig_url]
                    target_url = re.sub(r"https?://[a-z]{2}\.linkedin\.com", "https://linkedin.com", orig_url)
                    print(f"Reformatted url: {target_url}")
                    print(f"Navigating to {target_url}...", flush=True)
                    await page.goto(target_url)
                    await asyncio.sleep(2)  # let LinkedIn's SPA fully settle
                    attempts = 0
                    while True:
                        if attempts >= 5:
                            print(f"URL: {target_url}, Name: {name} has FAILED.")
                            break

                        act_result = await stagehand.act("""
                            goal: send a LinkedIn connection request to this profile.

                            CRITICAL PRIORITIES (follow from top to bottom):
                            1. IF AN INVITATION MODAL IS CURRENTLY OPEN ("You can customize this invitation" or "Add a note to your invitation"):
                            - Click "Send without a note" (or "Send" / "Send now").
                            - NEVER click "Cancel", the "X" close button, or click outside the modal.
                            - Do NOT click "Add a note".
                            2. IF A POPUP DEMANDS AN EMAIL ADDRESS OR SAYS WEEKLY INVITATION LIMIT REACHED:
                            - Click "Cancel" or "Close".
                            3. IF THE PROFILE ALREADY SHOWS "Pending" OR "1st":
                            - Do not take any action.
                            4. IF A DROPDOWN MENU IS CURRENTLY OPEN (from clicking 'More' or '...'):
                            - Click the "Connect" option from the menu.
                            5. IF A DIRECT "Connect" BUTTON IS VISIBLE IN THE MAIN PROFILE HEADER:
                            - Click the primary "Connect" button.
                            6. IF NO "Connect" BUTTON IS VISIBLE IN THE HEADER:
                            - Click "More" or "..." in the profile header to open the dropdown menu.
                            7. UNRELATED POPUPS ONLY:
                            - If a bottom-right chat drawer or cookie banner is in the way, minimize or dismiss it. NEVER dismiss the invitation modal.
                            8. DO NOT click "Follow", "Message", or "Endorse".
                            """)
                        print(f"  [Attempt {attempts + 1}] Act: {act_result.data.action_description} (success={act_result.data.success})", flush=True)

                        await asyncio.sleep(2)  # allow modal / button transition to render

                        status = await stagehand.extract(
                            "determine the connection status of this profile right now",
                            ConnectionStatus,
                        )
                        print(f"  [Attempt {attempts + 1}] Status: connected/pending={status.data.is_connected_or_pending}, action={status.data.action_taken!r}, limit={status.data.hit_limit}", flush=True)

                        attempts += 1
                        if status.data.hit_limit:
                            print("hit weekly limit / email requirement, stopping run")
                            return success_list
                        elif status.data.is_connected_or_pending:
                            print(f"connection request sent successfully to {name}")
                            self.write_succeeded([name])
                            success_list.append(orig_url)
                            break
            except Exception as e:
                print(f"Exception occured: {e}")
            finally:
                print("Closing stagehand", flush=True)
                await stagehand.close()
        except Exception as e:
            print(f"Stagehand error: {e}", flush=True)
        finally:
            print("Closing browser", flush=True)
            await browser.close()
            return success_list

    def suceedlist_exists(self):
        if Path(self.suceed_list).exists(): return True
        else: return False

    def write_succeeded(self, names):
        mode = None
        if(self.suceedlist_exists()): mode = "a"
        else: mode = "w"

        with open(Path(self.suceed_list), mode, newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            for item in names:
                writer.writerow([item])

    def cache_result(self, url, name):
        file = Path("./data/urlcache.json")
        cache = json.loads(file.read_text()) if file.exists() else {}
        cache[url] = name
        file.write_text(json.dumps(cache, indent=2))

    def check_cache(self, name):
        file = Path("./data/urlcache.json")
        cache = json.loads(file.read_text()) if file.exists() else {} #url: name

        inverted = {v: k for k, v in cache.items()} # name: url
        return inverted.get(name)
        
        return None
        
    def main(self) -> None:
        excel = self.import_data()
        print("Data imported from sheet.")

        engine = DDGS(timeout=10)
        queries = self.build_queries(excel) # Returns: {"company": [[name, query], []]...}
        print("Built queries.")

        results = {}
        chosen_urls = {} # {url: name}
        finished_names = self.get_finished_names()
        for org in queries.keys():
            for guy in queries[org]:
                name = guy[0]
                if name in finished_names:
                    print(f"Skipping: {name}, since it's already been done.")
                    continue
                query = guy[1]
                cache_url = self.check_cache(name)
                if cache_url:
                    print(f"HIT cache for: {name}")
                    chosen_urls[cache_url] = name
                else:
                    print(f"MISS cache for: {name}")
                    options = self.search_profile(engine, query)
                    chosen_url = self.hatz.chud_ai(options)
                    if chosen_url:
                        chosen_urls[chosen_url] = name
                        self.cache_result(chosen_url, name)
                    else:
                        chosen_urls[str(uuid.uuid4())] = name

        connected = asyncio.run(self.browser_time(chosen_urls)) # returns list of urls
        print(f"Succeeded on: {connected}")
        if not connected:
            print("No further connections completed on this run.")
        for url in connected:
            if url in chosen_urls:
                print(f"Removing {url} from the search")
                chosen_urls.pop(url)
        print(connected)