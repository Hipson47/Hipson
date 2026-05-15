---
name: fullstack-2026
description: >
  Build production Next.js App Router + FastAPI applications. Covers server-first
  React architecture, Server Components/Actions, BFF/proxy integration shapes,
  async FastAPI patterns, OpenAPI contracts, agent-friendly repo layouts, and testing
  strategies. Use when building or reviewing fullstack applications, designing
  API integration patterns, or setting up a new project with Next.js + Python backend.
---

# Fullstack 2026

## 1. Purpose
Build production-grade Next.js + FastAPI applications using 2026 best practices: server-first, type-safe, agent-friendly.

## 2. When to Use
- Starting a new fullstack project (Next.js + FastAPI)
- Reviewing existing architecture against 2026 standards
- Designing API integration patterns (BFF, proxy, direct)
- Setting up repo structure for AI-agent-assisted development
- Choosing state management, caching, and testing approaches

## 3. When NOT to Use
- Backend-only API design without frontend → use FastAPI docs directly
- Frontend-only without custom backend → use Next.js docs directly
- Mobile app development

## 4. Integration Shapes

### Shape A: Direct Frontend → Backend (simpler)
Browser and Server Components call FastAPI directly over HTTPS. Pushes CORS and cookie complexity to edge boundaries.
**Use when**: simple auth, fewer hops, clean CORS setup.

### Shape B: Next.js as BFF/Proxy (recommended for complex auth)
All browser traffic → Next.js (same-origin) → FastAPI (private network). Centralizes auth and error mapping.
**Use when**: complex auth/cookie needs, avoiding CORS entirely, SSR caching simplification.

**Key principle**: Server Actions are effectively endpoints. Treat them as public attack surface: authenticate, authorize, validate, rate-limit.

## 5. Recommended Baseline

**Next.js owns**: UI, SSR, caching strategy
- Server Components for read paths
- Server Actions for frontend-owned mutations
- Route Handlers for: proxy/BFF calls, file uploads, webhooks, streaming

**FastAPI owns**: data, domain behavior
- Pure resource server: domain logic, DB, background jobs, realtime
- OpenAPI as contract source-of-truth for generated TS clients

## 6. Agent-Friendly Repo Layout

```
repo/
  apps/
    web/                  # Next.js
    api/                  # FastAPI
  packages/
    api-client/           # Generated TS client from OpenAPI
    shared/               # Shared runtime utilities
  infra/
    docker/
    k8s/
  docs/
```

Explicit, repetitive structure. AI agents implement it reliably.

## 7. Key Patterns

### React Server-First Architecture
- Server Components are default; client components are explicit boundaries
- `use client` marks interactive islands
- Suspense for loading UI (not scattered `isLoading` booleans)
- Transitions for responsive mutations (`useTransition` + `isPending`)

### Next.js App Router Conventions
- `layout.js` — segment-level UI composition
- `loading.js` — streaming fallback with Suspense
- `error.js` — error boundaries (must be Client Components)
- `not-found.js` — 404 handling
- `proxy.js` (formerly middleware) — request interception before routing

### FastAPI Async Patterns
- `async def` for endpoints with I/O (DB, HTTP, file)
- `def` (sync) for CPU-bound work (runs in threadpool automatically)
- Async SQLAlchemy 2.0 for session management
- Pydantic v2 for request/response schemas

### OpenAPI as Contract
- FastAPI generates OpenAPI spec automatically
- Generate TypeScript client from spec (openapi-typescript-codegen or similar)
- Frontend types always in sync with backend

### Error Handling
- Problem Details (RFC 9457) for consistent error responses
- Map backend errors to frontend-friendly formats in BFF layer

## 8. Security

- **Server Actions are public attack surface** — validate all inputs
- **Authorization at data layer**, not just middleware/proxy (middleware bypass vulnerabilities exist)
- **RSC payload handling is security-sensitive** — follow framework security advisories
- Pin framework versions; run security scans in CI

## 9. State Management Taxonomy

| Truth lives in… | Tool |
|-----------------|------|
| Server (DB, API) | Server Components + TanStack Query |
| URL | Next.js router, searchParams |
| Browser (ephemeral UI) | useState, useReducer |
| Cross-component client state | Zustand or Jotai (lightweight) |
| Form state | React Hook Form or native actions |

## 10. Failure Modes
1. **Middleware-only auth** — authorization bypass. Fix: enforce at data access layer.
2. **Server Action without validation** — injection risk. Fix: validate + authorize every action.
3. **Sync DB calls in async endpoints** — blocks event loop. Fix: use async SQLAlchemy.
4. **Type drift** — frontend/backend types diverge. Fix: generate client from OpenAPI spec.
5. **Over-memoization** — unnecessary complexity. Fix: measure first, memoize at hot boundaries only.

## 11. Cross-Links
- Testing AI-written fullstack code → `eval-security-guardrails/`
- AI coding workflows for fullstack → `ai-coding-workflows/`
- System prompt for fullstack coding agent → `system-prompt-architect/` Example 5

## 12. Source Basis
backend.md (canonical — March 2026), frontend.md (canonical — March 2026).

## 13. Freshness Notes
`[FRESHNESS: April 2026]` Next.js App Router and FastAPI patterns are current. Monitor for: Next.js proxy API changes (was middleware, renamed), React Compiler GA, Turbopack stability updates.
---

# Fullstack 2026 — Examples

## Example 1: Bad vs Better — Integration Shape

**Bad (leaky abstraction):**
```ts
// Client component directly calling FastAPI with hardcoded URL
const res = await fetch('http://localhost:8000/api/users', {
  headers: { Authorization: `Bearer ${token}` }
})
```

**Better (BFF proxy):**
```ts
// app/api/users/route.ts — Next.js Route Handler as proxy
export async function GET(req: NextRequest) {
  const session = await getSession(req)  // auth centralized here
  if (!session) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  
  const res = await fetch(`${process.env.API_INTERNAL_URL}/users`, {
    headers: { 'X-User-Id': session.userId }  // internal auth
  })
  return NextResponse.json(await res.json())
}
```

**Why:** Auth centralized in BFF. No CORS. No token in browser. Internal API never exposed.

---

## Example 2: Bad vs Better — Server Action Security

**Bad:**
```ts
// app/actions.ts
'use server'
export async function deleteUser(userId: string) {
  await db.user.delete({ where: { id: userId } })  // No auth check!
}
```

**Better:**
```ts
// app/actions.ts
'use server'
import { getSession } from '@/lib/auth'
import { z } from 'zod'

const DeleteUserSchema = z.object({ userId: z.string().uuid() })

export async function deleteUser(raw: unknown) {
  const session = await getSession()
  if (!session?.isAdmin) throw new Error('Forbidden')
  
  const { userId } = DeleteUserSchema.parse(raw)  // validate
  if (userId === session.userId) throw new Error('Cannot delete self')
  
  await db.user.delete({ where: { id: userId } })
  revalidatePath('/admin/users')
}
```

---

## Example 3: Server Component Data Fetching

```tsx
// app/dashboard/page.tsx — Server Component (default)
import { Suspense } from 'react'
import { DashboardStats } from './stats'
import { RecentActivity } from './activity'

export default function DashboardPage() {
  return (
    <div>
      <h1>Dashboard</h1>
      <Suspense fallback={<StatsShimmer />}>
        <DashboardStats />  {/* async server component */}
      </Suspense>
      <Suspense fallback={<ActivityShimmer />}>
        <RecentActivity />  {/* streams independently */}
      </Suspense>
    </div>
  )
}

// app/dashboard/stats.tsx
async function DashboardStats() {
  const stats = await api.getStats()  // runs on server, no client JS
  return <StatsGrid data={stats} />
}
```

---

## Example 4: FastAPI Async Endpoint with Proper Error Handling

```python
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/api/users")

class UserSearch(BaseModel):
    q: str = Field(min_length=1, max_length=200)
    page: int = Field(default=1, ge=1)
    limit: int = Field(default=20, ge=1, le=100)

@router.get("/search")
async def search_users(
    params: UserSearch = Depends(),
    db: AsyncSession = Depends(get_db),
):
    """Search users by name (partial) or email (exact)."""
    try:
        results = await user_service.search(
            db, query=params.q, page=params.page, limit=params.limit
        )
        return {
            "items": results.items,
            "total": results.total,
            "page": params.page,
            "pages": results.pages,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
```

---

## Example 5: Generated API Client from OpenAPI

```bash
# Generate TypeScript client from FastAPI's OpenAPI spec
npx openapi-typescript-codegen \
  --input http://localhost:8000/openapi.json \
  --output packages/api-client/src \
  --client fetch

# Usage in Next.js Server Component:
import { UsersService } from '@repo/api-client'

async function UserList() {
  const users = await UsersService.searchUsers({ q: 'john', limit: 10 })
  return <ul>{users.items.map(u => <li key={u.id}>{u.name}</li>)}</ul>
}
```

---

# Fullstack 2026 — Checklist

## Pre-Flight
- [ ] Integration shape chosen (Direct vs BFF/Proxy)
- [ ] Repo structure follows agent-friendly layout
- [ ] OpenAPI spec generation configured
- [ ] TS client generation pipeline set up
- [ ] Auth strategy decided (session vs JWT vs OAuth)
- [ ] Error handling format agreed (Problem Details RFC 9457)

## In-Flight
- [ ] Server Components are default; `use client` only where needed
- [ ] Suspense used for loading states (not `isLoading` booleans)
- [ ] Server Actions validated, authorized, and rate-limited
- [ ] FastAPI endpoints use `async def` for I/O operations
- [ ] Pydantic v2 models for all request/response schemas
- [ ] Authorization enforced at data access layer (not just middleware)
- [ ] Types generated from OpenAPI (no manual type duplication)

## Final Review
- [ ] No hardcoded API URLs in client components
- [ ] No tokens/secrets in browser-accessible code
- [ ] Auth bypass tested (can middleware be skipped?)
- [ ] Error responses consistent (Problem Details format)
- [ ] Build passes with strict TypeScript
- [ ] Security scan on both frontend and backend

## Top 5 Failure Modes
1. **Middleware-only auth** — bypass vulnerability. Enforce at data layer.
2. **Unvalidated Server Actions** — injection risk. Always validate + authorize.
3. **Sync I/O in async endpoints** — blocks FastAPI event loop.
4. **Type drift** — frontend/backend types diverge. Generate from OpenAPI.
5. **Client-side secrets** — tokens in browser. Use BFF/proxy pattern.
