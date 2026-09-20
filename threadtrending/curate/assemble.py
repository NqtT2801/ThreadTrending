"""Beam search that turns scored comments into a narrative sequence.

The objective has two halves:

    maximise   sum(role_fit)  +  lam * sum(cohesion between neighbours)

``role_fit`` asks "is this a good hook / explanation / punchline?".
``cohesion`` asks "does this read as a response to the one before it?".
Optimising only the first gives a slideshow of individually-good comments;
the second is what makes the result feel like people talking to each other.

One structural rule does most of the heavy lifting: after the hook is chosen,
its own descendants in the reply tree are boosted, because a real reply to the
hook *is*, definitionally, a comment explaining the hook.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Sequence

from ..models import Chain, Comment, Role, Slot
from . import structure as st
from .scoring import CorpusStats, Features, estimate_seconds, features
from .tts_text import normalize_for_tts

log = logging.getLogger(__name__)

#: Inter-segment padding assumed while budgeting. Kept in sync with render.timeline.
GAP_S = 0.30
LEAD_TAIL_S = 1.80

#: A hook must never paginate -- a two-card hook kills the opening beat.
HOOK_MAX_SYLLABLES = 30

MIN_ITEMS, MAX_ITEMS = 6, 12
DEFAULT_BEAM = 12
DEFAULT_LAMBDA = 0.9
#: Per-step candidate fan-out. Beyond this the tail is never competitive.
FANOUT = 40

ROLE_FIT = {
    "hook": lambda f: 0.70 * f.shock + 0.10 * f.engagement + 0.20 * f.brevity,
    "context": lambda f: 0.55 * f.explain + 0.25 * f.entity + 0.20 * f.engagement,
    "explain": lambda f: 0.75 * f.explain + 0.15 * f.engagement + 0.10 * f.depth_bonus,
    "counter": lambda f: 0.70 * f.counter + 0.20 * f.explain + 0.10 * f.engagement,
    "reaction": lambda f: 0.60 * f.reaction + 0.25 * f.brevity + 0.15 * f.engagement,
    "punchline": lambda f: 0.40 * f.reaction + 0.25 * f.shock + 0.25 * f.engagement
    + 0.10 * f.brevity,
}

#: Boosts applied on top of role_fit.
HOOK_DESCENDANT_BONUS = 0.45
CHAIN_MEMBER_BONUS = 0.20


@dataclass
class Candidate:
    comment: Comment
    feats: Features

    @property
    def pk(self) -> str:
        return self.comment.pk


@dataclass
class _State:
    picks: tuple[Candidate, ...]
    used: frozenset[str]
    used_authors: tuple[str, ...]
    seconds: float
    score: float


def plan_roles(n_items: int) -> list[Role]:
    """A concrete slot plan of exactly ``n_items`` roles.

    Shape: HOOK -> [CONTEXT] -> EXPLAIN*k -> [COUNTER] -> REACTION*m -> PUNCHLINE
    """
    n = max(3, min(MAX_ITEMS, n_items))
    roles: list[Role] = ["hook"]
    remaining = n - 2  # hook and punchline are always spent

    if remaining > 0:
        roles.append("context")
        remaining -= 1

    n_reaction = 1 if remaining >= 3 else 0
    n_counter = 1 if remaining >= 4 else 0
    n_explain = max(0, remaining - n_reaction - n_counter)

    roles.extend(["explain"] * n_explain)
    if n_counter:
        roles.append("counter")
    roles.extend(["reaction"] * n_reaction)
    roles.append("punchline")
    return roles[:n]


def target_item_count(candidates: Sequence[Candidate], budget_s: float) -> int:
    """How many comments fit the budget, given what this thread actually offers."""
    if not candidates:
        return 0
    durations = sorted(c.feats.est_seconds for c in candidates)
    median = durations[len(durations) // 2]
    usable = max(0.0, budget_s - LEAD_TAIL_S)
    fits = int(usable // max(0.8, median + GAP_S))
    return max(MIN_ITEMS, min(MAX_ITEMS, min(fits, len(candidates))))


def build_candidates(
    comments: Sequence[Comment],
    stats: CorpusStats,
    rate: float = 1.0,
) -> list[Candidate]:
    return [Candidate(c, features(c, stats, rate)) for c in comments]


def _descendants_of(pk: str, comments: Sequence[Comment]) -> set[str]:
    kids = st.children_map(comments)
    out: set[str] = set()
    stack = [pk]
    while stack:
        for child in kids.get(stack.pop(), ()):
            if child.pk not in out:
                out.add(child.pk)
                stack.append(child.pk)
    return out


def assemble(
    candidates: Sequence[Candidate],
    chains: Sequence[Chain],
    stats: CorpusStats,
    *,
    budget_s: float = 50.0,
    lam: float = DEFAULT_LAMBDA,
    beam: int = DEFAULT_BEAM,
    rate: float = 1.0,
) -> list[Slot]:
    """Return an ordered, budget-respecting narrative sequence."""
    if not candidates:
        return []

    comments = [c.comment for c in candidates]
    roles = plan_roles(target_item_count(candidates, budget_s))
    if not roles:
        return []

    chain_members = {pk for chain in chains[:3] for pk in chain.pks}
    by_pk = {c.pk: c for c in candidates}
    cohesion_cache: dict[tuple[str, str], float] = {}

    def coh(a: Comment, b: Comment) -> float:
        key = (a.pk, b.pk)
        if key not in cohesion_cache:
            cohesion_cache[key] = st.cohesion(a, b, stats)
        return cohesion_cache[key]

    # --- seed: every plausible hook starts its own beam state -------------
    hook_pool = sorted(
        (c for c in candidates if c.feats.syllables <= HOOK_MAX_SYLLABLES),
        key=lambda c: -ROLE_FIT["hook"](c.feats),
    )[:beam] or sorted(candidates, key=lambda c: -ROLE_FIT["hook"](c.feats))[:beam]

    states = [
        _State(
            picks=(c,),
            used=frozenset({c.pk}),
            used_authors=(c.comment.username.lower(),),
            seconds=c.feats.est_seconds + GAP_S,
            score=ROLE_FIT["hook"](c.feats),
        )
        for c in hook_pool
    ]
    if not states:
        return []

    # Descendant sets are per-hook, so compute them once up front.
    hook_descendants = {s.picks[0].pk: _descendants_of(s.picks[0].pk, comments) for s in states}

    for role in roles[1:]:
        fit = ROLE_FIT[role]
        next_states: list[_State] = []

        for state in states:
            previous = state.picks[-1].comment
            descendants = hook_descendants[state.picks[0].pk]

            ranked = sorted(
                (c for c in candidates if c.pk not in state.used),
                key=lambda c: -fit(c.feats),
            )[:FANOUT]

            for cand in ranked:
                seconds = state.seconds + cand.feats.est_seconds + GAP_S
                if seconds + LEAD_TAIL_S > budget_s:
                    continue
                author = cand.comment.username.lower()
                if state.used_authors.count(author) >= 3:
                    continue

                value = fit(cand.feats) + lam * coh(previous, cand.comment)
                if cand.pk in descendants:
                    value += HOOK_DESCENDANT_BONUS
                if cand.pk in chain_members:
                    value += CHAIN_MEMBER_BONUS

                next_states.append(
                    _State(
                        picks=state.picks + (cand,),
                        used=state.used | {cand.pk},
                        used_authors=state.used_authors + (author,),
                        seconds=seconds,
                        score=state.score + value,
                    )
                )

        if not next_states:
            break  # nothing else fits the budget; ship what we have
        next_states.sort(key=lambda s: -s.score)
        states = next_states[:beam]

    best = max(states, key=lambda s: s.score)
    return _to_slots(best.picks, roles, stats, rate)


#: Roles a middle slot may be re-labelled as, once the search has picked it.
_MIDDLE_ROLES: tuple[Role, ...] = ("context", "explain", "counter", "reaction")


def _best_middle_role(feats: Features) -> Role:
    """Label a slot by what the comment actually is, not by its plan position.

    The slot plan drives the *search*; using it as the final label would tell the
    user a rebuttal is an explanation just because it landed in an explain slot.
    """
    return max(_MIDDLE_ROLES, key=lambda r: ROLE_FIT[r](feats))


def _to_slots(
    picks: Sequence[Candidate],
    roles: Sequence[Role],
    stats: CorpusStats,
    rate: float,
) -> list[Slot]:
    slots: list[Slot] = []
    last = len(picks) - 1
    for i, cand in enumerate(picks):
        if i == 0:
            role: Role = "hook"
        elif i == last:
            role = "punchline"  # whatever we ended on has to close the video
        else:
            role = _best_middle_role(cand.feats)
        tts_text = normalize_for_tts(cand.comment.text)
        previous = picks[i - 1].comment if i else None
        slots.append(
            Slot(
                comment=cand.comment,
                role=role,
                tts_text=tts_text,
                est_seconds=estimate_seconds(tts_text, rate),
                reason=" · ".join(st.cohesion_reasons(previous, cand.comment, stats))
                if previous
                else "mở màn",
                replies_to_previous=bool(
                    previous and cand.comment.parent_pk == previous.pk
                ),
            )
        )
    return slots


def curate_heuristic(
    comments: Sequence[Comment],
    root_pk: str,
    stats: CorpusStats,
    *,
    budget_s: float = 50.0,
    rate: float = 1.0,
) -> list[Slot]:
    """End-to-end heuristic curation. Also the fallback when the LLM is unavailable."""
    candidates = build_candidates(comments, stats, rate)
    chains = st.rank_chains(st.mine_chains(comments, root_pk), stats)
    slots = assemble(candidates, chains, stats, budget_s=budget_s, rate=rate)
    log.info(
        "heuristic curation: %d candidates, %d chains -> %d slots (%.1fs)",
        len(candidates), len(chains), len(slots), sum(s.est_seconds for s in slots),
    )
    return slots


def retime(slots: Sequence[Slot], rate: float) -> list[Slot]:
    """Recompute duration estimates after the user edits text or changes rate."""
    return [replace(s, est_seconds=estimate_seconds(s.tts_text, rate)) for s in slots]
