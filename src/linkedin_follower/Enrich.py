import time, random
import os, csv
import asyncio
import json
import threading
import uuid
import re

import requests as rq
from pathlib import Path
from dotenv import load_dotenv
from typing import List, Optional
from collections import defaultdict

import pandas as pd
import openai
from ddgs import DDGS
from ddgs.exceptions import DDGSException
from stagehand import Stagehand, local_browser
from pydantic import BaseModel, Field

from stagehand._generated.models import (
    LLMMessageGenerateParams,
    LLMMessageGenerateResult,
    LLMStructuredGenerateParams,
    LLMStructuredGenerateResult,
    LLMTextContent,
    LLMUsage,
    LLMRole,
    LLMToolUseContent,
    LLMMessageContentBlock,
)


class ConnectionStatus(BaseModel):
    """Schema for LinkedIn connection status extraction."""
    is_connected_or_pending: bool = Field(description="true if button now says Pending, 1st, or invitation was sent")
    hit_limit: bool = Field(description="true if weekly invitation limit modal or email verification popped up")
    action_taken: str = Field(description="sent_request, already_connected, hit_limit, or failed")


load_dotenv()

HATZ_API_URL = "https://ai.hatz.ai/v1/chat/completions"
HATZ_API_KEY = os.environ.get("HATZ_API_KEY", "")
HATZ_MODEL = "anthropic.claude-haiku-4-5"

ORG_NAME = "Organization"
SKIP_COL = "Phone"
BREAK_NAME = "Data Confidence"

SUCEED_LIST = "./data/suceedlist.csv"

_hatz_client = openai.AsyncOpenAI(
    base_url=HATZ_API_URL.removesuffix("/chat/completions"),  # wants …/v1
    api_key=HATZ_API_KEY,
    default_headers={"X-API-Key": HATZ_API_KEY},
)

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

    # Turns stagehand openai calls into hatz endpoint stuff ... doozey if i say so myself

def _stagehand_messages_to_openai(messages):
    """Convert Stagehand LLMMessage list → OpenAI messages list."""
    out = []
    for msg in messages:
        content_blocks = msg.content if isinstance(msg.content, list) else [msg.content]
        parts = []
        for block in content_blocks:
            # Unwrap RootModel wrappers
            b = block.root if hasattr(block, "root") else block
            if hasattr(b, "type"):
                if b.type == "text":
                    parts.append({"type": "text", "text": b.text})
                elif b.type == "image":
                    parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{b.mime_type};base64,{b.data}"},
                    })
                elif b.type == "tool_use":
                    # Tool-use content from assistant turns needs special handling
                    parts.append({"type": "text", "text": json.dumps({"tool_use": b.name, "id": b.id, "input": {k: v.root if hasattr(v, "root") else v for k, v in b.input.items()}})})
                elif b.type == "tool_result":
                    result_text = ""
                    for tb in (b.content or []):
                        tb_inner = tb.root if hasattr(tb, "root") else tb
                        if hasattr(tb_inner, "text"):
                            result_text += tb_inner.text
                    parts.append({"type": "text", "text": result_text})
            else:
                parts.append({"type": "text", "text": str(b)})
        if len(parts) == 1 and parts[0]["type"] == "text":
            out.append({"role": msg.role.value, "content": parts[0]["text"]})
        else:
            out.append({"role": msg.role.value, "content": parts})
    return out


async def hatz_llm_generate(params):
    """LLMGenerateCallback: handles both structured and message generate."""
    is_structured = isinstance(params, LLMStructuredGenerateParams)

    openai_messages = []
    if params.system_prompt:
        openai_messages.append({"role": "system", "content": params.system_prompt})
    openai_messages.extend(_stagehand_messages_to_openai(params.messages))

    kwargs = {
        "model": HATZ_MODEL,
        "messages": openai_messages,
    }
    if params.temperature is not None:
        kwargs["temperature"] = params.temperature
    if params.stop_sequences:
        kwargs["stop"] = params.stop_sequences

    if is_structured and params.response_format:
        rf = params.response_format
        schema_dict = _field_schema_to_dict(rf.schema_) if rf.schema_ else {}
        schema_instruction = (
            "IMPORTANT: Return a single, valid JSON object strictly adhering to this schema:\n"
            f"{json.dumps(schema_dict, indent=2)}\n\n"
            "Rules:\n"
            "- Output MUST be pure, raw JSON starting with '{' and ending with '}'.\n"
            "- Do NOT wrap the JSON in markdown code blocks like ```json or ```.\n"
            "- Do NOT include any preamble, commentary, or extra text."
        )
        if openai_messages:
            last_msg = openai_messages[-1]
            if isinstance(last_msg.get("content"), str):
                last_msg["content"] += "\n\n" + schema_instruction
            elif isinstance(last_msg.get("content"), list):
                last_msg["content"].append({"type": "text", "text": schema_instruction})
        else:
            openai_messages.append({"role": "system", "content": schema_instruction})
        kwargs["response_format"] = {"type": "json_object"}
    elif not is_structured and hasattr(params, "tools") and params.tools:
        kwargs["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    **({"description": t.description} if t.description else {}),
                    "parameters": {
                        "type": t.input_schema.type,
                        **({"properties": {
                            k: {ik: (_field_schema_to_dict(iv) if hasattr(iv, "root") else iv) for ik, iv in v.items()}
                            for k, v in t.input_schema.properties.items()
                        }} if t.input_schema.properties else {}),
                        **({"required": t.input_schema.required} if t.input_schema.required else {}),
                    },
                },
            }
            for t in params.tools
        ]

    try:
        response = await _hatz_client.chat.completions.create(**kwargs)
    except Exception as e:
        print(f"Hatz LLM error: {e}", flush=True)
        raise

    choice = response.choices[0]
    message = choice.message
    usage_data = response.usage

    usage = LLMUsage(
        input_tokens=getattr(usage_data, "prompt_tokens", None) or 0,
        output_tokens=getattr(usage_data, "completion_tokens", None) or 0,
        total_tokens=getattr(usage_data, "total_tokens", None) or 0,
    )

    text_content = message.content or ""
    stop_reason = choice.finish_reason or "stop"

    if is_structured:
        rf = params.response_format
        schema_dict = _field_schema_to_dict(rf.schema_) if (rf and rf.schema_) else {}
        structured = _parse_json_from_llm(text_content)
        fallback = _build_schema_fallback(schema_dict)

        if not isinstance(structured, dict):
            print(f"[Stagehand LLM] Note: Parsing '{rf.name if rf else 'unknown'}' as JSON failed. Raw: {text_content[:150]!r}. Using fallback.", flush=True)
            structured = fallback
        else:
            for req_key in schema_dict.get("required", []):
                if req_key not in structured:
                    structured[req_key] = fallback.get(req_key)

        return LLMStructuredGenerateResult(
            role=LLMRole.assistant,
            content=LLMTextContent(type="text", text=text_content),
            stop_reason=stop_reason,
            usage=usage,
            output_format="json_schema",
            structured_content=structured,
        )
    else:
        # Check for tool calls in the response
        content_blocks = []
        if text_content:
            content_blocks.append(
                LLMMessageContentBlock(root=LLMTextContent(type="text", text=text_content))
            )
        if message.tool_calls:
            for tc in message.tool_calls:
                content_blocks.append(
                    LLMMessageContentBlock(root=LLMToolUseContent(
                        type="tool_use",
                        id=tc.id,
                        name=tc.function.name,
                        input=json.loads(tc.function.arguments) if tc.function.arguments else {},
                    ))
                )
        if not content_blocks:
            content_blocks.append(
                LLMMessageContentBlock(root=LLMTextContent(type="text", text=""))
            )

        return LLMMessageGenerateResult(
            role=LLMRole.assistant,
            content=content_blocks if len(content_blocks) > 1 else content_blocks[0].root,
            stop_reason=stop_reason,
            usage=usage,
            output_format="text",
        )


def _field_schema_to_dict(val):
    """Recursively convert FieldSchema RootModels to plain dicts."""
    if hasattr(val, "root"):
        val = val.root
    if isinstance(val, dict):
        return {k: _field_schema_to_dict(v) for k, v in val.items()}
    if isinstance(val, list):
        return [_field_schema_to_dict(v) for v in val]
    return val


def _parse_json_from_llm(text: str):
    """Extract and parse a JSON dict or list from model output, handling markdown code fences."""
    if not text:
        return None
    cleaned = text.strip()
    if "```" in cleaned:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned)
        if m:
            cleaned = m.group(1).strip()
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            data = json.loads(text[start:end + 1])
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return None


def _build_schema_fallback(schema_dict: dict) -> dict:
    """Construct a minimal valid object satisfying the given JSON schema."""
    result = {}
    props = schema_dict.get("properties", {})
    for k, v in props.items():
        t = v.get("type")
        is_nullable = (
            v.get("nullable", False)
            or (isinstance(t, list) and "null" in t)
            or any(sub.get("type") == "null" for sub in v.get("anyOf", []))
            or any(sub.get("type") == "null" for sub in v.get("oneOf", []))
        )
        if is_nullable:
            result[k] = None
        elif t == "boolean":
            result[k] = False
        elif t == "string":
            result[k] = ""
        elif t in ("integer", "number"):
            result[k] = 0
        elif t == "array":
            result[k] = []
        elif t == "object":
            result[k] = {}
        else:
            result[k] = None
    return result


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
        stagehand = await Stagehand.create(
            browser=browser,
            model=hatz_llm_generate,
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
                        write_succeeded([name])
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
    finished_names = get_finished_names()
    for org in queries.keys():
        for guy in queries[org]:
            name = guy[0]
            if name in finished_names:
                print(f"Skipping: {name}, since it's already been done.")
                continue
            query = guy[1]
            cache_url = check_cache(name)
            if cache_url:
                print(f"HIT cache for: {name}")
                chosen_urls[cache_url] = name
            else:
                print(f"MISS cache for: {name}")

                chosen_url = None
                if chosen_url:
                    chosen_urls[chosen_url] = name
                    cache_result(chosen_url, name)
                else:
                    chosen_urls[str(uuid.uuid4())] = name

    connected = asyncio.run(browser_time(chosen_urls)) # returns list of urls
    print(f"Succeeded on: {connected}")
    if not connected:
        print("No further connections completed on this run.")
    for url in connected:
        if url in chosen_urls:
            print(f"Removing {url} from the search")
            chosen_urls.pop(url)
    print(connected)

if __name__ == "__main__":
    main()