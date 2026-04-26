"""Git repo info: branch, dirty state, ahead/behind, upstream tracking,
in-progress operations (merge / rebase / cherry-pick / revert / bisect)."""
import os
import subprocess


def _run(args, cwd, timeout=0.25):
    try:
        r = subprocess.run(
            ['git'] + args,
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


def get_git_info(cwd):
    if not cwd:
        return None
    inside = _run(['rev-parse', '--is-inside-work-tree'], cwd)
    if inside != 'true':
        return None

    branch = _run(['symbolic-ref', '--short', 'HEAD'], cwd)
    if branch is None:
        sha = _run(['rev-parse', '--short', 'HEAD'], cwd)
        branch = f'({sha})' if sha else '(detached)'

    status = _run(
        ['status', '--porcelain=v2', '--branch', '--untracked-files=all'],
        cwd, timeout=0.4,
    )
    ahead = 0
    behind = 0
    has_upstream = False
    added = modified = deleted = renamed = untracked = 0

    if status:
        for line in status.splitlines():
            if line.startswith('# branch.ab '):
                parts = line.split()
                try:
                    ahead = abs(int(parts[2]))
                    behind = abs(int(parts[3]))
                except (ValueError, IndexError):
                    pass
            elif line.startswith('# branch.upstream '):
                has_upstream = True
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

    git_dir = _run(['rev-parse', '--git-dir'], cwd)
    op_state = _detect_op_state(cwd, git_dir)

    return {
        'branch': branch,
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
