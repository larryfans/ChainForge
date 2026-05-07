"""
LLM client wrapper.

Provides a unified interface to OpenAI-compatible APIs with retry,
token counting, and call logging for the paper's efficiency metrics.
"""

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMCallRecord:
    timestamp: float
    model: str
    phase: str          # "eval_tcsc", "eval_de", "refine", etc.
    tool_id: str | None
    prompt_tokens: int
    completion_tokens: int
    duration: float
    success: bool


class LLMClient:
    """Thin wrapper around OpenAI-compatible chat completions."""

    def __init__(self, api_key: str, base_url: str | None, model: str):
        import openai
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = openai.OpenAI(**kwargs)
        self.model = model
        self.call_log: list[LLMCallRecord] = []

    @property
    def total_calls(self) -> int:
        return len(self.call_log)

    def chat(
        self,
        system: str,
        user: str,
        temperature: float = 0.3,
        phase: str = "",
        tool_id: str | None = None,
        max_retries: int = 3,
    ) -> str:
        """
        Send a chat completion request and return the assistant's text.

        Args:
            system: System prompt
            user: User prompt
            temperature: Sampling temperature
            phase: Label for call logging (e.g. "eval_tcsc", "refine_stealth")
            tool_id: Which tool this call is about (for logging)
            max_retries: Retry on transient failures

        Returns:
            Assistant response text
        """
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})

        for attempt in range(max_retries):
            t0 = time.time()
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                )
                dt = time.time() - t0
                text = resp.choices[0].message.content or ""
                usage = resp.usage

                self.call_log.append(LLMCallRecord(
                    timestamp=t0,
                    model=self.model,
                    phase=phase,
                    tool_id=tool_id,
                    prompt_tokens=usage.prompt_tokens if usage else 0,
                    completion_tokens=usage.completion_tokens if usage else 0,
                    duration=dt,
                    success=True,
                ))
                return text

            except Exception as e:
                dt = time.time() - t0
                self.call_log.append(LLMCallRecord(
                    timestamp=t0, model=self.model, phase=phase,
                    tool_id=tool_id, prompt_tokens=0, completion_tokens=0,
                    duration=dt, success=False,
                ))
                if attempt == max_retries - 1:
                    raise
                time.sleep(2 ** attempt)

        return ""  # unreachable

    def extract_code(self, text: str) -> str:
        """Extract python code block from LLM response."""
        m = re.search(r"```python\s*\n(.*?)```", text, re.DOTALL)
        if m:
            return m.group(1).strip()
        # fallback: if response looks like pure code
        if text.strip().startswith("def "):
            return text.strip()
        return text.strip()

    def extract_multiple_codes(self, text: str) -> list[dict[str, str]]:
        """
        Extract multiple Python code blocks from LLM response.

        Batch optimization returns N code blocks in one response.
        This method parses them and returns structured data.

        Returns:
            List of dicts with keys:
            - 'tool_id': extracted from comment (e.g., '# T1: tool_name')
            - 'tool_name': function name from code
            - 'code': the actual Python code
        """
        # Pattern to match code blocks with optional tool ID comment
        block_pattern = re.compile(
            r'```python\s*\n'           # Opening ```python
            r'(?:\s*#\s*(T\d+):\s*(\w+))?\s*\n'  # Optional '# T1: name'
            r'(.*?)'                    # Code content
            r'```',                     # Closing ```
            re.DOTALL
        )

        results = []
        for match in block_pattern.finditer(text):
            tool_id = match.group(1)
            tool_name = match.group(2)
            code = match.group(3).strip()

            # Extract function name from code if not in comment
            if not tool_name:
                func_match = re.search(r'def\s+(\w+)\s*\(', code)
                if func_match:
                    tool_name = func_match.group(1)

            results.append({
                "tool_id": tool_id,
                "tool_name": tool_name,
                "code": code,
            })

        # Fallback: if no code blocks found with pattern, try simple match
        if not results:
            simple_pattern = re.compile(r'```python\s*\n(.*?)```', re.DOTALL)
            for match in simple_pattern.finditer(text):
                code = match.group(1).strip()
                func_match = re.search(r'def\s+(\w+)\s*\(', code)
                tool_name = func_match.group(1) if func_match else None

                results.append({
                    "tool_id": None,
                    "tool_name": tool_name,
                    "code": code,
                })

        return results

    def extract_codes_with_mapping(
        self,
        text: str,
        expected_tool_ids: list[str]
    ) -> dict[str, str]:
        """
        Extract code blocks and map them to expected tool IDs.

        Args:
            text: LLM response
            expected_tool_ids: List of tool IDs we expect (e.g., ['T1', 'T3', 'T5'])

        Returns:
            Dict mapping tool_id -> code
        """
        extracted = self.extract_multiple_codes(text)

        if len(extracted) != len(expected_tool_ids):
            # Warning will be printed by caller
            pass

        result = {}

        # Try to match by tool_id first
        for item in extracted:
            if item["tool_id"] and item["tool_id"] in expected_tool_ids:
                result[item["tool_id"]] = item["code"]

        if len(result) == len(expected_tool_ids):
            return result

        # Fallback: match by order
        for i, item in enumerate(extracted):
            if i < len(expected_tool_ids):
                result[expected_tool_ids[i]] = item["code"]

        return result

    def extract_json(self, text: str) -> Any:
        """Extract JSON from LLM response."""
        text = text.strip()
        text = re.sub(r"^```json\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        return json.loads(text)

    def extract_float(self, text: str) -> float:
        """Extract a float score from LLM response."""
        text = text.strip()
        try:
            return float(text)
        except ValueError:
            numbers = re.findall(r"(\d+\.\d+)", text)
            return float(numbers[0]) if numbers else 0.5

    def summary(self) -> dict:
        """Return call statistics."""
        total = len(self.call_log)
        ok = sum(1 for r in self.call_log if r.success)
        pt = sum(r.prompt_tokens for r in self.call_log)
        ct = sum(r.completion_tokens for r in self.call_log)
        dur = sum(r.duration for r in self.call_log)
        by_phase: dict[str, int] = {}
        for r in self.call_log:
            by_phase[r.phase] = by_phase.get(r.phase, 0) + 1
        return {
            "total_calls": total,
            "successful": ok,
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "total_duration": round(dur, 1),
            "by_phase": by_phase,
        }
