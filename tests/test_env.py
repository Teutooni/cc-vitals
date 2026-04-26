import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import tests  # noqa: F401
import env


class HostStateCache(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self._patches = [
            mock.patch.object(env, '_HOST_CACHE_FILE',
                              Path(self._tmp.name) / 'host.json'),
        ]
        for p in self._patches:
            p.start()
        # Reset the per-process memoization between tests.
        self._original_memo = env._HOST_STATE_MEMO
        env._HOST_STATE_MEMO = None

    def tearDown(self):
        env._HOST_STATE_MEMO = self._original_memo
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()

    def test_first_call_writes_cache(self):
        with mock.patch.object(env, '_detect_env_slow_uncached',
                               return_value='linux') as slow, \
             mock.patch.object(env, '_detect_distro_uncached',
                               return_value=('ubuntu', 'Ubuntu 22.04', ['debian'])):
            env_value = env.detect_environment()
            self.assertEqual(env_value, 'linux')
            slow.assert_called_once()
            self.assertTrue(env._HOST_CACHE_FILE.exists())

    def test_cache_hit_avoids_slow_detection(self):
        # Pre-populate the cache.
        with mock.patch.object(env, '_detect_env_slow_uncached',
                               return_value='linux'), \
             mock.patch.object(env, '_detect_distro_uncached',
                               return_value=('ubuntu', 'Ubuntu', ['debian'])):
            env.detect_environment()

        env._HOST_STATE_MEMO = None  # force reload from disk

        with mock.patch.object(env, '_detect_env_slow_uncached') as slow:
            env_value = env.detect_environment()
            self.assertEqual(env_value, 'linux')
            slow.assert_not_called()  # served from disk cache

    def test_stale_cache_recomputes(self):
        # Write a stale cache entry by hand.
        import platform
        import json
        env._HOST_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        env._HOST_CACHE_FILE.write_text(json.dumps({
            'host': platform.node(),
            'env_slow': 'linux',
            'distro_id': 'old',
            'pretty': 'Old',
            'id_like': [],
            'ts': time.time() - env._HOST_CACHE_TTL_SECONDS - 1,
        }))
        env._HOST_STATE_MEMO = None

        with mock.patch.object(env, '_detect_env_slow_uncached',
                               return_value='wsl') as slow, \
             mock.patch.object(env, '_detect_distro_uncached',
                               return_value=('arch', 'Arch', [])):
            env_value = env.detect_environment()
            self.assertEqual(env_value, 'wsl')
            slow.assert_called_once()

    def test_different_host_invalidates(self):
        import platform
        import json
        env._HOST_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        env._HOST_CACHE_FILE.write_text(json.dumps({
            'host': platform.node() + '-other',
            'env_slow': 'wsl',
            'distro_id': 'ubuntu',
            'pretty': 'Ubuntu',
            'id_like': [],
            'ts': int(time.time()),
        }))
        env._HOST_STATE_MEMO = None

        with mock.patch.object(env, '_detect_env_slow_uncached',
                               return_value='linux') as slow, \
             mock.patch.object(env, '_detect_distro_uncached',
                               return_value=('arch', 'Arch', [])):
            env.detect_environment()
            slow.assert_called_once()

    def test_docker_check_overrides_cache(self):
        # Cache says linux, but live /.dockerenv check should win.
        with mock.patch.object(env, '_detect_env_slow_uncached',
                               return_value='linux'), \
             mock.patch.object(env, '_detect_distro_uncached',
                               return_value=('ubuntu', 'Ubuntu', [])):
            env.detect_environment()
        env._HOST_STATE_MEMO = None

        with mock.patch.object(env.Path, 'exists', return_value=True):
            self.assertEqual(env.detect_environment(), 'docker')

    def test_get_linux_distro_uses_cache(self):
        with mock.patch.object(env, '_detect_env_slow_uncached',
                               return_value='linux'), \
             mock.patch.object(env, '_detect_distro_uncached',
                               return_value=('arch', 'Arch Linux', [])):
            distro_id, pretty, id_like = env.get_linux_distro()
            self.assertEqual(distro_id, 'arch')
            self.assertEqual(pretty, 'Arch Linux')

    def test_corrupt_cache_treated_as_miss(self):
        env._HOST_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        env._HOST_CACHE_FILE.write_text('{not json')
        env._HOST_STATE_MEMO = None

        with mock.patch.object(env, '_detect_env_slow_uncached',
                               return_value='linux') as slow, \
             mock.patch.object(env, '_detect_distro_uncached',
                               return_value=('a', 'A', [])):
            env.detect_environment()
            slow.assert_called_once()


if __name__ == '__main__':
    unittest.main()
