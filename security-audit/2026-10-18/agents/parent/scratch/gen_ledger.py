#!/usr/bin/env python3
"""Parent-only helper: emit coverage-ledger.json with canonical IDs."""
from __future__ import annotations

import json
from pathlib import Path

UNRESERVED = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def encode_ref(value: str) -> str:
    out = []
    for byte in value.encode("utf-8"):
        out.append(chr(byte) if byte in UNRESERVED else f"%{byte:02X}")
    return "".join(out)


def coverage_id(refs: dict) -> str:
    fields = ["surface", "boundary", "subsystem", "attack_class"]
    if "lifecycle" in refs:
        fields.append("lifecycle")
    return "::".join(encode_ref(refs[f]) for f in fields)


ORDINARY = {
    "injection": "ATTACK-CLASSES.md#Injection",
    "access": "ATTACK-CLASSES.md#Access control",
    "files": "ATTACK-CLASSES.md#Resource and file handling",
    "crypto": "ATTACK-CLASSES.md#Cryptography and secrets",
    "biz": "ATTACK-CLASSES.md#Business logic",
    "abuse": "ATTACK-CLASSES.md#Feature abuse and data leakage",
    "chain": "ATTACK-CLASSES.md#Chained vulnerabilities and trust boundaries",
    "wild": "ATTACK-CLASSES.md#Wildcard",
    "obvious": "ATTACK-CLASSES.md#Obvious things",
}

# Shared exclusion reasons (source facts from recon).
EXCL = {
    "MEMORY-SAFETY-AND-BINARY.md#Core discipline": "SaaS path is Python/TypeScript; no unsafe native, kernel, or FFI parser in this surface.",
    "WEB-PROTOCOL-AND-AUTH.md#SAML signed-object and assertion binding": "No SAML IdP or assertion consumer in source.",
    "WEB-PROTOCOL-AND-AUTH.md#WebAuthn and passkey verification": "No WebAuthn/passkey registration or assertion code.",
    "WEB-PROTOCOL-AND-AUTH.md#MFA enrollment and assurance downgrade": "No MFA enrollment or second-factor paths.",
    "AI-AND-LLM.md#MCP server and tool identity confusion": "No MCP server or client in the product.",
    "AI-AND-LLM.md#Sub-agent and MCP trust inheritance": "No MCP or sub-agent dispatch in the product.",
    "CLIENT-SIDE.md#Service-worker registration and scope takeover": "No service-worker registration in frontend or mobile webviews.",
    "DESKTOP-MOBILE-AND-LOCAL-IPC.md#IPC peer-authentication gaps": "No Unix-socket/XPC/Binder local daemon in this surface.",
    "PROTOCOLS-RPC-AND-MESSAGING.md#Topic, routing-key, and subscription scope gaps": "No broker/topic subscription; worker polls Postgres.",
}

WEB_CORE = [
    "WEB-PROTOCOL-AND-AUTH.md#Core discipline",
    "WEB-PROTOCOL-AND-AUTH.md#Universal moves",
    "WEB-PROTOCOL-AND-AUTH.md#Validation rules",
]
AI_CORE = [
    "AI-AND-LLM.md#Core discipline",
    "AI-AND-LLM.md#Universal moves",
    "AI-AND-LLM.md#Validation rules",
]
CLIENT_CORE = [
    "CLIENT-SIDE.md#Core discipline",
    "CLIENT-SIDE.md#Universal moves",
    "CLIENT-SIDE.md#Validation rules",
]
DATA_CORE = [
    "DATA-ISOLATION-AND-LIFECYCLE.md#Core discipline",
    "DATA-ISOLATION-AND-LIFECYCLE.md#Universal moves",
    "DATA-ISOLATION-AND-LIFECYCLE.md#Validation rules",
]
CLOUD_CORE = [
    "CLOUD-AND-DEPLOYMENT.md#Core discipline",
    "CLOUD-AND-DEPLOYMENT.md#Universal moves",
    "CLOUD-AND-DEPLOYMENT.md#Validation rules",
]
RES_CORE = [
    "RESOURCE-EXHAUSTION-AND-AVAILABILITY.md#Core discipline",
    "RESOURCE-EXHAUSTION-AND-AVAILABILITY.md#Universal moves",
    "RESOURCE-EXHAUSTION-AND-AVAILABILITY.md#Validation rules",
]
MOB_CORE = [
    "DESKTOP-MOBILE-AND-LOCAL-IPC.md#Core discipline",
    "DESKTOP-MOBILE-AND-LOCAL-IPC.md#Universal moves",
    "DESKTOP-MOBILE-AND-LOCAL-IPC.md#Validation rules",
]
SUP_CORE = [
    "SUPPLY-CHAIN-AND-RELEASE.md#Core discipline",
    "SUPPLY-CHAIN-AND-RELEASE.md#Universal moves",
    "SUPPLY-CHAIN-AND-RELEASE.md#Validation rules",
]
PROTO_CORE = [
    "PROTOCOLS-RPC-AND-MESSAGING.md#Core discipline",
    "PROTOCOLS-RPC-AND-MESSAGING.md#Universal moves",
    "PROTOCOLS-RPC-AND-MESSAGING.md#Validation rules",
]


def unit(
    surface_ref,
    surface,
    boundary_ref,
    boundary,
    subsystem_ref,
    subsystem,
    attack_ref,
    attack,
    paths,
    ordinary,
    selected,
    extra_excl=None,
):
    refs = {
        "surface": surface_ref,
        "boundary": boundary_ref,
        "subsystem": subsystem_ref,
        "attack_class": attack_ref,
    }
    excluded = dict(EXCL)
    if extra_excl:
        excluded.update(extra_excl)
    # Do not exclude a block that is selected.
    excluded = {k: v for k, v in excluded.items() if k not in selected}
    return {
        "coverage_id": coverage_id(refs),
        "canonical_refs": refs,
        "surface": surface,
        "boundary": boundary,
        "subsystem": subsystem,
        "attack_class": attack,
        "starting_paths": paths,
        "ordinary_attack_class_block": ordinary,
        "selected_companion_blocks": selected,
        "excluded_blocks": [{"block": k, "reason": v} for k, v in sorted(excluded.items())],
        "prior_status": "none",
        "attempts": [],
        "wave": 1,
        "status": "planned",
        "agent_id": None,
        "reviewed_paths": [],
        "local_checks": [],
        "result_fingerprints": [],
        "unresolved": [],
    }


units = [
    unit(
        "backend/app/main.py#GET /health",
        "Unauthenticated health and default OpenAPI docs",
        "backend/app/main.py#FastAPI() default docs",
        "Anonymous caller versus internal path and schema disclosure",
        "backend/app",
        "FastAPI core API",
        "ATTACK-CLASSES.md#Obvious things",
        "Obvious things",
        ["backend/app/main.py", "backend/app/config.py"],
        ORDINARY["obvious"],
        WEB_CORE + ["WEB-PROTOCOL-AND-AUTH.md#Host and forwarded-header trust"],
        {"WEB-PROTOCOL-AND-AUTH.md#OAuth/OIDC request and callback binding": "Health and OpenAPI are not OAuth callbacks."},
    ),
    unit(
        "backend/app/main.py#POST /auth/register",
        "Open registration and login JWT issuance",
        "backend/app/auth.py#create_access_token",
        "Anonymous versus new-account and session minting",
        "backend/app",
        "FastAPI core API",
        "ATTACK-CLASSES.md#Access control",
        "Access control",
        ["backend/app/main.py", "backend/app/auth.py", "backend/app/config.py"],
        ORDINARY["access"],
        WEB_CORE
        + [
            "WEB-PROTOCOL-AND-AUTH.md#JWT verification and claim binding",
            "WEB-PROTOCOL-AND-AUTH.md#Session fixation and invalidation",
            "WEB-PROTOCOL-AND-AUTH.md#Password reset and broader recovery",
        ],
    ),
    unit(
        "backend/app/facebook_routes.py#GET /integrations/facebook/oauth/callback",
        "Facebook OAuth callback without Bearer",
        "backend/app/services/facebook_page.py#encode_oauth_state",
        "Unauthenticated browser versus Page-token write",
        "backend/app/services/facebook_page.py",
        "Facebook Page posting",
        "WEB-PROTOCOL-AND-AUTH.md#OAuth/OIDC request and callback binding",
        "OAuth callback binding",
        [
            "backend/app/facebook_routes.py",
            "backend/app/services/facebook_page.py",
            "backend/app/services/meta_graph.py",
        ],
        ORDINARY["access"],
        WEB_CORE
        + [
            "WEB-PROTOCOL-AND-AUTH.md#OAuth/OIDC request and callback binding",
            "WEB-PROTOCOL-AND-AUTH.md#JWT verification and claim binding",
            "WEB-PROTOCOL-AND-AUTH.md#Host and forwarded-header trust",
        ],
    ),
    unit(
        "backend/app/facebook_routes.py#POST /facebook/posts",
        "Facebook publish, calendar Approve, and Post now",
        "backend/app/services/facebook_page.py#require_facebook_poster",
        "Catalogger or outsider versus Page publish",
        "backend/app/services/facebook_page.py",
        "Facebook Page posting",
        "ATTACK-CLASSES.md#Business logic",
        "Business logic",
        [
            "backend/app/facebook_routes.py",
            "backend/app/services/facebook_page.py",
            "backend/app/services/facebook_calendar.py",
        ],
        ORDINARY["biz"],
        WEB_CORE + ["WEB-PROTOCOL-AND-AUTH.md#Ordinary CSRF"],
    ),
    unit(
        "backend/app/services/facebook_crypto.py#seal_facebook_secret",
        "Facebook and CT Seller token storage",
        "backend/app/services/facebook_crypto.py#XOR fallback",
        "Host or backup reader versus Page and CT secrets",
        "backend/app",
        "Token sealing",
        "ATTACK-CLASSES.md#Cryptography and secrets",
        "Cryptography and secrets",
        [
            "backend/app/services/facebook_crypto.py",
            "backend/app/services/ctseller.py",
            "backend/app/config.py",
        ],
        ORDINARY["crypto"],
        [],
        {"WEB-PROTOCOL-AND-AUTH.md#Core discipline": "At-rest sealing is not an HTTP identity boundary."},
    ),
    unit(
        "backend/app/main.py#POST /jobs",
        "Create catalog job with caller input_folder",
        "backend/app/services/processing.py#folder.rglob",
        "Authenticated catalogger versus host filesystem and Gemini",
        "backend/app/services/processing.py",
        "Catalog jobs",
        "ATTACK-CLASSES.md#Resource and file handling",
        "Resource and file handling",
        [
            "backend/app/main.py",
            "backend/app/services/processing.py",
            "backend/app/job_batch_storage.py",
        ],
        ORDINARY["files"],
        DATA_CORE + ["DATA-ISOLATION-AND-LIFECYCLE.md#Missing tenant or owner enforcement"],
    ),
    unit(
        "backend/app/main.py#POST /jobs/upload",
        "Multipart photo and video ingest",
        "backend/app/upload_storage.py#save_uploaded_files",
        "Client or staff upload versus disk, R2, and Gemini spend",
        "backend/app",
        "Catalog jobs",
        "ATTACK-CLASSES.md#Resource and file handling",
        "Resource and file handling",
        [
            "backend/app/main.py",
            "backend/app/upload_storage.py",
            "backend/app/catalog_limits.py",
        ],
        ORDINARY["files"],
        RES_CORE
        + [
            "RESOURCE-EXHAUSTION-AND-AVAILABILITY.md#Unbounded buffering and cardinality",
            "RESOURCE-EXHAUSTION-AND-AVAILABILITY.md#Decompression and representation amplification",
        ],
    ),
    unit(
        "backend/pipeline/pipeline.py#analyze photos",
        "Photo bytes and listing text into Gemini prompts",
        "backend/app/core/gemini.py#_user_prompt",
        "Lower-trust content versus model context and listing sinks",
        "backend/pipeline",
        "Gemini catalog pipeline",
        "ATTACK-CLASSES.md#Injection",
        "Injection",
        [
            "backend/pipeline/pipeline.py",
            "backend/app/core/gemini.py",
            "backend/app/services/facebook_caption.py",
        ],
        ORDINARY["injection"],
        AI_CORE
        + [
            "AI-AND-LLM.md#Indirect injection through retrieved or ingested content",
            "AI-AND-LLM.md#Prompt role and provenance confusion",
            "AI-AND-LLM.md#Insecure output rendering",
        ],
    ),
    unit(
        "backend/app/main.py#POST /jobs/{id}/run",
        "Catalog run, regenerate, and hunt that spend Gemini",
        "backend/app/config.py#GOOGLE_API_KEY",
        "Authenticated caller versus operator-owned Gemini bill",
        "backend/pipeline",
        "Gemini catalog pipeline",
        "RESOURCE-EXHAUSTION-AND-AVAILABILITY.md#Quota-accounting scope and reset gaps",
        "Quota-accounting and operator spend",
        [
            "backend/app/main.py",
            "backend/app/services/processing.py",
            "backend/app/services/audience_hunt.py",
        ],
        ORDINARY["abuse"],
        RES_CORE
        + [
            "RESOURCE-EXHAUSTION-AND-AVAILABILITY.md#Quota-accounting scope and reset gaps",
            "AI-AND-LLM.md#Unbounded delegated action loops",
            "AI-AND-LLM.md#Core discipline",
        ],
    ),
    unit(
        "backend/app/main.py#POST /lots/{id}/audience-hunt",
        "Audience hunt Gemini Pro plus Google Search tool",
        "backend/app/services/audience_hunt.py#_get_client",
        "Lot text versus Search tool and stored group URLs",
        "backend/app/services/audience_hunt.py",
        "Gemini catalog pipeline",
        "AI-AND-LLM.md#Tool-argument injection into a downstream sink",
        "LLM tool and action binding",
        [
            "backend/app/services/audience_hunt.py",
            "backend/app/main.py",
        ],
        ORDINARY["injection"],
        AI_CORE
        + [
            "AI-AND-LLM.md#Tool-argument injection into a downstream sink",
            "AI-AND-LLM.md#Excessive agency and confused-deputy authority",
            "AI-AND-LLM.md#Sensitive context extraction",
        ],
    ),
    unit(
        "backend/app/main.py#POST /integrations/ctseller/session",
        "CT Seller session mint for Watcher",
        "backend/app/main.py#CTSellerSessionOut",
        "Catalogger versus CT Seller access and refresh JWTs",
        "backend/app/services/ctseller.py",
        "CT Seller integration",
        "ATTACK-CLASSES.md#Feature abuse and data leakage",
        "Feature abuse and data leakage",
        ["backend/app/main.py", "backend/app/services/ctseller.py", "backend/app/schemas.py"],
        ORDINARY["abuse"],
        WEB_CORE + ["WEB-PROTOCOL-AND-AUTH.md#API-key scope and resource binding"],
    ),
    unit(
        "backend/app/main.py#POST /lots/{id}/post-to-ctseller",
        "CT Seller listing and photo post",
        "backend/app/services/roles.py#can_catalog",
        "Catalogger versus owner's CT Seller account",
        "backend/app/services/ctseller.py",
        "CT Seller integration",
        "ATTACK-CLASSES.md#Access control",
        "Access control",
        ["backend/app/main.py", "backend/app/services/ctseller.py", "backend/app/services/roles.py"],
        ORDINARY["access"],
        DATA_CORE + ["DATA-ISOLATION-AND-LIFECYCLE.md#Missing tenant or owner enforcement"],
    ),
    unit(
        "backend/app/main.py#GET /admin/accounts",
        "Master-admin cross-account routes",
        "backend/app/auth.py#is_master_admin_email",
        "Ordinary account owner versus all-tenant admin",
        "backend/app",
        "Master admin",
        "ATTACK-CLASSES.md#Access control",
        "Access control",
        ["backend/app/main.py", "backend/app/auth.py", "backend/app/config.py"],
        ORDINARY["access"],
        WEB_CORE + ["WEB-PROTOCOL-AND-AUTH.md#JWT verification and claim binding"],
    ),
    unit(
        "backend/app/services/roles.py#client_http_allowed",
        "Client upload-only HTTP allowlist",
        "backend/app/auth.py#enforce_client_http",
        "Client login versus catalog and integrations",
        "backend/app",
        "FastAPI core API",
        "ATTACK-CLASSES.md#Access control",
        "Access control",
        ["backend/app/services/roles.py", "backend/app/auth.py", "backend/app/main.py"],
        ORDINARY["access"],
        DATA_CORE + ["DATA-ISOLATION-AND-LIFECYCLE.md#Missing tenant or owner enforcement"],
    ),
    unit(
        "backend/app/main.py#GET /lots",
        "Lot, sale, and job account_id filters",
        "backend/app/main.py#_sale_for_user",
        "Same-deployment tenant versus another account's estate data",
        "backend/app",
        "FastAPI core API",
        "DATA-ISOLATION-AND-LIFECYCLE.md#Missing tenant or owner enforcement",
        "Tenant isolation",
        ["backend/app/main.py", "backend/app/models.py"],
        ORDINARY["access"],
        DATA_CORE
        + [
            "DATA-ISOLATION-AND-LIFECYCLE.md#Missing tenant or owner enforcement",
            "DATA-ISOLATION-AND-LIFECYCLE.md#Policy and query disagreement",
        ],
    ),
    unit(
        "backend/app/main.py#GET /exports/json",
        "Inventory exports and sale photo ZIP",
        "backend/app/main.py#export handlers",
        "Authenticated staff versus bulk estate data",
        "backend/app",
        "FastAPI core API",
        "ATTACK-CLASSES.md#Feature abuse and data leakage",
        "Feature abuse and data leakage",
        ["backend/app/main.py", "backend/app/services/sale_zip_export.py"],
        ORDINARY["abuse"],
        DATA_CORE + ["DATA-ISOLATION-AND-LIFECYCLE.md#Export and backup scope expansion"],
    ),
    unit(
        "backend/app/main.py#GET /lots/{id}/media-url",
        "Presigned R2 media URLs",
        "backend/app/object_storage.py#signed URL",
        "URL holder versus catalog object bytes",
        "backend/app/object_storage.py",
        "Object storage",
        "DATA-ISOLATION-AND-LIFECYCLE.md#Blob and signed-reference overreach",
        "Blob and signed URL overreach",
        [
            "backend/app/main.py",
            "backend/app/catalog_storage.py",
            "backend/app/object_storage.py",
        ],
        ORDINARY["files"],
        DATA_CORE + ["DATA-ISOLATION-AND-LIFECYCLE.md#Blob and signed-reference overreach"],
    ),
    unit(
        "backend/app/auth.py#get_current_user_sse",
        "SSE and upload JWT in query string",
        "backend/app/auth.py#access_token Query",
        "Log or Referer observer versus staff JWT",
        "backend/app",
        "FastAPI core API",
        "ATTACK-CLASSES.md#Cryptography and secrets",
        "Cryptography and secrets",
        ["backend/app/auth.py", "frontend/app/dashboard/page.tsx", "backend/app/main.py"],
        ORDINARY["crypto"],
        WEB_CORE + ["WEB-PROTOCOL-AND-AUTH.md#JWT verification and claim binding"],
    ),
    unit(
        "backend/app/main.py#CORSMiddleware",
        "Credentialed CORS and dashboard token storage",
        "backend/app/config.py#cors_allow_origins",
        "Foreign browser origin versus staff JWT and API",
        "frontend",
        "Next.js dashboard",
        "CLIENT-SIDE.md#Credentialed CORS trust",
        "Credentialed CORS and browser storage",
        [
            "backend/app/main.py",
            "backend/app/config.py",
            "frontend/lib/api.ts",
            "frontend/app/dashboard/page.tsx",
        ],
        ORDINARY["abuse"],
        CLIENT_CORE
        + [
            "CLIENT-SIDE.md#Credentialed CORS trust",
            "CLIENT-SIDE.md#Browser-storage disclosure and stale authorization",
            "CLIENT-SIDE.md#DOM-based XSS",
            "WEB-PROTOCOL-AND-AUTH.md#Ordinary CSRF",
            "WEB-PROTOCOL-AND-AUTH.md#Core discipline",
        ],
    ),
    unit(
        "mobile/lib/queue/sync.ts#upload",
        "Expo capture, queue sync, and signed video open",
        "mobile/lib/secureStorage.ts#SecureStore",
        "On-device peer or stolen phone versus API session",
        "mobile",
        "Expo field app",
        "DESKTOP-MOBILE-AND-LOCAL-IPC.md#Custom-scheme and deep-link ambiguity",
        "Mobile deep link and storage",
        [
            "mobile/lib/queue/sync.ts",
            "mobile/lib/secureStorage.ts",
            "mobile/lib/config.ts",
            "mobile/lib/playVideo.ts",
            "mobile/app.json",
        ],
        ORDINARY["access"],
        MOB_CORE
        + [
            "DESKTOP-MOBILE-AND-LOCAL-IPC.md#Custom-scheme and deep-link ambiguity",
            "DESKTOP-MOBILE-AND-LOCAL-IPC.md#File-open and intent authority confusion",
        ],
    ),
    unit(
        "DEPLOY.md#Railway services",
        "Railway, R2, and injected production secrets",
        "backend/app/config.py#production_must_be_safe",
        "Mis-set env versus public API and worker",
        "infra",
        "Cloud deployment",
        "CLOUD-AND-DEPLOYMENT.md#Unexpected service or management-plane reachability",
        "Cloud reachability and secret attachment",
        ["DEPLOY.md", "backend/app/config.py", "infra/docker/Dockerfile.api", "backend/app/worker.py"],
        ORDINARY["crypto"],
        CLOUD_CORE
        + [
            "CLOUD-AND-DEPLOYMENT.md#Unexpected service or management-plane reachability",
            "CLOUD-AND-DEPLOYMENT.md#Workload identity overreach",
        ],
    ),
    unit(
        "infra/docker/Dockerfile.api#pip install",
        "Docker images, EAS Update, and dependency pins",
        "mobile/eas.json#production channel",
        "Build input versus released API or phone bundle",
        "infra",
        "Supply chain and release",
        "SUPPLY-CHAIN-AND-RELEASE.md#Mutable and unbound build inputs",
        "Supply chain and release",
        [
            "infra/docker/Dockerfile.api",
            "infra/docker/Dockerfile.web",
            "requirements.txt",
            "mobile/eas.json",
            "mobile/app.json",
        ],
        ORDINARY["chain"],
        SUP_CORE
        + [
            "SUPPLY-CHAIN-AND-RELEASE.md#Mutable and unbound build inputs",
            "SUPPLY-CHAIN-AND-RELEASE.md#Build-context inclusion",
        ],
    ),
    unit(
        "backend/app/engagement_routes.py#POST /integrations/engagement/snapshots",
        "Watcher engagement snapshot ingest",
        "backend/app/services/engagement_store.py#item_url",
        "Authenticated Watcher versus stored URLs and later captions",
        "backend/app/services/engagement_store.py",
        "Engagement and Push",
        "ATTACK-CLASSES.md#Injection",
        "Injection",
        [
            "backend/app/engagement_routes.py",
            "backend/app/services/engagement_store.py",
            "backend/app/services/facebook_caption.py",
        ],
        ORDINARY["injection"],
        AI_CORE + ["AI-AND-LLM.md#Indirect injection through retrieved or ingested content"],
    ),
    unit(
        "backend/app/services/meta_graph.py#graph_request",
        "Meta Graph helper URL construction",
        "backend/app/services/meta_graph.py#path startswith http",
        "Caller-controlled path versus server-side fetch",
        "backend/app/services/meta_graph.py",
        "Facebook Page posting",
        "ATTACK-CLASSES.md#Resource and file handling",
        "Resource and file handling",
        ["backend/app/services/meta_graph.py", "backend/app/services/facebook_page.py"],
        ORDINARY["files"],
        CLOUD_CORE + ["CLOUD-AND-DEPLOYMENT.md#Metadata and internal-service reachability"],
    ),
    unit(
        "backend/app/main.py#GET /jobs/{id}/events",
        "Job SSE event stream",
        "backend/app/auth.py#get_current_user_sse",
        "Cross-job or unauthenticated reader versus job progress",
        "backend/app",
        "FastAPI core API",
        "PROTOCOLS-RPC-AND-MESSAGING.md#Per-item and streaming authorization gaps",
        "SSE identity and replay",
        ["backend/app/main.py", "backend/app/auth.py"],
        ORDINARY["access"],
        PROTO_CORE + ["PROTOCOLS-RPC-AND-MESSAGING.md#Per-item and streaming authorization gaps"],
    ),
    unit(
        "main.py#legacy CLI scanner",
        "Repo-root legacy Gemini scanner and sample listings",
        "main.py#API_KEY env",
        "Local operator versus Gemini key and listing JSON",
        "main.py",
        "Legacy CLI",
        "ATTACK-CLASSES.md#Wildcard",
        "Wildcard",
        ["main.py", "listings/SKU_1.json", ".gitignore"],
        ORDINARY["wild"],
        [],
        {"AI-AND-LLM.md#Core discipline": "Legacy CLI is operator-local; Gemini key is env-only in current source."},
    ),
    unit(
        "backend/app/auth.py#oauth2_scheme",
        "Bearer JWT mutations without cookie CSRF token",
        "frontend/lib/api.ts#Authorization header",
        "Cross-site browser versus state-changing API",
        "backend/app",
        "FastAPI core API",
        "WEB-PROTOCOL-AND-AUTH.md#Ordinary CSRF",
        "Ordinary CSRF",
        ["backend/app/auth.py", "backend/app/main.py", "frontend/lib/api.ts", "backend/app/config.py"],
        ORDINARY["access"],
        WEB_CORE + ["WEB-PROTOCOL-AND-AUTH.md#Ordinary CSRF", "CLIENT-SIDE.md#Credentialed CORS trust"],
    ),
    unit(
        "git history and tracked env templates",
        "Committed secrets and git history",
        ".gitignore#secrets",
        "Repo clone versus live credentials",
        "repository",
        "Repository contents",
        "ATTACK-CLASSES.md#Cryptography and secrets",
        "Cryptography and secrets",
        [".gitignore", "backend/.env.example", "mobile/.env.example"],
        ORDINARY["crypto"],
        SUP_CORE + ["SUPPLY-CHAIN-AND-RELEASE.md#Build-context inclusion"],
    ),
]

units.sort(key=lambda u: u["coverage_id"])
out = Path("/workspace/security-audit/2026-10-18/coverage-ledger.json")
out.write_text(json.dumps(units, indent=2) + "\n", encoding="utf-8")
print(f"wrote {len(units)} units to {out}")
for u in units:
    print(u["coverage_id"][:90], "...", u["attack_class"])
