# Tool Design Evolution — Embedded Lab Assistant (IT4210)

> Maps to **SCORING.md → Group Base → "Tool Design Evolution" (4 pts)**.
> Documents how the agent's tool specs progressed from a naive v1 to the
> hardened v2 actually shipped in `src/tools/` + `src/knowledge/loader.py`.

## 0. Design principle (why these tools exist)

The student need is a 3-step funnel: **mục đích lab → chuẩn bị lab → hướng dẫn
bài tập**. Tools are therefore *task-shaped*, not API-shaped. Every tool takes a
single `args: str` (exactly what the regex `Action: tool(args)` captures) and
returns a plain-text Observation the LLM can quote. This keeps the tool contract
trivial for weak local models (Phi-3) that struggle to emit valid JSON.

## 1. Tool inventory (v2, shipped)

| Tool | Args | Returns | Source |
| :--- | :--- | :--- | :--- |
| `get_lab_objective` | lab id `1/2/3` | mục đích lab | `lab_tools.py:11` |
| `get_lab_preparation` | lab id `1/2/3` | phần cứng / phần mềm / tài liệu | `lab_tools.py:20` |
| `get_exercise_guide` | `id [topic]` | các phần hướng dẫn + bài tập, lọc theo chủ đề | `lab_tools.py:37` |
| `search_lab_docs` | từ khóa | full-text, diacritics-insensitive | `lab_tools.py:63` |
| `lookup_pin_mapping` | id hoặc tên linh kiện | sơ đồ chân ghép nối | `lab_tools.py:74` |
| `web_search` | query | DuckDuckGo (datasheet, chuẩn giao tiếp) | `web_tools.py:14` |
| `fetch_url` | url | nội dung text rút gọn của 1 URL | `web_tools.py:43` |

## 2. Spec progression: v1 → v2

| # | Dimension | v1 (naive) | v2 (shipped) | Failure that forced the change | Code |
| :- | :-- | :-- | :-- | :-- | :-- |
| 1 | **Arg format** | expected clean JSON `{"lab": 2}` | single bare string, parsed + stripped of `'"().:` | weak models emitted `get_lab_objective: 2`, JSON parse failed | `agent.py:183` |
| 2 | **Empty/garbage args** | passed through → empty query → empty Observation | recover arg from the user question (lab digit, else full question) | `Action: search_lab_docs()` returned nothing → agent looped | `agent.py:188` |
| 3 | **Search matching** | exact substring, case-sensitive | lowercased + Vietnamese diacritics stripped (`đ→d`, NFKD) | "ngat" didn't match "ngắt"; "led" missed "LED" | `loader.py:22` |
| 4 | **Lab/topic granularity** | one giant `get_lab(id)` dump | `get_exercise_guide(id, topic)` filters sections by keyword | full-lab dumps blew up token count + context | `lab_tools.py:42-56` |
| 5 | **Pin lookup** | only by lab id | also by component name (`rc522`, `hs0038`, `ds1307`) across all labs | students asked "chân RC522?" without a lab number | `lab_tools.py:86-92` |
| 6 | **Unknown input** | raised / empty string | friendly message listing valid options (`1, 2, 3`, component hints) | dead-end Observations stalled the loop | `lab_tools.py:15,48,94` |
| 7 | **Tool crash isolation** | exception bubbled up, killed the loop | `_execute_tool` wraps each call in try/except → error Observation | one bad tool call crashed the whole agent run | `agent.py:241-245` |
| 8 | **Reach beyond local KB** | none (local JSON only) | `web_search` / `fetch_url` with graceful offline degradation | datasheet/HAL questions had no grounded source | `web_tools.py` |
| 9 | **Knowledge I/O cost** | re-read JSON every call | module-level cache (`_CACHE`), lazy load once | repeated disk reads per tool call | `loader.py:29-35` |

## 3. Knowledge-base spec (the "doc" the tools read)

Tools never parse PDFs at runtime. The 3 lab PDFs in `docs/` were extracted
**once** into a typed JSON contract `data/embedded_labs.json`:

```
labs.<id> = {
  title, objective[], preparation{hardware[], software[], documents[]},
  sections[{code, title, guide}], exercises[], pin_mappings{component: pins}
}
```

This separation (offline extraction → stable schema → cheap lookup) is what lets
every tool be a thin, deterministic function — the v2 lesson: **put the messy
parsing offline, keep the runtime tool contract boring.**

## 4. What we'd do in v3 (future tool work)

- **Hybrid retrieval**: add embedding search alongside keyword `search_lab_docs`
  for paraphrased questions (keyword search misses synonyms).
- **Typed arg schema** with auto-repair, so stronger models can pass structured
  args while weak models still get the lenient path.
- **Per-tool telemetry**: log tool latency + hit/miss to find dead tools.
