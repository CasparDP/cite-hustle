"""Front matter that CrossRef labels journal-article: mastheads, reports, calls, indexes.

One rule set shared by the collector filter, `cleanup-non-articles`, and the dashboard's
"suspected non-article" flag. The title regex is RE2-compatible so DuckDB can evaluate
the same pattern (`regexp_matches(lower(trim(title)), TITLE_RE)`).

Patterns are anchored on the whole (lowercased) title, so a research paper that merely
contains one of these words is never matched. Scholarly items without being research
articles (Discussion, Comment, Reply, Dialogue, Correspondence, Introduction) are kept.
"""

from __future__ import annotations

import re

_TITLES = [
    r"",
    r"no title available",
    r"miscellanea",
    r"notes",
    r"news (and )?notes",
    r"news items?",
    r"announcements?",
    r"advertisements?",
    r"advert: .*",
    r"management insights",
    r"recommendations for further reading",
    r"annotated listing of new books",
    r"books? received",
    r"publications received",
    r"forthcoming (papers|articles|conferences)",
    r"accepted manuscripts",
    r"jel classification system",
    r"additional journal content",
    r"other contents",
    r"(volume |table of )?contents?( of: .*)?",
    r"volume (information|contents)",
    r"title (page|index)",
    r"(subject |author |title )?index",
    r"index to (the )?(journal|volume)\b.*",
    r"frontmatter",
    r"subscription page",
    r"ifc",
    r"abstracts?",
    r"abstracts/résumés",
    r"résumés",
    r"about the authors",
    r"author information",
    r"(information|notice|notes|invitation) (for|to) contributors",
    r"style (instructions|guide for authors)",
    r"jfqa style requirements",
    r"foreword",
    r"preface",
    r"publisher's note",
    r"association meetings",
    r"call for papers?\b.*",
    r"recent referees",
    r"referees",
    r"referee list",
    r"reviewers and guest associate editors",
    r"acknowledge?ments?( of .*| to .*)?",
    r"from the editors?",
    r"a note from the editor",
    r"editor'?s'? (introduction|comments|report)",
    r"annual editor'?s? report",
    r"editorial (policy|data|changes|collaborators)",
    r"editorial statements?(—.*)?",
    r"jpe turnaround times.*",
    r"jpe submissions",
    r"submission of manuscripts.*",
    r"errat(a|um)",
    r"report of (the )?(treasurer|secretary|president|director|editors?|executive secretary).*",
    r"report of (the )?(finance committee|committee on (economic|the status)\b.*|(afa )?representative to .*)",
    r"report of (the )?independent auditors?",
    r"independent auditors'? report",
    r"treasurer'?s report",
    r"minutes of (the )?([0-9]{4} )?(annual|meeting of the executive|executive committee)\b.*",
    r"officers of the society.*",
    r"fellows of the econometric society",
    r"the econometric society (annual reports|research monograph series).*",
    r"committee on (government relations|economic education|the status of (women|minority groups)\b.*)",
    r"(general )?information on the association",
    r"travel fund",
    r"consolidated revenues and expenses reports",
    r"list of online reports",
    r"position listings",
    r"job openings for economists",
    r"doctoral dissertations in economics.*",
    r"(h\. gregg lewis|jacob mincer|sherwin rosen|sole) (prize|award)\b.*",
    r"academy of management (review decade award|code of ethics)",
    r"to those seeking permissions.*",
    r"conference reports",
    r"practice summar(y|ies)",
    # A journal or society name standing alone as the title (masthead pages)
    r"(the )?(american finance association|american economic association)",
    r"(the )?american economic (review|journal: .*)",
    r"(the )?journal of( [a-z&,']+){1,4}( call for papers)?",
    r"accounting and business research",
    r"institute for operations research and the management sciences",
]

# Whole-title match on the lowercased, trimmed title; trailing punctuation tolerated.
TITLE_RE = r"^(?:" + "|".join(_TITLES) + r")[\s.:]*$"

# Issue-level DOIs (one per issue, not per article), e.g. 10.1111/joar.v63.4
ISSUE_DOI_RE = r"\.issue-[0-9a-z]+$|\.v[0-9]+\.[0-9]+$|ahead-of-print$"

_title_re = re.compile(TITLE_RE)
_doi_re = re.compile(ISSUE_DOI_RE)


def is_front_matter(title: str | None, doi: str | None = None) -> bool:
    """True if the record is front matter rather than a research article."""
    if _title_re.match((title or "").strip().lower()):
        return True
    return bool(doi and _doi_re.search(doi.lower()))


def sql_predicate(alias: str = "a") -> str:
    """The same rule as a DuckDB predicate (patterns inlined as SQL string literals)."""
    title_re = TITLE_RE.replace("'", "''")
    doi_re = ISSUE_DOI_RE.replace("'", "''")
    return (
        f"(regexp_full_match(lower(trim(coalesce({alias}.title, ''))), '{title_re}')"
        f" OR regexp_matches(lower({alias}.doi), '{doi_re}'))"
    )
