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
import openai
import json
import requests as rq
import re

from typing import List, Optional

class HatzAdapter:
    def __init__(self, hatz_url, hatz_api_key, hatz_model):
        self.hatz_base_url = hatz_url.removesuffix("/chat/completions")
        self.hatz_url = hatz_url
        self.hatz_api_key = hatz_api_key
        self.hatz_model = hatz_model

        self._hatz_client = openai.AsyncOpenAI(
            base_url=self.hatz_base_url,
            api_key=hatz_api_key,
            default_headers={"X-API-Key": hatz_api_key},
        )

    async def hatz_llm_generate(self, params):
        """LLMGenerateCallback: handles both structured and message generate."""
        is_structured = isinstance(params, LLMStructuredGenerateParams)

        openai_messages = []
        if params.system_prompt:
            openai_messages.append({"role": "system", "content": params.system_prompt})
        openai_messages.extend(self._stagehand_messages_to_openai(params.messages))

        kwargs = {
            "model": self.hatz_model,
            "messages": openai_messages,
        }
        if params.temperature is not None:
            kwargs["temperature"] = params.temperature
        if params.stop_sequences:
            kwargs["stop"] = params.stop_sequences

        if is_structured and params.response_format:
            rf = params.response_format
            schema_dict = self._field_schema_to_dict(rf.schema_) if rf.schema_ else {}
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
            response = await self._hatz_client.chat.completions.create(**kwargs)
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
            schema_dict = self._field_schema_to_dict(rf.schema_) if (rf and rf.schema_) else {}
            structured = self._parse_json_from_llm(text_content)
            fallback = self._build_schema_fallback(schema_dict)

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


    def _field_schema_to_dict(self, val):
        """Recursively convert FieldSchema RootModels to plain dicts."""
        if hasattr(val, "root"):
            val = val.root
        if isinstance(val, dict):
            return {k: self._field_schema_to_dict(v) for k, v in val.items()}
        if isinstance(val, list):
            return [self._field_schema_to_dict(v) for v in val]
        return val


    def _parse_json_from_llm(self, text: str):
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


    def _build_schema_fallback(self, schema_dict: dict) -> dict:
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

    def _stagehand_messages_to_openai(self, messages):
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

    def chud_ai(self, search_results: List, person_name: str = "", org_name: str = "") -> Optional[str]:
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
            "X-API-Key": self.hatz_api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.hatz_model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_message},
            ],
        }

        try:
            response = rq.post(self.hatz_url, json=payload, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()
            ai_answer = data["choices"][0]["message"]["content"].strip()
            print(f"AI picked: {ai_answer}")
            return None if ai_answer.lower() == "none" else ai_answer
        except Exception as e:
            print(f"Hatz AI error: {e}")
            return None

        # Turns stagehand openai calls into hatz endpoint stuff ... doozey if i say so myself
