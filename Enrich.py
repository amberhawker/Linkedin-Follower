from ddgs import DDGS
from ddgs.exceptions import DDGSException, RatelimitException
from typing import List, Dict, Optional
import pandas as pd
from collections import defaultdict
import sys
import time, random

ORG_NAME = "Organization"
SKIP_COL = "Phone"
BREAK_NAME = "Data Confidence"

names = ["Todd Chapman", "Amber Hawker"]
orgs = ["CMIT Solutions", "Okanagan Marine Robotics"]
results = {}

def import_data():
    unenriched = pd.read_excel("data/unenriched.xlsx")
    print(unenriched)
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
    print(dataset)
    return dataset

def build_queries(excel):
    queries = []

    for org in excel.keys():
        names = excel[org]
        for name in names:
            queries.append(f'site:linkedin.com/in/ "{name}" "{org}"')
    return queries # List of queries

def search_profile(engine: DDGS, query: str):
    backends = ["bing", "brave", "duckduckgo", "google", "mojeek", "startpage", "yandex", "yahoo"]
    backendIdx = 0

    print(f"query: {query}")
    time.sleep(random.uniform(5.0, 10.0)) # Jitter

    for attempt in range(100):
        try:
            print(f"backend: {backends[backendIdx]}")
            results = list(engine.text(query, max_results=5, backends=backends[backendIdx]))
            return results
        except DDGSException as e:
            print(f"Error searching for {query}: {e}")
            backendIdx += 1
            if backendIdx >= len(backends): backendIdx = 0
            print(f"Swi: {backends[backendIdx]}")
            time.sleep(10 * (2**attempt))
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


def main() -> None:
    excel = import_data()

    engine = DDGS()
    queries = build_queries(excel) # Returns List object

    for query in queries:
        results[query] = search_profile(engine, query)
        for result in results[query]:
            print(f'Will search for: {result}')

    #print_results(queries)

if __name__ == "__main__":
    main()