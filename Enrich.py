from ddgs import DDGS
from ddgs.exceptions import DDGSException, RatelimitException
from typing import List, Dict, Optional
import pandas as pd
from collections import defaultdict
import sys
import time, random
import requests as rq
import os

HATZ_API_URL = "https://ai.hatz.ai/v1/chat/completions"
HATZ_API_KEY = os.environ.get("HATZ_API_KEY", "")  # Set your key in the HATZ_API_KEY env var
HATZ_MODEL = "gpt-4o"

ORG_NAME = "Organization"
SKIP_COL = "Phone"
BREAK_NAME = "Data Confidence"

names = ["Todd Chapman", "Amber Hawker"]
orgs = ["CMIT Solutions", "Okanagan Marine Robotics"]
results = {}

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

def build_queries(excel):
    # form of: {"company": [[name, query], []]...}
    dataset = defaultdict(list)
    for org in excel.keys():
        names = excel[org]
        for name in names:
            dataset[org].append([name, f'site:linkedin.com/in/ "{name}" {org}'])
            print(dataset[org])
    return dataset # List of queries

def search_profile(engine: DDGS, query: str):
    #backends = ["bing", "brave", "duckduckgo", "google", "mojeek", "startpage", "yandex", "yahoo"]
    backends = ["bing", "brave", "duckduckgo", "google", "yandex", "yahoo"]
    backendIdx = 0

    print(f"query: {query}")
    time.sleep(random.uniform(1.5, 3.0)) # Jitter

    for attempt in range(8):
        try:
            print(f"backend: {backends[backendIdx]}")
            results = list(engine.text(query, max_results=5, backends=backends[backendIdx]))
            return results
        except DDGSException as e:
            print(f"Error searching for {query}: {e}")
            backendIdx += 1
            if backendIdx >= len(backends): backendIdx = 0
            print(f"Swi: {backends[backendIdx]}")
            sleep = min(10 * (2**attempt), 60)
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
        "You are a research assistant helping find the correct LinkedIn profile URL for a person. "
        "Given a list of search results, pick the single best matching LinkedIn profile URL "
        "for the specified person and organization. "
        "If none of the results are a good match, respond with exactly: none\n"
        "Otherwise respond with ONLY the URL of the best matching result — nothing else."
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

def main() -> None:
    excel = import_data()

    engine = DDGS(timeout=10)
    queries = build_queries(excel) # Returns: {"company": [[name, query], []]...}

    results = {}
    for org in queries.keys():
        for guy in queries[org]:
            name = guy[0]
            query = guy[1]
            results[query] = search_profile(engine, query)
            chosen_url = chud_ai(results[query], person_name=name, org_name=org)
            print(chosen_url)

    #print_results(queries)

if __name__ == "__main__":
    main()