"""Pseudonymization Gateway v1 (01-TECH_SPEC §7, PRD §7.1 — compliance-critical).

In-process library on the Anthropic client path. Raw student-identifiable content
never reaches claude-fable-5: every outbound payload is scrubbed here, and the
resulting attestation is what `require_pseudonymized()` in spykt-anthropic-client
demands before a Fable call is allowed.

Outbound pipeline (in order):
  0. Parent-field stripping (payloads only, §7 Outbound: "payloads to Fable
     additionally strip parent-provided free-text fields unless whitelisted") —
     fields identified as parent-provided are replaced wholesale with
     `PARENT_FIELD_STRIPPED` before any content is inspected, unless their
     dotted path is whitelisted (whitelisted fields still go through the full
     scrub pipeline — the whitelist never bypasses scrubbing). Identification:
     caller-declared paths/keys (`parent_fields=`) UNION a key-name convention
     (any snake-case key containing a `parent`/`parents` segment). The stripped
     paths are recorded in the scrub report (hashed into the attestation).
  1. Known-entity replacement — stable deterministic tokens derived from the
     student's salt (HMAC-SHA256, truncated). The student's own name maps to
     their stable pseudonym. Case-insensitive, word-boundary aware,
     longest-match-first, catches possessives.
  2. spaCy NER pass (en_core_web_sm) — PERSON/ORG/GPE/FAC/LOC entities not
     already replaced become typed tokens; mappings saved for reversibility.
  3. Regex layer — emails, US phone numbers, street addresses, @handles,
     SSN-like patterns.

Scrub-or-refuse: `scrub_payload()` never attests over content the pipeline did
not inspect. Dict keys are scrubbed like values; numeric leaves whose rendering
matches a PII pattern (e.g. a phone number stored as an int), non-string dict
keys, and unsupported leaf types raise `GatewayScrubError` instead of passing
through silently. Callers must not catch-and-continue to Fable; the Orchestrator
degrade-to-Opus path (PRD §7.1) is the recovery route.

Inbound: `restore()` re-substitutes tokens from the store before persistence/UI.
Restore is canonicalizing, not byte-exact, for case-variant surface forms:
known-entity matching is case-insensitive but one token maps to one stored
original, so "MAYA CHEN yelled." restores as "Maya Chen yelled." (regression
test pins this before the Phase 2 red-team suite).

Residual-risk note (honest, per spec): NER is not perfect; Fable request-body
logging stays off regardless. The Phase 2 red-team suite (200+ cases) is the
gate; the tests here are the starter adversarial set.
"""

import hashlib
import hmac
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from spykt_gateway.store import PseudonymRecord, PseudonymStore

# NER labels we scrub (01-TECH_SPEC §7 outbound).
NER_LABELS = frozenset({"PERSON", "ORG", "GPE", "FAC", "LOC"})

# Marker substituted for a stripped parent-provided free-text field (§7 Outbound).
# Kept as a visible constant (not a deletion) so downstream consumers can see the
# field existed and was withheld — observable, per prime directive 3.
PARENT_FIELD_STRIPPED = "[PARENT_FIELD_STRIPPED]"

# Key-name convention for auto-detecting parent-provided fields: any key with a
# whole `parent`/`parents` segment ("parent_note", "note_from_parent"), never a
# mere substring ("parenting_style" does not match). Callers additionally declare
# fields via `parent_fields=`; the convention is defense in depth, not the API.
_PARENT_KEY_RE = re.compile(r"(?:^|[_\W])parents?(?:[_\W]|$)", re.IGNORECASE)

# Matches any token this gateway emits (typed PII tokens + stable pseudonyms).
TOKEN_RE = re.compile(r"PII_[A-Z]+_[0-9a-f]{8}|Student-[0-9a-f]{8}")

# Trailing words commonly attached to school names; stripping them yields the
# bare-name variants students actually use ("Lincoln High" → "Lincoln").
_SCHOOL_SUFFIXES = frozenset(
    {"high", "school", "hs", "academy", "prep", "preparatory", "middle", "elementary", "college", "institute"}
)

_STREET_WORDS = (
    "Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|Place|Pl|Way|"
    "Terrace|Ter|Circle|Cir|Highway|Hwy|Parkway|Pkwy"
)

# Regex layer, applied in order. Email before handle (both contain '@'); SSN
# before phone (both are dashed digit runs). Address accepts an already-emitted
# location token as the street body so "123 <PII_FAC_…>" doesn't leak the house
# number next to the token.
_REGEX_LAYERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("SSN", re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")),
    (
        "ADDRESS",
        re.compile(
            r"\b\d{1,5}\s+(?:(?:[A-Za-z][\w.]*|PII_[A-Z]+_[0-9a-f]{8})\s+){0,3}"
            rf"(?:{_STREET_WORDS}|PII_[A-Z]+_[0-9a-f]{{8}})\b\.?",
            re.IGNORECASE,
        ),
    ),
    ("PHONE", re.compile(r"(?<![\w.+])(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]?\d{3}[-. ]?\d{4}(?![\d-])")),
    ("HANDLE", re.compile(r"(?<![\w@.])@[A-Za-z0-9_](?:[A-Za-z0-9_.]*[A-Za-z0-9_])?")),
)

_KNOWN_LIST_KEYS: tuple[tuple[str, str], ...] = (
    ("nicknames", "NAME"),
    ("family_names", "FAMILY"),
    ("emails", "EMAIL"),
    ("phones", "PHONE"),
    ("handles", "HANDLE"),
)

_MAX_RESTORE_PASSES = 5

_DEFAULT_NLP: Any = None


def _load_default_nlp() -> Any:
    """Load en_core_web_sm once per process (slow; NER-only components)."""
    global _DEFAULT_NLP
    if _DEFAULT_NLP is None:
        import spacy

        _DEFAULT_NLP = spacy.load(
            "en_core_web_sm", disable=["tagger", "parser", "attribute_ruler", "lemmatizer"]
        )
    return _DEFAULT_NLP


def derive_token(salt: str, kind: str, value: str) -> str:
    """Stable deterministic token for (student salt, kind, value) — HMAC-SHA256 truncated."""
    digest = hmac.new(salt.encode(), f"{kind}:{value.lower()}".encode(), hashlib.sha256).hexdigest()[:8]
    return f"PII_{kind}_{digest}"


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [v for v in value if isinstance(v, str) and v.strip()]


def _school_variants(name: str) -> list[str]:
    """Bare-name variants of a school ('Lincoln High School' → 'Lincoln High', 'Lincoln')."""
    variants: list[str] = []
    words = name.split()
    while len(words) > 1 and words[-1].rstrip(".").lower() in _SCHOOL_SUFFIXES:
        words = words[:-1]
        candidate = " ".join(words)
        if len(candidate) >= 3:
            variants.append(candidate)
    return variants


class GatewayScrubError(RuntimeError):
    """Raised when a payload contains content the scrub pipeline cannot inspect.

    Attesting `pseudonymized=True` over uninspected content would defeat the
    retention gate (01-TECH_SPEC §4.1.4), so the gateway refuses loudly instead.
    Recovery is the Orchestrator's degrade-to-Opus path (PRD §7.1), never
    catch-and-continue to Fable.
    """


@dataclass(frozen=True)
class ScrubResult:
    """Scrubbed text plus the report and attestation the retention gate requires."""

    text: str
    report: dict[str, Any]
    attestation: dict[str, Any]


def _report_and_attestation(
    replacements: list[dict[str, str]], stripped_parent_fields: list[str] | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    counts: dict[str, int] = {}
    for entry in replacements:
        counts[entry["kind"]] = counts.get(entry["kind"], 0) + 1
    report = {
        "replacements": replacements,
        "counts": counts,
        "stripped_parent_fields": stripped_parent_fields or [],
    }
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":"))
    attestation = {
        "pseudonymized": True,
        "scrub_report_hash": hashlib.sha256(canonical.encode()).hexdigest(),
    }
    return report, attestation


class PseudonymizationGateway:
    """Outbound scrub / inbound restore around every Fable-bound payload."""

    def __init__(
        self,
        store: PseudonymStore,
        nlp: Any = None,
        parent_field_whitelist: Iterable[str] = (),
    ) -> None:
        """`parent_field_whitelist`: dotted paths (list indices omitted) of parent-provided
        fields allowed through to Fable — still fully scrubbed, never raw (§7 Outbound).
        This is the config surface; deployments set it once, callers may extend per call.
        """
        self._store = store
        self._nlp = nlp
        self._parent_field_whitelist = frozenset(parent_field_whitelist)

    # -- public API ---------------------------------------------------------

    def scrub(
        self,
        student_id: str,
        text: str,
        known_entities: dict[str, Any] | None = None,
    ) -> ScrubResult:
        """Scrub one string; returns text + report + attestation."""
        record = self._record_for(student_id)
        known = self._compile_known_entities(record, known_entities)
        replacements: list[dict[str, str]] = []
        scrubbed = self._scrub_text(record, text, known, replacements)
        report, attestation = _report_and_attestation(replacements)
        return ScrubResult(text=scrubbed, report=report, attestation=attestation)

    def scrub_payload(
        self,
        student_id: str,
        payload: dict[str, Any],
        known_entities: dict[str, Any] | None = None,
        *,
        parent_fields: Iterable[str] = (),
        parent_field_whitelist: Iterable[str] = (),
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Recursively scrub `payload` (keys and values); returns (payload, attestation).

        Parent-provided free-text fields are stripped unless whitelisted (§7 Outbound):
        - `parent_fields`: caller-declared parent-provided fields — each entry matches a
          full dotted path ("meta.parent_note", list indices omitted) or a bare key name.
          Keys with a `parent`/`parents` snake segment are auto-detected regardless.
        - `parent_field_whitelist`: dotted paths (exact match only — deliberately narrower
          than detection) allowed through; they are still fully scrubbed, never raw.
          Unioned with the constructor whitelist.

        Raises GatewayScrubError for anything the pipeline cannot inspect: non-string
        dict keys, numeric leaves matching a PII pattern, unsupported leaf types, or a
        post-scrub key collision. Never attests over uninspected content.
        """
        record = self._record_for(student_id)
        known = self._compile_known_entities(record, known_entities)
        replacements: list[dict[str, str]] = []
        stripped: list[str] = []
        declared = frozenset(parent_fields)
        whitelist = self._parent_field_whitelist | frozenset(parent_field_whitelist)

        def walk(node: Any, path: str) -> Any:
            if isinstance(node, str):
                return self._scrub_text(record, node, known, replacements)
            if isinstance(node, dict):
                out: dict[str, Any] = {}
                for key, value in node.items():
                    if not isinstance(key, str):
                        raise GatewayScrubError(
                            f"Refusing to attest payload: non-string dict key {key!r} at "
                            f"{path or '<root>'!r} cannot be inspected by the scrub pipeline."
                        )
                    child = f"{path}.{key}" if path else key
                    # Keys are content too: scrub them like values (dict keys can carry PII).
                    scrubbed_key = self._scrub_text(record, key, known, replacements)
                    if scrubbed_key in out:
                        raise GatewayScrubError(
                            f"Refusing to attest payload: keys at {path or '<root>'!r} collide "
                            f"after scrubbing ({scrubbed_key!r}); one branch would be lost silently."
                        )
                    is_parent = child in declared or key in declared or _PARENT_KEY_RE.search(key)
                    if is_parent and child not in whitelist:
                        stripped.append(child)
                        out[scrubbed_key] = PARENT_FIELD_STRIPPED
                        continue
                    out[scrubbed_key] = walk(value, child)
                return out
            if isinstance(node, list):
                return [walk(item, path) for item in node]
            if isinstance(node, tuple):
                return tuple(walk(item, path) for item in node)
            if isinstance(node, bool) or node is None:
                return node
            if isinstance(node, int | float):
                rendered = str(node)
                for kind, pattern in _REGEX_LAYERS:
                    if pattern.search(rendered):
                        raise GatewayScrubError(
                            f"Refusing to attest payload: numeric value at {path or '<root>'!r} "
                            f"matches the {kind} PII pattern; send it as a string so the pipeline "
                            "can tokenize it, or drop it."
                        )
                return node
            raise GatewayScrubError(
                f"Refusing to attest payload: unsupported leaf type "
                f"{type(node).__name__!r} at {path or '<root>'!r} cannot be inspected."
            )

        scrubbed = walk(payload, "")
        _, attestation = _report_and_attestation(replacements, stripped)
        return scrubbed, attestation

    def restore(self, student_id: str, text: str) -> str:
        """Inbound re-substitution: replace tokens with stored originals before persistence/UI."""

        def repl(match: re.Match[str]) -> str:
            original = self._store.lookup_token(student_id, match.group(0))
            return original if original is not None else match.group(0)

        # Iterate to a fixpoint: an ADDRESS token's original may itself contain
        # a nested location token.
        for _ in range(_MAX_RESTORE_PASSES):
            restored = TOKEN_RE.sub(repl, text)
            if restored == text:
                break
            text = restored
        return text

    # -- pipeline internals ---------------------------------------------------

    def _record_for(self, student_id: str) -> PseudonymRecord:
        return self._store.get(student_id) or self._store.create(student_id)

    def _get_nlp(self) -> Any:
        if self._nlp is None:
            self._nlp = _load_default_nlp()
        return self._nlp

    def _compile_known_entities(
        self, record: PseudonymRecord, known_entities: dict[str, Any] | None
    ) -> tuple[re.Pattern[str], dict[str, tuple[str, str, str]]] | None:
        """Build the longest-match-first pattern and value→(kind, token, original) lookup."""
        known = known_entities or {}
        entries: list[tuple[str, str, str]] = []  # (value, kind, token)

        for name in _as_list(known.get("name")):
            # The student's own name maps to their stable pseudonym.
            entries.append((name, "NAME", record.pseudonym))
            # Name parts ("Maya Chen" → "Maya", "Chen") get their own stable tokens
            # so each surface form restores exactly.
            for part in name.split():
                if len(part) > 1 and part.lower() != name.lower():
                    entries.append((part, "NAME", derive_token(record.salt, "NAME", part)))

        for key, kind in _KNOWN_LIST_KEYS:
            for value in _as_list(known.get(key)):
                entries.append((value, kind, derive_token(record.salt, kind, value)))

        for school in _as_list(known.get("school")):
            for value in (school, *_school_variants(school)):
                entries.append((value, "SCHOOL", derive_token(record.salt, "SCHOOL", value)))

        for city in _as_list(known.get("city")):
            entries.append((city, "CITY", derive_token(record.salt, "CITY", city)))

        lookup: dict[str, tuple[str, str, str]] = {}
        for value, kind, token in entries:
            lookup.setdefault(value.lower(), (kind, token, value))
        if not lookup:
            return None

        # Longest-match-first so "Lincoln High" wins over bare "Lincoln".
        # Lookarounds instead of \b so values starting with non-word chars
        # (e.g. "@handle") still anchor correctly.
        ordered = sorted(lookup, key=len, reverse=True)
        pattern = re.compile(
            "|".join(rf"(?<!\w){re.escape(value)}(?!\w)" for value in ordered), re.IGNORECASE
        )
        return pattern, lookup

    def _scrub_text(
        self,
        record: PseudonymRecord,
        text: str,
        known: tuple[re.Pattern[str], dict[str, tuple[str, str, str]]] | None,
        replacements: list[dict[str, str]],
    ) -> str:
        text = self._apply_known_entities(record, text, known, replacements)
        text = self._apply_ner(record, text, replacements)
        text = self._apply_regex_layer(record, text, replacements)
        return text

    def _apply_known_entities(
        self,
        record: PseudonymRecord,
        text: str,
        known: tuple[re.Pattern[str], dict[str, tuple[str, str, str]]] | None,
        replacements: list[dict[str, str]],
    ) -> str:
        if known is None:
            return text
        pattern, lookup = known

        def repl(match: re.Match[str]) -> str:
            kind, token, original = lookup[match.group(0).lower()]
            self._store.save_token_mapping(record.student_id, token, original)
            replacements.append({"kind": kind, "token": token})
            return token

        return pattern.sub(repl, text)

    def _apply_ner(
        self, record: PseudonymRecord, text: str, replacements: list[dict[str, str]]
    ) -> str:
        doc = self._get_nlp()(text)
        token_spans = [(m.start(), m.end()) for m in TOKEN_RE.finditer(text)]
        pieces: list[str] = []
        cursor = 0
        for ent in doc.ents:
            if ent.label_ not in NER_LABELS or ent.start_char < cursor:
                continue
            # Never rewrite inside/around a token we already emitted.
            if any(ent.start_char < end and ent.end_char > start for start, end in token_spans):
                continue
            # Contact-shaped strings (@handles, emails) belong to the regex
            # layer, which types them correctly (HANDLE/EMAIL, stable tokens).
            if "@" in ent.text:
                continue
            token = derive_token(record.salt, ent.label_, ent.text)
            self._store.save_token_mapping(record.student_id, token, ent.text)
            replacements.append({"kind": ent.label_, "token": token})
            pieces.append(text[cursor : ent.start_char])
            pieces.append(token)
            cursor = ent.end_char
        pieces.append(text[cursor:])
        return "".join(pieces)

    def _apply_regex_layer(
        self, record: PseudonymRecord, text: str, replacements: list[dict[str, str]]
    ) -> str:
        for kind, pattern in _REGEX_LAYERS:
            def repl(match: re.Match[str], kind: str = kind) -> str:
                value = match.group(0)
                token = derive_token(record.salt, kind, value)
                self._store.save_token_mapping(record.student_id, token, value)
                replacements.append({"kind": kind, "token": token})
                return token

            text = pattern.sub(repl, text)
        return text
