# Clerk setup (01-TECH_SPEC §1)

Auth roles: `student`, `parent`, `coach`, `admin`. Organization = family unit.

## One-time dashboard configuration
1. Create the Clerk application (name: Spykt).
2. **Roles:** add `role` to the session token custom claims (publicMetadata.role → `role`),
   values restricted to the four roles above. Family membership: Clerk Organizations, one org
   per family; org id mirrored to `families.id` mapping via webhook; add `family_id` claim.
3. **Session token claims** (Dashboard → Sessions → Customize session token):
   ```json
   { "role": "{{user.public_metadata.role}}", "family_id": "{{org.public_metadata.family_id}}" }
   ```
   These are the claims the RLS policies read (see DECISIONS_NEEDED D-001).
4. **Webhooks:** point `user.created|updated|deleted` and `organization*` events at
   `POST {api}/webhooks/clerk`. NOTE: the Phase 0 endpoint is an unverified stub —
   do not enable in production until Phase 1 adds svix signature verification.
5. **Supabase third-party auth:** register Clerk as a third-party auth provider in Supabase
   so PostgREST populates `request.jwt.claims` from Clerk JWTs.

## Env vars
- `CLERK_SECRET_KEY` (api, web server-side)
- `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` (web)
- `CLERK_WEBHOOK_SIGNING_SECRET` (api, Phase 1)
