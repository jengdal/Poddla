import asyncio
import unittest

from podcasts.asyncio_utils import wait_lock_or_func


class WaitLockOrFuncTests(unittest.IsolatedAsyncioTestCase):
    async def test_lock_wins_when_uncontended(self):
        lock = asyncio.Lock()

        async def never_wakes():
            await asyncio.Event().wait()

        outcome, result = await wait_lock_or_func(lock, never_wakes())

        self.assertEqual(outcome, "lock")
        self.assertIsNone(result)
        self.assertTrue(lock.locked())

    async def test_func_wins_when_lock_is_held(self):
        lock = asyncio.Lock()
        await lock.acquire()

        async def wakes_immediately():
            return "the payload"

        try:
            outcome, result = await wait_lock_or_func(lock, wakes_immediately())
        finally:
            lock.release()

        self.assertEqual(outcome, "func")
        self.assertEqual(result, "the payload")

    async def test_func_raising_after_lock_already_won_does_not_propagate(self):
        # Regression test: the watched coroutine can finish (with an exception) in the same
        # tick as an uncontended lock.acquire(), landing both tasks in asyncio.wait()'s
        # "done" set together. Once we're in the "lock won" branch we already own the lock,
        # so nothing about the watched coroutine's own failure may be allowed to escape —
        # the caller would have no way to find out it needs to release what it's holding.
        lock = asyncio.Lock()

        async def raises_immediately():
            raise ValueError("boom")

        outcome, result = await wait_lock_or_func(lock, raises_immediately())

        self.assertEqual(outcome, "lock")
        self.assertIsNone(result)
        self.assertTrue(lock.locked())
