import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import tests  # noqa: F401
import git


def _git(cwd, *args):
    subprocess.run(
        ['git'] + list(args),
        cwd=cwd, check=True,
        env={**os.environ,
             'GIT_AUTHOR_NAME': 'T', 'GIT_AUTHOR_EMAIL': 't@e',
             'GIT_COMMITTER_NAME': 'T', 'GIT_COMMITTER_EMAIL': 't@e'},
        capture_output=True,
    )


def _git_available():
    try:
        subprocess.run(['git', '--version'], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


class RunUsesNoOptionalLocks(unittest.TestCase):
    """Pin the flag so a future refactor doesn't silently re-introduce the
    statusline-vs-commit race for `.git/index.lock`."""
    def test_no_optional_locks_passed_to_subprocess(self):
        fake = mock.Mock()
        fake.returncode = 0
        fake.stdout = 'main\n'
        with mock.patch.object(git.subprocess, 'run', return_value=fake) as run:
            git._run(['status', '--porcelain=v2'], '/tmp')
        argv = run.call_args.args[0]
        self.assertEqual(argv[0], 'git')
        self.assertIn('--no-optional-locks', argv)
        # The flag must come before the subcommand — git only accepts it
        # as a top-level option.
        self.assertLess(argv.index('--no-optional-locks'), argv.index('status'))


@unittest.skipUnless(_git_available(), 'git not installed')
class GetGitInfo(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.repo = self._tmp.name
        _git(self.repo, 'init', '-q', '-b', 'main')
        _git(self.repo, 'commit', '-q', '--allow-empty', '-m', 'init')
        # Isolate the on-disk cache so tests don't leak across each other
        # and don't pollute the user's real plugin-data.
        self._cache_tmp = TemporaryDirectory()
        self._patch = mock.patch.object(
            git, 'CACHE_FILE',
            Path(self._cache_tmp.name) / 'git-cache.json',
        )
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._cache_tmp.cleanup()
        self._tmp.cleanup()

    def test_clean_repo(self):
        info = git.get_git_info(self.repo)
        self.assertIsNotNone(info)
        self.assertEqual(info['branch'], 'main')
        self.assertEqual(info['ahead'], 0)
        self.assertEqual(info['behind'], 0)
        self.assertEqual(info['added'], 0)
        self.assertEqual(info['modified'], 0)
        self.assertEqual(info['deleted'], 0)
        self.assertEqual(info['untracked'], 0)
        self.assertFalse(info['upstream'])
        self.assertIsNone(info['op_state'])

    def test_modified_and_untracked(self):
        Path(self.repo, 'a.txt').write_text('hi')
        _git(self.repo, 'add', 'a.txt')
        _git(self.repo, 'commit', '-q', '-m', 'a')
        Path(self.repo, 'a.txt').write_text('changed')
        Path(self.repo, 'new.txt').write_text('untracked')
        info = git.get_git_info(self.repo)
        self.assertEqual(info['modified'], 1)
        self.assertEqual(info['untracked'], 1)

    def test_added_file(self):
        Path(self.repo, 'b.txt').write_text('new')
        _git(self.repo, 'add', 'b.txt')
        info = git.get_git_info(self.repo)
        self.assertEqual(info['added'], 1)

    def test_deleted_file(self):
        Path(self.repo, 'c.txt').write_text('hi')
        _git(self.repo, 'add', 'c.txt')
        _git(self.repo, 'commit', '-q', '-m', 'c')
        Path(self.repo, 'c.txt').unlink()
        info = git.get_git_info(self.repo)
        self.assertEqual(info['deleted'], 1)

    def test_non_git_path_returns_none(self):
        with TemporaryDirectory() as d:
            self.assertIsNone(git.get_git_info(d))

    def test_no_cwd(self):
        self.assertIsNone(git.get_git_info(None))


@unittest.skipUnless(_git_available(), 'git not installed')
class GitCaching(unittest.TestCase):
    def setUp(self):
        self._repo_tmp = TemporaryDirectory()
        self.repo = self._repo_tmp.name
        _git(self.repo, 'init', '-q', '-b', 'main')
        _git(self.repo, 'commit', '-q', '--allow-empty', '-m', 'init')
        self._cache_tmp = TemporaryDirectory()
        self._patch = mock.patch.object(
            git, 'CACHE_FILE',
            Path(self._cache_tmp.name) / 'git-cache.json',
        )
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._cache_tmp.cleanup()
        self._repo_tmp.cleanup()

    def test_cache_hit_skips_subprocess(self):
        first = git.get_git_info(self.repo, timeout=5.0, cache_ttl=60.0)
        self.assertIsNotNone(first)
        with mock.patch.object(git, '_query_git') as q:
            second = git.get_git_info(self.repo, timeout=5.0, cache_ttl=60.0)
            q.assert_not_called()
        self.assertEqual(first, second)

    def test_signature_change_invalidates_cache(self):
        first = git.get_git_info(self.repo, timeout=5.0, cache_ttl=60.0)
        # Simulate an index update by bumping HEAD's mtime — the
        # signature check will see a fresh fingerprint and re-query.
        head = Path(self.repo, '.git', 'HEAD')
        new_mt = head.stat().st_mtime + 10
        os.utime(head, (new_mt, new_mt))
        with mock.patch.object(git, '_query_git', wraps=git._query_git) as q:
            git.get_git_info(self.repo, timeout=5.0, cache_ttl=60.0)
            self.assertEqual(q.call_count, 1)
        self.assertIsNotNone(first)

    def test_ttl_expiry_invalidates_cache(self):
        git.get_git_info(self.repo, timeout=5.0, cache_ttl=60.0)
        # Pretend the cached entry was populated long ago.
        import json
        cf = git.CACHE_FILE
        data = json.loads(cf.read_text())
        data['entries'][self.repo]['updated_at'] = 0
        cf.write_text(json.dumps(data))
        with mock.patch.object(git, '_query_git', wraps=git._query_git) as q:
            git.get_git_info(self.repo, timeout=5.0, cache_ttl=1.0)
            self.assertEqual(q.call_count, 1)

    def test_timeout_falls_back_to_stale_cache(self):
        first = git.get_git_info(self.repo, timeout=5.0, cache_ttl=60.0)
        # Force a fresh query to fail by stubbing _query_git → None,
        # while also expiring the TTL so the cache hit branch isn't taken.
        import json
        cf = git.CACHE_FILE
        data = json.loads(cf.read_text())
        data['entries'][self.repo]['updated_at'] = 0
        cf.write_text(json.dumps(data))
        with mock.patch.object(git, '_query_git', return_value=None):
            stale = git.get_git_info(self.repo, timeout=5.0, cache_ttl=1.0)
        self.assertEqual(stale, first)

    def test_timeout_with_no_cache_returns_error_stub(self):
        # In a repo, no prior cache, git fails — surface a stub so the
        # segment can render a warning rather than disappearing.
        with mock.patch.object(git, '_query_git', return_value=None):
            info = git.get_git_info(self.repo, timeout=5.0, cache_ttl=60.0)
        self.assertIsNotNone(info)
        self.assertEqual(info.get('error'), 'timeout')
        self.assertIsNone(info.get('branch'))

    def test_non_repo_still_returns_none_on_failure(self):
        # Outside a repo, _signature returns None and we never query
        # git. Result must remain None so the segment cleanly drops.
        with TemporaryDirectory() as d:
            with mock.patch.object(git, '_query_git', return_value=None):
                self.assertIsNone(git.get_git_info(d))

    def test_lru_prunes_old_entries(self):
        # Create more than MAX_CACHED_CWDS distinct fake repos.
        original_max = git.MAX_CACHED_CWDS
        with mock.patch.object(git, 'MAX_CACHED_CWDS', 3):
            for i in range(5):
                with TemporaryDirectory() as d:
                    _git(d, 'init', '-q', '-b', 'main')
                    _git(d, 'commit', '-q', '--allow-empty', '-m', 'init')
                    git.get_git_info(d, timeout=5.0, cache_ttl=60.0)
                    # We can't assert about d after it's deleted, so just
                    # let the loop close the TemporaryDirectory and move on.
            import json
            data = json.loads(git.CACHE_FILE.read_text())
            self.assertLessEqual(len(data['entries']), 3)
        self.assertEqual(git.MAX_CACHED_CWDS, original_max)


class SignatureUnit(unittest.TestCase):
    def test_returns_none_for_non_repo(self):
        with TemporaryDirectory() as d:
            self.assertIsNone(git._signature(d))

    def test_returns_list_for_dir_repo(self):
        with TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, '.git'))
            Path(d, '.git', 'HEAD').write_text('ref: refs/heads/main\n')
            sig = git._signature(d)
            self.assertIsInstance(sig, list)
            self.assertEqual(len(sig), 2)

    def test_returns_list_for_worktree_pointer(self):
        with TemporaryDirectory() as d:
            Path(d, '.git').write_text('gitdir: /elsewhere\n')
            sig = git._signature(d)
            self.assertIsInstance(sig, list)
            self.assertGreater(sig[0], 0)


@unittest.skipUnless(_git_available(), 'git not installed')
class DetectOpState(unittest.TestCase):
    def test_none_for_clean_repo(self):
        with TemporaryDirectory() as d:
            _git(d, 'init', '-q', '-b', 'main')
            _git(d, 'commit', '-q', '--allow-empty', '-m', 'init')
            git_dir = git._run(['rev-parse', '--git-dir'], d)
            self.assertIsNone(git._detect_op_state(d, git_dir))

    def test_merge_marker(self):
        with TemporaryDirectory() as d:
            _git(d, 'init', '-q', '-b', 'main')
            _git(d, 'commit', '-q', '--allow-empty', '-m', 'init')
            git_dir = git._run(['rev-parse', '--git-dir'], d)
            Path(d, '.git', 'MERGE_HEAD').write_text('deadbeef\n')
            op = git._detect_op_state(d, git_dir)
            self.assertEqual(op, ('merge', None))

    def test_cherry_pick_marker(self):
        with TemporaryDirectory() as d:
            _git(d, 'init', '-q', '-b', 'main')
            _git(d, 'commit', '-q', '--allow-empty', '-m', 'init')
            git_dir = git._run(['rev-parse', '--git-dir'], d)
            Path(d, '.git', 'CHERRY_PICK_HEAD').write_text('deadbeef\n')
            op = git._detect_op_state(d, git_dir)
            self.assertEqual(op, ('cherry-pick', None))

    def test_revert_marker(self):
        with TemporaryDirectory() as d:
            _git(d, 'init', '-q', '-b', 'main')
            _git(d, 'commit', '-q', '--allow-empty', '-m', 'init')
            git_dir = git._run(['rev-parse', '--git-dir'], d)
            Path(d, '.git', 'REVERT_HEAD').write_text('deadbeef\n')
            self.assertEqual(git._detect_op_state(d, git_dir), ('revert', None))

    def test_rebase_with_progress(self):
        with TemporaryDirectory() as d:
            _git(d, 'init', '-q', '-b', 'main')
            _git(d, 'commit', '-q', '--allow-empty', '-m', 'init')
            git_dir = git._run(['rev-parse', '--git-dir'], d)
            rm_dir = Path(d, '.git', 'rebase-merge')
            rm_dir.mkdir()
            (rm_dir / 'msgnum').write_text('2\n')
            (rm_dir / 'end').write_text('5\n')
            op = git._detect_op_state(d, git_dir)
            self.assertEqual(op, ('rebase', '2/5'))

    def test_bisect_marker(self):
        with TemporaryDirectory() as d:
            _git(d, 'init', '-q', '-b', 'main')
            _git(d, 'commit', '-q', '--allow-empty', '-m', 'init')
            git_dir = git._run(['rev-parse', '--git-dir'], d)
            Path(d, '.git', 'BISECT_LOG').write_text('git bisect start\n')
            self.assertEqual(git._detect_op_state(d, git_dir), ('bisect', None))


if __name__ == '__main__':
    unittest.main()
