"""
Fan-out helper.

A dashboard needs profile + services + domains + unpaid invoices + open tickets.
Sequentially that is five WHMCS calls at ~1.1s each - about six seconds of
staring at a spinner. Run together they cost roughly as much as the slowest one.

The service layer is synchronous by design (it is also called from Celery and
management commands), so the parallelism lives here, in threads. WHMCS calls are
pure network waiting, so the GIL is not in the way; a thread pool gives the same
win as async without making every service function async.
"""

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from django.db import close_old_connections

logger = logging.getLogger(__name__)


def gather(tasks: dict[str, Callable[[], Any]], *, timeout: float = 25.0) -> dict[str, Any]:
    """
    Run each callable concurrently and return ``{name: result}``.

    A section that fails does not take the page down with it: its slot comes
    back as ``None`` and the error is logged. A dashboard missing its ticket
    count is far better than a dashboard that is a 500.
    """
    if not tasks:
        return {}

    results: dict[str, Any] = dict.fromkeys(tasks)

    with ThreadPoolExecutor(max_workers=min(len(tasks), 8)) as pool:
        futures = {pool.submit(_guard(func)): name for name, func in tasks.items()}
        try:
            for future in as_completed(futures, timeout=timeout):
                name = futures[future]
                try:
                    results[name] = future.result()
                except Exception:
                    logger.warning("dashboard section %r failed", name, exc_info=True)
        except TimeoutError:
            unfinished = [futures[f] for f in futures if not f.done()]
            logger.warning("dashboard sections timed out: %s", unfinished)

    return results


def _guard(func: Callable[[], Any]) -> Callable[[], Any]:
    def wrapper() -> Any:
        try:
            return func()
        finally:
            close_old_connections()

    return wrapper
