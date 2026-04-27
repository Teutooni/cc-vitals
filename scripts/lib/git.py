"""Git repo info: branch, dirty state, ahead/behind, upstream tracking,
in-progress operations (merge / rebase / cherry-pick / revert / bisect).

Most repos render in a few ms, but `git status` on slow filesystems
(WSL→NTFS, network mounts, very large repos) can take seconds. Results
are cached per-cwd in plugin-data, fingerprinted by `.git/HEAD` and
`.git/index` mtimes — branch switches and staging changes invalidate
immediately, while a short time TTL covers working-tree edits (which
don't bump those mtimes). On timeout we return whatever is cached
rather than blanking the segment.
"""
import os
import subprocess
import time

from state import (
    DATA_DIR,
    load_json,
    prune_sessions_lru,
    save_json_atomic,
)


CACHE_FILE = DATA_DIR / 'git-cache.json'

# Bumped from the original 0.4s — slow filesystems (WSL→NTFS, large
# repos) blew through it on every render. Caching means we usually
# don't pay this anyway.
DEFAULT_TIMEOUT_SECONDS = 3.0
DEFAULT_CACHE_TTL_SECONDS = 5.0
MAX_CACHED_CWDS = 50


def _run(args, cwd, timeout=0.25):
    # `--no-optional-locks` keeps read-only commands like `status` from
    # racing for `.git/index.lock` against a concurrent `commit` /
    # `add` the user is running in the same repo. Without this flag,
    # any concurrent interactive git operation has a non-trivial chance
    # of failing with "Unable to create index.lock: File exists".
    try:
        r = subprocess.run(
            ['git', '--no-optional-locks'] + args,
            cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def _read_int(path):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _detect_op_state(cwd, git_dir):
    """Return a tuple (op, progress) describing an in-progress git operation,
    or None. Progress is "step/total" for rebases, else None."""
    if not git_dir:
        return None
    if not os.path.isabs(git_dir):
        git_dir = os.path.join(cwd, git_dir)

    def has(name):
        return os.path.exists(os.path.join(git_dir, name))

    if has('rebase-merge') or has('rebase-apply'):
        for sub, step_f, total_f in (
            ('rebase-merge', 'msgnum', 'end'),
            ('rebase-apply', 'next',   'last'),
        ):
            d = os.path.join(git_dir, sub)
            if not os.path.isdir(d):
                continue
            step = _read_int(os.path.join(d, step_f))
            total = _read_int(os.path.join(d, total_f))
            if step is not None and total is not None:
                return ('rebase', f'{step}/{total}')
            return ('rebase', None)
        return ('rebase', None)
    if has('MERGE_HEAD'):
        return ('merge', None)
    if has('CHERRY_PICK_HEAD'):
        return ('cherry-pick', None)
    if has('REVERT_HEAD'):
        return ('revert', None)
    if has('BISECT_LOG'):
        return ('bisect', None)
    return None


def _signature(cwd):
    """Cheap mtime fingerprint. Bumps on branch switch (HEAD) and on
    `git add`/`commit`/`reset` (index). Working-tree edits don't touch
    these — that's what the time TTL is for.

    Returns a list (not tuple) so it round-trips through JSON intact,
    or None if cwd isn't a repo."""
    try:
        git_path = os.path.join(cwd, '.git')
        if os.path.isdir(git_path):
            head_mt = os.stat(os.path.join(git_path, 'HEAD')).st_mtime
            try:
                idx_mt = os.stat(os.path.join(git_path, 'index')).st_mtime
            except OSError:
                idx_mt = 0.0
            return [head_mt, idx_mt]
        if os.path.isfile(git_path):
            # Worktree / submodule pointer file. Its own mtime is the
            # next-best signal we can get without parsing it.
            return [os.stat(git_path).st_mtime, 0.0]
        return None
    except OSError:
        return None


def _query_git(cwd, timeout):
    """Run `git status` and synthesize the info dict. None on failure."""
    status = _run(
        ['status', '--porcelain=v2', '--branch', '--untracked-files=all'],
        cwd, timeout=timeout,
    )
    if status is None:
        return None

    branch = None
    head_oid = None
    ahead = 0
    behind = 0
    has_upstream = False
    added = modified = deleted = renamed = untracked = 0

    for line in status.splitlines():
        if line.startswith('# branch.head '):
            branch = line[len('# branch.head '):].strip()
        elif line.startswith('# branch.oid '):
            head_oid = line[len('# branch.oid '):].strip()
        elif line.startswith('# branch.upstream '):
            has_upstream = True
        elif line.startswith('# branch.ab '):
            parts = line.split()
            try:
                ahead = abs(int(parts[2]))
                behind = abs(int(parts[3]))
            except (ValueError, IndexError):
                pass
        elif line.startswith('? '):
            untracked += 1
        elif line.startswith('2 '):
            renamed += 1
        elif line.startswith('1 ') and len(line) >= 4:
            # "1 XY ..." — X = staged vs HEAD, Y = worktree vs index.
            # Classify by priority: deleted > added > modified (ignore '.').
            xy = line[2:4]
            if 'D' in xy:
                deleted += 1
            elif 'A' in xy:
                added += 1
            elif 'M' in xy or 'T' in xy:
                modified += 1
        elif line.startswith('u '):
            modified += 1

    if branch == '(detached)':
        branch = f'({head_oid[:7]})' if head_oid else '(detached)'

    git_dir = _run(['rev-parse', '--git-dir'], cwd, timeout=timeout)
    op_state = _detect_op_state(cwd, git_dir)
    if op_state is not None:
        # JSON round-trip would coerce tuple→list anyway; do it now so
        # cached and fresh results compare equal.
        op_state = list(op_state)

    return {
        'branch': branch or '(unknown)',
        'ahead': ahead,
        'behind': behind,
        'upstream': has_upstream,
        'added': added,
        'modified': modified,
        'deleted': deleted,
        'renamed': renamed,
        'untracked': untracked,
        'op_state': op_state,
    }


def get_git_info(cwd, timeout=None, cache_ttl=None):
    """Return the cached or freshly-queried git info dict, or None.

    Cache hit when (cwd, signature) matches and age < cache_ttl.
    Otherwise run `git` with the given timeout. On timeout fall back
    to whatever stale entry is cached for this cwd, if any."""
    if not cwd:
        return None
    timeout = DEFAULT_TIMEOUT_SECONDS if timeout is None else float(timeout)
    cache_ttl = DEFAULT_CACHE_TTL_SECONDS if cache_ttl is None else float(cache_ttl)

    sig = _signature(cwd)
    if sig is None:
        return None  # cwd isn't inside a repo

    cache = load_json(CACHE_FILE) or {}
    entries = cache.setdefault('entries', {})
    entry = entries.get(cwd) or {}
    cached_sig = entry.get('signature')
    cached_info = entry.get('info')
    cached_age = time.time() - float(entry.get('updated_at') or 0)

    if cached_info and cached_sig == sig and cached_age < cache_ttl:
        return cached_info

    info = _query_git(cwd, timeout)
    if info is not None:
        entries[cwd] = {
            'info': info,
            'signature': sig,
            'updated_at': time.time(),
        }
        prune_sessions_lru(entries, MAX_CACHED_CWDS, key='updated_at')
        save_json_atomic(CACHE_FILE, cache)
        return info

    # In a repo but git failed/timed out. Stale cache > blank > silent.
    if cached_info is not None:
        return cached_info
    # No cache either — surface a sentinel so render_git can flag it
    # rather than blanking the segment. The user explicitly preferred
    # a visible "something is wrong" over silent disappearance.
    return {
        'branch': None,
        'ahead': 0,
        'behind': 0,
        'upstream': False,
        'added': 0,
        'modified': 0,
        'deleted': 0,
        'renamed': 0,
        'untracked': 0,
        'op_state': None,
        'error': 'timeout',
    }
