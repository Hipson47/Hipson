# Testing Strategies for AI‑Written Code in AI‑First Development

## Why AI‑written tests fail in production

AI‑first development shifts the bottleneck from “writing code” to “verifying code.” Recent industry surveys show a large “verification gap”: most developers report they do not fully trust AI‑generated code to be functionally correct, yet only about half consistently verify it before committing. citeturn21search0turn21search2turn21search6 That mismatch is exactly where your core anti‑pattern appears: an agent writes implementation, then writes *tests that confirm the implementation*, not the requirement, producing green CI and broken production. citeturn13view0turn21search0turn6search4

Two structural forces make this worse in AI‑first stacks:

In long‑running “agent loops,” the model optimizes for “tests pass” rather than “risk reduced.” Agent tools explicitly advertise iterating until tests pass; without guardrails, that can produce self‑confirming suites (high coverage, low fault detection). citeturn19search0turn19search8turn20search0

Empirically, coverage is not a reliable proxy for bug‑finding. Research on “pseudo‑tested” code demonstrates that it is common for code to be executed by tests while its effects are not asserted—so the implementation can be removed or mutated with no test failures. citeturn10search8turn20search11turn10search27

The practical implication is blunt: if AI writes most code, you must treat tests like a security boundary—designed to resist *collusion* between implementation and test generation. citeturn13view0turn2search39turn20search23

A clear, opinionated definition of “good tests for AI‑written code” in this environment:

They are derived from *acceptance criteria/specs*, not from reading the implementation. citeturn6search15turn6search7turn12view0  
They fail on plausible broken implementations (not just on syntax errors). citeturn2search39turn2search2turn20search23  
They use the highest‑fidelity boundary that is still fast enough to run often (more integration/E2E than classic unit pyramids, because unit tests are easiest for AI to fake). citeturn1search17turn9search16turn1search12  
Their effectiveness is measured with mutation testing (and/or oracle‑gap techniques), not just line coverage. citeturn2search39turn2search2turn10search27

This document operationalizes that into concrete patterns, prompts, and CI gates for your stack: Next.js (App Router) + React + TypeScript, FastAPI + PostgreSQL + Redis, GitHub Actions, and AI coding agents (Cursor Agent, Claude Code CLI). citeturn9search16turn1search2turn19search1turn19search0

## AI testing anti‑patterns with bad and good examples

This section names the failure modes you are trying to prevent. Many are well‑known in traditional testing, but AI agents amplify them by producing “plausible” tests at scale and updating tests during fixes unless explicitly constrained. citeturn13view0turn20search0turn21search0

### Tautological tests

**What it is**  
A test that re‑implements the same logic as the system under test (SUT), so it passes even when the requirement is wrong. These tests “freeze the implementation” and mostly detect refactors, not regressions. citeturn10search1turn10search9

**BAD (TypeScript)** — a discount function is tested by re‑doing the same math

```ts
// discount.ts
export function discountTotal(subtotal: number, pct: number) {
  return subtotal - subtotal * (pct / 100)
}

// discount.test.ts (BAD)
import { discountTotal } from "./discount"

test("applies discount", () => {
  const subtotal = 200
  const pct = 10
  expect(discountTotal(subtotal, pct)).toBe(subtotal - subtotal * (pct / 100))
})
```

**Why this fails in AI‑first work**  
An agent can “invent” both function and test, and both will agree—even if the business rule is “round to cents” or “max discount = $50.” citeturn10search9turn20search23

**GOOD (TypeScript)** — assert a requirement, not a formula  
Example requirement: “Discount is capped at $50 and result is rounded to cents.”

```ts
// discount.test.ts (GOOD)
import { discountTotal } from "./discount"

test("caps discount at $50", () => {
  expect(discountTotal(1000, 10)).toBe(950) // 10% would be $100, cap => $50
})

test("rounds to cents", () => {
  expect(discountTotal(0.03, 10)).toBe(0.03) // $0.003 discount => rounds away
})
```

To make this AI‑proof, the cap and rounding rules must come from acceptance criteria/specs that are written before the implementation exists. citeturn6search7turn6search15turn13view0

### Mock theater

**What it is**  
Over‑mocking so the test proves your mocks work, not your system. Excessive mocking is a known source of false confidence because the stub can misrepresent the collaborator contract. citeturn10search2turn10search10turn1search14

**BAD (FastAPI/Python)** — endpoint test mocks out the entire service and database

```py
# test_users_bad.py
def test_create_user(client, mocker):
    svc = mocker.patch("app.routes.users.user_service")
    svc.create_user.return_value = {"id": "123", "email": "a@b.com"}
    r = client.post("/users", json={"email": "a@b.com"})
    assert r.status_code == 200
```

**Why this fails**  
It does not verify request validation, serialization, DB constraints, uniqueness, transaction behavior, or real error handling. With AI, this pattern is common because it is easy to generate and always green. citeturn1search2turn4search9turn10search2

**GOOD (FastAPI/Python)** — integration test hits real app + real Postgres container  
Use dependency overrides only to redirect to a *test database*, not to replace core logic. FastAPI explicitly supports dependency overrides for testing. citeturn7search1turn4search2

```py
import pytest
from httpx import AsyncClient

@pytest.mark.anyio
async def test_create_user_persists(async_client: AsyncClient):
    r = await async_client.post("/users", json={"email": "a@b.com"})
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "a@b.com"

    r2 = await async_client.get(f"/users/{body['id']}")
    assert r2.status_code == 200
    assert r2.json()["email"] == "a@b.com"
```

“Mock theater” is also why MSW (network‑level mocking) is preferred over mocking fetch directly: it preserves the HTTP boundary while controlling responses. citeturn7search12turn7search4turn1search14

### Assertion‑free tests

**What it is**  
Tests that execute code but do not meaningfully assert outcomes (“it runs” is not a requirement). Mutation testing literature describes this as an “oracle gap” risk: code is covered but not validated. citeturn20search23turn10search27turn2search39

**BAD (TypeScript)**

```ts
test("renders page", async () => {
  render(<SignupPage />)
  await new Promise((r) => setTimeout(r, 10))
  // no assertions
})
```

**GOOD (TypeScript)** — assert user‑visible outcomes

```ts
test("shows error when email is invalid", async () => {
  render(<SignupForm />)
  await userEvent.type(screen.getByLabelText(/email/i), "not-an-email")
  await userEvent.click(screen.getByRole("button", { name: /sign up/i }))
  expect(await screen.findByText(/invalid email/i)).toBeVisible()
})
```

This aligns with Testing Library’s explicit goal: avoid implementation details and test what users see and do. citeturn1search17turn1search3turn1search4

### Happy path tunnel vision

**What it is**  
Only testing success. AI tends to generate “golden path” tests unless instructed otherwise. This is one of the most common real‑world sources of production bugs because failures (timeouts, 500s, malformed data) are normal in distributed systems. citeturn3search2turn18search0turn9search21

**BAD (FastAPI/Python)**

```py
def test_login_ok(client):
    r = client.post("/login", json={"email": "a@b.com", "password": "pw"})
    assert r.status_code == 200
```

**GOOD (FastAPI/Python)** — minimum failure set you should require for every endpoint

```py
def test_login_rejects_wrong_password(client):
    r = client.post("/login", json={"email": "a@b.com", "password": "wrong"})
    assert r.status_code == 401

def test_login_rejects_missing_fields(client):
    r = client.post("/login", json={"email": "a@b.com"})
    assert r.status_code == 422
```

FastAPI’s validation behavior (422 on invalid request bodies) is part of the actual contract, so tests must assert it. citeturn1search2turn18search0

### Snapshot Stockholm syndrome

**What it is**  
Snapshot tests that are updated reflexively when they fail, becoming “approval tests of whatever exists today,” not tests of intended behavior. This is a widely documented risk: snapshots often fail to convey intent and are overused. citeturn10search3turn10search7

**BAD (React)**

```ts
test("profile matches snapshot", () => {
  const { container } = render(<Profile user={user} />)
  expect(container).toMatchSnapshot()
})
```

**GOOD (React)** — assert specific, meaningful properties; use snapshots only for stable, high‑signal output  
Keep snapshots small and intentional (e.g., email templates), and still pair with assertions for key semantics.

```ts
test("renders user name and role", () => {
  render(<Profile user={{ name: "Ava", role: "admin" }} />)
  expect(screen.getByRole("heading", { name: "Ava" })).toBeVisible()
  expect(screen.getByText(/admin/i)).toBeVisible()
})
```

### Coverage theater

**What it is**  
High line/branch coverage with low defect detection. Research on pseudo‑tested methods and oracle gaps shows why: tests can execute code without asserting outcomes, or they only assert weak properties. citeturn10search8turn20search23turn20search15

**BAD (TypeScript)**

```ts
expect(result).toBeTruthy()
```

**GOOD** — require “semantic assertions”  
If the requirement says “returns 409 on duplicate email,” you assert `409`, error code, and DB state, not “truthy.” This is exactly why mutation testing matters: it can reveal pseudo‑tested code even with high coverage. citeturn2search39turn2search2turn10search27

### Synchronized mutation

**What it is**  
When fixing a bug, the AI changes both production code and the tests in the same loop, hiding regressions. Thoughtworks explicitly warns that as agents generate larger change sets, review gets harder and established practices (TDD, static analysis) must be embedded into workflows. citeturn13view0turn19search27

**BAD workflow**  
Agent: “Fix failing test” → edits production code *and* edits the assertion to match new behavior.

**GOOD workflow gate**  
Require: “test change must be an isolated commit with explanation,” and “bug fix must make the existing failing test pass without modifying it.” When tests truly have a bug, treat it like production: reproduce, explain, fix with a targeted diff. citeturn13view0turn2search39turn19search18

### Copy‑paste test factories

**What it is**  
AI creates uniform tests with identical structure, missing scenario‑specific edge cases. This correlates with a deeper problem: tests mirror code structure instead of meaningfully sampling behaviors. citeturn10search25turn1search17

**BAD**  
One test per class/method, all variations shallow.

**GOOD**  
Build tests around behaviors and invariants: state transitions, idempotency, concurrency, and failure recovery. Property‑based testing and Schemathesis can force diversity automatically. citeturn3search2turn3search12turn3search9

### Loose assertion epidemic

**What it is**  
Assertions that are too vague (truthy, defined, instanceOf) so many bugs slip through.

**BAD**

```ts
expect(user).toBeDefined()
```

**GOOD**

```ts
expect(user).toEqual({ id: "u_123", email: "a@b.com", role: "user" })
```

This is not pedantry: mutation/oracle‑gap research shows that weak oracles are the root of pseudo‑tested code. citeturn20search23turn10search8

### Test isolation failure

**What it is**  
Tests pass individually but fail in suite due to shared state—global dependency overrides, shared DB data, leaked mocks, cached singletons. This is especially common in FastAPI dependency overrides if overrides are not cleared between tests. citeturn7search1turn7search5turn18search7

**BAD (FastAPI)** — override globally, never reset

```py
app.dependency_overrides[get_db] = lambda: fake_db
# ... tests run ...
```

**GOOD** — apply override in a fixture and reset after  
Pytest’s fixture patterns and teardown via `yield` exist exactly for this kind of isolation problem. citeturn18search19turn18search27turn18search11

```py
import pytest

@pytest.fixture
def override_db():
    app.dependency_overrides[get_db] = get_test_db
    yield
    app.dependency_overrides.clear()
```

## TDD‑AI protocol that prevents self‑confirming tests

This is the core “battle‑tested” protocol: it is a workflow, not a tool choice. It is designed to exploit what agents are good at (speed, breadth) without letting them define correctness. citeturn13view0turn6search15turn19search18

### Phase framing: specs first, code last

Thoughtworks calls out spec‑driven development as an emerging technique for AI‑assisted workflows: start with a structured functional specification, then break down into tasks, then implement. citeturn6search15turn6search7turn6search22 This maps cleanly onto TDD done correctly: tests become “executable specs,” but only if the spec is written independently of the implementation. citeturn12view1turn13view0turn19search18

The non‑negotiable rule in AI‑first development:

**Humans define what “correct” means before any agent writes production code.** citeturn13view0turn6search2turn6search4

The rationale is supported by evidence that confidence in AI outputs can reduce critical thinking effort; pushing spec definition up front counteracts that behavioral drift. citeturn6search2turn6search10turn13view0

### Phase 1: human acceptance criteria

Write acceptance criteria that are testable and *observable* at product boundaries (UI, HTTP, DB state). Avoid internal design details. This aligns with the general guidance to test behavior rather than implementation. citeturn1search17turn1search4turn1search3

A template that consistently produces good tests:

**Feature**: one sentence.  
**Primary user goal**: what the user can do.  
**Preconditions**: auth state, existing data.  
**Happy path example**: request/interaction → expected response/UI → stored state.  
**Failure examples** (minimum set): validation error, auth error, conflict, server error/timeout.  
**Edge/boundary examples**: empty, max length, unicode, timezone, double‑submit.  
**Non‑functional** (if relevant): performance budget, accessibility, audit logging.

In APIs, include security acceptance criteria aligned to common OWASP API risks (authorization, authentication, rate limiting, injections). citeturn18search0turn18search1turn18search13

### Phase 2: AI writes tests first from criteria, not code

This is where you use the agent aggressively—but with constraints.

The most effective technique is to anchor the agent with an instruction file and a fixed protocol. Both Thoughtworks and modern agent tools emphasize agent instruction files (AGENTS.md / rules) to “engineer context.” citeturn12view0turn19search3turn19search10

Cursor explicitly documents using rules and notes AGENTS.md as a straightforward alternative to its own rules format. citeturn19search3turn19search18

Claude Code exposes a CLI with workflows and CI integration; without constraints, it can auto‑iterate until tests pass, re‑introducing synchronized mutation risk. citeturn19search1turn19search27turn13view0

**A hard gate you should enforce for Phase 2:**  
If the generated tests pass immediately on an empty or stub implementation, assume they are tautological or assertion‑weak.

Practical technique: create a deliberately wrong stub (return constant, skip validation) and require the suite to fail. This matches the logic of mutation testing: tests should fail on small injected faults. citeturn2search39turn2search2turn10search27

### Phase 3: AI writes implementation without modifying tests

Make this a mechanical rule:

**Implementation PR cannot include test edits**, unless the PR is explicitly labeled “test bug fix” and includes a written explanation and reproduction. This is how you prevent synchronized mutation. citeturn13view0turn19search27turn2search39

### Phase 4: human reviews test quality with a red‑flag checklist

A review checklist that works specifically against AI anti‑patterns:

Do any tests compute expected values using the same logic as the implementation (tautology)? citeturn10search9turn10search1  
Are mocks replacing internal logic or the database (mock theater)? citeturn10search2turn4search9  
Are there real assertions for user‑visible output, DB state, or API contract (oracle strength)? citeturn20search23turn10search27  
Is there at least one failure path and one boundary case per feature? citeturn18search0turn3search2  
Would a trivial bug (wrong comparison, off‑by‑one, missing auth) be caught? (If unsure, run mutation tests.) citeturn2search39turn2search1

### Phase 5: mutation testing as the effectiveness metric

Mutation testing is widely described as a criterion for assessing test efficacy by injecting small faults and seeing if tests detect them. It is also explicitly positioned as stronger than mere coverage because it measures whether tests actually validate behavior. citeturn2search39turn2search2turn2search1

This matters more in AI‑first development because pseudo‑tested/oracle‑gap code is exactly what AI tends to create when optimizing for green tests. citeturn10search8turn20search23turn13view0

### AI agent prompt templates that actually work

The key prompt engineering rule is: never ask “write tests for this code.” That invites mirroring. Instead, ask for tests from specs and require negative testing, mutation resistance, and constraint on mocking. citeturn19search18turn6search15turn10search9

**Test‑first generation prompt**

```text
You are writing tests FIRST from acceptance criteria. Do NOT read or infer the current implementation.
Rules:
- Treat this as a spec; assert observable outcomes (UI text, HTTP status/body, DB state).
- Include: happy path + validation error + auth error + conflict + one boundary.
- Avoid: snapshot-only testing, toBeTruthy/defined, asserting internal state, mocking internal modules.
- Use real boundaries: MSW for HTTP in frontend tests; real Postgres in backend tests.
- Tests MUST fail against a deliberately wrong stub implementation (describe why they fail).

Acceptance criteria:
[paste criteria]
Target stack:
- Next.js App Router + React + TypeScript (Vitest + RTL)
- FastAPI + Postgres + pytest + httpx
Output: tests only.
```

This aligns with agent workflow guidance to be explicit about TDD so the agent avoids creating mock implementations and focuses on expected I/O pairs. citeturn19search18turn13view0

**Review prompt for tautologies and mock abuse**

```text
Review these tests as a QA architect for AI anti-patterns:
- tautological assertions
- mock theater / over-mocking
- weak or missing oracles
- happy-path-only
- test isolation issues (shared state, global overrides)
For each issue: quote the exact line(s), explain risk, propose a concrete improvement.
Do NOT change production code.
```

**Mutation‑resistance prompt**

```text
Strengthen these tests to kill common mutants:
- inverted conditionals
- off-by-one boundaries
- removed error handling
- removed auth checks
- wrong HTTP status codes
- removed DB uniqueness constraint handling
Add assertions and negative tests; keep mocks minimal.
```

Mutation testing research and tooling docs make this concrete: you are explicitly targeting the kinds of changes mutation tools introduce. citeturn2search2turn2search39turn2search1

## Frontend patterns for Next.js App Router and React

Your frontend strategy must respect a major constraint of the modern React/Next architecture: async Server Components and streaming make classic unit test tooling incomplete. Next.js guidance explicitly notes that some tools (including Vitest) do not fully support async Server Components and recommends E2E tests for those components. citeturn9search16turn9search2turn5search10

That constraint is one reason the “AI‑proof pyramid” differs from traditional: you bias toward integration/E2E where the system boundary is harder to fake. citeturn9search16turn13view0turn1search12

### Unit testing: Vitest + React Testing Library with behavior‑first rules

Testing Library’s core philosophy is to avoid implementation details and give confidence by testing interactions the way users experience them. Its docs explicitly recommend queries like `getByRole` and `getByLabelText` as top preferences because they map to the accessibility tree and user behavior. citeturn1search17turn1search3turn1search33

A minimal setup is documented in Next.js’s official Vitest guide. citeturn5search10turn9search2

**AI‑proof rules for component tests**

Prefer `getByRole`/`getByLabelText` over `getByTestId`, because “test IDs” are invisible to users and make tests brittle. citeturn1search3turn10search7turn1search17  
Use `user-event` to simulate real input; do not manipulate component state directly. citeturn1search17turn1search4  
Never test internal state; assert rendered output and side effects visible to the user. citeturn1search21turn1search4turn1search17  
For async server‑side behavior, prefer E2E (or integration at the HTTP boundary), per Next.js guidance. citeturn9search2turn9search16

### Integration testing: MSW as the default mocking strategy

MSW is explicitly designed to mock APIs at the network level and be reusable across environments; its docs include setups for Node tests (Vitest) and explain how it patches request modules in Node. citeturn7search12turn7search4turn7search0 Vitest itself recommends MSW for mocking requests. citeturn7search26

This matters because “mock theater” often comes from mocking fetch or internal API clients directly, which forces you to re‑implement backend behavior in test code. citeturn1search14turn7search12

A “good” integration test in this stack validates:

Page renders initial state  
User triggers an action  
A real HTTP request is issued (intercepted by MSW)  
UI updates based on response  
Error path is asserted (500/timeout/malformed)

### Accessibility checks: axe‑core with honest expectations

Deque’s axe‑core is a popular accessibility testing engine intended to integrate into test environments. citeturn5search23turn5search1 However, automated accessibility checks are not complete: Deque’s own materials emphasize automated tests detect only a portion of issues and must be part of a broader process. citeturn5search27

For unit tests, `jest-axe` is a common wrapper approach. citeturn5search1turn5search4

### E2E testing: Playwright as the truth layer

Playwright provides explicit CI documentation and official guidance for retries (useful for flaky test mitigation) and web‑first auto‑retrying assertions (avoid non‑retrying assertions when pages update asynchronously). citeturn1search12turn9search3turn9search21

Next.js maintains a Playwright testing guide updated in 2026, which is directly relevant for your stack. citeturn1search16

**Key Playwright rules (anti‑flakiness + anti‑AI‑fakery)**

Use web‑first, auto‑retrying assertions (`expect(locator).toBeVisible()`), not fixed sleeps; Playwright explicitly warns non‑retrying assertions can cause flakiness. citeturn9search21turn9search3  
Keep tests isolated; use deterministic auth (storageState), not UI logins in every test (unless the login flow itself is the test). This is consistent with common flaky‑test guidance and Playwright’s retry design. citeturn9search3turn9search33  
Treat E2E as the place you verify async server components and streaming behaviors, per Next.js guidance. citeturn9search16turn9search2

### The AI‑proof frontend test pyramid

Because unit tests are the easiest place for AI to generate tautologies (mirroring internal details), you bias toward integration and a small number of critical E2E journeys. This is aligned with the broader “test behavior” philosophy and the practical constraints of async Server Components. citeturn1search17turn9search16turn13view0

A pragmatic ratio for AI‑written codebases:

Many integration tests (page + MSW + real component tree interactions)  
A smaller set of classic unit tests for pure functions and local edge logic  
A mandatory E2E suite for every critical user journey (auth, CRUD, payment, permissions)

The justification is not ideology; it’s the boundary hardness: the closer you test to real browser + real network + real backend, the harder it is for AI to “fake green” by mirroring code. citeturn7search12turn1search12turn9search16

## Backend patterns for FastAPI with PostgreSQL and Redis

FastAPI’s documentation strongly supports testing at the HTTP boundary: its `TestClient` is built on HTTPX and designed to test FastAPI apps directly. citeturn1search2turn1search8 For async tests, FastAPI explains why TestClient magic doesn’t work inside async functions and recommends using HTTPX directly. citeturn1search5turn7search25

### Unit tests: isolate business logic, not correctness boundaries

Use unit tests where they are genuinely high value: pure business rules, deterministic transformations, permission matrices. The risk in AI‑first development is that the agent will mock the world and create “tests of mocks.” citeturn10search2turn13view0

**When mocking is correct**  
External APIs, email/SMS providers, payment gateways, time services. This aligns with traditional guidance: stubs/fakes for slow, nondeterministic external systems. citeturn10search2turn10search10

**When mocking hides real bugs**  
Database behavior, ORM constraints, transaction isolation, migrations. This is especially true if you swap Postgres for SQLite in tests.

### Use real Postgres, not SQLite

SQLite foreign key constraints are disabled by default unless explicitly enabled; this is documented by SQLite itself and by SQLAlchemy’s SQLite dialect docs. citeturn4search4turn4search16

For Postgres‑targeted applications, using SQLite can create false confidence because SQL dialect and concurrency behavior differ; Neon’s guidance explicitly warns that SQLite vs Postgres differences can lead to misleading tests. citeturn4search9turn4search16

Therefore, for integration tests:

**Run Postgres in Docker (or Testcontainers) and test against the real engine.** Testcontainers provides explicit guidance for Postgres in Python. citeturn4search2turn4search26

### DB fixture strategy that doesn’t rot

You need a repeatable, fast way to reset DB state.

Use one of these two patterns:

Transaction rollback per test (with a top‑level transaction and nested savepoints)  
Database recreation/seed per test module for heavier suites

The details vary by ORM, but the principle is test isolation. Pytest fixtures are designed for this and support teardown via `yield`. citeturn18search19turn18search27

### Integration tests: real FastAPI endpoints with HTTPX AsyncClient

FastAPI supports this pattern directly in its docs for async tests. citeturn1search5

Your integration tests should validate:

Request validation (422s)  
Auth/role enforcement (401/403)  
DB state changes  
Idempotency and conflict (409)  
Cache behavior (if Redis is involved)  
Error behavior (500s, timeouts)  

### Contract and fuzz testing: OpenAPI‑driven Schemathesis

Schemathesis generates test cases from OpenAPI and runs property‑based testing to explore API behavior systematically; it also validates responses against schema. citeturn3search2turn3search10turn3search18 Academic work on schema‑aware fuzzers (including Schemathesis) supports the approach of deriving fuzzers from API schemas. citeturn3search35turn3search22

This is a direct countermeasure to AI’s “happy path tunnel vision”: instead of asking the agent to imagine edge cases, Schemathesis generates them from the schema constraints. citeturn3search2turn13view0

### Security testing: make OWASP risks executable

OWASP’s API Security Top 10 provides a practical taxonomy of common API failure modes (authorization, authentication, injections, rate limiting, etc.). citeturn18search0turn18search36

SQL injection prevention guidance emphasizes parameterized queries and related defenses. citeturn18search1turn18search5

In an AI‑written backend, you should literally encode these into tests:

Auth bypass attempts (no token, expired token, wrong role)  
Object‑level authorization tests (IDOR‑style)  
Rate limit tests (where implemented)  
Input fuzzing (Schemathesis + custom cases)  
Schema conformance checks

This is not optional: empirical studies find AI‑authored code tends to require more verification for reliability/security, and industry reports show higher issue rates in AI‑coauthored PRs (logic/security/maintainability). citeturn21search3turn21search31turn21search23

## Mutation testing and property‑based testing as the AI test‑quality firewall

Mutation testing is the closest thing you have to an objective test‑quality metric: it measures whether your tests detect injected faults, not whether they merely execute code. Google’s internal work and broader mutation testing literature describe it as assessing test suite efficacy by inserting small faults and measuring detection. citeturn2search39turn2search23turn2search27

### Mutation testing: what to run and how to interpret

A mutation score is the percentage of mutants killed by tests; mutants that survive indicate weak assertions or missing scenarios. Tool docs explicitly explain that if the test suite passes on mutated code, there is a mismatch between tests and functionality. citeturn2search2turn2search1

This maps directly to the AI anti‑patterns:

Tautologies often survive (because expected values are derived the same way)  
Assertion‑free tests survive (no oracle)  
Happy‑path‑only suites survive many error‑handling mutants  
Mock theater survives integration mutants (because boundary isn’t real) citeturn2search39turn10search9turn20search23

image_group{"layout":"carousel","aspect_ratio":"16:9","query":["Stryker mutation testing report example","mutmut mutation testing output example","Cosmic Ray mutation testing report example"],"num_per_query":1}

### Stryker for TypeScript and JavaScript

Stryker provides plugins like a TypeScript checker to avoid wasting time on type‑error mutants. citeturn2search0turn2search36

A key practical note for modern stacks: Stryker may not support certain Vitest modes (e.g., browser mode), and practitioners document working around this by adjusting execution strategy. citeturn2search24turn5search9

**CI strategy (opinionated)**  
Run Stryker on PRs only, and scope it to changed files (or critical directories) to control cost. Mutation testing’s computational intensity is well‑known and cost‑reduction strategies are an active research area. citeturn2academia40turn2search39

### mutmut (or Cosmic Ray) for Python

mutmut is designed to be usable and incremental, remembering work done and speeding up runs by knowing which tests to execute. citeturn2search1turn2search25

Cosmic Ray explicitly describes how mutation testing adds value beyond coverage by determining whether tests check behavior. citeturn2search2turn2search18

### Property‑based testing: force input diversity

Property‑based testing frameworks define properties (“invariants”) that must hold for all valid inputs, and then generate many test cases automatically.

Hypothesis is the standard property‑based library for Python and supports stateful testing via state machines. citeturn3search12turn3search8turn3search23  
fast‑check is a property‑based testing framework for TypeScript/JavaScript and explicitly supports model‑based testing. citeturn3search27turn3search1turn3search9

This directly corrects AI’s tendency to write example‑based tests with shallow edge coverage. citeturn3search9turn13view0

### Combining Schemathesis + Hypothesis: API properties at scale

Schemathesis is built on property‑based testing and generates tests from schemas, which is exactly what you want for AI‑written backends where missing edge cases are common. citeturn3search2turn3search10turn3search35

A pragmatic property set for APIs:

Create then read returns the same data (for any valid input)  
Invalid inputs never return 500 (should return 4xx)  
Unauthorized access never returns data  
Responses always validate against schema

These are the classes of failures schema‑aware fuzzing is designed to detect. citeturn3search2turn3search35turn18search0

## CI/CD pipeline and the trust‑but‑verify checklist

AI‑first development must treat CI as a layered verification system: fast checks first, then progressively more expensive “truth layers,” with mutation testing and E2E as merge gates for critical paths. Thoughtworks explicitly recommends reinforcing established practices like TDD and static analysis and embedding them into coding workflows as agents scale changes. citeturn13view0turn12view1

### Pipeline stages with concrete GitHub Actions primitives

GitHub provides official docs for dependency caching and for Postgres service containers in workflows. citeturn5search5turn5search8turn5search12

For security scanning, GitHub documents CodeQL code scanning and dependency review actions. citeturn8search0turn8search4turn8search1 GitHub also announced CodeQL Action v4 and deprecation timelines for v3. citeturn8search19turn8search3

For Playwright in CI, Playwright offers a CI setup guide. citeturn1search12

For performance regression, Lighthouse CI has official guidance (including web.dev docs) and GitHub Action integrations. citeturn8search25turn8search21turn8search13  
For load testing in CI, Grafana provides k6 examples for GitHub Actions. citeturn8search6turn8search10

### Opinionated merge gates for AI‑generated code

Do not merge unless:

Static analysis passes (TypeScript typecheck + lint; Python lint/type checks)  
Unit/integration tests pass with real Postgres for backend  
API schema checks pass (OpenAPI validation + Schemathesis smoke)  
E2E covers the shipped journey (Playwright)  
Mutation score doesn’t regress on critical paths (Stryker + mutmut)  
Security gates show no new vulnerable dependencies (dependency review) and CodeQL is green citeturn13view0turn2search39turn8search1turn8search0

The reason to be this strict is empirical: AI‑assisted PRs show higher rates of logic/security/quality issues and introduce verification bottlenecks unless you enforce quality gates. citeturn21search3turn21search23turn21search0

### Practical “Trust but verify” checklist

Use this per PR, every time:

Tests were written before implementation or in a separate commit; tests are derived from acceptance criteria, not code. citeturn6search15turn13view0  
Breaking the implementation intentionally makes at least one test fail (sanity check against tautologies). citeturn10search9turn2search39  
No vague assertions (`toBeTruthy`, `toBeDefined`, snapshot‑only). citeturn20search23turn10search27  
Mocks are limited to external systems; DB tests use real Postgres (not SQLite) to avoid false confidence. citeturn4search9turn4search16turn4search2  
At least one error path and one boundary case exist for the feature. citeturn18search0turn3search2  
If code and tests were both modified to “fix a bug,” require manual review for synchronized mutation risk. citeturn13view0turn19search27  
Mutation score meets threshold (e.g., 80%+ for critical paths; lower for utilities), and score doesn’t drop on PR. citeturn2search39turn2search1  
E2E verifies the actual user journey (especially for async Server Components, per Next.js guidance). citeturn9search16turn1search16

### Source index (URLs)

```text
Thoughtworks Technology Radar Vol. 33 (Nov 2025):
https://www.thoughtworks.com/content/dam/thoughtworks/documents/radar/2025/11/tr_technology_radar_vol_33_en.pdf

Thoughtworks: AGENTS.md (Technology Radar):
https://www.thoughtworks.com/radar/techniques/agents-md

Thoughtworks: Spec-driven development (Technology Radar):
https://www.thoughtworks.com/en-de/radar/techniques/spec-driven-development

Next.js Testing Guide (App Router):
https://nextjs.org/docs/app/guides/testing

Next.js Testing: Vitest (updated Feb 27, 2026):
https://nextjs.org/docs/app/guides/testing/vitest

Next.js Testing: Playwright (updated Feb 27, 2026):
https://nextjs.org/docs/pages/guides/testing/playwright

React Testing Library docs:
https://testing-library.com/docs/react-testing-library/intro/
https://testing-library.com/docs/queries/byrole/

FastAPI Testing:
https://fastapi.tiangolo.com/tutorial/testing/
https://fastapi.tiangolo.com/advanced/async-tests/
https://fastapi.tiangolo.com/advanced/testing-dependencies/

MSW:
https://mswjs.io/docs/quick-start/
https://mswjs.io/docs/integrations/node/

Mutation testing:
https://stryker-mutator.io/
https://mutmut.readthedocs.io/
https://cosmic-ray.readthedocs.io/

Schemathesis:
https://schemathesis.io/
https://github.com/schemathesis/schemathesis

GitHub Actions security and dependencies:
https://docs.github.com/code-security/code-scanning/introduction-to-code-scanning/about-code-scanning-with-codeql
https://github.com/actions/dependency-review-action

Playwright CI:
https://playwright.dev/docs/ci-intro

OWASP API Security Top 10 (2023):
https://owasp.org/API-Security/editions/2023/en/0x11-t10/

OWASP SQL Injection Prevention Cheat Sheet:
https://cheatsheetseries.owasp.org/cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.html

SonarSource verification gap (Jan 2026):
https://www.sonarsource.com/company/press-releases/sonar-data-reveals-critical-verification-gap-in-ai-coding/

CodeRabbit State of AI vs Human report (Dec 2025):
https://www.coderabbit.ai/blog/state-of-ai-vs-human-code-generation-report

Cursor agent best practices (Jan 2026):
https://cursor.com/blog/agent-best-practices

Claude Code docs:
https://code.claude.com/docs/en/quickstart
https://code.claude.com/docs/en/security

Vercel React Best Practices (Jan 2026):
https://vercel.com/blog/introducing-react-best-practices
```