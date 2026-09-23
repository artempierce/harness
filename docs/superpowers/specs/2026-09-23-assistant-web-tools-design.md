# Web search & fetch — design

**Status:** approved by Sol.

**Roadmap position:** not part of the original A–F roadmap in `docs/PLAN.md`
— requested directly by Sol, scoped via `superpowers:brainstorming` on
2026-09-23. Ticketed as GitHub issue #37. Unblocked — nothing gates it.

## 1. Problem

The assistant persona can read and list files inside the project, and
nothing else. It has no way to look anything up outside the repo. This adds
two tools — `search_web(query)` and `fetch_url(url)` — so it can research a
question or read a specific page, the same way it already reads a file.

## 2. Scope

**In scope:**
- `ninja/web.py`: `search_web(query: str, http: httpx.Client) -> str` and
  `fetch_url(url: str, http: httpx.Client) -> str`.
- Two new entries in `ninja/tools.py`'s `SCHEMAS`, two new dispatch branches
  in `run()`, and an `http: httpx.Client | None = None` parameter on `run()`
  (mirroring the existing `spawn` parameter's injection pattern for
  `delegate`).
- `assistant`'s `tools:` list gains `search_web` and `fetch_url`, and its
  `PERSONA.md` gains a short instruction about treating fetched/searched
  content as data, not instructions.
- New dependencies: `httpx`, `beautifulsoup4`. New env var:
  `TAVILY_API_KEY` in `.env.example`.
- Tests, fully offline, via `httpx.MockTransport` — no real network, no API
  key needed to run the suite.

**Out of scope, explicitly:**
- **A confirmation gate before every call.** `ARCHITECTURE.md` states a
  principle ("reach outside — network... asks, every time, naming what it
  will touch") that nothing in the codebase implements yet — building it
  for real is a separate, larger piece of work. Ruled during brainstorming:
  these tools run automatically, like `read_file` does today — traced,
  visible in the dashboard, not interrupted. Revisit if that proves too
  permissive in practice.
- **A per-turn cost ceiling on network calls.** No existing tool has one
  either (not `read_file`, not `delegate`'s per-call cost). YAGNI until a
  concrete cost problem shows up.
- **Anthropic's server-side `web_search`/`web_fetch` tools.** Considered and
  rejected during brainstorming: execution would move outside `tools.py`'s
  own dispatcher, breaking the stated principle in that file ("the model
  never executes anything itself. It asks, the harness decides") and losing
  offline testability.
- **A dedicated `researcher` persona.** `ARCHITECTURE.md` originally sketched
  `search_web` for a separate persona; Sol chose to add both tools to the
  existing `assistant` persona instead. A split can happen later if
  `assistant`'s tool list grows too broad.
- **JavaScript-rendered pages.** `fetch_url` reads static HTML only.
- **Caching.** Every call is a fresh request.
- **Following redirects automatically.** See §3.2 — a redirect is reported,
  not followed, because auto-following is the standard SSRF-via-redirect
  bypass and re-validating every hop is more machinery than a v1 needs.

## 3. Design

### 3.1 `search_web(query, http) -> str`

Calls Tavily's search API (`POST https://api.tavily.com/search`) with the
query and `TAVILY_API_KEY`. Returns the top 5 results, one per line, as
`title — url — snippet`. Capped at 5 to keep the token cost of a search
bounded regardless of how many results Tavily returns.

If `TAVILY_API_KEY` isn't set, raises a `ValueError` with a message the
persona can relay directly ("web search isn't configured — set
TAVILY_API_KEY"), the same shape `delegate` uses for "delegate needs a
runtime to spawn into."

### 3.2 `fetch_url(url, http) -> str`

Four checks before any request is made, then one request:

1. **Scheme guard:** the URL's scheme must be `http` or `https`. Anything
   else (`file://`, `ftp://`, `data:`, …) is refused before any network
   call — the same shape as `tools.py`'s existing path-boundary check,
   applied to the network instead of the filesystem.
2. **SSRF guard:** the host is resolved and checked against private,
   loopback, and link-local ranges: `127.0.0.0/8`, `10.0.0.0/8`,
   `172.16.0.0/12`, `192.168.0.0/16`, `169.254.0.0/16` (which covers the
   cloud metadata address `169.254.169.254`), and `::1`. A hostname that
   resolves to any of these is refused with the same message shape as the
   scheme guard.
3. **Request, redirects not followed** (`follow_redirects=False`). A 3xx
   response returns a short message naming the `Location` header instead of
   walking there — auto-following is the standard way an SSRF guard on the
   *original* URL gets bypassed by a redirect to an internal address.
4. **Content-type check:** only `text/html` and `text/plain` are read;
   anything else (a PDF, an image, a binary) returns a short "not a
   readable page" message rather than being force-decoded.

On success: HTML is reduced to text with BeautifulSoup (tags stripped,
whitespace collapsed — no attempt at boilerplate/ad removal, which is a
"nice to have" for a research tool, not a correctness requirement).
Truncated at 20,000 characters, matching `read_file`'s existing `MAX_READ`
constant and truncation message shape exactly
(`[truncated: N more characters]`).

The returned string is wrapped:

```
<fetched-content source="{url}">
{text}
</fetched-content>
```

`search_web`'s results are wrapped the same way, with `source="tavily"`.
This mirrors E1a's `<output>...</output>` delimiter around judged text —
the reasoning is identical: this is the least trustworthy text the system
will ever see, and it must be visibly marked as data. `assistant`'s
`PERSONA.md` gains one sentence saying so, the conversational-persona
equivalent of the judge's own instruction.

### 3.3 Where it lives

`ninja/web.py`, not `tools.py` — same split as `skills.py`/`semantic.py`:
`tools.py` stays the dispatcher (schema + allowlist + argument-type
checks), the actual work lives in its own file. `tools.run()` gains one new
parameter:

```python
def run(
    name: str,
    args: dict,
    allowed: Sequence[str],
    *,
    persona: str | None = None,
    spawn: Callable[[str, str], str] | None = None,
    http: httpx.Client | None = None,
) -> str:
```

`http` defaults to `None`; the two new dispatch branches construct a real
`httpx.Client()` when it's `None`, matching how `delegate` already handles
`spawn`. Tests always pass an explicit client built on
`httpx.MockTransport`, so the default path is exercised only by `assistant`
running for real, never by the suite.

### 3.4 The memory risk `ARCHITECTURE.md` names

`ARCHITECTURE.md` (§ description of `search_web`) flags: "if web results
ever reach `episodes`, get consolidated into `facts`, and land in working
memory, that is a path from a web page to routing behaviour." Checked
during brainstorming against `ninja/episodic.py`'s actual behavior: episodic
memory stores only what was *said* — "the tool calls stay in `traces`."
A tool's raw return value, including everything `fetch_url`/`search_web`
return, never enters `chat_log` directly. It can only reach memory if the
assistant chooses to quote it in a reply — the same existing shape as
reading a file today, not a new risk this feature introduces. No additional
work needed for v1; recorded here because it was a named concern, not
because anything had to change to address it.

### 3.5 Trace/dashboard rendering

No new trace event kind. Every tool call already goes through the same
generic `trace.tool(name, args, ok, result, ms, depth)` event regardless of
which tool — `search_web`/`fetch_url` get `ninja trace`/cockpit visibility
for free, the same as every existing tool. Checked directly in
`ninja/agent.py` rather than assumed, given this project's history with the
D1-class "new event kind, unrendered" bug.

## 4. Testing

`tests/test_web.py` (new, mirrors `tests/test_tools.py`'s security-test
framing — these must not be relaxed to make a feature work):
- The SSRF guard refuses each private/loopback/link-local range, including
  the cloud metadata address specifically.
- The scheme guard refuses `file://`, `ftp://`, `data:`.
- A redirect response is reported, not followed (mock a 3xx, assert the
  mock transport records exactly one request).
- A non-html/text content type is refused without being decoded.
- Truncation at 20,000 characters, with the exact message shape.
- The `<fetched-content source="...">`/`</fetched-content>` wrapper is
  present in both `fetch_url` and `search_web`'s output.
- `search_web` raises when `TAVILY_API_KEY` is unset.
- `search_web` returns at most 5 results even when the stubbed response
  contains more.

`tests/test_tools.py` (extended, not a new file): the two new schema
entries are well-formed (existing `test_schemas_are_well_formed` already
covers this generically); allowlist enforcement works for both new tools
using the same pattern as every other tool
(`test_a_tool_outside_the_allowlist_is_refused`-style).

All of the above run via `httpx.MockTransport` — no real network, no
`TAVILY_API_KEY` needed to run the suite, matching `docs/TESTING.md`'s
money rules (only `tests/test_live.py` spends real money).

## 5. Decision log

- **Both tools, not just one** (§2): search alone can't read a specific
  page in full; fetch alone needs a URL from somewhere. Together they're
  the two-step research pattern (`ARCHITECTURE.md`'s own framing for
  `search_web` implied this eventually anyway).
- **Client-side dispatcher over Anthropic's server-side tools** (§2): ruled
  during brainstorming against `tools.py`'s own stated principle — "the
  model never executes anything itself. It asks, the harness decides" — a
  server-side tool can't be offline-tested and moves execution outside the
  harness's own control.
- **On `assistant`, not a new `researcher` persona** (§2): Sol's call,
  overriding `ARCHITECTURE.md`'s original sketch. Revisit if `assistant`'s
  tool list grows unwieldy.
- **No confirmation gate for v1** (§2): the gate `ARCHITECTURE.md` describes
  doesn't exist in the codebase yet; building it is separate, larger work.
  These tools run automatically like `read_file`, traced but not
  interrupted.
- **Redirects are reported, not followed** (§3.2): the standard SSRF-via-
  redirect bypass is auto-following past the original guard; not following
  is simpler and safer than re-validating every hop.
- **Tavily as the search provider** (§3.1): Sol's choice — a real free tier,
  built for LLM-agent consumption. Isolated behind one function, so
  swapping providers later touches one place.
