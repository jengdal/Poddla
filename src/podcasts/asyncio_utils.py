import asyncio
import logging

logger = logging.getLogger(__name__)


async def wait_lock_or_func(lock: asyncio.Lock, coro):
    lock_task = asyncio.create_task(lock.acquire())
    func_task = asyncio.create_task(coro)

    done, pending = await asyncio.wait(
        {lock_task, func_task},
        return_when=asyncio.FIRST_COMPLETED,
    )

    if lock_task in done:
        # Lock was acquired first
        func_task.cancel()
        try:
            await func_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("wait_lock_or_func: The watched coroutine failed.")
        return "lock", None
    else:
        # Function completed first
        func_result = func_task.result()
        # Cancel the lock attempt
        lock_task.cancel()
        try:
            await lock_task
        except asyncio.CancelledError:
            pass
        else:
            # Rare race: lock got acquired right as we cancelled — release it
            lock.release()
        return "func", func_result
