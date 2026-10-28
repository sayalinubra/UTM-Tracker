"""Uploaded username / comment-link files: parsing, column detection, merging and saving.

Password safety: columns whose header looks like a password/secret are never read, values are
cut at ',' or ':' (see inputs.parse_accounts), and nothing but usernames/links is ever written.
"""
import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime

from .inputs import parse_accounts, parse_comment_link, parse_comments

SECRET_HEADER_RE = re.compile(r"pass|pwd|secret|token|api[\s_-]*key|otp|\bpin\b|cookie|session|auth",
                              re.IGNORECASE)
USER_HEADER_RE = re.compile(r"user|account|handle|redditor|profile", re.IGNORECASE)
EXACT_USER_HEADER_RE = re.compile(r"(reddit[\s_-]*)?(user(name)?|user[\s_-]+name|account|handle|redditor)",
                                  re.IGNORECASE)
REDDIT_LINK_RE = re.compile(r"(reddit\.com|redd\.it)/", re.IGNORECASE)


@dataclass
class ParsedUpload:
    values: list = field(default_factory=list)      # usernames or raw comment links, de-duplicated
    columns: list = field(default_factory=list)     # usable CSV columns (password-like ones removed)
    column: str = None                              # the column actually used
    needs_column_choice: bool = False
    warnings: list = field(default_factory=list)
    duplicates_in_file: int = 0


@dataclass
class MergeResult:
    values: list
    added: list
    duplicates: int
    removed: list = field(default_factory=list)


def decode(data):
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _read_csv(text):
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return [], []
    header = [h.strip() for h in rows[0]]
    return header, rows[1:]


def _safe_columns(header, result):
    safe = []
    for h in header:
        if SECRET_HEADER_RE.search(h):
            result.warnings.append(f"Ignored column '{h}': it looks like it holds passwords or secrets, "
                                   f"so it was never read or stored.")
        elif h:
            safe.append(h)
    return safe


def _column_values(header, rows, column):
    idx = header.index(column)
    return [r[idx].strip() for r in rows if idx < len(r) and r[idx].strip()]


def _dedupe(values, key):
    seen, out, dupes = set(), [], 0
    for v in values:
        k = key(v)
        if k in seen:
            dupes += 1
            continue
        seen.add(k)
        out.append(v)
    return out, dupes


def comment_key(raw):
    link, _ = parse_comment_link(raw)
    return link.comment_id if link and link.comment_id else raw.strip().lower()


def parse_usernames_upload(filename, text, column=None):
    result = ParsedUpload()
    if filename.lower().endswith(".csv"):
        header, rows = _read_csv(text)
        result.columns = _safe_columns(header, result)
        if column is None:
            exact = [h for h in result.columns if EXACT_USER_HEADER_RE.fullmatch(h)]
            loose = [h for h in result.columns if USER_HEADER_RE.search(h)]
            candidates = exact or loose
            if len(candidates) == 1:
                column = candidates[0]
            else:
                result.needs_column_choice = True
                return result
        if column not in result.columns:
            result.needs_column_choice = True
            return result
        result.column = column
        lines = _column_values(header, rows, column)
    else:
        lines = text.splitlines()
    parsed = parse_accounts("\n".join(lines))
    result.warnings += [w.replace("accounts.txt line", "uploaded file line") for w in parsed.warnings]
    raw_count = sum(1 for line in lines if line.strip() and not line.strip().startswith("#"))
    result.values = parsed.usernames
    result.duplicates_in_file = max(0, raw_count - len(parsed.usernames)
                                    - sum(1 for w in parsed.warnings if "skipped" in w))
    return result


def parse_links_upload(filename, text, column=None):
    result = ParsedUpload()
    if filename.lower().endswith(".csv"):
        header, rows = _read_csv(text)
        result.columns = _safe_columns(header, result)
        if column is None:
            scores = {h: sum(1 for v in _column_values(header, rows, h) if REDDIT_LINK_RE.search(v))
                      for h in result.columns}
            best = max(scores.values(), default=0)
            winners = [h for h, s in scores.items() if s == best and s > 0]
            if len(winners) == 1:
                column = winners[0]
            else:
                result.needs_column_choice = True
                return result
        if column not in result.columns:
            result.needs_column_choice = True
            return result
        result.column = column
        lines = [v for v in _column_values(header, rows, column) if REDDIT_LINK_RE.search(v)]
    else:
        lines = text.splitlines()
    parsed = parse_comments("\n".join(lines))
    result.warnings += [w.replace("comments.txt line", "uploaded file line") for w in parsed.warnings]
    raw = [link.raw for link in parsed.links]
    result.values = raw
    content = [line for line in lines if line.strip() and not line.strip().startswith("#")]
    result.duplicates_in_file = max(0, len(content) - len(raw) - len(parsed.warnings))
    return result


def merge(existing, new, mode, key=str.lower):
    """mode 'add': existing + new (de-duplicated). mode 'replace': just new (de-duplicated)."""
    new_unique, dupes_in_new = _dedupe(new, key)
    if mode == "replace":
        new_keys = {key(v) for v in new_unique}
        removed = [v for v in existing if key(v) not in new_keys]
        return MergeResult(new_unique, [v for v in new_unique if key(v) not in {key(e) for e in existing}],
                           dupes_in_new, removed)
    have = {key(v) for v in existing}
    added = [v for v in new_unique if key(v) not in have]
    return MergeResult(list(existing) + added, added, dupes_in_new + (len(new_unique) - len(added)))


HEADERS = {
    "accounts": ("# One Reddit username per line. \"u/name\" and \"/u/name\" are fine.\n"
                 "# Lines starting with # are ignored. Usernames only - never put passwords here.\n"
                 "# Edit this file any time, or upload a list in the dashboard sidebar.\n"),
    "comments": ("# Optional: one Reddit COMMENT link per line. These are always tracked,\n"
                 "# even if they're old or posted from an account not in accounts.txt.\n"
                 "# Lines starting with # are ignored.\n"),
}


def save_list(path, kind, merge_result, mode, existing_text, source_name, now=None):
    """Write the merged list back to accounts.txt / comments.txt.

    'add' appends only the new values (keeping the file's existing lines and comments);
    'replace' rewrites the file with the standard header plus the new list.
    """
    stamp = (now or datetime.now()).strftime("%Y-%m-%d %H:%M")
    if mode == "replace":
        body = HEADERS[kind] + f"# Replaced from upload '{source_name}' on {stamp}\n\n"
        body += "".join(v + "\n" for v in merge_result.values)
    else:
        if not merge_result.added:
            return False
        body = existing_text.rstrip("\n") + ("\n\n" if existing_text.strip() else HEADERS[kind] + "\n")
        body += f"# Added from upload '{source_name}' on {stamp}\n"
        body += "".join(v + "\n" for v in merge_result.added)
    path.write_text(body, encoding="utf-8")
    return True
