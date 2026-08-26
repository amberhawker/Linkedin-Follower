from ddgs import DDGS
from typing import List, Dict, Optional
import pandas as pd
from collections import defaultdict
import sys
import time

ORG_NAME = "Organization"
SKIP_COL = "Phone"
BREAK_NAME = "Data Confidence"

names = ["Todd Chapman", "Amber Hawker"]
orgs = ["CMIT Solutions", "Okanagan Marine Robotics"]
results = {}

def import_data():
    unenriched = pd.read_excel("data/unenriched.xlsx")
    print(unenriched)
    dataset = defaultdict(set)
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

def build_queries():
    queries = []
    for name, org in zip(names, orgs):
        if not name or not org:
            continue
        query = f'site:linkedin.com/in/ "{name}" "{org}"'
        queries.append(query)
    return queries

def search_profile(engine: DDGS, query: str):
    print(f"query: {query}")
    try:
        results = list(engine.text(query, max_results=3))
        return results
    except Exception as e:
        print(f"Error searching for {query}: {e}")
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
    import_data()

    engine = DDGS()
    #queries = build_queries()

    #for query in queries:
    #    results[query] = search_profile(engine, query)

    #print_results(queries)

if __name__ == "__main__":
    main()