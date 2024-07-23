"""Custom logging class for Bermuda."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.bluetooth import MONOTONIC_TIME

if TYPE_CHECKING:
    import logging


class BermudaLogSpamLess:
    """
    A class to provide a way to cache specific log entries so we can rate-limit them.

    Log via this class, adding a "key" to each call, and we will rate-limit any later log
    messages that use the same key by the spam_interval defined in the constructor.
    """

    _logger: logging.Logger
    _interval: float
    _keycache = {}

    def __init__(self, logger: logging.Logger, spam_interval: float) -> None:
        self._logger = logger
        self._interval = spam_interval

    def _check_key(self, key):
        """
        Check if the given key has been used recently.

        Returns -1 if the message should be suppressed,
        but if the message should be logged it returns the number of attempted uses
        since last time it was sent - which might be zero.
        """
        if key in self._keycache:
            # key exists, check timestamps
            cache = self._keycache[key]
            if cache["stamp"] < MONOTONIC_TIME() - self._interval:
                # It's time to emit the message
                count = cache["count"]
                cache["count"] = 0
                cache["stamp"] = MONOTONIC_TIME()
                return count
            # We sent this message recently, don't spam
            cache["count"] += 1
            return -1
        else:
            # Key is completely new, store the new stamp and let it through
            self._keycache[key] = {
                "stamp": MONOTONIC_TIME(),
                "count": 0,
            }
            return 0

    def _prep_message(self, key, msg):
        """
        Checks if message should be logged and returns the message reformatted
        to indicate how many previous messages were supressed.
        """
        count = self._check_key(key)
        if count == 0:
            # No previously suppressed, just log it as-is.
            return msg
        elif count > 0:
            return f"{msg} ({count} previous messages suppressed)"
        return None

    def debug(self, key, msg, *args, **kwargs):
        """Send log message, if no log was issued with the same key recently."""
        newmsg = self._prep_message(key, msg)
        if newmsg is not None:
            self._logger.debug(newmsg, *args, **kwargs)

    def info(self, key, msg, *args, **kwargs):
        """Send log message, if no log was issued with the same key recently."""
        newmsg = self._prep_message(key, msg)
        if newmsg is not None:
            self._logger.info(newmsg, *args, **kwargs)

    def warning(self, key, msg, *args, **kwargs):
        """Send log message, if no log was issued with the same key recently."""
        newmsg = self._prep_message(key, msg)
        if newmsg is not None:
            self._logger.warning(newmsg, *args, **kwargs)

    def error(self, key, msg, *args, **kwargs):
        """Send log message, if no log was issued with the same key recently."""
        newmsg = self._prep_message(key, msg)
        if newmsg is not None:
            self._logger.error(newmsg, *args, **kwargs)
