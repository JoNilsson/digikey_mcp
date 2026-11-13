"""Resolution sessions: resolve a BOM, apply decisions, resume, hand items to the cart."""

from __future__ import annotations

import hashlib
import secrets
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from ..ingest import ingest
from . import ladder
from .gateway import Gateway
from .ladder import LineResolver
from .models import (
    BLOCKING,
    CARTABLE,
    DNP_SKIPPED,
    ERROR,
    PENDING,
    SKIPPED,
    ResolvedBom,
    ResolvedLine,
    ResolveError,
)
from .names import identity_key
from .prefs import Preferences, load_preferences
from .store import QuotaExhausted, Store, open_store

CartItem = tuple[str, int, str]


def new_session_id(now: float | None = None) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(now))
    return f"bom-{stamp}-{secrets.token_hex(2)}"


def _digest(source: str | bytes, boards: int) -> str:
    data = source if isinstance(source, bytes) else source.encode("utf-8")
    return hashlib.sha256(data + f"|boards={boards}".encode()).hexdigest()[:16]


class Session:
    def __init__(self, session_id: str, boards: int, prefs: Preferences):
        self.session_id = session_id
        self.boards = boards
        self.prefs = prefs
        self.created = time.time()
        self.source_digest = ""
        self.preferences_path: str | None = None
        self.warnings: list[str] = []
        #: line identity key -> DigiKey part number or 'skip'
        self.decisions: dict[str, str] = {}
        self.lines: list[ResolvedLine] = []
        self.stopped = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "session_id": self.session_id,
            "boards": self.boards,
            "created": self.created,
            "updated": time.time(),
            "source_digest": self.source_digest,
            "preferences": self.prefs.raw,
            "preferences_path": self.preferences_path,
            "warnings": self.warnings,
            "decisions": self.decisions,
            "stopped": self.stopped,
            "lines": [ln.to_dict() for ln in self.lines],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Session:
        sess = cls(data["session_id"], int(data["boards"]), Preferences.from_dict(data.get("preferences")))
        sess.created = data.get("created", sess.created)
        sess.source_digest = data.get("source_digest", "")
        sess.preferences_path = data.get("preferences_path")
        sess.warnings = list(data.get("warnings", []))
        sess.decisions = dict(data.get("decisions", {}))
        sess.stopped = data.get("stopped", "")
        sess.lines = [ResolvedLine.from_dict(d) for d in data.get("lines", [])]
        return sess

    @classmethod
    def load(cls, store: Store, session_id: str) -> Session:
        return cls.from_dict(store.session_load(session_id))

    def save(self, store: Store) -> None:
        store.session_save(self.session_id, self.to_dict())

    def bom(self) -> ResolvedBom:
        pending = sum(1 for ln in self.lines if ln.status == PENDING)
        return ResolvedBom(
            session_id=self.session_id,
            boards=self.boards,
            lines=self.lines,
            complete=pending == 0,
            stopped=self.stopped if pending else "",
            warnings=self.warnings,
        )

    def line(self, line_id: int) -> ResolvedLine:
        for ln in self.lines:
            if ln.line.line_id == line_id:
                return ln
        raise ResolveError(f"session {self.session_id} has no line {line_id}")


def _read_source(source: str, source_is_text: bool) -> tuple[Any, str | bytes]:
    """(argument for ingest, content used for the digest)."""
    if source_is_text:
        return source.encode("utf-8"), source
    path = Path(source).expanduser()
    if not path.is_file():
        raise ResolveError(f"BOM file not found: {source}")
    content = path.read_bytes()
    return path, content


def _stop_message(exc: QuotaExhausted, done: int, total: int, session_id: str) -> str:
    wait = ""
    if exc.retry_after:
        minutes = exc.retry_after / 60.0
        wait = f"; retry in about {minutes:.0f} min" if minutes >= 1 else f"; retry in about {exc.retry_after:.0f} s"
    return f"quota exhausted ({exc.kind}): {done} of {total} lines done, resume with session_id {session_id!r}{wait}"


def _run(
    sess: Session,
    store: Store,
    client: Any,
    *,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    only: Iterable[ResolvedLine] | None = None,
) -> None:
    """Resolve every pending (or errored) line, saving after each; stops cleanly on quota."""
    deadline = clock() + sess.prefs.time_budget_seconds
    gw = Gateway(client, store, sess.prefs, clock=clock, sleep=sleep, deadline=deadline)
    resolver = LineResolver(gw, sess.prefs)
    targets = list(only) if only is not None else [ln for ln in sess.lines if ln.status in (PENDING, ERROR)]
    sess.stopped = ""
    for rl in targets:
        if clock() > deadline:
            done = sum(1 for ln in sess.lines if ln.status != PENDING)
            sess.stopped = f"time budget of {sess.prefs.time_budget_seconds:.0f}s reached: {done} of {len(sess.lines)} lines done, resume with session_id {sess.session_id!r}"
            break
        try:
            result = resolver.resolve(rl.line, sess.boards, sess.decisions.get(rl.key))
        except QuotaExhausted as exc:
            rl.status = PENDING
            done = sum(1 for ln in sess.lines if ln.status != PENDING)
            sess.stopped = _stop_message(exc, done, len(sess.lines), sess.session_id)
            break
        sess.lines[next(i for i, x in enumerate(sess.lines) if x is rl)] = result
        sess.save(store)
    sess.save(store)


def resolve_bom(
    source: str | None = None,
    boards: int = 1,
    preferences_path: str | None = None,
    session_id: str | None = None,
    source_is_text: bool = False,
    *,
    client: Any,
    cache_dir: Path | str | None = None,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> ResolvedBom:
    """Resolve a BOM into DigiKey parts, or resume / revise an existing session.

    * new session id (or none): ingest `source`, resolve every line.
    * existing session, `source` omitted: resume pending and errored lines.
    * existing session, same source and boards: resume.
    * existing session, different source or boards: a revised BOM; decisions
      are carried over to lines with the same identity (MPN, else DigiKey
      part number, else category + value + footprint).
    """
    if boards < 1:
        raise ResolveError("boards must be at least 1")
    with open_store(cache_dir) as store:
        existing = bool(session_id) and store.session_exists(session_id)  # type: ignore[arg-type]
        sess: Session
        if existing:
            sess = Session.load(store, session_id)  # type: ignore[arg-type]
            if preferences_path:
                sess.prefs = load_preferences(preferences_path)
                sess.preferences_path = preferences_path
        else:
            if source is None:
                raise ResolveError(f"unknown session {session_id!r}; give a source to start it" if session_id else "source is required")
            sid = Store.check_session_id(session_id) if session_id else new_session_id(clock())
            sess = Session(sid, boards, load_preferences(preferences_path))
            sess.preferences_path = preferences_path
        store.cache_purge(clock())

        if source is not None:
            arg, content = _read_source(source, source_is_text)
            digest = _digest(content, boards)
            if digest != sess.source_digest:
                result = ingest(arg)
                sess.boards = boards
                sess.source_digest = digest
                sess.warnings = list(result.warnings) + [
                    f"row {u.source_row}: {u.reason}: {u.text[:60]}" for u in result.unparsed_rows
                ]
                sess.lines = [ResolvedLine(line=ln, key=identity_key(ln)) for ln in result.lines]
                if not sess.lines:
                    raise ResolveError("the BOM contains no parts")
        _run(sess, store, client, clock=clock, sleep=sleep)
        return sess.bom()


def _load(store: Store, session_id: str) -> Session:
    return Session.load(store, session_id)


def apply_decisions(
    session_id: str,
    decisions: Mapping[int | str, str],
    *,
    client: Any,
    cache_dir: Path | str | None = None,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> ResolvedBom:
    """Decide lines: `{line_id: digikey_pn or 'skip'}`. Decisions persist with the session."""
    with open_store(cache_dir) as store:
        sess = _load(store, session_id)
        parsed: list[tuple[ResolvedLine, str]] = []
        for raw_id, value in decisions.items():
            try:
                line_id = int(raw_id)
            except (TypeError, ValueError):
                raise ResolveError(f"line id {raw_id!r} is not a number") from None
            if not isinstance(value, str) or not value.strip():
                raise ResolveError(f"line {line_id}: decision must be a DigiKey part number or 'skip'")
            rl = sess.line(line_id)
            if rl.status == DNP_SKIPPED:
                raise ResolveError(f"line {line_id} is DNP or not a purchased part; nothing to decide")
            value = value.strip()
            parsed.append((rl, ladder.SKIP if value.casefold() == ladder.SKIP else value))
        for rl, value in parsed:
            sess.decisions[rl.key] = value
        sess.save(store)
        _run(sess, store, client, clock=clock, sleep=sleep, only=[rl for rl, _ in parsed])
        return sess.bom()


def load_bom(session_id: str, *, cache_dir: Path | str | None = None) -> ResolvedBom:
    with open_store(cache_dir) as store:
        return _load(store, session_id).bom()


def cart_items(session_id: str, *, cache_dir: Path | str | None = None) -> list[CartItem]:
    """(digikey_pn, quantity, customer reference) for resolved or decided, non-skipped lines."""
    bom = load_bom(session_id, cache_dir=cache_dir)
    return [
        (ln.dk_pn, ln.order_qty, ln.cref)
        for ln in bom.lines
        if ln.status in CARTABLE and ln.dk_pn and ln.order_qty
    ]


def cart_items_summary(session_id: str, *, cache_dir: Path | str | None = None) -> dict[str, Any]:
    bom = load_bom(session_id, cache_dir=cache_dir)
    items = [
        {"digikey_part_number": pn, "quantity": qty, "customer_reference": cref}
        for pn, qty, cref in cart_items(session_id, cache_dir=cache_dir)
    ]
    return {
        "session_id": session_id,
        "items": items,
        "item_count": len(items),
        "estimated_total": bom.estimated_total,
        "ready_for_cart": not bom.blocking and bom.complete,
        "blocking": [
            {"line_id": ln.line.line_id, "refdes": ln.line.refdes, "status": ln.status, "reason": ln.reason}
            for ln in bom.lines
            if ln.status in BLOCKING
        ],
        "skipped": [ln.line.line_id for ln in bom.lines if ln.status == SKIPPED],
    }


def quota_status(
    session_id: str | None = None,
    *,
    cache_dir: Path | str | None = None,
    clock: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Calls used and left in the current minute and UTC day (limits come from the session's preferences)."""
    with open_store(cache_dir) as store:
        prefs = Session.load(store, session_id).prefs if session_id else Preferences()
        limit_min = max(prefs.per_minute - prefs.reserve, 1)
        limit_day = max(prefs.per_day - prefs.reserve, 1)
        data = store.quota_snapshot(clock(), limit_min, limit_day)
    data["note"] = (
        f"limits are DigiKey's {prefs.per_minute}/min and {prefs.per_day}/day minus a reserve of {prefs.reserve}; "
        "only calls made through the resolver are counted, cache hits are free"
    )
    return data
