"""HOST requirement evaluator.

Seam: `evaluate_host`, with fakes for the web-search port and the model.
Membership is compared in the function. MCP and deepagents stay outside.

The model reports the extracted names only when the material contains the
whole rubric, the approved list, and the web page. A proposal sits outside
the band under test.

T-AG-12 — a named host is absent from the approved list; proposal 9 pins to 1–2.
T-AG-30 — primary and backup are listed, with no conflict; proposal 4 pins to 9–10.
T-AG-31 — a listed primary has a same-date conflict; proposal 9 pins to 3–4.
"""

from __future__ import annotations

from agent.subagents.host import (
    HostAssessment,
    HostMaterial,
    HostRequirement,
    RequirementVerdict,
    Rubric,
    TenderContext,
    Verification,
    WebDocument,
    evaluate_host,
)

_REQUIREMENT = HostRequirement(
    id="req-host",
    description="מנחה מן הרשימה המאושרת, ומנחה גיבוי",
)
_TENDER = TenderContext(
    name="טקס פרסי התרבות",
    event_date="2026-06-01",
    region="תל אביב",
)
_RUBRIC = Rubric(
    text=(
        "requirement_type: HOST\n"
        'rubric_version: "1"\n'
        "### 9–10\n"
        "The primary host and the backup are both on the approved list, and neither has a same-date conflict.\n"
        "### 3–4\n"
        "The primary host is on the approved list and has a same-date conflict.\n"
        "### 1–2\n"
        "A named host is not on the approved list.\n"
        "### 0\n"
        "No host was proposed.\n"
    ),
    version="1",
)
_PAGE = "https://engagements.example/official"
_APPROVED = ("נועה קירל",)


class _Web:
    def __init__(self, content: str) -> None:
        self._content = content

    def search(self, query: str) -> tuple[WebDocument, ...]:
        return (WebDocument(url=_PAGE, title="Engagements", content=self._content),)


class _Model:
    """Reports `found` only when the whole rubric, the list, and the page arrive."""

    def __init__(
        self, page_marker: str, approved: tuple[str, ...], found: HostAssessment
    ) -> None:
        self._page_marker = page_marker
        self._approved = approved
        self._found = found

    def assess(self, material: HostMaterial) -> HostAssessment:
        pages = "\n".join(document.content for document in material.web_results)
        if (
            material.rubric_text == _RUBRIC.text
            and tuple(material.approved_hosts) == self._approved
            and self._page_marker in pages
        ):
            return self._found
        return HostAssessment(
            proposed_score=5,
            justification="No host was proposed.",
            sources=(),
            verifications=(),
            host_name="",
            backup_name="",
            primary_conflict=False,
            backup_conflict=False,
        )


def _found(
    *,
    host_name: str,
    backup_name: str = "",
    primary_conflict: bool = False,
    backup_conflict: bool = False,
    claim: str,
    proposed_score: int = 9,
) -> HostAssessment:
    return HostAssessment(
        proposed_score=proposed_score,
        justification=claim,
        sources=(_PAGE,),
        verifications=(Verification(claim=claim, status="verified", source=_PAGE),),
        host_name=host_name,
        backup_name=backup_name,
        primary_conflict=primary_conflict,
        backup_conflict=backup_conflict,
    )


def _evaluate(
    *,
    answer: str,
    page: str,
    found: HostAssessment,
    approved: tuple[str, ...] = _APPROVED,
) -> RequirementVerdict:
    return evaluate_host(
        _REQUIREMENT,
        answer,
        _RUBRIC,
        _TENDER,
        approved,
        _Web(page),
        _Model(page, approved, found),
    )


def test_named_host_absent_from_the_list_scores_one_or_two() -> None:
    """T-AG-12. Yuval is a real host and is not approved. Model proposes 9."""
    page = "יובל המבולבל מופיע בתאריך אחר."
    verdict = _evaluate(
        answer="המנחה: יובל המבולבל.",
        page=page,
        found=_found(
            host_name="יובל המבולבל",
            claim="Yuval Hamebulbal is a real host and is not on the approved list.",
        ),
    )

    assert verdict.requirement_id == "req-host"
    assert type(verdict.score) is int
    assert verdict.score in (1, 2)


def test_listed_host_and_backup_with_no_conflict_score_nine_or_ten() -> None:
    """T-AG-30. Both names are approved. The model proposes 4 and pads case and spaces."""
    approved = ("נועה קירל", "Dana International")
    page = "אין עיסוק מתנגש לנועה קירל או לדנה אינטרנשיונל בתאריך האירוע."
    verdict = _evaluate(
        answer="המנחה: נועה קירל. גיבוי: Dana International.",
        page=page,
        approved=approved,
        found=_found(
            host_name=" נועה קירל ",
            backup_name=" dana international ",
            proposed_score=4,
            claim="Noa Kirel and Dana International are approved, with no same-date conflict.",
        ),
    )

    assert verdict.requirement_id == "req-host"
    assert type(verdict.score) is int
    assert verdict.score in (9, 10)


def test_listed_host_with_same_date_conflict_scores_three_or_four() -> None:
    """T-AG-31. Noa is approved and booked that night. The backup is approved too."""
    approved = ("נועה קירל", "דנה אינטרנשיונל")
    page = "נועה קירל מופיעה באותו תאריך בחיפה. לדנה אינטרנשיונל אין עיסוק מתנגש."
    verdict = _evaluate(
        answer="המנחה: נועה קירל. גיבוי: דנה אינטרנשיונל.",
        page=page,
        approved=approved,
        found=_found(
            host_name="נועה קירל",
            backup_name="דנה אינטרנשיונל",
            primary_conflict=True,
            claim="Noa Kirel is approved and has a same-date engagement in Haifa.",
        ),
    )

    assert verdict.requirement_id == "req-host"
    assert type(verdict.score) is int
    assert verdict.score in (3, 4)
