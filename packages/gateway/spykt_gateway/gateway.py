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
     longest-match-first, catches possessives. School names additionally
     derive bare-name variants ("Lincoln High" → "Lincoln") and initialisms
     ("Lincoln High" → "LHS", "West Ridge High School" → "WRHS").
  2. spaCy NER pass (en_core_web_sm) — PERSON/ORG/GPE/FAC/LOC entities not
     already replaced become typed tokens; mappings saved for reversibility.
     Confidence net: PRODUCT/WORK_OF_ART entities that are name-shaped
     (capitalized word sequences) are scrubbed too — the sm model frequently
     mislabels person names (red-team finding, Phase 2).
  3. Heuristic layer — context patterns NER misses: title+name (Ms. Trần),
     initial+surname (R. Feldstein), kinship+name ("her stepdad Victor",
     "jonah is my brother"), lowercase chat names near message verbs
     ("i told rohan", "jae-won texted"), quoted aliases ('goes by "JB"'),
     school-suffix phrases incl. all-lowercase ("cedar falls high"), team
     mascots ("Steel Stallions", "Go Warhawks"), platform-labeled handles
     ("snap is zoet_03"), team numbers, last-four-of-SSN fragments, bare
     street names after location nouns.
  4. Regex layer — emails (incl. linebreak-split and spelled-out "x dot y at
     z dot com" / "(at)" obfuscations), SSNs (dashed or spaced), profile
     URLs (github.com/user), street addresses, US phone numbers (incl.
     linebreak-split and bare 7-digit), @handles, personal domains,
     digit-bearing underscore handles, letter-prefixed IDs (S-221487,
     DC-2027-4415), and standalone 5-9 digit runs (student IDs, ZIPs).
  5. Derived-entity re-scan — every entity surfaced by NER/heuristics (plus
     name parts and school-name variants) is re-scanned case-insensitively
     across the whole text, so bare-surname or lowercase re-mentions after a
     first match cannot survive ("Priya is my sister" → the quoted 'Priya'
     earlier in the essay is caught too).

Over-scrubbing is the accepted failure direction (PRD §7.1: false positives
are cheap, leaks are not); the tests in packages/gateway pin the utility
floor — clearly non-entity common words must survive.

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

# Confidence-net labels: en_core_web_sm often mislabels person names as these
# (red-team finding: "Ines Bouchard" → PRODUCT). Scrubbed only when the entity
# is name-shaped — a sequence of capitalized words.
NER_NET_LABELS = frozenset({"PRODUCT", "WORK_OF_ART"})
_NAME_SHAPED_RE = re.compile(r"[A-Z][\w'’.-]*(?:\s+[A-Z][\w'’.-]*)*\Z")

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
# bare-name variants students actually use ("Lincoln High" → "Lincoln",
# "Liberty North High School" → "Liberty"). Directional/grade words are
# suffixes too: red-team places-010 re-mentions "Liberty North" as bare
# "Liberty".
_SCHOOL_SUFFIXES = frozenset(
    {
        "high", "school", "hs", "academy", "prep", "preparatory", "middle", "elementary",
        "college", "collegiate", "institute", "arts",
        "north", "south", "east", "west", "central", "senior", "junior",
    }
)

_STREET_WORDS = (
    "Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|Place|Pl|Way|"
    "Terrace|Ter|Circle|Cir|Highway|Hwy|Parkway|Pkwy"
)

# Regex layer, applied in order. Email before handle (both contain '@'); SSN
# before phone (both are dashed digit runs); phones before bare digit runs so
# a 10-digit phone is typed PHONE, not ID. Address accepts an already-emitted
# location token as the street body so "123 <PII_FAC_…>" doesn't leak the house
# number next to the token. All layers substitute via the token-span-safe
# helper: a match may fully absorb an earlier token (nested, restore()
# resolves it) but never partially overlaps or re-tokenizes one.
_REGEX_LAYERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Emails, tolerating one linebreak around the '@' (PDF/scan extraction
    # splits them: "f.delacroix\n@stcharlesprep.org").
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+[ \t]*\n?[ \t]*@[ \t]*\n?[ \t]*[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    # Spelled-out / obfuscated emails: "maya dot chen at gmail dot com",
    # "priya.nair(at)gmail" (truncated TLD included — '(at)' is distinctive).
    (
        "EMAIL",
        re.compile(
            r"\b[\w+-]+(?:\s+dot\s+[\w+-]+)+\s+at\s+[\w-]+(?:\s+dot\s+[\w-]+)+\b"
            r"|\b[\w.+-]+\s*(?:\(at\)|\[at\])\s*[\w-]+(?:\s*(?:\(dot\)|\[dot\]|\.)\s*[\w-]+)*\b",
            re.IGNORECASE,
        ),
    ),
    # SSNs, dashed or spoken/spaced ("573 19 0284").
    ("SSN", re.compile(r"(?<!\d)\d{3}[- ]\d{2}[- ]\d{4}(?!\d)")),
    # Profile/repo URLs whose path is a username: github.com/marcusbell7,
    # t.me/anya_v07, twitch.tv/keikoplays.
    (
        "HANDLE",
        re.compile(r"(?<![\w@.])(?:https?://)?(?:www\.)?[A-Za-z0-9-]+(?:\.[A-Za-z]{2,6})+/[A-Za-z0-9_.~/-]+"),
    ),
    (
        "ADDRESS",
        re.compile(
            r"\b\d{1,5}\s+(?:(?:[A-Za-z][\w.]*|PII_[A-Z]+_[0-9a-f]{8})\s+){0,3}"
            rf"(?:{_STREET_WORDS}|PII_[A-Z]+_[0-9a-f]{{8}})\b\.?"
            r"(?:,?\s+(?:Apt|Apartment|Suite|Ste|Unit|#)\.?\s*[\w-]+)?",
            re.IGNORECASE,
        ),
    ),
    # US phones; first separator tolerates a linebreak ("(415)\n555-0182").
    ("PHONE", re.compile(r"(?<![\w.+])(?:\+?1[-. ]?)?\(?\d{3}\)?[-.\s]?\d{3}[-. ]?\d{4}(?![\d-])")),
    # Bare 7-digit local number ("555-0182" alone is still a partial leak).
    ("PHONE", re.compile(r"(?<![\d.(-])\d{3}-\d{4}(?![\d-])")),
    ("HANDLE", re.compile(r"(?<![\w@.])@[A-Za-z0-9_](?:[A-Za-z0-9_.]*[A-Za-z0-9_])?")),
    # Bare personal domains ("niathompson.me") — common portfolio-site leak.
    ("HANDLE", re.compile(r"(?<![\w@.])[A-Za-z0-9][A-Za-z0-9-]{2,}\.(?:com|net|org|io|me|co|dev|app|tv)\b(?![\w./-])")),
    # Digit-bearing underscore usernames with no sigil ("zoet_03").
    ("HANDLE", re.compile(r"\b(?=\w*\d)[A-Za-z][A-Za-z0-9.]*_[A-Za-z0-9_.]*[A-Za-z0-9]\b")),
    # Letter-prefixed identifiers: district/student IDs ("S-221487"),
    # initials+year logins ("DC-2027-4415"). "W-2" stays (needs ≥2 digits).
    ("ID", re.compile(r"\b[A-Z]{1,4}-\d{2,6}(?:-\d{2,6})+\b|\b[A-Z]{1,3}-\d{5,8}\b")),
    # Standalone 5-9 digit runs: student IDs, lunch codes, ZIPs. Years (4
    # digits) and 10+ digit runs (phones, caught above) are excluded.
    ("ID", re.compile(r"(?<![\d.-])\d{5,9}(?!\d)(?!\.\d)")),
)

# -- heuristic layer (context patterns NER misses) ----------------------------

# A capitalized word that is not one of our emitted tokens.
_CAP = r"(?!PII_|Student-)[A-Z][\w'’-]+"

_DAYS_MONTHS = frozenset(
    {
        "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
        "january", "february", "march", "april", "may", "june", "july", "august",
        "september", "october", "november", "december", "thanksgiving", "christmas",
        "halloween", "easter",
    }
)
_PRONOUNISH = frozenset(
    {
        "i", "me", "you", "he", "she", "it", "we", "they", "him", "her", "them", "us",
        "who", "that", "this", "which", "what", "everyone", "someone", "anyone",
        "nobody", "people", "name", "and", "one", "guy", "girl", "kid",
    }
)
_CHAT_NAME_STOPWORDS = _PRONOUNISH | frozenset(
    {
        "mom", "dad", "mother", "father", "coach", "teacher", "counselor", "advisor",
        "already", "also", "just", "then", "never", "finally", "literally", "basically",
        "again", "back", "about", "everybody", "myself", "himself", "herself",
    }
)
_HANDLE_LABEL_STOPWORDS = frozenset(
    {
        "not", "the", "my", "your", "his", "her", "our", "their", "a", "an", "on", "in",
        "is", "was", "it", "that", "this", "public", "private", "new", "old", "gone",
        "deleted", "taken", "required", "invalid", "down", "up", "off", "dead",
        "server", "group", "channel", "chat", "call", "mod", "and", "but", "for",
    }
)

# Determiner-ish words that must not start a school-name phrase.
_SCHOOL_DET = r"(?:The|His|Her|My|Our|Their|Its|A|An|This|That|These|Those|Junior|Senior|New|Old|Every|Some|Any)"
_SCHOOL_SUFFIX_ALT = r"(?:High\s+School|Middle\s+School|High|Academy|Prep(?:aratory)?|Collegiate|Institute)"
# Words that must not start an all-lowercase school phrase ("junior high",
# "getting high", "tour cedar falls high" must anchor at "cedar").
_LOWER_SCHOOL_STOP = (
    r"(?:the|his|her|my|our|their|its|a|an|this|that|junior|senior|new|old|current|former|"
    r"so|too|very|really|super|pretty|kinda|sorta|how|why|is|was|are|were|be|been|being|"
    r"get|gets|got|getting|feel|feels|felt|score|scored|scoring|aim|aims|aimed|aiming|"
    r"ride|rides|riding|rode|fly|flying|flew|stay|stays|stayed|and|or|of|in|on|at|to|"
    r"for|from|with|tour|tours|toured|visit|visits|visited|shadow|attend|attends|attended)"
)
_KIN = (
    r"(?:mom|mother|dad|father|stepdad|stepmom|stepfather|stepmother|brother|sister|"
    r"aunt|uncle|cousin|grandma|grandpa|grandmother|grandfather|guardian|nephew|niece)"
)

# (kind, pattern, stopwords-for-captured-value); group 1 is what gets scrubbed.
_HEURISTIC_RULES: tuple[tuple[str, re.Pattern[str], frozenset[str]], ...] = (
    # Title + name: "Ms. Trần", "Mr. D. Abernathy", "Coach Ramirez".
    (
        "PERSON",
        re.compile(
            rf"\b(?:Mr|Ms|Mrs|Dr|Prof|Professor|Coach|Principal|Counselor|Miss|Mx)\.?\s+"
            rf"((?:[A-Z]\.\s+)*{_CAP}(?:\s+{_CAP})?)"
        ),
        _DAYS_MONTHS,
    ),
    # Abbreviated first name + surname: "R. Feldstein".
    ("PERSON", re.compile(rf"\b([A-Z]\.\s+{_CAP})"), frozenset()),
    # Kinship word + capitalized name: "my mom Marisol", "her stepdad Victor".
    ("PERSON", re.compile(rf"\b{_KIN}\s+({_CAP})\b"), _DAYS_MONTHS | _PRONOUNISH),
    # "<name> is my (little) sister/brother/…" — catches lowercase chat names
    # ("jonah is my brother"); the re-scan then catches earlier quoted mentions.
    (
        "PERSON",
        re.compile(
            rf"\b((?!PII_|Student-)[\w'’-]+)\s+is\s+(?:my|his|her|their)\s+"
            rf"(?:little\s+|big\s+|younger\s+|older\s+|step|half-)*{_KIN}\b",
            re.IGNORECASE,
        ),
        _PRONOUNISH,
    ),
    # Lowercase chat-register names adjacent to messaging verbs:
    # "i told rohan about it", "jae-won texted me".
    (
        "PERSON",
        re.compile(r"\b(?:told|texted|messaged|dm'd|dmed)\s+([a-z][a-z]+(?:-[a-z]+)*)\b"),
        _CHAT_NAME_STOPWORDS,
    ),
    (
        "PERSON",
        re.compile(r"\b([a-z][a-z]+(?:-[a-z]+)*)\s+(?:texted|told|messaged|dm'd|dmed)\b"),
        _CHAT_NAME_STOPWORDS,
    ),
    # Quoted alias: 'goes by "JB"'.
    (
        "NAME",
        re.compile(r"\b(?:goes\s+by|known\s+as)\s+[\"'“‘]([^\"'”’\n]{1,25})[\"'”’]", re.IGNORECASE),
        frozenset(),
    ),
    # Capitalized school-suffix phrases NER misses: "Mount Rainier Lutheran
    # High", "Westbrook High", "Oakcrest Collegiate".
    (
        "SCHOOL",
        re.compile(
            rf"(?<![\w'’-])((?:(?!{_SCHOOL_DET}\b)(?!PII_|Student-)[A-Z][\w'’-]+\s+){{1,4}}"
            rf"{_SCHOOL_SUFFIX_ALT}(?:\s+School)?)(?![\w'’-])"
        ),
        frozenset(),
    ),
    # All-lowercase school phrases from casual notes: "cedar falls high".
    (
        "SCHOOL",
        re.compile(
            rf"(?<![\w'’-])((?:(?!{_LOWER_SCHOOL_STOP}\b)[a-z][\w'’-]*\s+){{1,3}}"
            rf"(?:high\s+school|high))(?![\w'’-])"
        ),
        frozenset(),
    ),
    # Team/mascot names: capitalized phrase ending in a plural ("Steel
    # Stallions", "Circuit Breakers") — independently identifying (§7).
    (
        "ORG",
        re.compile(
            r"(?<![\w'’-])((?:(?!PII_|Student-)(?!(?:The|His|Her|My|Our|Their|These|Those)\b)"
            r"[A-Z][a-z][\w'’-]*\s+){1,2}(?!PII_|Student-)[A-Z][a-z][A-Za-z-]*s)(?![\w'’-])"
        ),
        frozenset(),
    ),
    # Cheer form: "Go Warhawks".
    ("ORG", re.compile(rf"\bGo\s+({_CAP})"), frozenset()),
    # Team numbers are publicly searchable to one school ("FIRST Robotics
    # Team 4817"). NER may already have absorbed the team name into an
    # ORG token ("FIRST Robotics Team" → PII_ORG_…), so an emitted ORG/SCHOOL
    # token is an equally valid anchor for the trailing number.
    (
        "ID",
        re.compile(
            r"\b(?:[Tt]eam|[Tt]roop|[Ss]quad|PII_(?:ORG|SCHOOL)_[0-9a-f]{8})\s+#?(\d{2,6})\b"
        ),
        frozenset(),
    ),
    # "last four of my social … 8213" — SSN fragment with only context cues.
    ("ID", re.compile(r"\blast\s+(?:four|4)\b[^.\n]{0,40}?(?<!\d)(\d{4})(?!\d)", re.IGNORECASE), frozenset()),
    # Platform-labeled handles with no sigil: "insta: ethanv.shoots",
    # "my snap is zoet_03", "his tag is kdraws22".
    (
        "HANDLE",
        re.compile(
            r"\b(?:insta(?:gram)?|snap(?:chat)?|discord|tiktok|twitch|telegram|github|"
            r"gamertag|tag|handle|username|ig)\s*(?:name)?\s*(?:is|:|=)?\s+"
            r"@?([A-Za-z][A-Za-z0-9_.]*[A-Za-z0-9_])\b",
            re.IGNORECASE,
        ),
        _HANDLE_LABEL_STOPWORDS,
    ),
    # Bare street name after a location noun: "the branch library on Kessler".
    (
        "ADDRESS",
        re.compile(
            rf"\b(?:library|branch|corner|house|apartment|building|office|store|caf[eé]|shop|park)\s+"
            rf"on\s+({_CAP})\b"
        ),
        _DAYS_MONTHS,
    ),
)

# Values never worth re-scanning for (school-variant leftovers, particles).
_DERIVED_STOPWORDS = _DAYS_MONTHS | _PRONOUNISH | _SCHOOL_SUFFIXES | frozenset(
    {"van", "von", "der", "de", "la", "del", "los", "las", "san", "saint", "mount", "lake", "fort"}
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


def _school_abbreviations(name: str) -> list[str]:
    """Initialisms of a school name ('West Ridge High School' → 'WRHS', 'Lincoln High' → 'LHS').

    Students and staff routinely abbreviate ("the WRHS thread", "CHS lets
    seniors leave early"); the abbreviation pins the school as surely as the
    name. Two-letter forms are skipped (collision surface too large).
    """
    words = [w for w in name.split() if w[:1].isalpha()]
    if len(words) < 2:
        return []
    initials = "".join(w[0] for w in words).upper()
    out: list[str] = []
    if len(initials) >= 3:
        out.append(initials)
    if words[-1].rstrip(".").lower() != "school" and len(initials) + 1 >= 3:
        out.append(initials + "S")  # "Lincoln High" is spoken as "LHS"
    return out


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
            for value in (school, *_school_variants(school), *_school_abbreviations(school)):
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
        # `derived` collects every entity surfaced by NER/heuristics (lowercased
        # value → (kind, first-seen surface form)) for the final whole-text
        # re-scan: bare-surname / case-variant re-mentions must not survive.
        derived: dict[str, tuple[str, str]] = {}
        text = self._apply_known_entities(record, text, known, replacements)
        text = self._apply_ner(record, text, replacements, derived)
        text = self._apply_heuristics(record, text, replacements, derived)
        text = self._apply_regex_layer(record, text, replacements)
        text = self._apply_derived_rescan(record, text, derived, replacements)
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
        self,
        record: PseudonymRecord,
        text: str,
        replacements: list[dict[str, str]],
        derived: dict[str, tuple[str, str]],
    ) -> str:
        doc = self._get_nlp()(text)
        token_spans = [(m.start(), m.end()) for m in TOKEN_RE.finditer(text)]
        pieces: list[str] = []
        cursor = 0
        for ent in doc.ents:
            if ent.start_char < cursor:
                continue
            if ent.label_ in NER_LABELS:
                kind = ent.label_
            elif ent.label_ in NER_NET_LABELS and _NAME_SHAPED_RE.fullmatch(ent.text):
                # Confidence net: sm-model mislabels of name-shaped spans
                # ("Ines Bouchard" → PRODUCT) are scrubbed as PERSON.
                kind = "PERSON"
            else:
                continue
            # Never rewrite inside/around a token we already emitted.
            if any(ent.start_char < end and ent.end_char > start for start, end in token_spans):
                continue
            # Contact-shaped strings (@handles, emails) belong to the regex
            # layer, which types them correctly (HANDLE/EMAIL, stable tokens).
            if "@" in ent.text:
                continue
            token = derive_token(record.salt, kind, ent.text)
            self._store.save_token_mapping(record.student_id, token, ent.text)
            replacements.append({"kind": kind, "token": token})
            self._register_derived(derived, kind, ent.text)
            pieces.append(text[cursor : ent.start_char])
            pieces.append(token)
            cursor = ent.end_char
        pieces.append(text[cursor:])
        return "".join(pieces)

    def _apply_heuristics(
        self,
        record: PseudonymRecord,
        text: str,
        replacements: list[dict[str, str]],
        derived: dict[str, tuple[str, str]],
    ) -> str:
        for kind, pattern, stopwords in _HEURISTIC_RULES:
            text = self._substitute(
                record, text, pattern, kind, replacements,
                group=1, stopwords=stopwords, derived=derived,
            )
        return text

    def _apply_regex_layer(
        self, record: PseudonymRecord, text: str, replacements: list[dict[str, str]]
    ) -> str:
        for kind, pattern in _REGEX_LAYERS:
            text = self._substitute(record, text, pattern, kind, replacements)
        return text

    def _substitute(
        self,
        record: PseudonymRecord,
        text: str,
        pattern: re.Pattern[str],
        kind: str,
        replacements: list[dict[str, str]],
        *,
        group: int = 0,
        stopwords: frozenset[str] = frozenset(),
        derived: dict[str, tuple[str, str]] | None = None,
    ) -> str:
        """Token-span-safe substitution of `pattern`'s `group` with typed tokens.

        A match may fully absorb an already-emitted token (nested; restore()
        iterates to a fixpoint) but is skipped if it partially overlaps one or
        IS one — a pattern must never re-tokenize or corrupt an existing token.
        """
        token_spans = [(m.start(), m.end()) for m in TOKEN_RE.finditer(text)]
        picked: list[tuple[int, int, str]] = []
        for match in pattern.finditer(text):
            start, end = match.span(group)
            if start < 0 or start == end:
                continue
            value = match.group(group)
            if value.strip().lower() in stopwords or TOKEN_RE.fullmatch(value):
                continue
            if any(
                ts < end and te > start and not (start <= ts and te <= end)
                for ts, te in token_spans
            ):
                continue
            picked.append((start, end, value))
        if not picked:
            return text
        pieces: list[str] = []
        cursor = 0
        for start, end, value in picked:
            if start < cursor:
                continue
            token = derive_token(record.salt, kind, value)
            self._store.save_token_mapping(record.student_id, token, value)
            replacements.append({"kind": kind, "token": token})
            if derived is not None:
                self._register_derived(derived, kind, value)
            pieces.append(text[cursor:start])
            pieces.append(token)
            cursor = end
        pieces.append(text[cursor:])
        return "".join(pieces)

    @staticmethod
    def _register_derived(derived: dict[str, tuple[str, str]], kind: str, value: str) -> None:
        """Register a discovered entity (and its useful sub-forms) for the re-scan."""
        low = value.lower()
        if len(low) >= 3 and low not in _DERIVED_STOPWORDS:
            derived.setdefault(low, (kind, value))
        # Bare-surname re-mention after a full-name match: register each part.
        if kind == "PERSON":
            for part in value.split():
                part = part.strip(".,'’-")
                if len(part) >= 3 and part.lower() not in _DERIVED_STOPWORDS:
                    derived.setdefault(part.lower(), ("PERSON", part))
        # School/org phrases re-mentioned bare: "cedar falls high" → "cedar falls".
        if kind in ("SCHOOL", "ORG"):
            for variant in _school_variants(value):
                if variant.lower() not in _DERIVED_STOPWORDS:
                    derived.setdefault(variant.lower(), (kind, variant))

    def _apply_derived_rescan(
        self,
        record: PseudonymRecord,
        text: str,
        derived: dict[str, tuple[str, str]],
        replacements: list[dict[str, str]],
    ) -> str:
        """Case-insensitive whole-text re-scan of every derived entity value.

        Catches re-mentions the position-bound layers missed: a quoted first
        mention ("'Priya wouldn't look at me'") once a later pattern
        identified "Priya", lowercase re-mentions, bare surnames.
        """
        if not derived:
            return text
        ordered = sorted(derived, key=len, reverse=True)
        pattern = re.compile(
            "|".join(rf"(?<!\w){re.escape(value)}(?!\w)" for value in ordered), re.IGNORECASE
        )

        def repl(match: re.Match[str]) -> str:
            kind, original = derived[match.group(0).lower()]
            token = derive_token(record.salt, kind, original)
            self._store.save_token_mapping(record.student_id, token, original)
            replacements.append({"kind": kind, "token": token})
            return token

        return pattern.sub(repl, text)
